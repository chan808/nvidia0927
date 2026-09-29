"""Actual HTTP and file application, with an explicitly doubled patch proposer."""
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time

import pytest

from test_control_plane import remote, report
from test_registered_project_repair import project, BoundaryProposer
from tracebridge.change_policy import PolicyDenied
from tracebridge.incident_memory import IncidentStore, RunConflict
from tracebridge.local_runner import GatewayError
from tracebridge.project_lifecycle import get_application, save_application, save_recovery
from tracebridge.project_repair import apply_project_change, prepare_project_change, save_repair_policy, load_repair_policy, _snapshot, _snapshot_hash
from tracebridge.project_recovery import verify_project_recovery
from tracebridge.recovery_contract import RecoverySpec


@pytest.fixture
def live_api(project):
    profile, policy, _, _, root = project
    state = {"snapshot": "0" * 64, "accepted": lambda count: count < 5, "fail": False, "invalid": False, "oversized": False, "requests": []}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_GET(self):
            self.respond({"ok": state["accepted"](0) and state["accepted"](4) and not state["accepted"](6)})
        def do_POST(self):
            count = json.loads(self.rfile.read(int(self.headers["Content-Length"])))['count']
            self.respond({"accepted": state["accepted"](count) and not state["fail"], "private_value": "NEVER_SAVE_RESPONSE_VALUE"})
        def respond(self, data):
            state["requests"].append((self.command, self.path))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("X-TraceBridge-Snapshot-SHA256", state["snapshot"])
            self.end_headers()
            self.wfile.write(b"x" * 32000 if state["oversized"] else b"not-json" if state["invalid"] else json.dumps(data).encode())
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}"
    policy["recovery"] = {"samples": 2, "interval_seconds": 1, "checks": [
        {"id": "journey", "method": "POST", "read_only": True, "url": url + "/quotas", "json_body": {"count": 5}, "expected_json": {"accepted": True}},
        {"id": "regression", "read_only": True, "url": url + "/limits", "expected_json": {"ok": True}}]}
    save_repair_policy(policy, profile)
    def deploy():
        # This test server loads an immutable source revision, independent of later owner edits.
        module = {}
        exec((root / "app.py").read_text(encoding="utf-8"), module)
        state["accepted"] = module["accepted"]
        state["snapshot"] = _snapshot_hash(_snapshot(root, deadline=time.monotonic() + 10))
    try:
        yield state, deploy
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def local_apply(project):
    profile, _, source, db, _ = project
    job = prepare_project_change(source, profile, db_path=db, proposer=BoundaryProposer())["job"]
    application = apply_project_change(profile.project_id, job["work_id"], profile, db_path=db, expected_diff_sha256=job["diff"]["sha256"])
    assert application["persistence"]["status"] == "SAVED"
    return job, application


def remote_apply(remote):
    owner, _, runner, _, _, _, profile = remote
    first = report(owner, profile)
    runner.run_once()
    candidate = owner.request("POST", f"/v1/projects/{profile.project_id}/changes", {"text": "prepare candidate", "service": "api",
        "use_nvidia": True, "previous_job_id": first["job_id"]}, idempotency_key="recovery-candidate")
    runner.run_once()
    result = owner.request("GET", "/v1/jobs/" + candidate["job_id"])
    job = result["result"]["job"]
    application = apply_project_change(profile.project_id, job["work_id"], profile, db_path=runner.db_path, expected_diff_sha256=job["diff"]["sha256"])
    assert application["persistence"]["status"] == "SAVED"
    return candidate["job_id"], job, application


def queue_check(owner, profile, candidate, job, source, key):
    return owner.request("POST", f"/v1/projects/{profile.project_id}/recovery-checks", {
        "candidate_job_id": candidate, "expected_source_run_id": source,
        "policy_sha256": job["policy"]["sha256"]}, idempotency_key=key)


