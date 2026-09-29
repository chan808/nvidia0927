"""Anonymous intake through real runner files; model is an explicit test double."""
from copy import deepcopy
import json
from uuid import uuid4

import pytest
from sqlalchemy import select, update

from test_control_plane import remote, project
from tracebridge.service_workflow import decide
from tracebridge.storage import schema as s
from tracebridge.incident_memory import IncidentStore
from tracebridge.local_runner import GatewayError
from test_project_recovery import live_api
from tracebridge.project_repair import save_repair_policy
from tracebridge.report_agent import DEFAULT_MODEL, FINISH_TOOL


def enable(remote, **changes):
    owner, _, _, _, _, _, profile = remote
    current = owner.request("GET", f"/v1/projects/{profile.project_id}/service-policy")
    policy = {**current["policy"], "enabled": True, "services": ["api"], **changes}
    return owner.request("PUT", f"/v1/projects/{profile.project_id}/service-policy",
        {"expected_revision": current["revision"], "policy": policy})


def body(**changes):
    return {"text": "다섯 개만 넣었는데 실패해요. QUOTA_BOUNDARY requestId=quota-001",
        "service": "api", "context": {"occurred_at": "2026-09-29T12:00:00+09:00"}, **changes}


def send(remote, payload=None, key=None):
    http, profile = remote[3], remote[-1]
    return http.post(f"/v1/public/projects/{profile.project_id}/reports", json=payload or body(),
        headers={"Idempotency-Key": key or uuid4().hex})


def status(remote, receipt):
    http, profile = remote[3], remote[-1]
    return http.get(f"/v1/public/projects/{profile.project_id}/reports/{receipt['report_id']}",
        headers={"Authorization": "Bearer " + receipt["receipt_token"]})


def test_public_intake_disabled_by_default_and_no_operator_access(remote):
    assert send(remote).status_code == 404
    enable(remote)
    receipt = send(remote).json()
    queued = status(remote, receipt).json()
    assert queued["status"] == "QUEUED" and queued["can_answer"] is False
    assert remote[3].get("/v1/projects", headers={"Authorization": "Bearer " + receipt["receipt_token"]}).status_code == 401
    assert remote[3].get(f"/report/{remote[-1].project_id}").status_code == 200


def test_retry_and_duplicate_have_separate_receipts_and_one_job(remote):
    enable(remote)
    key = uuid4().hex
    first = send(remote, key=key).json()
    retry = send(remote, key=key).json()
    duplicate = send(remote).json()
    assert first == retry and first["report_id"] != duplicate["report_id"]
    assert first["receipt_token"] != duplicate["receipt_token"]
    assert len(remote[0].request("GET", f"/v1/projects/{remote[-1].project_id}/jobs")["jobs"]) == 1
    assert send(remote, body(text="different"), key).status_code == 409


@pytest.mark.parametrize("change", [{"service": "other"}, {"context": {"environment": "prod"}},
    {"context": {"occurred_at": "2026-09-29T12:00:00"}}, {"text": " "}, {"use_nvidia": True}, {"policy_id": "foreign"}])
def test_public_input_cannot_choose_permissions_or_scope(remote, change):
    enable(remote)
    response = send(remote, body(**change))
    assert response.status_code in {404, 422}
    assert remote[4].calls == 0


def test_receipt_token_scope_expiry_and_rotation(remote):
    enable(remote)
    receipt = send(remote).json()
    wrong = {**receipt, "receipt_token": "x" * 64}
    assert status(remote, wrong).status_code == 404
    other = send(remote, body(text="다른 제보")).json()
    assert status(remote, {**receipt, "receipt_token": other["receipt_token"]}).status_code == 404
    assert remote[3].get(f"/v1/public/projects/other/reports/{receipt['report_id']}",
        headers={"Authorization": "Bearer " + receipt["receipt_token"]}).status_code == 404
    with remote[3].app.state.storage.transaction() as tx:
        tx.update(s.public_reports, {"expires": 1}, id=receipt["report_id"])
    assert status(remote, receipt).status_code == 404


