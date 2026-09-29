"""Verified logical transfer, preserving opaque hashes and original JSON bytes.

Both source writers must be stopped: two SQLite files cannot provide one shared
snapshot. Backups use SQLite's backup API, including WAL, without source writes.
"""
from contextlib import ExitStack, closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import time

from sqlalchemy import delete, func, insert, select

from . import schema as s

CONTROL_TABLES = ("pairings", "runners", "bindings", "jobs", "model_steps", "query_embeddings", "job_events", "index_jobs", "storage_settings",
                  "service_policies", "public_reports", "public_limits")
INCIDENT_TABLES = ("incidents", "runs", "cards", "change_jobs", "card_search", "project_applications", "card_embeddings")
TABLE_NAMES = CONTROL_TABLES + INCIDENT_TABLES
ACTIVE_WORK = {"RUNNING", "CANCEL_REQUESTED", "RECOVERY_REQUIRED"}


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _fingerprint(rows):
    return hashlib.sha256(_canonical(sorted(rows, key=_canonical)).encode()).hexdigest()


def _snapshot(path, target):
    path = Path(path).resolve()
    if not path.is_file():
        raise ValueError("An existing source SQLite file is required")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source, closing(sqlite3.connect(target)) as copy:
        source.backup(copy)
        if copy.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("Source SQLite integrity check failed")


def read_sqlite(control_path, incident_path):
    if Path(control_path).resolve() == Path(incident_path).resolve():
        raise ValueError("Distinct control and incident files required")
    result = {}
    with tempfile.TemporaryDirectory(prefix="tracebridge-transfer-") as temporary:
        for group, source_path in ((CONTROL_TABLES, control_path), (INCIDENT_TABLES, incident_path)):
            copy = Path(temporary) / ("control.sqlite3" if group is CONTROL_TABLES else "incidents.sqlite3")
            _snapshot(source_path, copy)
            with closing(sqlite3.connect(copy)) as db:
                db.row_factory = sqlite3.Row
                present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                unknown = present - set(group)
                shadows = {"card_search_data", "card_search_idx", "card_search_content", "card_search_docsize", "card_search_config"}
                unknown = {name for name in unknown if not name.startswith("sqlite_") and name not in shadows}
                if unknown:
                    raise ValueError("Unsupported source tables: " + ",".join(sorted(unknown)))
                required = {"jobs", "runners", "bindings", "pairings", "model_steps"} if group is CONTROL_TABLES else {"incidents", "runs", "cards", "card_search", "change_jobs"}
                if required - present:
                    raise ValueError("Source schema is incomplete")
                for name in group:
                    result[name] = [dict(row) for row in db.execute(f'SELECT * FROM "{name}"')] if name in present else []
    return result


def inventory(records):
    return {"format": "tracebridge_storage_inventory_v1", "tables": {name: {"rows": len(records[name]), "sha256": _fingerprint(records[name])} for name in TABLE_NAMES},
            "active_jobs": sum(row["state"] in ACTIVE_WORK for row in records["jobs"]),
            "unknown_model_steps": sum(row["state"] in {"REQUESTED", "OUTCOME_UNKNOWN"} for row in records["model_steps"]),
            "unknown_query_embeddings": sum(row["state"] in {"REQUESTED", "OUTCOME_UNKNOWN"} for row in records["query_embeddings"])}


def require_frozen(records, source_frozen):
    if not source_frozen:
        raise ValueError("Stop source writers and explicitly confirm --source-frozen before transferring")
    if any(row["state"] in ACTIVE_WORK for row in records["jobs"]):
        raise ValueError("Resolve active/interrupted jobs and confirm local runners stopped before transfer")
    if any(row["state"] == "RUNNING" for row in records["index_jobs"]):
        raise ValueError("Stop and resolve active indexing before transfer")


def _compatible(name, row):
    table = s.metadata.tables[name]
    unknown = set(row) - set(table.c.keys())
    if unknown:
        raise ValueError("Unsupported columns in source " + name)
    values = dict(row)
    for column in table.c:
        if column.name not in values and column.default is not None and not column.default.is_callable:
            values[column.name] = column.default.arg
    if name == "card_embeddings":
        from ..semantic_memory import normalize_vector
        vector = normalize_vector(json.loads(row["vector_json"]))
        values.update(embedding=vector, dimensions=len(vector))
    return values


def postgres_records(storage):
    if not storage.postgres:
        raise ValueError("PostgreSQL target required")
    records = {}
    with storage.engine.connect().execution_options(isolation_level="REPEATABLE READ") as db, db.begin():
        for name in TABLE_NAMES:
            table = s.metadata.tables[name]
            columns = [column for column in table.c if name != "card_embeddings" or column.name not in {"embedding", "dimensions"}]
            records[name] = [dict(row) for row in db.execute(select(*columns)).mappings()]
    return records