def recovery_commit_retry_scenario(remote, live_api, monkeypatch):
    """A crash after incident save must not lose the control-plane audit on retry."""
    from tracebridge.storage.control import ControlTransaction
    owner, _, runner, http, _, _, profile = remote
    state, deploy = live_api
    runner.publish_profiles()
    candidate, job, application = remote_apply(remote)
    runner.flush_applications()
    deploy()
    queued = queue_check(owner, profile, candidate, job, application["run"]["run_id"], "commit-retry")
    original_update = ControlTransaction.update
    def interrupted_update(self, table, values, **where):
        if table.name == "jobs" and values.get("state") == "SUCCEEDED" and where.get("id") == queued["job_id"]:
            raise RuntimeError("SIMULATED_CONTROL_COMMIT_INTERRUPTION")
        return original_update(self, table, values, **where)
    with monkeypatch.context() as patch:
        patch.setattr(ControlTransaction, "update", interrupted_update)
        with pytest.raises(RuntimeError, match="SIMULATED_CONTROL_COMMIT_INTERRUPTION"):
            runner.run_once()
    assert owner.request("GET", "/v1/jobs/" + queued["job_id"])["state"] == "RUNNING"
    observations = list(state["requests"])
    assert len(observations) == 4
    runner.flush_outbox()
    saved = owner.request("GET", "/v1/jobs/" + queued["job_id"])
    assert saved["state"] == "SUCCEEDED" and saved["incident_state"] == "RESOLVED"
    assert state["requests"] == observations
    assert sum(item["kind"] == "SERVICE_RECOVERY" for item in owner.request("GET", f"/v1/jobs/{queued['job_id']}/events")["events"]) == 1
    (runner.state_directory / (queued["job_id"] + ".ack.json")).unlink()
    runner.flush_outbox()
    assert sum(item["kind"] == "SERVICE_RECOVERY" for item in owner.request("GET", f"/v1/jobs/{queued['job_id']}/events")["events"]) == 1
    with http.app.state.storage.incidents() as store:
        assert len(store.get_incident(profile.project_id, application["run"]["incident_id"])["runs"]) == 4


def test_sqlite_recovery_audit_survives_control_commit_interruption(remote, live_api, monkeypatch):
    recovery_commit_retry_scenario(remote, live_api, monkeypatch)


def test_apply_then_live_journey_pass_and_failure_reopen(project, live_api):
    profile, _, _, db, _ = project
    state, deploy = live_api
    job, application = local_apply(project)
    deploy()
    assert state["accepted"](5)
    passed = verify_project_recovery(profile, job["work_id"], db_path=db)
    assert passed["verification"]["status"] == "PASSED"
    assert passed["run"]["fix_verified"] and not passed["run"]["cause_confirmed"]
    assert passed["verification"]["incident_state"] == "RESOLVED"
    assert state["requests"] == [("POST", "/quotas"), ("GET", "/limits")] * 2
    assert "NEVER_SAVE_RESPONSE_VALUE" not in json.dumps(passed)
    with IncidentStore(db) as store:
        assert save_recovery(store, passed) == "ALREADY_SAVED"
        assert get_application(store, profile.project_id, job["work_id"])["service_recovery"] == "NOT_VERIFIED"
    state["fail"] = True
    failed = verify_project_recovery(profile, job["work_id"], db_path=db)
    assert failed["verification"]["status"] == "FAILED"
    assert failed["verification"]["incident_state"] == "REOPENED"
    assert not failed["run"]["fix_verified"] and failed["run"]["fix_applied"]
    with IncidentStore(db) as store:
        assert store.get_incident(profile.project_id, application["run"]["incident_id"])["latest_run_id"] == failed["run"]["run_id"]
        assert len(store.get_incident(profile.project_id, application["run"]["incident_id"])["runs"]) == 5
        assert save_recovery(store, passed) == "ALREADY_SAVED"


@pytest.mark.parametrize("mode", ["old-runtime", "missing-runtime", "invalid-json", "unreachable", "oversized"])
def test_uncertain_runtime_or_response_keeps_incident_open(project, live_api, mode):
    profile, policy, _, db, _ = project
    state, deploy = live_api
    job, _ = local_apply(project)
    deploy()
    if mode == "old-runtime": state["snapshot"] = "0" * 64
    if mode == "missing-runtime": state["snapshot"] = ""
    if mode == "invalid-json": state["invalid"] = True
    if mode == "oversized": state["oversized"] = True
    if mode == "unreachable":
        # Keep the registered owner URL; simulate network loss instead of changing its policy.
        state["invalid"] = True
        import httpx
        from unittest.mock import patch
        with patch.object(httpx.Client, "stream", side_effect=httpx.ConnectError("offline")):
            result = verify_project_recovery(profile, job["work_id"], db_path=db)
    else:
        result = verify_project_recovery(profile, job["work_id"], db_path=db)
    assert result["verification"]["status"] == "INCONCLUSIVE"
    assert result["verification"]["incident_state"] == "OPEN" and not result["run"]["fix_verified"]