def test_public_observation_to_automatic_candidate_and_private_memory(remote):
    enable(remote, use_nvidia=True, automation="PREPARE", repair_policy_id="quota-repair")
    receipt = send(remote, body(allow_external_analysis=True)).json()
    remote[2].run_once()
    jobs = remote[0].request("GET", f"/v1/projects/{remote[-1].project_id}/jobs")["jobs"]
    assert len(jobs) == 2
    assert jobs[0]["assessment"]["difficulty"] in {"MEDIUM", "SMALL"}
    remote[2].run_once()
    public = status(remote, receipt).json()
    assert public["status"] == "WAITING_REVIEW", public
    serialized = json.dumps(public)
    assert all(value not in serialized for value in ("app.py", "checks.py", "QUOTA_BOUNDARY", "quota-001", "source_run_id", "diff", "model", "root_job_id"))
    candidate = remote[0].request("GET", "/v1/jobs/" + jobs[0]["id"])["result"]
    assert candidate["job"]["candidate_fix_verified"] and not candidate["job"]["original_applied"]
    with IncidentStore(remote[3].app.state.storage.incident_target) as store:
        card = store.get_card(remote[-1].project_id, candidate["run"]["run_id"])
        assert card["knowledge_quality"] == "CANDIDATE_VERIFIED"
        assert card["review"]["status"] == "PENDING"


def test_model_requires_both_owner_and_reporter_permission(remote):
    enable(remote, use_nvidia=True, automation="PREPARE", repair_policy_id="quota-repair")
    receipt = send(remote).json()
    remote[2].run_once()
    assert remote[4].calls == 0 and status(remote, receipt).json()["status"] == "NEEDS_REVIEW"
    assert len(remote[0].request("GET", f"/v1/projects/{remote[-1].project_id}/jobs")["jobs"]) == 1


def test_changed_policy_does_not_dispatch_automatic_repair(remote):
    enable(remote, use_nvidia=True, automation="PREPARE", repair_policy_id="quota-repair")
    receipt = send(remote, body(allow_external_analysis=True)).json()
    enable(remote, enabled=False)
    remote[2].run_once()
    assert status(remote, receipt).json()["status"] == "UNRESOLVED"
    assert status(remote, receipt).json()["can_answer"] is False
    assert remote[4].calls == 0
    assert len(remote[0].request("GET", f"/v1/projects/{remote[-1].project_id}/jobs")["jobs"]) == 1


def test_rough_report_followup_idempotency_and_isolation(remote):
    enable(remote)
    receipt = send(remote, body(text="뭔가 안 돼요", context={})).json()
    remote[2].run_once()
    assert status(remote, receipt).json()["status"] == "NEEDS_CONTEXT"
    assert status(remote, receipt).json()["can_answer"] is True
    path = f"/v1/public/projects/{remote[-1].project_id}/reports/{receipt['report_id']}/answers"
    headers = {"Authorization": "Bearer " + receipt["receipt_token"], "Idempotency-Key": uuid4().hex}
    assert remote[3].post(path, json=body(), headers={**headers, "Authorization": "Bearer bad"}).status_code == 404
    response = remote[3].post(path, json=body(), headers=headers)
    assert response.status_code == 202, response.text
    assert remote[3].post(path, json=body(), headers=headers).json() == response.json()
    assert remote[3].post(path, json=body(text="different answer"), headers=headers).status_code == 409
    remote[2].run_once()
    assert status(remote, receipt).json()["answers_remaining"] == 9


def test_queue_and_durable_rate_limits(remote):
    enable(remote, pending_limit=1)
    assert send(remote).status_code == 202
    assert send(remote, body(text="different report")).status_code == 429
    enable(remote, pending_limit=20, hourly_limit=2)
    # The first policy's accepted request counts toward the same durable hour bucket.
    assert send(remote, body(text="other report")).status_code == 202
    assert send(remote, body(text="third report")).status_code == 429


def test_policy_revision_and_nonproduction_application_gates(remote):
    initial = enable(remote)
    with pytest.raises(GatewayError) as stale:
        remote[0].request("PUT", f"/v1/projects/{remote[-1].project_id}/service-policy",
            {"expected_revision": 0, "policy": initial["policy"]})
    assert stale.value.status_code == 409
    with pytest.raises(GatewayError) as unregistered:
        enable(remote, automation="APPLY_NONPROD", use_nvidia=True, repair_policy_id="quota-repair")
    assert unregistered.value.status_code == 422


