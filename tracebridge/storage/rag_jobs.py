"""Durable embedding jobs and a per-investigation query call budget.

Provider calls run outside transactions. Ambiguous calls require owner recovery;
only query vectors are cached, so every retrieval sees current review/scope.
"""
import hashlib
import json
import secrets
import time
from uuid import uuid4

from sqlalchemy import func, select, update

from ..project_sources import redact
from ..semantic_memory import index_reviewed_cards, normalize_vector
from . import schema as s


def expire_index(tx, *, project_id=None):
    stmt = update(s.index_jobs).where(s.index_jobs.c.state == "RUNNING", s.index_jobs.c.lease_until <= time.time())
    if project_id is not None:
        stmt = stmt.where(s.index_jobs.c.project_id == project_id)
    tx.connection.execute(stmt.values(state="RECOVERY_REQUIRED", error_type="InterruptedIndexing", lease_token=None))


def enqueue_index(storage, project_id, model_key, limit=50):
    if not storage.postgres:
        raise ValueError("Durable indexing requires PostgreSQL; use direct indexing for SQLite")
    with storage.transaction() as tx:
        if not tx.one(s.bindings, project_id=project_id, lock=True):
            raise ValueError("Project binding not found")
        expire_index(tx, project_id=project_id)
        old = tx.connection.execute(select(s.index_jobs).where(s.index_jobs.c.project_id == project_id,
            s.index_jobs.c.model_key == model_key, s.index_jobs.c.state.in_(["QUEUED", "RUNNING", "RECOVERY_REQUIRED"]))
            .order_by(s.index_jobs.c.created.desc()).limit(1)).mappings().first()
        if old:
            return index_summary(old)
        row = {"id": uuid4().hex, "project_id": project_id, "model_key": model_key, "state": "QUEUED", "limit_count": limit,
               "created": time.time(), "started": None, "result_json": None, "error_type": None, "lease_token": None, "lease_until": 0}
        tx.insert(s.index_jobs, **row)
        return index_summary(row)


def index_summary(row):
    return {"index_job_id": row["id"], "project_id": row["project_id"], "model_key": row["model_key"], "state": row["state"],
            "limit": row["limit_count"], "created": row["created"], "started": row["started"], "error_type": row["error_type"],
            "result": json.loads(row["result_json"]) if row["result_json"] else None}


def recover_index(storage, project_id, job_id):
    with storage.transaction() as tx:
        if not tx.one(s.bindings, project_id=project_id, lock=True):
            raise ValueError("Project binding not found")
        expire_index(tx, project_id=project_id)
        row = tx.one(s.index_jobs, id=job_id, project_id=project_id, lock=True)
        if not row or row["state"] not in {"FAILED", "RECOVERY_REQUIRED"}:
            raise ValueError("Only failed or interrupted indexing can be retried")
        busy = tx.connection.execute(select(s.index_jobs.c.id).where(s.index_jobs.c.project_id == project_id,
            s.index_jobs.c.model_key == row["model_key"], s.index_jobs.c.id != job_id,
            s.index_jobs.c.state.in_(["QUEUED", "RUNNING", "RECOVERY_REQUIRED"])).limit(1)).first()
        if busy:
            raise ValueError("Another indexing request already owns this scope")
        tx.update(s.index_jobs, {"state": "QUEUED", "lease_token": None, "lease_until": 0, "error_type": None, "started": None, "result_json": None}, id=job_id)
        return index_summary(tx.one(s.index_jobs, id=job_id))


def cancel_index(storage, project_id, job_id):
    with storage.transaction() as tx:
        row = tx.one(s.index_jobs, id=job_id, project_id=project_id, lock=True)
        if not row:
            raise ValueError("Index job not found in this project")
        if row["state"] != "SUCCEEDED":
            tx.update(s.index_jobs, {"state": "CANCELLED", "lease_token": None, "lease_until": 0}, id=job_id)
        return index_summary(tx.one(s.index_jobs, id=job_id))