def test_registration_policy_source_changes_and_health_only_are_not_recovery(project, live_api):
    profile, policy, _, db, root = project
    state, deploy = live_api
    job, _ = local_apply(project)
    deploy()
    (root / "app.py").write_text("OWNER_NEW_EDIT = True\n")
    with pytest.raises(PolicyDenied, match="원본이 바뀌었"):
        verify_project_recovery(profile, job["work_id"], db_path=db)
    assert not state["requests"]


@pytest.mark.parametrize("mutation", ["health-only", "policy", "profile"])
def test_recovery_is_pinned_to_owner_policy_and_reported_api(project, live_api, mutation):
    profile, policy, source, db, _ = project
    state, deploy = live_api
    if mutation == "health-only":
        policy["recovery"]["checks"][0] = {**policy["recovery"]["checks"][1], "id": "journey"}
        save_repair_policy(policy, profile)
    job, _ = local_apply(project)
    deploy()
    if mutation == "policy":
        policy["recovery"]["samples"] = 3
        save_repair_policy(policy, profile)
    if mutation == "profile": profile.config_path.write_bytes(profile.config_path.read_bytes() + b"\n")
    with pytest.raises(PolicyDenied):
        verify_project_recovery(profile, job["work_id"], db_path=db)
    assert not state["requests"]


def test_remote_application_outbox_and_recovery_results(remote, live_api):
    owner, api, runner, http, model, _, profile = remote
    state, deploy = live_api
    runner.publish_profiles()
    candidate, job, application = remote_apply(remote)
    with pytest.raises(GatewayError, match="Report the PC application"):
        queue_check(owner, profile, candidate, job, application["run"]["run_id"], "before-receipt")
    runner.flush_applications()
    saved = owner.request("GET", "/v1/jobs/" + candidate)
    assert saved["original_applied"] and saved["service_recovery"] == "NOT_VERIFIED"
    assert saved["result"]["job"]["original_applied"] is False
    ack = runner.state_directory / (job["work_id"] + ".application-ack.json")
    ack.unlink()
    runner.flush_applications()
    assert ack.exists()
    events = owner.request("GET", f"/v1/jobs/{candidate}/events")["events"]
    assert sum(item["kind"] == "ORIGINAL_APPLIED" for item in events) == 1
    deploy()
    queued = queue_check(owner, profile, candidate, job, saved["latest_run_id"], "post-check")
    assert queue_check(owner, profile, candidate, job, saved["latest_run_id"], "post-check")["job_id"] == queued["job_id"]
    runner.run_once()
    passed = owner.request("GET", "/v1/jobs/" + queued["job_id"])
    assert passed["state"] == "SUCCEEDED" and passed["incident_state"] == "RESOLVED", passed
    assert model.calls == 1
    assert passed["result"]["verification"]["status"] == "PASSED"
    assert any(item["kind"] == "SERVICE_RECOVERY" for item in owner.request("GET", f"/v1/jobs/{queued['job_id']}/events")["events"])
    state["fail"] = True
    failed_job = queue_check(owner, profile, candidate, job, passed["latest_run_id"], "post-failure")
    runner.run_once()
    failed = owner.request("GET", "/v1/jobs/" + failed_job["job_id"])
    assert failed["state"] == "SUCCEEDED" and failed["service_recovery"] == "FAILED"
    assert failed["incident_state"] == "REOPENED"
    followup = owner.request("POST", f"/v1/projects/{profile.project_id}/reports", {"text": "회복 검사 실패를 조사해줘", "service": "api", "previous_job_id": failed_job["job_id"]}, idempotency_key="reopen-followup")
    with http.app.state.storage.transaction() as tx:
        from tracebridge.storage import schema as tables
        row = tx.one(tables.jobs, id=followup["job_id"])
        assert json.loads(row["body"])["previous_result"]["run_id"] == failed["latest_run_id"]


@pytest.mark.parametrize("field", ["project_id", "diff_sha256", "source_snapshot_sha256", "applied_snapshot_sha256", "paths", "policy", "run"])
def test_central_application_rejects_wrong_candidate_facts(remote, field):
    owner, api, runner, _, _, _, profile = remote
    candidate, job, application = remote_apply(remote)
    forged = deepcopy(application)
    if field == "paths": forged[field] = ["checks.py"]
    elif field == "policy": forged[field]["version"] += 1
    elif field == "run": forged[field]["fix_verified"] = True
    else: forged[field] = "wrong" if field == "project_id" else "0" * 64
    with pytest.raises(GatewayError) as rejected:
        api.request("POST", f"/v1/runner/projects/{profile.project_id}/applications", {"candidate_job_id": candidate, "application": forged})
    assert rejected.value.status_code == 422
    assert owner.request("GET", "/v1/jobs/" + candidate)["original_applied"] is False


