"""Real PostgreSQL/pgvector integration; explicitly configured isolated databases."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from tracebridge.control_plane import create_app
from tracebridge.local_runner import ControlClient, LocalRunner
from tracebridge.local_runner import GatewayError
from tracebridge.storage import open_control_store
from tracebridge.storage import schema as s
from tracebridge.storage.control import ControlTransaction
from test_control_plane import ModelDouble, report, test_remote_investigation_then_candidate_runs_real_snapshot_checks as remote_scenario
from test_registered_project_repair import project
from test_project_recovery import live_api, recovery_commit_retry_scenario, test_remote_application_outbox_and_recovery_results as recovery_scenario
from test_semantic_memory import EmbeddingsDouble, saved
from tracebridge.storage.rag_jobs import enqueue_index, recover_index, run_index_once
from tracebridge.storage.transfer import compare_records, export_sqlite, import_sqlite, postgres_records, read_sqlite


@pytest.fixture
def pg_url():
    configured = os.getenv("TRACEBRIDGE_TEST_DATABASE_URL")
    if not configured:
        pytest.skip("Set TRACEBRIDGE_TEST_DATABASE_URL for real PostgreSQL integration")
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url
    base = make_url(configured)
    name = "tracebridge_test_" + uuid4().hex
    admin_url = base.set(database="postgres", drivername="postgresql").render_as_string(hide_password=False)
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield base.set(database=name).render_as_string(hide_password=False)
    finally:
        # Only this fixture's generated database can be removed.
        assert name.startswith("tracebridge_test_") and len(name) == 49
        with psycopg.connect(admin_url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest.fixture
def pg_store(pg_url):
    store = open_control_store(pg_url)
    yield store
    store.close()


@pytest.fixture
def pg_remote(pg_url, project, tmp_path):
    profile, *_ = project
    model = ModelDouble()
    model.embeddings = EmbeddingsDouble()
    app = create_app(pg_url, operator_token="owner-" + "x" * 40, model_client=model, embedding_client=model.embeddings)
    with TestClient(app) as http:
        owner = ControlClient("http://testserver", "owner-" + "x" * 40, client=http)
        code = owner.request("POST", "/v1/pairings", {"project_ids": [profile.project_id]})["code"]
        credentials = http.post("/v1/pairings/consume", json={"code": code}).json()
        api = ControlClient("http://testserver", credentials["token"], client=http)
        runner = LocalRunner(api, tmp_path / "runner", project_ids=[profile.project_id], registry=profile.config_path.parent)
        runner.publish_profiles()
        yield owner, api, runner, http, model, pg_url, profile
    app.state.storage.close()


def test_real_postgres_investigation_candidate_and_audit(pg_remote):
    remote_scenario(pg_remote)
    owner, _, _, http, _, _, profile = pg_remote
    assert http.get("/health").json()["storage"] == "POSTGRESQL"
    jobs = owner.request("GET", f"/v1/projects/{profile.project_id}/jobs")["jobs"]
    assert len(jobs) == 2
    for job in jobs:
        events = owner.request("GET", f"/v1/jobs/{job['id']}/events")["events"]
        assert events[0]["kind"] == "SUBMITTED" and events[-1]["to_state"] == "SUCCEEDED"
    with http.app.state.storage.incidents() as store:
        assert len(store.get_incident(profile.project_id, jobs[0]["incident_id"])["runs"]) == 2


def test_real_postgres_application_recovery_and_reopen(pg_remote, live_api):
    recovery_scenario(pg_remote, live_api)


def test_real_postgres_recovery_commit_interruption_and_retry(pg_remote, live_api, monkeypatch):
    recovery_commit_retry_scenario(pg_remote, live_api, monkeypatch)


def test_parallel_claim_serializes_one_active_job_per_runner(pg_remote):
    owner, api, _, _, _, _, profile = pg_remote
    report(owner, profile)
    owner.request("POST", f"/v1/projects/{profile.project_id}/reports", {"text": "second", "service": "api"}, idempotency_key="second")
    with ThreadPoolExecutor(max_workers=8) as pool:
        responses = list(pool.map(lambda _: api.request("POST", "/v1/runner/jobs/claim", {})["job"], range(8)))
    assert sum(item is not None for item in responses) == 1
    assert owner.request("GET", f"/v1/projects/{profile.project_id}/jobs")["counts"] == {"RUNNING": 1, "QUEUED": 1}


def test_completion_and_incident_rollback_together_on_final_write_failure(pg_remote, monkeypatch):
    owner, _, runner, http, _, _, profile = pg_remote
    queued = report(owner, profile)
    original = ControlTransaction.update
    def fail_final(self, table, values, **where):
        if table is s.jobs and values.get("state") == "SUCCEEDED":
            raise RuntimeError("synthetic database outage at completion")
        return original(self, table, values, **where)
    monkeypatch.setattr(ControlTransaction, "update", fail_final)
    # Runner retains the already obtained result in its local outbox.
    with pytest.raises(RuntimeError, match="synthetic database outage"):
        runner.run_once()
    storage = http.app.state.storage
    with storage.transaction() as tx:
        assert tx.one(s.jobs, id=queued["job_id"])["state"] == "RUNNING"
        assert tx.connection.execute(select(func.count()).select_from(s.runs)).scalar_one() == 0
        assert tx.connection.execute(select(func.count()).select_from(s.cards)).scalar_one() == 0
        assert all(event["to_state"] != "SUCCEEDED" for event in tx.events(queued["job_id"], 0, 100))
    monkeypatch.setattr(ControlTransaction, "update", original)
    runner.flush_outbox()
    assert owner.request("GET", "/v1/jobs/" + queued["job_id"])["state"] == "SUCCEEDED"


def test_native_pgvector_fts_scope_and_immediate_rejection(pg_store):
    provider = EmbeddingsDouble()
    with pg_store.incidents() as store:
        correct = saved(store, summary="가입이 실패했습니다.")
        saved(store, environment="prod")
        saved(store, service="billing")
        saved(store, project="foreign")
        saved(store, review=None)
        saved(store, review="reject")
        scope = {"service": "api", "environment": "dev"}
        result = store.search("agolive", "가입", scope_filters=scope)
        assert result["strategy"] == "POSTGRES_FTS" and result["hit_count"] == 1
        assert store.search("agolive", "/api/users", scope_filters=scope)["cards"][0]["card_id"] == correct["run_id"]
        assert store.index_reviewed(provider, "agolive")["indexed"] == 3
        result = store.search("agolive", "계정을 만들 수 없어요", embedding_client=provider, scope_filters=scope)
        assert result["strategy"] == "VECTOR", result
        assert result["semantic"]["strategy"] == "PGVECTOR_EXACT"
        assert [card["card_id"] for card in result["cards"]] == [correct["run_id"]]
        calls = len(provider.calls)
        assert store.search("agolive", "계정을 만들 수 없어요", embedding_client=provider, scope_filters={"service": "foreign"})["hit_count"] == 0
        assert len(provider.calls) == calls
        store.review_card("agolive", correct["run_id"], "reject", reviewer="owner")
        assert store.search("agolive", "계정을 만들 수 없어요", embedding_client=provider, scope_filters=scope)["hit_count"] == 0


def test_review_change_during_embedding_cannot_commit_stale_native_vector(pg_store):
    with pg_store.incidents() as store:
        run = saved(store)
        def reject():
            with pg_store.incidents() as other:
                other.review_card("agolive", run["run_id"], "reject", reviewer="other-owner")
        provider = EmbeddingsDouble(before_return=reject)
        result = store.index_reviewed(provider, "agolive")
        assert result["indexed"] == 0 and result["stale_skipped"] == 1
        with pg_store.transaction() as tx:
            assert tx.connection.execute(select(func.count()).select_from(s.card_embeddings)).scalar_one() == 0


def test_postgres_embedding_failure_preserves_lexical_retrieval(pg_store):
    class Offline(EmbeddingsDouble):
        def embed(self, *args, **kwargs):
            raise TimeoutError("synthetic provider outage")
    with pg_store.incidents() as store:
        saved(store)
        store.index_reviewed(EmbeddingsDouble(), "agolive")
        result = store.search("agolive", "회원가입이", embedding_client=Offline())
        assert result["status"] == "OK" and result["hit_count"] == 1
        assert result["semantic"] == {"status": "FAILED", "error_type": "TimeoutError"}


def test_server_query_vector_cache_rechecks_reviews_and_limits_new_calls(pg_remote):
    owner, api, _, http, model, _, profile = pg_remote
    storage, provider = http.app.state.storage, model.embeddings
    with storage.incidents() as store:
        card = saved(store, project=profile.project_id, environment=profile.environment)
        store.index_reviewed(provider, profile.project_id)
    report(owner, profile, use_nvidia=True)
    job = api.request("POST", "/v1/runner/jobs/claim", {})["job"]
    def query(text):
        return api.request("POST", f"/v1/runner/jobs/{job['job_id']}/memory", {"epoch": job["epoch"], "step_id": "memory-1", "payload": {"query": text}})
    first = query("계정을 만들 수 없어요")
    assert first["semantic"]["embedding_calls"] == 1 and first["hit_count"] == 1
    replay = query("계정을 만들 수 없어요")
    assert replay["semantic"]["embedding_calls"] == 0 and replay["semantic"]["query_cache_hits"] == 1
    assert query("account creation problem")["semantic"]["embedding_calls"] == 1
    denied = query("계정을 등록 못했어요")
    assert denied["semantic"]["error_type"] == "QueryEmbeddingBudgetExhausted" and denied["semantic"]["embedding_calls"] == 0
    with storage.incidents() as store:
        store.review_card(profile.project_id, card["run_id"], "reject", reviewer="owner")
    assert query("계정을 만들 수 없어요")["hit_count"] == 0
    assert [kind for kind, _ in provider.calls].count("query") == 2


def test_background_index_endpoint_persists_request_and_worker_result(pg_remote):
    owner, _, _, http, model, _, profile = pg_remote
    storage = http.app.state.storage
    with storage.incidents() as store:
        saved(store, project=profile.project_id)
    calls = len(model.embeddings.calls)
    queued = owner.request("POST", f"/v1/memory/{profile.project_id}/index")
    assert queued["state"] == "QUEUED" and len(model.embeddings.calls) == calls
    assert owner.request("POST", f"/v1/memory/{profile.project_id}/index")["index_job_id"] == queued["index_job_id"]
    completed = run_index_once(storage, model.embeddings)
    assert completed["state"] == "SUCCEEDED" and completed["result"]["indexed"] == 1
    status = owner.request("GET", f"/v1/memory/{profile.project_id}/index-jobs/{queued['index_job_id']}")
    assert status == completed
    assert run_index_once(storage, model.embeddings) is None


def test_expired_index_worker_cannot_write_vectors_or_finish_recovered_job(pg_remote):
    owner, _, _, http, _, _, profile = pg_remote
    storage = http.app.state.storage
    with storage.incidents() as store:
        saved(store, project=profile.project_id)
    queued = enqueue_index(storage, profile.project_id, EmbeddingsDouble.identity)
    def interrupt():
        with storage.transaction() as tx:
            tx.update(s.index_jobs, {"lease_until": 0}, id=queued["index_job_id"])
    outcome = run_index_once(storage, EmbeddingsDouble(before_return=interrupt))
    assert outcome["state"] == "RECOVERY_REQUIRED" and outcome["error_type"] == "InterruptedIndexing"
    with storage.transaction() as tx:
        assert tx.connection.execute(select(func.count()).select_from(s.card_embeddings)).scalar_one() == 0
    recover_index(storage, profile.project_id, queued["index_job_id"])
    assert run_index_once(storage, EmbeddingsDouble())["state"] == "SUCCEEDED"


def test_embedding_dimension_change_requires_new_profile_identity(pg_store):
    with pg_store.incidents() as store:
        run = saved(store)
        store.index_reviewed(EmbeddingsDouble(), "agolive")
        store.review_card("agolive", run["run_id"], "edit", reviewer="owner", changes={"finding": "새로운 판정"})
        class Changed(EmbeddingsDouble):
            def embed(self, *args, **kwargs):
                return [[1, 0, 0]]
        with pytest.raises(ValueError, match="dimensions changed"):
            store.index_reviewed(Changed(), "agolive")


def _sqlite_source(tmp_path):
    import time
    from tracebridge.incident_memory import IncidentStore
    from test_incident_memory import record
    source = open_control_store(tmp_path / "source/state.sqlite3")
    with source.transaction() as tx:
        tx.insert(s.runners, id="runner", digest="a" * 64, projects='["agolive"]', expires=time.time() + 86400, revoked=0, last_seen=time.time())
        tx.insert(s.bindings, project_id="agolive", runner_id="runner", manifest='{"service_ids":["api"],"environment":"dev"}')
        tx.insert(s.jobs, id="job", project_id="agolive", incident_id="incident", run_id="run", kind="investigate", body='{"service":"api"}',
            input_hash="opaque-original-input", idempotency_key="key", state="FAILED", runner_id="runner", expires=time.time() + 86400, created=time.time())
        tx.insert(s.model_steps, job_id="job", step_id="uncertain", input_hash="opaque-model-input", state="OUTCOME_UNKNOWN", response=None)
    with IncidentStore(source.incident_target) as store:
        run = record()
        # This private field deliberately contributes to the original input hash
        # but is omitted from the stored minimal record.
        run["private_input"] = "synthetic private field, not available for recomputing the digest"
        store.save_run(run)
        store.review_card("agolive", run["run_id"], "approve", reviewer="owner")
        from tracebridge.semantic_memory import index_reviewed_cards
        index_reviewed_cards(store, "agolive", EmbeddingsDouble())
    source.close()
    return tmp_path / "source/state.sqlite3", tmp_path / "source/server-incidents.sqlite3"


def test_verified_transfer_preserves_opaque_hashes_json_events_and_reverse_export(pg_store, tmp_path):
    control, incidents = _sqlite_source(tmp_path)
    original = read_sqlite(control, incidents)
    assert import_sqlite(pg_store, control, incidents, source_frozen=True)["verification"]["status"] == "MATCH"
    assert compare_records(original, postgres_records(pg_store))["status"] == "MATCH"
    with pg_store.transaction() as tx:
        assert tx.one(s.model_steps, job_id="job", step_id="uncertain")["state"] == "OUTCOME_UNKNOWN"
        tx.update(s.jobs, {"state": "CANCELLED"}, id="job")
        events = tx.events("job", 0, 100)
        assert events[-1]["id"] > max(row["id"] for row in original["job_events"])
    exported = export_sqlite(pg_store, tmp_path / "reverse", source_frozen=True)
    assert exported["verification"]["status"] == "MATCH"
    assert compare_records(postgres_records(pg_store), read_sqlite(tmp_path / "reverse/state.sqlite3", tmp_path / "reverse/server-incidents.sqlite3"))["status"] == "MATCH"
    assert read_sqlite(control, incidents) == original


def test_transfer_refuses_active_work_unfrozen_source_and_nonempty_target(pg_store, tmp_path):
    control, incidents = _sqlite_source(tmp_path)
    with pytest.raises(ValueError, match="source-frozen"):
        import_sqlite(pg_store, control, incidents)
    source = open_control_store(control)
    with source.transaction() as tx:
        tx.update(s.jobs, {"state": "RUNNING"}, id="job")
    with pytest.raises(ValueError, match="Resolve active"):
        import_sqlite(pg_store, control, incidents, source_frozen=True)
    with source.transaction() as tx:
        tx.update(s.jobs, {"state": "FAILED"}, id="job")
    source.close()
    import_sqlite(pg_store, control, incidents, source_frozen=True)
    with pytest.raises(ValueError, match="empty target"):
        import_sqlite(pg_store, control, incidents, source_frozen=True)
    import sqlite3
    with sqlite3.connect(incidents) as db:
        db.execute("CREATE TABLE card_search_extra (value TEXT)")
    with pytest.raises(ValueError, match="Unsupported source tables"):
        read_sqlite(control, incidents)


def test_postgres_expired_lease_recovery_and_epoch_fencing(pg_remote):
    owner, api, _, http, _, _, profile = pg_remote
    queued = report(owner, profile)
    first = api.request("POST", "/v1/runner/jobs/claim", {})["job"]
    owner.request("POST", f"/v1/projects/{profile.project_id}/reports", {"text": "second", "service": "api"}, idempotency_key="second")
    with http.app.state.storage.transaction() as tx:
        tx.update(s.jobs, {"lease_until": 0}, id=first["job_id"])
    assert api.request("POST", "/v1/runner/jobs/claim", {})["job"] is None
    assert owner.request("GET", "/v1/jobs/" + queued["job_id"])["state"] == "RECOVERY_REQUIRED"
    owner.request("POST", "/v1/jobs/" + first["job_id"] + "/resume", {})
    resumed = api.request("POST", "/v1/runner/jobs/claim", {})["job"]
    assert resumed["job_id"] == first["job_id"] and resumed["epoch"] == 2
    with pytest.raises(GatewayError) as denied:
        api.request("POST", f"/v1/runner/jobs/{first['job_id']}/renew", {"epoch": first["epoch"]})
    assert denied.value.status_code == 409
    owner.request("POST", f"/v1/jobs/{first['job_id']}/cancel", {})
    api.request("POST", f"/v1/runner/jobs/{first['job_id']}/stopped", {"epoch": resumed["epoch"]})
    assert api.request("POST", "/v1/runner/jobs/claim", {})["job"] is not None


def test_query_timeout_is_durable_and_not_repeated(pg_remote, monkeypatch):
    owner, api, _, http, model, _, profile = pg_remote
    provider = model.embeddings
    with http.app.state.storage.incidents() as store:
        saved(store, project=profile.project_id, environment=profile.environment)
        store.index_reviewed(provider, profile.project_id)
    attempted = []
    def timeout(*args, **kwargs):
        attempted.append(True)
        raise TimeoutError("synthetic timeout")
    monkeypatch.setattr(provider, "embed", timeout)
    report(owner, profile, use_nvidia=True)
    job = api.request("POST", "/v1/runner/jobs/claim", {})["job"]
    request = {"epoch": job["epoch"], "step_id": "memory", "payload": {"query": "계정을 만들 수 없어요"}}
    url = f"/v1/runner/jobs/{job['job_id']}/memory"
    assert api.request("POST", url, request)["semantic"]["error_type"] == "TimeoutError"
    assert api.request("POST", url, request)["semantic"]["error_type"] == "QueryEmbeddingOutcomeUnknown"
    assert len(attempted) == 1
    events = owner.request("GET", f"/v1/jobs/{job['job_id']}/events")["events"]
    assert len([event for event in events if event["kind"] == "MEMORY_SEARCH"]) == 2
    assert "계정을" not in json.dumps(events, ensure_ascii=False)


def test_failed_import_rolls_back_every_target_table(pg_store, tmp_path):
    import sqlite3
    from sqlalchemy.exc import IntegrityError
    control, incidents = _sqlite_source(tmp_path)
    with sqlite3.connect(incidents) as db:
        db.execute("INSERT INTO cards(card_id,project_id,incident_id,run_id,review_status,signals_json,card_json) VALUES ('bad','agolive','absent','absent','PENDING','{}','{}')")
    with pytest.raises(IntegrityError):
        import_sqlite(pg_store, control, incidents, source_frozen=True)
    from tracebridge.storage.transfer import TABLE_NAMES
    with pg_store.transaction() as tx:
        assert all(tx.connection.execute(select(func.count()).select_from(s.metadata.tables[name])).scalar_one() == 0 for name in TABLE_NAMES)