def run_index_once(storage, provider):
    if not storage.postgres:
        raise ValueError("Durable indexing requires PostgreSQL; use direct indexing for SQLite")
    now = time.time()
    with storage.transaction() as tx:
        expire_index(tx)
        row = tx.connection.execute(select(s.index_jobs).where(s.index_jobs.c.state == "QUEUED", s.index_jobs.c.model_key == provider.identity)
            .order_by(s.index_jobs.c.created, s.index_jobs.c.id).limit(1).with_for_update(skip_locked=True)).mappings().first()
        if not row:
            return None
        row = dict(row)
        lease = secrets.token_hex(32)
        tx.update(s.index_jobs, {"state": "RUNNING", "started": now, "lease_token": lease, "lease_until": now + 60}, id=row["id"])
    try:
        with storage.incidents() as store:
            result = index_reviewed_cards(store, row["project_id"], FencedIndexProvider(storage, provider, row["id"], lease), limit=row["limit_count"])
        changes = {"state": "SUCCEEDED", "result_json": json.dumps(result), "error_type": None}
    except Exception as exc:
        changes = {"state": "FAILED", "error_type": type(exc).__name__}
    with storage.transaction() as tx:
        expire_index(tx, project_id=row["project_id"])
        tx.connection.execute(update(s.index_jobs).where(s.index_jobs.c.id == row["id"], s.index_jobs.c.state == "RUNNING",
            s.index_jobs.c.lease_token == lease, s.index_jobs.c.lease_until > time.time()).values(**changes, lease_token=None, lease_until=0))
        return index_summary(tx.one(s.index_jobs, id=row["id"]))


class FencedIndexProvider:
    def __init__(self, storage, provider, job_id, lease):
        self.storage, self.provider, self.job_id, self.lease = storage, provider, job_id, lease
        self.identity = provider.identity

    def index_guard(self, connection):
        row = connection.execute(select(s.index_jobs).where(s.index_jobs.c.id == self.job_id).with_for_update()).mappings().first()
        if not row or row["state"] != "RUNNING" or row["lease_token"] != self.lease or row["lease_until"] <= time.time():
            raise RuntimeError("IndexLeaseExpired")
        connection.execute(update(s.index_jobs).where(s.index_jobs.c.id == self.job_id).values(lease_until=time.time() + 60))

    def embed(self, texts, *, input_type):
        with self.storage.transaction() as tx:
            self.index_guard(tx.connection)
        return self.provider.embed(texts, input_type=input_type)


class QueryEmbeddingBudgetExhausted(RuntimeError):
    pass


class QueryEmbeddingOutcomeUnknown(RuntimeError):
    pass


class BudgetedQueryEmbeddings:
    def __init__(self, storage, provider, job, *, budget=2):
        self.storage, self.provider, self.job, self.budget = storage, provider, job, budget
        self.identity = provider.identity
        self.attempts = self.cache_hits = 0

    def embed(self, texts, *, input_type):
        if input_type != "query" or len(texts) != 1:
            raise ValueError("Job cache supports one query embedding only")
        texts = [redact(texts[0])[:3000]]
        query_hash = hashlib.sha256(json.dumps(texts, ensure_ascii=False).encode()).hexdigest()
        key = {"job_id": self.job["id"], "query_hash": query_hash, "model_key": self.identity}
        with self.storage.transaction() as tx:
            if not tx.active(self.job["id"], self.job["runner_id"], self.job["epoch"], time.time()):
                raise RuntimeError("ActiveMemoryLeaseRequired")
            old = tx.one(s.query_embeddings, **key)
            if old:
                if old["state"] == "SUCCEEDED":
                    self.cache_hits += 1
                    return [normalize_vector(json.loads(old["vector_json"]))]
                raise QueryEmbeddingOutcomeUnknown("Query embedding outcome pending or unknown")
            used = tx.connection.execute(select(func.count()).select_from(s.query_embeddings).where(s.query_embeddings.c.job_id == self.job["id"])).scalar_one()
            if used >= self.budget:
                raise QueryEmbeddingBudgetExhausted("Query embedding budget exhausted")
            tx.insert(s.query_embeddings, **key, state="REQUESTED", created=time.time())
        self.attempts += 1
        try:
            response = self.provider.embed(texts, input_type="query")
            if len(response) != 1:
                raise ValueError("One query vector required")
            vector = normalize_vector(response[0])
        except Exception as exc:
            with self.storage.transaction() as tx:
                tx.update(s.query_embeddings, {"state": "OUTCOME_UNKNOWN", "error_type": type(exc).__name__}, **key)
            raise
        with self.storage.transaction() as tx:
            tx.update(s.query_embeddings, {"state": "SUCCEEDED", "vector_json": json.dumps(vector, allow_nan=False)}, **key)
        return [vector]

    def usage(self):
        return {"embedding_calls": self.attempts, "query_cache_hits": self.cache_hits, "query_embedding_budget": self.budget}