def test_legacy_application_and_conflicting_duplicate_are_checked(project):
    profile, _, _, db, _ = project
    job, application = local_apply(project)
    legacy = deepcopy(application)
    legacy.pop("policy")
    # Exercise a legacy receipt in an independent server store with the same candidate history.
    with IncidentStore(db) as store:
        store.connection.execute("UPDATE project_applications SET record_json=? WHERE work_id=?", (json.dumps(legacy), job["work_id"]))
        store.connection.commit()
        assert save_application(store, legacy) == "ALREADY_SAVED"
        forged = deepcopy(legacy)
        forged["run"]["run_id"] = "f" * 32
        with pytest.raises(RunConflict): save_application(store, forged)


@pytest.mark.parametrize("mutation", ["sample", "outcome", "runtime", "window", "method", "run"])
def test_storage_rejects_forged_recovery_proof(project, live_api, mutation):
    profile, _, _, db, _ = project
    _, deploy = live_api
    job, _ = local_apply(project)
    deploy()
    result = verify_project_recovery(profile, job["work_id"], db_path=db)
    forged = deepcopy(result)
    check = forged["verification"]["checks"][0]
    if mutation == "sample": check["sample"] = 9
    if mutation == "outcome": check["outcome_matches"] = False
    if mutation == "runtime": check["runtime_snapshot_sha256"] = "0" * 64
    if mutation == "window": forged["verification"]["finished_at"] = forged["verification"]["started_at"]
    if mutation == "method":
        for item in forged["verification"]["checks"]: item["method"] = "GET"
    if mutation == "run": forged["run"]["cause_confirmed"] = True
    with IncidentStore(db) as store, pytest.raises((ValueError, RunConflict)):
        save_recovery(store, forged)


@pytest.mark.parametrize("url", ["https://example.com/quotas", "http://localhost/quotas", "http://127.0.0.1:8000/quotas?token=x", "http://user:pass@127.0.0.1:8000/quotas"])
def test_recovery_policy_url_and_read_only_limits(url):
    with pytest.raises(ValueError):
        RecoverySpec.model_validate({"checks": [{"id": "journey", "url": url, "read_only": True, "expected_json": {"ok": True}}]})


def test_owner_edit_during_window_is_preserved_and_keeps_incident_open(project, live_api):
    profile, _, _, db, root = project
    state, deploy = live_api
    job, _ = local_apply(project)
    deploy()
    def guard():
        if state["requests"] and "OWNER" not in (root / "app.py").read_text():
            (root / "app.py").write_text("OWNER_EDIT = True\n", encoding="utf-8")
    result = verify_project_recovery(profile, job["work_id"], db_path=db, execution_guard=guard)
    assert result["verification"]["source_unchanged"] is False
    assert result["verification"]["status"] == "INCONCLUSIVE"
    assert (root / "app.py").read_text().strip() == "OWNER_EDIT = True"


def test_application_retry_preserves_source_and_rejects_other_runner(remote, monkeypatch):
    owner, api, runner, http, _, _, profile = remote
    candidate, job, application = remote_apply(remote)
    original = (profile.root / "app.py").read_bytes()
    real_request = api.request
    def unavailable(method, path, payload=None, **kwargs):
        if path.endswith("/applications"):
            raise GatewayError("Temporary outage", 503)
        return real_request(method, path, payload, **kwargs)
    monkeypatch.setattr(api, "request", unavailable)
    with pytest.raises(GatewayError): runner.flush_applications()
    assert (profile.root / "app.py").read_bytes() == original
    monkeypatch.setattr(api, "request", real_request)
    code = owner.request("POST", "/v1/pairings", {"project_ids": [profile.project_id]})["code"]
    other = http.post("/v1/pairings/consume", json={"code": code}).json()
    response = http.post(f"/v1/runner/projects/{profile.project_id}/applications", headers={"Authorization": "Bearer " + other["token"]},
        json={"candidate_job_id": candidate, "application": application})
    assert response.status_code == 403
    runner.flush_applications()
    assert owner.request("GET", "/v1/jobs/" + candidate)["original_applied"]