def test_failure_is_recorded_and_never_reported_as_resolved(remote, monkeypatch):
    enable(remote)
    receipt = send(remote).json()
    import tracebridge.local_runner as runner_module
    monkeypatch.setattr(runner_module, "investigate_submission", lambda *a, **kw: (_ for _ in ()).throw(ValueError("private details")))
    with pytest.raises(ValueError):
        remote[2].run_once()
    public = status(remote, receipt).json()
    assert public["status"] == "UNRESOLVED" and public["can_answer"] is False and "private details" not in json.dumps(public)


def test_decision_uses_current_facts_and_keeps_risk_separate():
    run = {"run_id": "one", "correlation": "EXACT_ID", "run_status": "COMPLETED", "route": "GUIDANCE",
        "observed_status": 422, "diagnosis_type": "expected_validation", "responsibility": {"status": "INPUT_OMISSION"},
        "log_scope": {"aggregate": {"complete": True}}, "evidence": [{"id": "verified"}]}
    assert decide(run)["action"] == "GUIDANCE"
    bad = deepcopy(run)
    bad["responsibility"]["status"] = "UNCONFIRMED"
    assert decide(bad)["action"] == "NEEDS_CONTEXT"
    bad["log_scope"]["aggregate"]["conflicts"] = True
    assert decide(bad)["action"] == "NEEDS_CONTEXT"
    run.update(route="WORK_CANDIDATE", observed_status=500, diagnosis_type="migration_missing")
    assert decide(run)["action"] == "NEEDS_REVIEW" and decide(run)["assessment"]["risk"] == "HIGH"


def automatic_recovery_scenario(remote, project, live_api):
    policy, root = project[1], project[-1]
    policy["auto_apply_nonprod"] = True
    save_repair_policy(policy, remote[-1])
    remote[2].publish_profiles()
    enable(remote, use_nvidia=True, automation="APPLY_NONPROD", repair_policy_id="quota-repair", auto_review_recovered=True)
    receipt = send(remote, body(allow_external_analysis=True)).json()
    remote[2].run_once()
    jobs = remote[0].request("GET", f"/v1/projects/{remote[-1].project_id}/jobs")["jobs"]
    assert len(jobs) == 2
    remote[2].run_once()
    assert "count <= 5" in (root / "app.py").read_text()
    jobs = remote[0].request("GET", f"/v1/projects/{remote[-1].project_id}/jobs")["jobs"]
    assert len(jobs) == 3 and jobs[0]["state"] == "QUEUED"
    assert status(remote, receipt).json()["status"] == "QUEUED"
    live_api[1]()  # Test service explicitly loads applied code; no deployment automation.
    remote[2].run_once()
    assert status(remote, receipt).json()["status"] == "RESOLVED"
    recovery = remote[0].request("GET", "/v1/jobs/" + jobs[0]["id"])["result"]
    with remote[3].app.state.storage.incidents() as store:
        card = store.get_card(remote[-1].project_id, recovery["run"]["run_id"])
        assert card["review"]["status"] == "APPROVED"
        assert card["knowledge_quality"] == "SERVICE_RECOVERY_VERIFIED"
    # Re-delivery must not create another repair/recovery or reapply source.
    remote[2].flush_applications()
    assert len(remote[0].request("GET", f"/v1/projects/{remote[-1].project_id}/jobs")["jobs"]) == 3
    return receipt


def test_automatic_nonproduction_application_http_recovery_and_knowledge(remote, project, live_api):
    automatic_recovery_scenario(remote, project, live_api)


def test_automatic_application_requires_fresh_server_and_pc_permissions(remote, project, live_api, monkeypatch):
    policy = project[1]
    policy["auto_apply_nonprod"] = True
    save_repair_policy(policy, remote[-1])
    remote[2].publish_profiles()
    enable(remote, use_nvidia=True, automation="APPLY_NONPROD", repair_policy_id="quota-repair")
    receipt = send(remote, body(allow_external_analysis=True)).json()
    remote[2].run_once()
    import tracebridge.local_runner as local
    prepare = local.prepare_project_change
    def revoke_after_candidate(*args, **kwargs):
        result = prepare(*args, **kwargs)
        enable(remote, automation="PREPARE")
        return result
    monkeypatch.setattr(local, "prepare_project_change", revoke_after_candidate)
    remote[2].run_once()
    assert "count < 5" in (project[-1] / "app.py").read_text()
    assert status(remote, receipt).json()["status"] == "WAITING_REVIEW"


