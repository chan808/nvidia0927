"""Adversarial regression cases found during the second storage/RAG review."""
import json
import sqlite3
import time

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError

from test_control_plane import remote, report
from test_registered_project_repair import project
from test_postgres_storage import pg_url, pg_store, pg_remote, _sqlite_source
from test_semantic_memory import EmbeddingsDouble, saved
from test_incident_memory import record
from tracebridge.incident_memory import IncidentStore
from tracebridge.local_runner import GatewayError
from tracebridge.report_agent import FINISH_TOOL
from tracebridge.storage import open_control_store
from tracebridge.storage import schema as s
from tracebridge.storage.rag_jobs import enqueue_index, run_index_once
from tracebridge.storage.transfer import compare_records, import_sqlite, postgres_records, read_sqlite


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("action", ["edit", "reject"])
def test_review_change_during_search_discards_old_lexical_snapshot(backend, action, request, tmp_path):
    pg_store = request.getfixturevalue("pg_store") if backend == "postgres" else None
    store = pg_store.incidents() if backend == "postgres" else IncidentStore(tmp_path / "memory.sqlite3")
    with store:
        run = saved(store)
        from tracebridge.semantic_memory import index_reviewed_cards
        index_reviewed_cards(store, "agolive", EmbeddingsDouble())
        def edit():
            other = pg_store.incidents() if backend == "postgres" else IncidentStore(tmp_path / "memory.sqlite3")
            with other:
                changes = {"symptom": "결제 장애", "finding": "결제 오류", "next_action": "결제 경로 점검"} if action == "edit" else None
                other.review_card("agolive", run["run_id"], action, reviewer="owner", changes=changes)
        result = store.search("agolive", "회원가입이", embedding_client=EmbeddingsDouble(before_return=edit))
        assert result["cards"] == [], "A card changed after lexical ranking must not reuse its old match"


@pytest.mark.parametrize("change", [
    {"tools": ["malformed"]}, {"tools": None}, {"max_tokens": "bad"}, {"timeout": 0},
    {"messages": [{"role": "unsupported", "content": "bad"}]},
    {"tool_choice": {"type": "function", "function": {"name": "unregistered"}}},
    {"messages": [{"role": [], "content": "bad"}]}, {"max_tokens": True}, {"timeout": -1},
    {"tools": [{"type": "function", "function": {"name": []}}]},
    {"messages": [{"role": "user", "content": {"invalid": "shape"}}]},
    {"messages": [{"role": "assistant", "tool_calls": ["invalid"]}]},
    {"messages": [{"role": "tool", "content": "missing call id"}]},
])
def test_invalid_model_request_does_not_consume_call_budget(remote, change):
    owner, api, _, http, model, _, profile = remote
    report(owner, profile, use_nvidia=True)
    job = api.request("POST", "/v1/runner/jobs/claim", {})["job"]
    payload = {"messages": [{"role": "user", "content": "synthetic"}], "tools": [FINISH_TOOL], **change}
    with pytest.raises(GatewayError) as invalid:
        api.request("POST", f"/v1/runner/jobs/{job['job_id']}/model", {"epoch": job["epoch"], "step_id": "invalid", "payload": payload})
    assert invalid.value.status_code == 422
    assert model.calls == 0
    with http.app.state.storage.transaction() as tx:
        assert tx.model_count(job["job_id"]) == 0


@pytest.mark.parametrize("result", [{"run": []}, {"run": {}, "job": []}])
def test_invalid_result_envelope_is_rejected_without_partial_records(remote, result):
    owner, api, _, http, _, _, profile = remote
    report(owner, profile)
    job = api.request("POST", "/v1/runner/jobs/claim", {})["job"]
    with pytest.raises(GatewayError) as invalid:
        api.request("POST", f"/v1/runner/jobs/{job['job_id']}/result", {"epoch": job["epoch"], "result": result})
    assert invalid.value.status_code == 422
    with http.app.state.storage.transaction() as tx:
        assert tx.one(s.jobs, id=job["job_id"])["state"] == "RUNNING"


def test_result_cannot_skip_assigned_incident_revision(remote):
    owner, api, _, http, _, _, profile = remote
    report(owner, profile)
    job = api.request("POST", "/v1/runner/jobs/claim", {})["job"]
    result = record(project=profile.project_id, incident=job["input"]["incident_id"])
    result.update(run_id=job["input"]["run_id"], revision=100)
    with pytest.raises(GatewayError) as invalid:
        api.request("POST", f"/v1/runner/jobs/{job['job_id']}/result", {"epoch": job["epoch"], "result": result})
    assert invalid.value.status_code == 422
    with http.app.state.storage.incidents() as store:
        with pytest.raises(ValueError):
            store.get_run(profile.project_id, job["input"]["run_id"])