def test_recovery_cancel_and_changed_policy_never_issue_http(remote, live_api):
    owner, _, runner, _, _, _, profile = remote
    state, deploy = live_api
    runner.publish_profiles()
    candidate, job, application = remote_apply(remote)
    runner.flush_applications()
    deploy()
    queued = queue_check(owner, profile, candidate, job, application["run"]["run_id"], "cancelled-check")
    owner.request("POST", f"/v1/jobs/{queued['job_id']}/cancel", {})
    assert runner.run_once() is None and not state["requests"]
    queued = queue_check(owner, profile, candidate, job, application["run"]["run_id"], "changed-check")
    policy, _ = load_repair_policy(profile)
    data = policy.model_dump()
    data["recovery"]["samples"] = 3
    save_repair_policy(data, profile)
    with pytest.raises(PolicyDenied): runner.run_once()
    assert not state["requests"]


def test_investigation_cannot_claim_applied_or_recovered(remote):
    owner, api, _, _, _, _, profile = remote
    queued = report(owner, profile)
    assignment = api.request("POST", "/v1/runner/jobs/claim", {})["job"]
    from tracebridge.report_agent import investigate_submission
    from tracebridge.report_contract import ReportContext
    payload = assignment["input"]
    result = investigate_submission(payload["text"], project_profile=profile, context=ReportContext(**payload["context"]),
        run_id=payload["run_id"], incident_id=payload["incident_id"])
    result["fix_verified"] = True
    with pytest.raises(GatewayError) as rejected:
        api.request("POST", f"/v1/runner/jobs/{queued['job_id']}/result", {"epoch": assignment["epoch"], "result": result})
    assert rejected.value.status_code == 422


def test_remote_recovery_page_requests_checks_and_shows_reopened_incident(remote, live_api, monkeypatch):
    from streamlit.testing.v1 import AppTest
    import tracebridge.local_runner
    owner, _, runner, _, _, _, profile = remote
    state, deploy = live_api
    runner.publish_profiles()
    candidate, job, _ = remote_apply(remote)
    runner.flush_applications()
    deploy()
    monkeypatch.setenv("TRACEBRIDGE_CONTROL_URL", "http://testserver")
    monkeypatch.setenv("TRACEBRIDGE_OPERATOR_TOKEN", "synthetic-owner")
    monkeypatch.setattr(tracebridge.local_runner, "ControlClient", lambda *args, **kwargs: owner)
    page = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "pages/3_Remote_Projects.py"), default_timeout=20)
    page.session_state.remote_job_id = candidate
    page.run()
    assert not page.exception
    page.button(key="verify_remote_recovery").click().run()
    assert not page.exception
    first_check = page.session_state["remote_job_id"]
    runner.run_once()
    page.button(key="refresh_remote_job").click().run()
    assert not page.exception
    assert any("PASSED" in item.value for item in page.markdown)
    state["fail"] = True
    page.button(key="repeat_remote_recovery").click().run()
    runner.run_once()
    page.button(key="refresh_remote_job").click().run()
    assert not page.exception
    assert any("REOPENED" in item.value for item in page.markdown)
    page.button(key="repeat_remote_recovery").click().run()
    assert page.session_state["remote_job_id"] != first_check


def test_separate_regression_and_read_only_permission_are_required():
    base = {"checks": [{"id": id_, "url": "http://127.0.0.1:8000/quotas", "read_only": True, "expected_json": {"ok": True}} for id_ in ("journey", "regression")]}
    for mutation in ("missing-regression", "same-check", "write-request", "numeric-permission", "empty-outcome"):
        data = deepcopy(base)
        if mutation == "missing-regression": data["checks"].pop()
        if mutation == "same-check": data["regression_check_id"] = "journey"
        if mutation == "write-request": data["checks"][0]["read_only"] = False
        if mutation == "numeric-permission": data["checks"][0]["read_only"] = 1
        if mutation == "empty-outcome": data["checks"][0]["expected_json"] = {}
        with pytest.raises(ValueError): RecoverySpec.model_validate(data)


def test_recovery_jobs_cannot_request_external_models_or_memory(remote, live_api):
    owner, api, runner, _, model, _, profile = remote
    runner.publish_profiles()
    candidate, job, application = remote_apply(remote)
    runner.flush_applications()
    queued = queue_check(owner, profile, candidate, job, application["run"]["run_id"], "no-model")
    assignment = api.request("POST", "/v1/runner/jobs/claim", {})["job"]
    for endpoint in ("model", "memory"):
        with pytest.raises(GatewayError) as rejected:
            api.request("POST", f"/v1/runner/jobs/{queued['job_id']}/{endpoint}", {"epoch": assignment["epoch"], "step_id": "denied", "payload": {}})
        assert rejected.value.status_code == 409
    assert model.calls == 1