def test_duplicate_receipt_follows_latest_shared_incident_without_exposing_answers(remote):
    enable(remote)
    first = send(remote, body(text="동작이 안 돼요", context={})).json()
    second = send(remote, body(text="동작이 안 돼요", context={})).json()
    remote[2].run_once()
    path = f"/v1/public/projects/{remote[-1].project_id}/reports/{first['report_id']}/answers"
    response = remote[3].post(path, json=body(text="PRIVATE_FOLLOWUP requestId=quota-001"), headers={
        "Authorization": "Bearer " + first["receipt_token"], "Idempotency-Key": uuid4().hex})
    assert response.status_code == 202
    assert status(remote, second).json()["status"] == "QUEUED"
    remote[2].run_once()
    assert "PRIVATE_FOLLOWUP" not in json.dumps(status(remote, second).json())


def test_routing_retry_after_commit_interruption_creates_one_child(remote, monkeypatch):
    enable(remote, use_nvidia=True, automation="PREPARE", repair_policy_id="quota-repair")
    send(remote, body(allow_external_analysis=True))
    service = remote[3].app.state.public_service
    enqueue = service.enqueue
    def fail_after_child(*args, **kwargs):
        queued = enqueue(*args, **kwargs)
        if args[1] == "prepare_change":
            raise RuntimeError("commit interrupted")
        return queued
    monkeypatch.setattr(service, "enqueue", fail_after_child)
    with pytest.raises(RuntimeError):
        remote[2].run_once()
    monkeypatch.setattr(service, "enqueue", enqueue)
    remote[2].run_once()  # Flush cached root result, then execute its single child.
    jobs = remote[0].request("GET", f"/v1/projects/{remote[-1].project_id}/jobs")["jobs"]
    assert len(jobs) == 2 and all(job["state"] == "SUCCEEDED" for job in jobs)
    remote[2].flush_outbox()
    assert len(remote[0].request("GET", f"/v1/projects/{remote[-1].project_id}/jobs")["jobs"]) == 2


def test_owner_settings_screen_enables_scoped_public_page(remote, monkeypatch):
    from pathlib import Path
    from streamlit.testing.v1 import AppTest
    import tracebridge.local_runner as local
    monkeypatch.setenv("TRACEBRIDGE_CONTROL_URL", "http://testserver")
    monkeypatch.setenv("TRACEBRIDGE_OPERATOR_TOKEN", "owner-" + "x" * 40)
    monkeypatch.setattr(local, "ControlClient", lambda *args, **kwargs: remote[0])
    page = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "pages/3_Remote_Projects.py"), default_timeout=20).run()
    project_id = remote[-1].project_id
    page.button(key="load-public:" + project_id).click().run()
    prefix = f"public:{project_id}:0:"
    page.checkbox(key=prefix + "enabled").check()
    page.multiselect(key=prefix + "services").set_value(["api"])
    page.text_area(key=prefix + "names").set_value("api=이용 한도")
    next(button for button in page.button if button.label == "설정 저장").click().run()
    assert not page.exception
    descriptor = remote[3].get(f"/v1/public/projects/{project_id}")
    assert descriptor.status_code == 200 and descriptor.json()["services"] == [{"id": "api", "label": "이용 한도"}]


def test_public_context_credentials_are_redacted_before_storage(remote):
    enable(remote)
    receipt = send(remote, body(context={"operation": "password=PRIVATE_CONTEXT_SECRET"}))
    assert receipt.status_code == 202
    with remote[3].app.state.storage.transaction() as tx:
        report = tx.one(s.public_reports, id=receipt.json()["report_id"])
        job = tx.one(s.jobs, id=report["root_job_id"])
        assert "PRIVATE_CONTEXT_SECRET" not in job["body"]
        assert "[REDACTED]" in json.loads(job["body"])["context"]["operation"]


def test_live_public_model_work_requires_os_sandbox(remote):
    remote[3].app.state.public_service.model_is_double = False
    with pytest.raises(GatewayError) as denied:
        enable(remote, use_nvidia=True, automation="PREPARE", repair_policy_id="quota-repair")
    assert denied.value.status_code == 422
    enable(remote)
    assert send(remote).status_code == 202