def test_index_status_can_recover_after_worker_stops(pg_remote):
    owner, _, _, http, model, _, profile = pg_remote
    queued = enqueue_index(http.app.state.storage, profile.project_id, model.embeddings.identity)
    with http.app.state.storage.transaction() as tx:
        tx.update(s.index_jobs, {"state": "RUNNING", "lease_token": "old", "lease_until": 0}, id=queued["index_job_id"])
    url = f"/v1/memory/{profile.project_id}/index-jobs/{queued['index_job_id']}"
    assert owner.request("GET", url)["state"] == "RECOVERY_REQUIRED"
    assert owner.request("POST", url + "/retry", {})["state"] == "QUEUED"


def test_cancelled_index_cannot_commit_late_vectors_or_overwrite_state(pg_remote):
    owner, _, _, http, _, _, profile = pg_remote
    storage = http.app.state.storage
    with storage.incidents() as store:
        saved(store, project=profile.project_id)
    job = enqueue_index(storage, profile.project_id, EmbeddingsDouble.identity)
    def stop():
        assert owner.request("POST", f"/v1/memory/{profile.project_id}/index-jobs/{job['index_job_id']}/cancel", {})["state"] == "CANCELLED"
    outcome = run_index_once(storage, EmbeddingsDouble(before_return=stop))
    assert outcome["state"] == "CANCELLED" and outcome["result"] is None
    with storage.transaction() as tx:
        assert tx.connection.execute(select(func.count()).select_from(s.card_embeddings)).scalar_one() == 0
    replacement = enqueue_index(storage, profile.project_id, EmbeddingsDouble.identity)
    assert replacement["index_job_id"] != job["index_job_id"]
    assert run_index_once(storage, EmbeddingsDouble())["state"] == "SUCCEEDED"


def test_no_batch_worker_cannot_finish_after_lease_expiration(pg_remote, monkeypatch):
    import tracebridge.storage.rag_jobs as worker
    _, _, _, http, _, _, profile = pg_remote
    storage = http.app.state.storage
    job = enqueue_index(storage, profile.project_id, EmbeddingsDouble.identity)
    def delayed(*args, **kwargs):
        with storage.transaction() as tx:
            tx.update(s.index_jobs, {"lease_until": 0}, id=job["index_job_id"])
        return {"status": "OK", "indexed": 0}
    monkeypatch.setattr(worker, "index_reviewed_cards", delayed)
    outcome = run_index_once(storage, EmbeddingsDouble())
    assert outcome["state"] == "RECOVERY_REQUIRED" and outcome["result"] is None


def test_legacy_job_history_import_can_pass_separate_verification(pg_store, tmp_path):
    control, incidents = _sqlite_source(tmp_path)
    with sqlite3.connect(control) as db:
        db.execute("DELETE FROM job_events")
    original = read_sqlite(control, incidents)
    import_sqlite(pg_store, control, incidents, source_frozen=True)
    assert compare_records(original, postgres_records(pg_store))["status"] == "MATCH"


def test_mixed_legacy_history_preserves_events_and_rejects_changed_baseline(pg_store, tmp_path):
    control, incidents = _sqlite_source(tmp_path)
    source = open_control_store(control)
    with source.transaction() as tx:
        original = dict(tx.one(s.jobs, id="job"))
        original.update(id="legacy-job", run_id="legacy-run", idempotency_key="legacy-key")
        tx.insert(s.jobs, **original)
        tx.delete(s.job_events, job_id="legacy-job")
    source.close()
    original = read_sqlite(control, incidents)
    import_sqlite(pg_store, control, incidents, source_frozen=True)
    comparison = compare_records(original, postgres_records(pg_store))
    assert comparison["status"] == "MATCH" and comparison["legacy_history_snapshots"] == 1
    with pg_store.transaction() as tx:
        tx.update(s.job_events, {"to_state": "SUCCEEDED"}, job_id="legacy-job", kind="MIGRATED_SNAPSHOT")
    assert compare_records(original, postgres_records(pg_store))["mismatched_tables"] == ["job_events"]


def test_native_store_rejects_card_link_to_other_project(pg_store):
    with pg_store.incidents() as store:
        run = saved(store)
    with pytest.raises(IntegrityError):
        with pg_store.transaction() as tx:
            tx.update(s.cards, {"project_id": "foreign"}, card_id=run["run_id"])