def compare_records(expected, actual):
    mismatches = []
    snapshots = 0
    for name in TABLE_NAMES:
        # Added default scalar fields must not rewrite original payloads/hashes.
        prepared = [_compatible(name, row) for row in expected[name]]
        if name == "card_embeddings":
            prepared = [{key: value for key, value in row.items() if key not in {"embedding", "dimensions"}} for row in prepared]
        observed = actual[name]
        if name == "job_events":
            # Older jobs may lack history even in a database with newer events.
            # Allow exactly one explicit baseline per such job, while comparing
            # every original event byte-for-byte and rejecting arbitrary extras.
            missing = _jobs_without_history(expected)
            expected_ids = {row["id"] for row in prepared}
            extra = [row for row in observed if row["id"] not in expected_ids]
            valid = len(extra) == len(missing) and {row["job_id"] for row in extra} == set(missing)
            for row in extra:
                job = missing.get(row["job_id"])
                valid = valid and job is not None and row["kind"] == "MIGRATED_SNAPSHOT" and row["from_state"] is None
                valid = valid and row["to_state"] == (job or {}).get("state") and row["epoch"] == (job or {}).get("epoch")
                valid = valid and row["metadata_json"] == '{"history_before_migration":"NOT_RECORDED"}'
            if valid:
                observed = [row for row in observed if row["id"] in expected_ids]
                snapshots = len(extra)
        if _fingerprint(prepared) != _fingerprint(observed):
            mismatches.append(name)
    return {"status": "MATCH" if not mismatches else "MISMATCH", "mismatched_tables": mismatches,
            "legacy_history_snapshots": snapshots,
            "expected_rows": {name: len(expected[name]) for name in TABLE_NAMES}, "actual_rows": {name: len(actual[name]) for name in TABLE_NAMES}}


def _jobs_without_history(records):
    recorded = {row["job_id"] for row in records["job_events"]}
    return {row["id"]: row for row in records["jobs"] if row["id"] not in recorded}


def import_sqlite(storage, control_path, incident_path, *, source_frozen=False):
    records = read_sqlite(control_path, incident_path)
    require_frozen(records, source_frozen)
    if not storage.postgres:
        raise ValueError("Import target must be PostgreSQL")
    with storage.engine.begin() as db:
        # Exclude all other writers while checking and populating an empty target.
        db.exec_driver_sql("LOCK TABLE " + ",".join("tracebridge." + name for name in TABLE_NAMES) + " IN ACCESS EXCLUSIVE MODE")
        if any(db.execute(select(func.count()).select_from(s.metadata.tables[name])).scalar_one() for name in TABLE_NAMES):
            raise ValueError("Migration requires an empty target; no existing records are overwritten")
        for name in TABLE_NAMES:
            if name == "job_events":
                continue
            for row in records[name]:
                db.execute(insert(s.metadata.tables[name]).values(**_compatible(name, row)))
        # Empty-target import triggers only generated provisional events here.
        db.execute(delete(s.job_events))
        for row in records["job_events"]:
            db.execute(insert(s.job_events).values(**row))
        # Preserve imported IDs and allocate generated baselines above them.
        db.exec_driver_sql("SELECT setval(pg_get_serial_sequence('tracebridge.job_events','id'), COALESCE((SELECT MAX(id) FROM tracebridge.job_events),1), EXISTS(SELECT 1 FROM tracebridge.job_events))")
        for row in _jobs_without_history(records).values():
            db.execute(insert(s.job_events).values(job_id=row["id"], kind="MIGRATED_SNAPSHOT", to_state=row["state"], epoch=row["epoch"],
                occurred=time.time(), metadata_json='{"history_before_migration":"NOT_RECORDED"}'))
        # Validate row content before commit so mismatches leave an empty target.
        actual = {}
        for name in TABLE_NAMES:
            table = s.metadata.tables[name]
            columns = [column for column in table.c if name != "card_embeddings" or column.name not in {"embedding", "dimensions"}]
            actual[name] = [dict(row) for row in db.execute(select(*columns)).mappings()]
        checked = compare_records(records, actual)
        if checked["status"] != "MATCH":
            raise ValueError("Migration parity failed: " + ",".join(checked["mismatched_tables"]))
    return {"status": "IMPORTED", "verification": checked, "source": inventory(records),
            "notes": ["Original input hashes and JSON strings are preserved; no model calls were replayed.",
                      "Credentials remain scoped digests; native vectors are derived from the original vector_json."]}


def export_sqlite(storage, directory, *, source_frozen=False):
    records = postgres_records(storage)
    require_frozen(records, source_frozen)
    directory = Path(directory).resolve()
    if directory.exists():
        raise ValueError("Choose a new private export directory; existing files are never overwritten")
    directory.mkdir(parents=True, mode=0o700)
    control_path, incident_path = directory / "state.sqlite3", directory / "server-incidents.sqlite3"
    from .control import open_control_store
    from ..incident_memory import IncidentStore
    control = open_control_store(control_path)
    control.close()
    with IncidentStore(incident_path):
        pass
    with closing(sqlite3.connect(incident_path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS project_applications (work_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, record_json TEXT NOT NULL)")
    with ExitStack() as stack:
        control_db = stack.enter_context(closing(sqlite3.connect(control_path)))
        stack.enter_context(control_db)
        incident_db = stack.enter_context(closing(sqlite3.connect(incident_path)))
        stack.enter_context(incident_db)
        for name in TABLE_NAMES:
            if name == "job_events":
                continue
            db = control_db if name in CONTROL_TABLES else incident_db
            for row in records[name]:
                columns = list(row)
                db.execute(f'INSERT INTO "{name}" (' + ",".join('"' + column + '"' for column in columns) + ") VALUES (" + ",".join("?" for _ in columns) + ")", list(row.values()))
        control_db.execute("DELETE FROM job_events")
        for row in records["job_events"]:
            control_db.execute("INSERT INTO job_events(id,job_id,kind,from_state,to_state,epoch,occurred,metadata_json) VALUES (?,?,?,?,?,?,?,?)",
                [row[key] for key in ("id", "job_id", "kind", "from_state", "to_state", "epoch", "occurred", "metadata_json")])
    check = compare_records(records, read_sqlite(control_path, incident_path))
    if check["status"] != "MATCH":
        raise ValueError("Reverse export parity failed; preserve the export for inspection")
    result = {"status": "EXPORTED", "verification": check, "inventory": inventory(records)}
    (directory / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result
