"""Shared SQLite/PostgreSQL application contracts and incident revision fences."""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import json

from . import incident_memory as m


def application_run(prepared, run_id, executed_at):
    run = deepcopy(prepared)
    run.update(run_id=run_id, revision=prepared["revision"] + 1, executed_at=executed_at,
        fix_applied=True, fix_verified=False, cause_confirmed=False, run_status="COMPLETED",
        stop_reason="reviewed_candidate_applied",
        summary="검토한 후보를 동일한 원본 snapshot에 적용했습니다. 서비스 재기동·배포·회복 확인은 별도입니다.")
    run["change"].update(original_applied=True, verification_scope="REGISTERED_PROJECT_SNAPSHOT_ONLY")
    return run


def validate_application(application, job, prepared):
    if set(application) - {"project_id", "work_id", "source_run_id", "diff_sha256", "status", "paths", "source_snapshot_sha256",
            "applied_snapshot_sha256", "deployment_status", "service_recovery", "policy", "run", "persistence"}:
        raise ValueError("Unknown application fields")
    record = {key: deepcopy(value) for key, value in application.items() if key != "persistence"}
    for key in ("project_id", "work_id", "source_run_id"):
        m._id(record[key], key)
    run = record["run"]
    when = datetime.fromisoformat(run["executed_at"])
    if when.tzinfo is None:
        raise ValueError("Application time requires a timezone")
    expected = application_run(prepared, m._id(run["run_id"], "run_id"), run["executed_at"])
    if (job.get("record_format") != "project_change_job_v1" or job.get("candidate_fix_verified") is not True
            or record["project_id"] != job["project_id"] or record["work_id"] != job["work_id"]
            or record["source_run_id"] != job["result_run_id"] or record["status"] != "APPLIED"
            or record["diff_sha256"] != job["diff"]["sha256"]
            or record["source_snapshot_sha256"] != job["baseline"]["snapshot_sha256"]
            or record["applied_snapshot_sha256"] != job["candidate"]["snapshot_sha256"]
            or record["deployment_status"] != "NOT_ATTEMPTED" or record["service_recovery"] != "NOT_VERIFIED"
            or not record["paths"] or len(record["paths"]) > 6
            or any(not isinstance(path, str) or path.startswith("/") or ".." in path.split("/") or "\\" in path or ":" in path for path in record["paths"])
            or ("paths" in job["diff"] and sorted(record["paths"]) != sorted(job["diff"]["paths"]))
            or m.minimal_record(run) != m.minimal_record(expected)):
        raise ValueError("Application differs from the verified candidate")
    # Existing PC records are supported without inventing an earlier policy approval.
    if "policy" in record and record["policy"] != job["policy"]:
        raise ValueError("Application policy differs from the candidate")
    record["run"] = m.minimal_record(run)
    return record


@contextmanager
def transaction(store):
    if hasattr(store, "transaction"):
        with store.transaction() as db:
            yield db
    else:
        with store.connection:
            store.connection.execute("BEGIN IMMEDIATE")
            yield store.connection


def _application(db, project, work_id):
    if hasattr(db, "exec_driver_sql"):
        from sqlalchemy import select
        from .storage.schema import project_applications as table
        row = db.execute(select(table.c.record_json).where(table.c.project_id == project, table.c.work_id == work_id)).first()
    else:
        row = db.execute("SELECT record_json FROM project_applications WHERE project_id=? AND work_id=?", (project, work_id)).fetchone()
    return json.loads(row[0]) if row else None


def get_application(store, project, work_id):
    with transaction(store) as db:
        record = _application(db, project, work_id)
    if record is None:
        raise ValueError("Application not found in this project")
    return record


def list_applications(store, project):
    with transaction(store) as db:
        if hasattr(db, "exec_driver_sql"):
            from sqlalchemy import select
            from .storage.schema import project_applications as table
            rows = db.execute(select(table.c.record_json).where(table.c.project_id == project).order_by(table.c.work_id).limit(100)).all()
        else:
            rows = db.execute("SELECT record_json FROM project_applications WHERE project_id=? ORDER BY work_id LIMIT 100", (project,)).fetchall()
    return [json.loads(row[0]) for row in rows]


def _latest(db, project, incident):
    if hasattr(db, "exec_driver_sql"):
        from sqlalchemy import select
        from .storage.schema import incidents
        row = db.execute(select(incidents.c.latest_run_id).where(incidents.c.project_id == project,
            incidents.c.incident_id == incident).with_for_update()).first()
    else:
        row = db.execute("SELECT latest_run_id FROM incidents WHERE project_id=? AND incident_id=?", (project, incident)).fetchone()
    return row[0] if row else None


def _insert_run(store, db, run):
    if hasattr(db, "exec_driver_sql"):
        return store._insert_run(db, run)
    return store._insert_run(run)


def save_application(store, application):
    job = store.get_change(application["project_id"], application["work_id"])
    prepared = store.get_run(job["project_id"], job["result_run_id"])
    record = validate_application(application, job, prepared)
    with transaction(store) as db:
        # Lock the incident before checking duplicate receipts, including concurrent retries.
        latest = _latest(db, record["project_id"], job["incident_id"])
        old = _application(db, record["project_id"], record["work_id"])
        if old:
            if validate_application(old, job, prepared) != record:
                raise m.RunConflict("Different application already exists")
            return "ALREADY_SAVED"
        if latest != record["source_run_id"]:
            raise m.RunConflict("Application source is no longer the latest incident run")
        _insert_run(store, db, record["run"])
        values = dict(work_id=record["work_id"], project_id=record["project_id"], record_json=m._json(record))
        if hasattr(db, "exec_driver_sql"):
            from sqlalchemy import insert
            from .storage.schema import project_applications
            db.execute(insert(project_applications).values(**values))
        else:
            db.execute("INSERT INTO project_applications VALUES (?,?,?)", tuple(values.values()))
    return "SAVED"


def save_recovery(store, result):
    from .project_recovery import validate_recovery
    verification, run = result["verification"], result["run"]
    application = get_application(store, run["project_id"], verification["work_id"])
    source = store.get_run(run["project_id"], verification["source_run_id"])
    validate_recovery(result, application, source)
    with transaction(store) as db:
        latest = _latest(db, run["project_id"], run["incident_id"])
        if latest != source["run_id"]:
            # Exact same result may be acknowledged after a lost response or a later run.
            try:
                existing = store._get_run(db, run["project_id"], run["run_id"]) if hasattr(db, "exec_driver_sql") else store.get_run(run["project_id"], run["run_id"])
            except ValueError:
                raise m.RunConflict("Recovery source is no longer the latest incident run") from None
            if existing != m.minimal_record(run):
                raise m.RunConflict("Different recovery result already exists")
            return "ALREADY_SAVED"
        return _insert_run(store, db, run)