@pytest.mark.parametrize("link", ["embedding", "change", "application", "latest_run", "incident"])
def test_native_store_rejects_other_cross_project_references(pg_store, link):
    with pg_store.incidents() as store:
        run = saved(store)
        foreign = saved(store, project="foreign")
    with pytest.raises(IntegrityError):
        with pg_store.transaction() as tx:
            if link == "embedding":
                tx.insert(s.card_embeddings, project_id="foreign", card_id=run["run_id"], model_key="test", content_sha256="test", vector_json="[1,0]", indexed_at=0)
            elif link == "change":
                tx.insert(s.change_jobs, project_id="agolive", incident_id=run["incident_id"], work_id="work", source_run_id=run["run_id"], result_run_id=foreign["run_id"], content_hash="test", record_json="{}")
            elif link == "application":
                tx.insert(s.project_applications, project_id="foreign", work_id="absent", record_json="{}")
            elif link == "latest_run":
                tx.update(s.incidents, {"latest_run_id": foreign["run_id"]}, project_id="agolive", incident_id=run["incident_id"])
            else:
                tx.update(s.runs, {"project_id": "foreign"}, run_id=run["run_id"])


def test_existing_revision_upgrade_is_atomic_and_preserves_records(pg_url):
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from tracebridge.storage.incidents import PostgresIncidentStore
    engine = create_engine(pg_url.replace("postgresql://", "postgresql+psycopg://", 1), hide_parameters=True).execution_options(schema_translate_map={None: "tracebridge"})
    config = Config()
    config.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "tracebridge/storage/alembic"))
    try:
        with engine.begin() as db:
            db.exec_driver_sql("CREATE SCHEMA tracebridge")
            config.attributes["connection"] = db
            command.upgrade(config, "20260929_02")
        with PostgresIncidentStore(engine) as store:
            run = saved(store)
        with engine.begin() as db:
            db.exec_driver_sql("UPDATE tracebridge.cards SET project_id='foreign'")
        with pytest.raises(IntegrityError):
            open_control_store(pg_url)
        with engine.begin() as db:
            assert db.exec_driver_sql("SELECT version_num FROM tracebridge.alembic_version").scalar_one() == "20260929_02"
            assert db.exec_driver_sql("SELECT count(*) FROM pg_constraint WHERE conname='runs_scope_key' AND connamespace='tracebridge'::regnamespace").scalar_one() == 0
            db.exec_driver_sql("UPDATE tracebridge.cards SET project_id='agolive'")
        upgraded = open_control_store(pg_url)
        try:
            with upgraded.incidents() as store:
                assert store.get_run("agolive", run["run_id"])["summary"] == run["summary"]
                assert store.get_card("agolive", run["run_id"])["review"]["status"] == "APPROVED"
            with upgraded.transaction() as tx:
                assert tx.connection.exec_driver_sql("SELECT version_num FROM tracebridge.alembic_version").scalar_one() == "20260929_04"
        finally:
            upgraded.close()
    finally:
        engine.dispose()


def test_latest_job_is_scoped_when_projects_share_incident_identifier(remote):
    owner, _, _, http, _, _, profile = remote
    first = report(owner, profile)
    with http.app.state.storage.transaction() as tx:
        row = dict(tx.one(s.jobs, id=first["job_id"]))
        row.update(id="foreign-job", project_id="foreign", idempotency_key="foreign-key", created=time.time() + 1)
        tx.insert(s.jobs, **row)
        assert tx.latest_job(profile.project_id, first["incident_id"])["id"] == first["job_id"]


def test_native_change_history_uses_run_order_instead_of_random_identifiers(pg_store):
    runs = []
    with pg_store.incidents() as store:
        for revision in range(1, 5):
            run = record(incident=runs[0]["incident_id"] if runs else None)
            run.update(revision=revision, executed_at=f"2026-09-29T03:00:0{revision}+00:00")
            if revision in {2, 4}:
                run["run_id"] = ("z" if revision == 2 else "a") * 32
                for key in ("evidence", "observations", "report_clues"):
                    for item in run.get(key, []):
                        item["run_id"] = run["run_id"]
            store.save_run(run)
            runs.append(run)
    with pg_store.transaction() as tx:
        for number, (source, result) in enumerate(((runs[0], runs[1]), (runs[2], runs[3]))):
            tx.insert(s.change_jobs, work_id=f"work-{number}", project_id="agolive", incident_id=source["incident_id"],
                source_run_id=source["run_id"], result_run_id=result["run_id"], content_hash=f"hash-{number}", record_json=json.dumps({"work_id": f"work-{number}"}))
    with pg_store.incidents() as store:
        assert [job["work_id"] for job in store.list_changes("agolive")] == ["work-1", "work-0"]


@pytest.mark.parametrize("target", ["mysql://user:password@host/database", "", ":memory:"])
def test_invalid_database_target_never_creates_sqlite_fallback(tmp_path, monkeypatch, target):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        open_control_store(target)
    assert list(tmp_path.iterdir()) == []