def test_report_cannot_select_test_provider_or_disable_isolation(remote):
    enable(remote)
    assert send(remote, body(model_is_double=True)).status_code == 422


def long_history_retry_scenario(remote):
    enable(remote, use_nvidia=True, automation="PREPARE", repair_policy_id="quota-repair")
    receipt = send(remote, body(allow_external_analysis=True)).json()
    storage = remote[3].app.state.storage
    with storage.transaction() as tx:
        root = tx.one(s.public_reports, id=receipt["report_id"])["root_job_id"]
        for index in range(250):
            tx.insert(s.job_events, job_id=root, kind="SYNTHETIC_HISTORY", epoch=0,
                occurred=float(index), metadata_json="{}")
    remote[2].run_once()
    remote[2].run_once()
    saved = remote[0].request("GET", "/v1/jobs/" + root)
    cached = json.loads((remote[2].state_directory / (root + ".result.json")).read_bytes())
    response = remote[1].request("POST", f"/v1/runner/jobs/{root}/result", {"epoch": saved["epoch"], "result": cached})
    assert response["status"] == "ALREADY_SAVED"
    with storage.transaction() as tx:
        events = tx.events(root, 0, 1000)
        assert sum(event["kind"] == "AUTO_DECISION" for event in events) == 1
    assert len(remote[0].request("GET", f"/v1/projects/{remote[-1].project_id}/jobs")["jobs"]) == 2
    assert status(remote, receipt).json()["status"] == "WAITING_REVIEW"


def test_long_history_does_not_repeat_completed_automatic_routing(remote):
    long_history_retry_scenario(remote)


def failed_followup_after_recovery_scenario(remote, project, live_api, monkeypatch):
    receipt = automatic_recovery_scenario(remote, project, live_api)
    path = f"/v1/public/projects/{remote[-1].project_id}/reports/{receipt['report_id']}/answers"
    response = remote[3].post(path, json=body(text="다시 실패해요"), headers={
        "Authorization": "Bearer " + receipt["receipt_token"], "Idempotency-Key": uuid4().hex})
    assert response.status_code == 202
    import tracebridge.local_runner as local
    monkeypatch.setattr(local, "investigate_submission", lambda *a, **kw: (_ for _ in ()).throw(ValueError("private failure")))
    with pytest.raises(ValueError):
        remote[2].run_once()
    assert status(remote, receipt).json()["status"] == "UNRESOLVED"
    assert status(remote, receipt).json()["can_answer"] is False


def test_failed_followup_cannot_inherit_previous_recovery_success(remote, project, live_api, monkeypatch):
    failed_followup_after_recovery_scenario(remote, project, live_api, monkeypatch)


def test_policy_revocation_after_candidate_queued_stops_execution(remote, monkeypatch):
    enable(remote, use_nvidia=True, automation="PREPARE", repair_policy_id="quota-repair")
    receipt = send(remote, body(allow_external_analysis=True)).json()
    remote[2].run_once()
    calls = remote[4].calls
    enable(remote, enabled=False)
    import tracebridge.local_runner as local
    def denied_execution(*args, **kwargs):
        raise AssertionError("Revoked public work reached code execution")
    monkeypatch.setattr(local, "prepare_project_change", denied_execution)
    with pytest.raises(ValueError, match="Public execution permission changed"):
        remote[2].run_once()
    assert remote[4].calls == calls
    assert status(remote, receipt).json()["status"] == "UNRESOLVED"


def revoked_model_permission_scenario(remote, monkeypatch):
    enable(remote, use_nvidia=True)
    send(remote, body(allow_external_analysis=True))
    job = remote[1].request("POST", "/v1/runner/jobs/claim", {})["job"]
    enable(remote, use_nvidia=False)
    payload = {"model": DEFAULT_MODEL, "messages": [{"role": "user", "content": "synthetic"}],
        "tools": [FINISH_TOOL], "max_tokens": 500}
    with pytest.raises(GatewayError) as denied:
        remote[1].request("POST", f"/v1/runner/jobs/{job['job_id']}/model",
            {"epoch": job["epoch"], "step_id": "model-1", "payload": payload})
    assert denied.value.status_code == 403 and remote[4].calls == 0
    import tracebridge.semantic_memory as semantic
    def denied_embedding():
        raise AssertionError("Revoked permission loaded an external provider")
    monkeypatch.setattr(semantic, "embedding_client_from_env", denied_embedding)
    memory = remote[1].request("POST", f"/v1/runner/jobs/{job['job_id']}/memory",
        {"epoch": job["epoch"], "step_id": "memory", "payload": {"query": "synthetic"}})
    assert "semantic" not in memory
    if hasattr(remote[4], "embeddings"):
        assert remote[4].embeddings.calls == []


