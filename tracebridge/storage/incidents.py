"""PostgreSQL incident repository: validated records and native scoped retrieval."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import json
import re
import time

from sqlalchemy import Float, bindparam, case, cast, delete, func, insert, literal_column, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB, array, insert as pg_insert
from sqlalchemy.exc import IntegrityError

from .. import incident_memory as m
from ..project_sources import redact
from ..semantic_memory import current_review_matches, document, fuse_candidates, normalize_vector
from . import schema as s


class PostgresIncidentStore:
    def __init__(self, engine, *, connection=None):
        self.engine, self.bound = engine, connection

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    @contextmanager
    def transaction(self):
        if self.bound is not None:
            yield self.bound
        else:
            with self.engine.begin() as connection:
                yield connection

    def _get_run(self, db, project_id, run_id):
        row = db.execute(select(s.runs.c.record_json).where(s.runs.c.project_id == m._id(project_id, "project_id"), s.runs.c.run_id == m._id(run_id, "run_id"))).first()
        if not row:
            raise ValueError("Run not found in this project")
        return json.loads(row[0])

    def _insert_run(self, db, result):
        record = m.minimal_record(result)
        digest = m._hash(m._json({key: value for key, value in result.items() if key not in {"persistence", "presentation", "manual_export"}}))
        legacy = m._hash(m._json({key: value for key, value in result.items() if key != "persistence"}))
        project, incident, run_id = record["project_id"], record["incident_id"], record["run_id"]
        when = record.get("executed_at") or datetime.now(timezone.utc).isoformat()
        old = db.execute(select(s.runs).where(s.runs.c.run_id == run_id)).mappings().first()
        if old:
            exact = {key: value for key, value in result.items() if key != "persistence"} == json.loads(old["record_json"])
            if old["content_hash"] not in {digest, legacy} and not exact:
                raise m.RunConflict("Run ID already has different contents")
            return "ALREADY_SAVED"
        statement = pg_insert(s.incidents).values(project_id=project, incident_id=incident, created_at=when, updated_at=when,
            latest_run_id=run_id, latest_revision=record["revision"])
        db.execute(statement.on_conflict_do_update(index_elements=[s.incidents.c.project_id, s.incidents.c.incident_id],
            set_={"updated_at": when, "latest_run_id": run_id, "latest_revision": record["revision"]},
            where=s.incidents.c.latest_revision < record["revision"]))
        db.execute(insert(s.runs).values(run_id=run_id, project_id=project, incident_id=incident, revision=record["revision"],
            executed_at=when, route=record["route"], run_status=record["run_status"], content_hash=digest, record_json=m._json(record)))
        card = m._new_card({**record, "executed_at": when})
        db.execute(insert(s.cards).values(card_id=run_id, project_id=project, incident_id=incident, run_id=run_id,
            review_status="PENDING", signals_json=m._json(record["signals"]), card_json=m._json(card)))
        return "SAVED"

    def save_run(self, result):
        try:
            with self.transaction() as db:
                return self._insert_run(db, result)
        except IntegrityError as exc:
            raise m.RunConflict("Incident revision already has a different run") from exc

    def save_project_change(self, job, run):
        digest = m.validate_project_change(job, run)
        try:
            with self.transaction() as db:
                old = db.execute(select(s.change_jobs.c.content_hash).where(s.change_jobs.c.work_id == job["work_id"])).first()
                if old:
                    if old[0] != digest:
                        raise m.RunConflict("Change job already has different contents")
                    return "ALREADY_SAVED"
                source = self._get_run(db, job["project_id"], job["source_run_id"])
                incident = db.execute(select(s.incidents).where(s.incidents.c.project_id == job["project_id"],
                    s.incidents.c.incident_id == job["incident_id"]).with_for_update()).mappings().first()
                if (not incident or source["incident_id"] != job["incident_id"] or m._hash(m._json(source)) != job["source_record_sha256"]
                        or incident["latest_run_id"] != source["run_id"] or run["revision"] != incident["latest_revision"] + 1):
                    raise m.RunConflict("Source run changed; preserve the obtained result for review")
                self._insert_run(db, run)
                db.execute(insert(s.change_jobs).values(work_id=job["work_id"], project_id=job["project_id"], incident_id=job["incident_id"],
                    source_run_id=job["source_run_id"], result_run_id=job["result_run_id"], content_hash=digest, record_json=m._json(job)))
        except IntegrityError as exc:
            raise m.RunConflict("Change or incident revision already exists") from exc
        return "SAVED"

    def get_run(self, project_id, run_id):
        with self.transaction() as db:
            return self._get_run(db, project_id, run_id)

    def get_evidence(self, project_id, run_id, evidence_id):
        for item in self.get_run(project_id, run_id)["evidence"]:
            if item["id"] == evidence_id:
                return item
        raise ValueError("Evidence not found in this run")

    def get_change(self, project_id, work_id):
        with self.transaction() as db:
            row = db.execute(select(s.change_jobs.c.record_json).where(s.change_jobs.c.project_id == project_id, s.change_jobs.c.work_id == work_id)).first()
        if not row:
            raise ValueError("Change job not found in this project")
        return json.loads(row[0])

    def find_change(self, project_id, source_run_id):
        with self.transaction() as db:
            row = db.execute(select(s.change_jobs.c.record_json).where(s.change_jobs.c.project_id == project_id, s.change_jobs.c.source_run_id == source_run_id)).first()
        return json.loads(row[0]) if row else None

    def list_changes(self, project_id, incident_id=None):
        statement = select(s.change_jobs.c.record_json).join(s.runs, s.runs.c.run_id == s.change_jobs.c.result_run_id)
        statement = statement.where(s.change_jobs.c.project_id == m._id(project_id, "project_id"))
        if incident_id is not None:
            statement = statement.where(s.change_jobs.c.incident_id == m._id(incident_id, "incident_id"))
            statement = statement.order_by(s.runs.c.revision.desc())
        else:
            statement = statement.order_by(s.runs.c.executed_at.desc(), s.runs.c.revision.desc())
        with self.transaction() as db:
            return [json.loads(row[0]) for row in db.execute(statement.order_by(s.change_jobs.c.work_id.desc()).limit(50))]

    def list_incidents(self, project_id):
        with self.transaction() as db:
            return [dict(row) for row in db.execute(select(s.incidents).where(s.incidents.c.project_id == project_id)
                .order_by(s.incidents.c.updated_at.desc()).limit(50)).mappings()]

    def get_incident(self, project_id, incident_id):
        with self.transaction() as db:
            row = db.execute(select(s.incidents).where(s.incidents.c.project_id == project_id, s.incidents.c.incident_id == incident_id)).mappings().first()
            if not row:
                raise ValueError("Incident not found in this project")
            runs = [dict(item) for item in db.execute(select(s.runs.c.run_id, s.runs.c.revision, s.runs.c.executed_at, s.runs.c.route, s.runs.c.run_status)
                .where(s.runs.c.project_id == project_id, s.runs.c.incident_id == incident_id).order_by(s.runs.c.revision)).mappings()]
        changes = [{key: item[key] for key in ("work_id", "source_run_id", "result_run_id", "status", "review_status", "candidate_fix_verified")}
                   for item in self.list_changes(project_id, incident_id)]
        return {**dict(row), "runs": runs, "changes": changes}

    def resume_result(self, project_id, incident_id):
        incident = self.get_incident(project_id, incident_id)
        result = self.get_run(project_id, incident["latest_run_id"])
        if not result.get("session"):
            raise ValueError("This run has no resumable intake session")
        result["history"] = [self.get_run(project_id, item["run_id"]) for item in incident["runs"][-7:-1]]
        return result

    def _get_card(self, db, project_id, card_id):
        row = db.execute(select(s.cards.c.card_json, s.runs.c.record_json, s.runs.c.executed_at)
            .join(s.runs, s.runs.c.run_id == s.cards.c.run_id).where(s.cards.c.project_id == project_id, s.cards.c.card_id == card_id)).first()
        if not row:
            raise ValueError("Card not found in this project")
        card, record = json.loads(row[0]), json.loads(row[1])
        if card["project_id"] != project_id:
            raise ValueError("Card project scope mismatch")
        record.setdefault("executed_at", row[2])
        change = db.execute(select(s.change_jobs.c.record_json).where(s.change_jobs.c.project_id == project_id,
            s.change_jobs.c.result_run_id == card["source_run_id"])).first()
        job = json.loads(change[0]) if change else None
        investigation = self._get_run(db, project_id, job["source_run_id"]) if job else None
        return m._enrich_card(card, record, job, investigation)

    def get_card(self, project_id, card_id):
        with self.transaction() as db:
            return self._get_card(db, project_id, card_id)

    def review_card(self, project_id, card_id, action, *, reviewer, changes=None, note=""):
        reviewer = m._text(m._id(reviewer, "reviewer"), 80)
        changes = {} if changes is None else changes
        if (not isinstance(changes, dict) or action not in {"approve", "edit", "reject"} or set(changes) - m.EDITABLE
                or action != "edit" and changes or action == "edit" and not changes):
            raise ValueError("Review edits may change wording and applicability only; factual verification is immutable")
        with self.transaction() as db:
            db.execute(select(s.cards.c.card_id).where(s.cards.c.project_id == project_id, s.cards.c.card_id == card_id).with_for_update())
            card = self._get_card(db, project_id, card_id)
            status = {"approve": "APPROVED", "edit": "EDITED", "reject": "REJECTED"}[action]
            revision = card["review"]["revision"] + 1
            event = {"action": action, "reviewer": reviewer, "at": datetime.now(timezone.utc).isoformat(), "note": m._text(note, 300),
                "before_status": card["review"]["status"], "before": {key: deepcopy(card[key]) for key in changes}, "changes": m._review_changes(card, changes, revision)}
            card.update(event["changes"])
            for key in changes:
                card["field_sources"][key] = {"kind": "REVIEW_EDIT", "run_id": card["source_run_id"], "review_revision": revision, "reviewer": reviewer, "at": event["at"]}
            card["review"] = {"status": status, "revision": revision, "reviewer": reviewer, "at": event["at"], "note": event["note"], "history": [*card["review"]["history"], event]}
            db.execute(update(s.cards).where(s.cards.c.project_id == project_id, s.cards.c.card_id == card_id).values(review_status=status, card_json=m._json(card)))
            db.execute(delete(s.card_search).where(s.card_search.c.card_id == card_id))
            db.execute(delete(s.card_embeddings).where(s.card_embeddings.c.project_id == project_id, s.card_embeddings.c.card_id == card_id))
            if status in m.REUSABLE:
                text = " ".join([card["symptom"], card["finding"], card["next_action"], *[step["step"] for step in card["check_sequence"]], *[term for values in card["signals"].values() for term in values]])
                db.execute(insert(s.card_search).values(card_id=card_id, search_text=text))
        return card

    def export_manual(self, project_id, *, card_ids=None):
        if card_ids is not None and (not isinstance(card_ids, list) or len(card_ids) > 1000):
            raise ValueError("Manual card selection needs at most 1000 IDs")
        with self.transaction() as db:
            ids = card_ids if card_ids is not None else list(db.execute(select(s.cards.c.card_id).where(s.cards.c.project_id == project_id,
                s.cards.c.review_status.in_(list(m.REUSABLE))).order_by(s.cards.c.card_id)).scalars())
            cards = [self._get_card(db, project_id, value) for value in dict.fromkeys(ids)]
        cards = [card for card in cards if card["review"]["status"] in m.REUSABLE]
        return {"status": "OK", "project_id": project_id, "card_count": len(cards), "card_ids": [card["card_id"] for card in cards], "markdown": m._render_manual(project_id, cards)}

    def _scope(self, project_id, scope_filters, exclude_incident_id):
        scope_filters = {} if scope_filters is None else scope_filters
        if (not isinstance(scope_filters, dict) or set(scope_filters) - {"service", "environment"}
                or any(not isinstance(value, str) or not 1 <= len(value) <= 80 for value in scope_filters.values())):
            raise ValueError("Known service/environment retrieval filters required")
        clauses = [s.cards.c.project_id == m._id(project_id, "project_id"), s.cards.c.review_status.in_(list(m.REUSABLE))]
        for key, value in scope_filters.items():
            clauses.append(cast(s.cards.c.card_json, JSONB)["scope"][key].astext == value)
        if exclude_incident_id:
            clauses.append(s.cards.c.incident_id != m._id(exclude_incident_id, "incident_id"))
        return clauses, scope_filters

    def search(self, project_id, query="", *, signals=None, exclude_incident_id=None, scope_filters=None, embedding_client=None):
        started = time.perf_counter()
        if not isinstance(query, str) or len(query) > 12000:
            raise ValueError("Search query must be at most 12000 characters")
        supplied = signals or m.signals_from_text(query)
        if (not isinstance(supplied, dict) or set(supplied) - set(m.SIGNAL_KEYS)
                or any(not isinstance(values, list) or any(not isinstance(value, str) or not 1 <= len(value) <= 200 for value in values) for values in supplied.values())):
            raise ValueError("Known bounded exact signals required")
        exact = {key: list(dict.fromkeys(supplied.get(key, [])))[:8] for key in m.SIGNAL_KEYS}
        exact["paths"] = [m._path(value) for value in exact["paths"]]
        clauses, scope_filters = self._scope(project_id, scope_filters, exclude_incident_id)
        expressions = [case((cast(s.cards.c.signals_json, JSONB)[key].has_any(array(exact[key])), weight), else_=0)
            for key, weight in (("error_codes", 4), ("paths", 1), ("exceptions", 4), ("stack_fingerprints", 6)) if exact[key]]
        ids, strategy = [], "NONE"
        review_revision = cast(s.cards.c.card_json, JSONB)["review"]["revision"].as_integer().label("review_revision")
        ranked = []
        with self.transaction() as db:
            if expressions:
                score = sum(expressions)
                ranked = db.execute(select(s.cards.c.card_id, review_revision).where(*clauses, score > 0).order_by(score.desc(), s.cards.c.card_id.desc()).limit(20)).all()
                ids = [row.card_id for row in ranked]
                if ids:
                    strategy = "EXACT"
            terms = [term for term in dict.fromkeys(re.findall(r"[^\W_]+(?:_[^\W_]+)*", redact(query), re.UNICODE)) if 2 <= len(term) <= 80][:12]
            if not ids and terms:
                tsquery = func.to_tsquery(literal_column("'simple'"), " | ".join(term + ":*" if re.fullmatch(r"[가-힣]+", term) else term for term in terms))
                vector = func.to_tsvector(literal_column("'simple'"), s.card_search.c.search_text)
                ranked = db.execute(select(s.cards.c.card_id, review_revision).join(s.card_search, s.card_search.c.card_id == s.cards.c.card_id)
                    .where(*clauses, vector.op("@@")(tsquery)).order_by(func.ts_rank_cd(vector, tsquery).desc(), s.cards.c.card_id.desc()).limit(20)).all()
                ids = [row.card_id for row in ranked]
                strategy = "POSTGRES_FTS"
        revisions = {row.card_id: row.review_revision for row in ranked}
        semantic = None
        selected = ids[:2]
        if embedding_client is not None:
            if strategy == "EXACT" and len(selected) == 2:
                semantic = {"status": "SKIPPED_EXACT_MATCH", "embedding_calls": 0}
            else:
                try:
                    semantic = self.vector_search(project_id, query, embedding_client, scope_filters=scope_filters, exclude_incident_id=exclude_incident_id)
                    vector_candidates = semantic.pop("candidates")
                    vector_ids = [item["card_id"] for item in vector_candidates]
                    for item in vector_candidates:
                        revisions.setdefault(item["card_id"], item["review_revision"])
                    if vector_ids:
                        selected = fuse_candidates(ids, vector_ids, exact=strategy == "EXACT")
                        strategy = "HYBRID" if ids else "VECTOR"
                except Exception as exc:
                    semantic = {"status": "FAILED", "error_type": type(exc).__name__}
        # Recheck current approval and scope after retrieval/provider I/O.
        cards = [self.get_card(project_id, value) for value in selected]
        cards = [card for card in cards if current_review_matches(card, revisions[card["card_id"]], scope_filters, exclude_incident_id)]
        for card in cards:
            card["review"].pop("history", None)
            for hypothesis in card["hypotheses"]:
                for prefix in ("supporting", "contradicting"):
                    hypothesis.pop(prefix + "_evidence_ids", None)
                    hypothesis[prefix + "_evidence_refs"] = [f"historical:{ref['run_id']}:{ref['evidence_id']}" for ref in hypothesis[prefix + "_evidence_refs"]]
        result = {"status": "OK", "strategy": strategy, "hit_count": len(cards), "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "query_signals": exact, "query_hash": m._hash(query), "cards": cards, "retrieval_scope": scope_filters}
        if semantic is not None:
            result["semantic"] = semantic
        return result

    def index_reviewed(self, provider, project_id, *, limit=50):
        if type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError("Index at most 200 cards per request")
        project_id = m._id(project_id, "project_id")
        with self.transaction() as db:
            rows = db.execute(select(s.cards.c.card_id, s.card_embeddings.c.content_sha256)
                .outerjoin(s.card_embeddings, (s.card_embeddings.c.card_id == s.cards.c.card_id)
                    & (s.card_embeddings.c.project_id == s.cards.c.project_id) & (s.card_embeddings.c.model_key == provider.identity))
                .where(s.cards.c.project_id == project_id, s.cards.c.review_status.in_(list(m.REUSABLE)))
                .order_by(s.card_embeddings.c.indexed_at.asc().nulls_first(), s.cards.c.card_id).limit(1000)).all()
        pending = []
        for row in rows:
            text, digest = document(self.get_card(project_id, row[0]))
            if text.strip() and digest != row[1]:
                pending.append((row[0], text, digest))
        remaining, pending = max(0, len(pending) - limit), pending[:limit]
        indexed = stale = calls = 0
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
            vectors = [normalize_vector(value) for value in vectors]
            if len({len(value) for value in vectors}) != 1:
                raise ValueError("Embedding dimensions changed within a batch")
            with self.transaction() as db:
                if hasattr(provider, "index_guard"):
                    provider.index_guard(db)
                profile_key = "embedding-dimensions:" + provider.identity
                db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(profile_key, 0))))
                profile = db.execute(select(s.storage_settings.c.value).where(s.storage_settings.c.key == profile_key)).scalar_one_or_none()
                if profile is not None and int(profile) != len(vectors[0]):
                    raise ValueError("Embedding dimensions changed; configure a new model identity")
                if profile is None:
                    existing_dimensions = list(db.execute(select(s.card_embeddings.c.dimensions).where(
                        s.card_embeddings.c.model_key == provider.identity, s.card_embeddings.c.dimensions.is_not(None)).distinct()).scalars())
                    if any(value != len(vectors[0]) for value in existing_dimensions):
                        raise ValueError("Embedding dimensions changed; configure a new model identity")
                    db.execute(insert(s.storage_settings).values(key=profile_key, value=str(len(vectors[0]))))
                for (card_id, _, digest), vector in zip(batch, vectors, strict=True):
                    db.execute(select(s.cards.c.card_id).where(s.cards.c.project_id == project_id, s.cards.c.card_id == card_id).with_for_update())
                    current = self._get_card(db, project_id, card_id)
                    if current["review"]["status"] not in m.REUSABLE or document(current)[1] != digest:
                        stale += 1
                        continue
                    values = dict(project_id=project_id, card_id=card_id, model_key=provider.identity, content_sha256=digest,
                        vector_json=json.dumps(vector, allow_nan=False), indexed_at=time.time(), embedding=vector, dimensions=len(vector))
                    statement = pg_insert(s.card_embeddings).values(**values)
                    db.execute(statement.on_conflict_do_update(index_elements=[s.card_embeddings.c.project_id, s.card_embeddings.c.card_id, s.card_embeddings.c.model_key],
                        set_={key: value for key, value in values.items() if key not in {"project_id", "card_id", "model_key"}}))
                    indexed += 1
        return {"status": "OK", "indexed": indexed, "stale_skipped": stale, "pending_within_scan": remaining,
                "scan_limit": 1000, "scan_truncated": len(rows) == 1000, "embedding_calls": calls, "model_key": provider.identity}

    def vector_search(self, project_id, query, provider, *, scope_filters, exclude_incident_id=None):
        clauses, _ = self._scope(project_id, scope_filters, exclude_incident_id)
        clauses.extend([s.card_embeddings.c.model_key == provider.identity, s.card_embeddings.c.embedding.is_not(None)])
        join = s.cards.join(s.card_embeddings, (s.cards.c.card_id == s.card_embeddings.c.card_id) & (s.cards.c.project_id == s.card_embeddings.c.project_id))
        with self.transaction() as db:
            any_row = db.execute(select(s.cards.c.card_id).select_from(join).where(*clauses).limit(1)).first()
        if not any_row or not query.strip():
            return {"status": "EMPTY_INDEX", "candidates": [], "embedding_calls": 0, "model_key": provider.identity}
        response = provider.embed([redact(query)[:3000]], input_type="query")
        if len(response) != 1:
            raise ValueError("One query embedding required")
        vector = normalize_vector(response[0])
        distance = s.card_embeddings.c.embedding.op("<=>", return_type=Float)(bindparam("query_vector", vector, type_=s.NativeVector()))
        with self.transaction() as db:
            rows = db.execute(select(s.cards.c.card_id, s.card_embeddings.c.content_sha256, distance.label("distance"))
                .select_from(join).where(*clauses, s.card_embeddings.c.dimensions == len(vector)).order_by(distance, s.cards.c.card_id).limit(20)).all()
            mismatch = db.execute(select(func.count()).select_from(join).where(*clauses, s.card_embeddings.c.dimensions != len(vector))).scalar_one()
        candidates, stale = [], 0
        for row in rows:
            card = self.get_card(project_id, row[0])
            if not current_review_matches(card, card["review"]["revision"], scope_filters, exclude_incident_id) or document(card)[1] != row[1]:
                stale += 1
                continue
            score = 1 - row[2]
            if score >= 0.45:
                candidates.append({"card_id": row[0], "score": round(score, 6), "review_revision": card["review"]["revision"]})
        return {"status": "OK", "candidates": candidates, "embedding_calls": 1, "model_key": provider.identity,
                "strategy": "PGVECTOR_EXACT", "candidate_count": len(rows), "scan_truncated": False,
                "stale_skipped": stale, "dimension_mismatches": mismatch}
