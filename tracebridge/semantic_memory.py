"""Optional bounded vector retrieval of reviewed, redacted incident summaries.

SQLite remains the source of truth. This small local index uses exact cosine
search, not an ANN service. No embedding call occurs without an explicit client.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import time
from urllib.parse import urlparse

import httpx

from .project_sources import redact


VECTOR_SCHEMA = """
CREATE TABLE IF NOT EXISTS card_embeddings (
    project_id TEXT NOT NULL, card_id TEXT NOT NULL REFERENCES cards(card_id),
    model_key TEXT NOT NULL, content_sha256 TEXT NOT NULL, vector_json TEXT NOT NULL,
    indexed_at REAL NOT NULL, PRIMARY KEY(project_id,card_id,model_key)
);
CREATE INDEX IF NOT EXISTS embeddings_project_model ON card_embeddings(project_id,model_key);
"""


def normalize_vector(values) -> list[float]:
    if not isinstance(values, list) or not 1 <= len(values) <= 4096:
        raise ValueError("Embedding must contain 1 to 4096 finite numbers")
    if any(type(value) not in (float, int) or not math.isfinite(value) for value in values):
        raise ValueError("Embedding contains a non-finite or invalid number")
    norm = math.hypot(*values)
    if not math.isfinite(norm) or norm == 0:
        raise ValueError("Embedding has no valid direction")
    return [value / norm for value in values]


def document(card: dict) -> tuple[str, str]:
    text = redact("\n".join([
        card["symptom"], card["finding"], card["next_action"],
        *[step["step"] for step in card.get("check_sequence", [])],
        *[value for values in card["signals"].values() for value in values],
    ]))[:3000]
    identity = {"text": text, "scope": card.get("scope", {}),
                "review_revision": card["review"]["revision"], "format": 1}
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return text, digest


class NimEmbeddingClient:
    """NIM /v1/embeddings with separate passage/query modes and no retries."""

    def __init__(self, url: str, model: str, *, api_key: str = "", client=None):
        parsed = urlparse(url)
        if (parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username
                or parsed.password or parsed.query or parsed.fragment):
            raise ValueError("Explicit embedding service URL required")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Embedding HTTP is limited to loopback; use HTTPS for remote NIM")
        if not isinstance(model, str) or not 1 <= len(model) <= 200:
            raise ValueError("Explicit embedding model required")
        self.url, self.model, self.api_key, self.client = url.rstrip("/"), model, api_key, client
        self.identity = hashlib.sha256((self.url + "\n" + model + "\nfloat-v1").encode()).hexdigest()

    def embed(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        if input_type not in {"query", "passage"} or not 1 <= len(texts) <= 16:
            raise ValueError("Bounded passage/query embedding inputs required")
        if any(not isinstance(text, str) or not 1 <= len(text) <= 3000 for text in texts):
            raise ValueError("Embedding text must contain 1 to 3000 characters")
        payload = {"model": self.model, "input": [redact(text) for text in texts],
                   "input_type": input_type, "encoding_format": "float"}
        headers = {"Authorization": "Bearer " + self.api_key} if self.api_key else {}
        if self.client is not None:
            response = self.client.post(self.url + "/embeddings", json=payload, headers=headers)
        else:
            with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
                response = client.post(self.url + "/embeddings", json=payload, headers=headers)
        response.raise_for_status()
        if len(response.content) > 2_000_000:
            raise ValueError("Embedding response exceeds the size limit")
        data = response.json().get("data", [])
        if (not isinstance(data, list) or len(data) != len(texts)
                or any(not isinstance(item, dict) or type(item.get("index")) is not int for item in data)
                or sorted(item["index"] for item in data) != list(range(len(texts)))):
            raise ValueError("Embedding response indices differ from the requested batch")
        vectors = [normalize_vector(item.get("embedding")) for item in sorted(data, key=lambda item: item["index"])]
        if len({len(vector) for vector in vectors}) != 1:
            raise ValueError("Embedding dimensions changed within a batch")
        return vectors


def embedding_client_from_env():
    if os.getenv("TRACEBRIDGE_EMBEDDINGS_ENABLED") != "1":
        return None
    return NimEmbeddingClient(os.environ["TRACEBRIDGE_EMBEDDING_URL"], os.environ["TRACEBRIDGE_EMBEDDING_MODEL"],
        api_key=os.getenv("TRACEBRIDGE_EMBEDDING_API_KEY", ""))


def index_reviewed_cards(store, project_id: str, provider, *, limit: int = 50) -> dict:
    """Explicit indexing only; recheck edits/rejection after each remote batch."""
    if hasattr(store, "index_reviewed"):
        return store.index_reviewed(provider, project_id, limit=limit)
    from .incident_memory import _id
    project_id = _id(project_id, "project_id")
    if type(limit) is not int or not 1 <= limit <= 200:
        raise ValueError("Index at most 200 cards per request")
    rows = store.connection.execute("""SELECT c.card_id,e.content_sha256 FROM cards c
        LEFT JOIN card_embeddings e ON e.project_id=c.project_id AND e.card_id=c.card_id AND e.model_key=?
        WHERE c.project_id=? AND c.review_status IN ('APPROVED','EDITED')
        ORDER BY e.indexed_at IS NOT NULL,e.indexed_at,c.rowid LIMIT 1000""", (provider.identity, project_id)).fetchall()
    pending = []
    for row in rows:
        card = store.get_card(project_id, row["card_id"])
        text, digest = document(card)
        if text.strip() and digest != row["content_sha256"]:
            pending.append((card["card_id"], text, digest))
    remaining = max(0, len(pending) - limit)
    pending = pending[:limit]
    indexed, stale, calls = 0, 0, 0
    started = time.monotonic()
    for offset in range(0, len(pending), 16):
        if time.monotonic() - started >= 20:
            remaining += len(pending) - offset
            break
        batch = pending[offset:offset + 16]
        vectors = provider.embed([item[1] for item in batch], input_type="passage")
        calls += 1
        if len(vectors) != len(batch):
            raise ValueError("Embedding batch cardinality changed")
        vectors = [normalize_vector(vector) for vector in vectors]
        if len({len(vector) for vector in vectors}) != 1:
            raise ValueError("Embedding dimensions changed within a batch")
        with store.connection:
            store.connection.execute("BEGIN IMMEDIATE")
            for (card_id, _, digest), vector in zip(batch, vectors, strict=True):
                current = store.get_card(project_id, card_id)
                if current["review"]["status"] not in {"APPROVED", "EDITED"} or document(current)[1] != digest:
                    stale += 1
                    continue
                store.connection.execute("""INSERT INTO card_embeddings VALUES (?,?,?,?,?,?)
                    ON CONFLICT(project_id,card_id,model_key) DO UPDATE SET
                    content_sha256=excluded.content_sha256,vector_json=excluded.vector_json,indexed_at=excluded.indexed_at""",
                    (project_id, card_id, provider.identity, digest, json.dumps(vector, allow_nan=False), time.time()))
                indexed += 1
    eligible = store.connection.execute("SELECT COUNT(*) FROM cards WHERE project_id=? AND review_status IN ('APPROVED','EDITED')", (project_id,)).fetchone()[0]
    return {"status": "OK", "indexed": indexed, "stale_skipped": stale, "pending_within_scan": remaining,
            "eligible_cards": eligible, "scan_limit": 1000, "scan_truncated": eligible > 1000,
            "embedding_calls": calls, "model_key": provider.identity}


def current_review_matches(card, revision, scope_filters, exclude_incident_id):
    """Return only the reviewed version that actually earned its ranking."""
    return (card["review"]["status"] in {"APPROVED", "EDITED"}
            and card["review"]["revision"] == revision
            and card["incident_id"] != exclude_incident_id
            and all(card.get("scope", {}).get(key) == value for key, value in scope_filters.items()))


def vector_candidates(store, project_id: str, query: str, provider, *, scope_filters: dict,
                      exclude_incident_id: str | None, limit: int = 500) -> dict:
    clauses = ["c.project_id=?", "e.model_key=?", "c.review_status IN ('APPROVED','EDITED')"]
    args = [project_id, provider.identity]
    for key, value in scope_filters.items():
        clauses.append(f"json_extract(c.card_json,'$.scope.{key}')=?")
        args.append(value)
    if exclude_incident_id:
        clauses.append("c.incident_id<>?")
        args.append(exclude_incident_id)
    rows = store.connection.execute(f"""SELECT c.card_id,e.content_sha256,e.vector_json FROM cards c
        JOIN card_embeddings e ON e.card_id=c.card_id AND e.project_id=c.project_id
        WHERE {' AND '.join(clauses)} ORDER BY e.indexed_at DESC,c.card_id LIMIT ?""", [*args, limit + 1]).fetchall()
    if not rows or not query.strip():
        return {"status": "EMPTY_INDEX", "candidates": [], "embedding_calls": 0, "model_key": provider.identity}
    response = provider.embed([redact(query)[:3000]], input_type="query")
    if len(response) != 1:
        raise ValueError("One query embedding required")
    vector = normalize_vector(response[0])
    candidates, stale, dimensions = [], 0, 0
    for row in rows[:limit]:
        card = store.get_card(project_id, row["card_id"])
        if not current_review_matches(card, card["review"]["revision"], scope_filters, exclude_incident_id) or document(card)[1] != row["content_sha256"]:
            stale += 1
            continue
        saved = normalize_vector(json.loads(row["vector_json"]))
        if len(saved) != len(vector):
            dimensions += 1
            continue
        score = sum(a * b for a, b in zip(saved, vector, strict=True))
        # This is retrieval confidence, never evidence that the causes are equal.
        if score >= 0.45:
            candidates.append({"card_id": row["card_id"], "score": round(score, 6), "review_revision": card["review"]["revision"]})
    candidates.sort(key=lambda item: (-item["score"], item["card_id"]))
    return {"status": "OK", "candidates": candidates[:20], "embedding_calls": 1,
            "scanned": min(len(rows), limit), "scan_truncated": len(rows) > limit,
            "stale_skipped": stale, "dimension_mismatches": dimensions, "model_key": provider.identity}


def fuse_candidates(lexical: list[str], semantic: list[str], *, exact: bool) -> list[str]:
    scores = {}
    for ranking in (lexical, semantic):
        for rank, card_id in enumerate(ranking, start=1):
            scores[card_id] = scores.get(card_id, 0) + 1 / (60 + rank)
    ranked = sorted(scores, key=lambda card_id: (-scores[card_id], card_id))
    # Exact request/error fingerprints always precede semantic similarity.
    return [*lexical, *[card_id for card_id in ranked if card_id not in lexical]][:2] if exact else ranked[:2]