def test_owner_model_optout_blocks_already_assigned_public_external_calls(remote, monkeypatch):
    revoked_model_permission_scenario(remote, monkeypatch)


def application_interruption_scenario(remote, project, live_api, monkeypatch, stage):
    policy = project[1]
    policy["auto_apply_nonprod"] = True
    save_repair_policy(policy, remote[-1])
    remote[2].publish_profiles()
    enable(remote, use_nvidia=True, automation="APPLY_NONPROD", repair_policy_id="quota-repair")
    receipt = send(remote, body(allow_external_analysis=True)).json()
    runner = remote[2]
    runner.run_once()
    apply = runner.automatic_application
    def interrupted(job, result):
        if stage == "after":
            result = apply(job, result)
            assert result["automatic_application"]["status"] == "APPLIED"
        raise RuntimeError("synthetic interruption")
    with monkeypatch.context() as patch:
        patch.setattr(runner, "automatic_application", interrupted)
        with pytest.raises(RuntimeError, match="synthetic interruption"):
            runner.run_once()
    calls = remote[4].calls
    import tracebridge.project_repair as repair
    def no_repeat(*args, **kwargs):
        raise AssertionError("An interrupted result was applied again")
    monkeypatch.setattr(repair, "apply_project_change", no_repeat)
    if stage == "after":
        live_api[1]()
    runner.run_once()
    expected = "RESOLVED" if stage == "after" else "WAITING_REVIEW"
    assert status(remote, receipt).json()["status"] == expected
    assert remote[4].calls == calls
    jobs = remote[0].request("GET", f"/v1/projects/{remote[-1].project_id}/jobs")["jobs"]
    assert len(jobs) == (3 if stage == "after" else 2)
    assert all(job["state"] == "SUCCEEDED" for job in jobs)
    source = (project[-1] / "app.py").read_text()
    assert ("count <= 5" in source) == (stage == "after")


@pytest.mark.parametrize("stage", ["before", "after"])
def test_interrupted_application_reuses_result_and_never_reapplies(remote, project, live_api, monkeypatch, stage):
    application_interruption_scenario(remote, project, live_api, monkeypatch, stage)


def epoch_fencing_scenario(remote, project, live_api, endpoint):
    policy = project[1]
    policy["auto_apply_nonprod"] = True
    save_repair_policy(policy, remote[-1])
    remote[2].publish_profiles()
    enable(remote, use_nvidia=True, automation="APPLY_NONPROD", repair_policy_id="quota-repair")
    send(remote, body(allow_external_analysis=True))
    remote[2].run_once()
    first = remote[1].request("POST", "/v1/runner/jobs/claim", {})["job"]
    with remote[3].app.state.storage.transaction() as tx:
        tx.update(s.jobs, {"lease_until": 0}, id=first["job_id"])
    remote[0].request("GET", "/v1/jobs/" + first["job_id"])
    remote[0].request("POST", f"/v1/jobs/{first['job_id']}/resume", {})
    resumed = remote[1].request("POST", "/v1/runner/jobs/claim", {})["job"]
    assert resumed["epoch"] > first["epoch"]
    path = f"/v1/runner/jobs/{first['job_id']}/{endpoint}"
    with pytest.raises(GatewayError) as stale:
        remote[1].request("GET", path + "?epoch=" + str(first["epoch"]))
    assert stale.value.status_code == 409
    assert remote[1].request("GET", path + "?epoch=" + str(resumed["epoch"]))["allowed"] is True


@pytest.mark.parametrize("endpoint", ["public-execution", "automatic-application"])
def test_public_execution_permissions_are_fenced_by_runner_epoch(remote, project, live_api, endpoint):
    epoch_fencing_scenario(remote, project, live_api, endpoint)
