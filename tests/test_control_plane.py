"""HTTP auth/leases and real local-file investigation; model calls are doubles."""
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace

from fastapi.testclient import TestClient
from openai.types.chat import ChatCompletion
import pytest

from test_registered_project_repair import project, BoundaryProposer
from tracebridge.control_plane import create_app
from tracebridge.local_runner import ControlClient, LocalRunner, GatewayModelClient, GatewayError, pair_runner
from tracebridge.report_agent import DEFAULT_MODEL, FINISH_TOOL


class ModelDouble:
    def __init__(self):
        self.calls = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.calls += 1
        names = [item["function"]["name"] for item in kwargs["tools"]]
        if "propose_patch" in names:
            context = json.loads(kwargs["messages"][-1]["content"])
            value = BoundaryProposer().propose(context, 1)
            for edit in value["edits"]:
                edit["lines"] = edit.pop("content").splitlines()
            name = "propose_patch"
        else:
            value = {"intent": "investigate", "symptom_summary": "quota boundary", "cause": "", "explanation": "", "supporting_evidence_ids": [],
                "contradicting_evidence_ids": [], "verification_step": "", "possible_fix": "", "missing_information": [], "next_steps": [],
                "previous_hypothesis_outcome": "not_rechecked", "counter_evidence_ids": []}
            name = "finish_investigation"
        return ChatCompletion.model_validate({"id": "test-model-" + str(self.calls), "object": "chat.completion", "created": 1, "model": DEFAULT_MODEL,
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
                "tool_calls": [{"id": "test-tool", "type": "function", "function": {"name": name, "arguments": json.dumps(value)}}]}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150}})


@pytest.fixture
def remote(project, tmp_path):
    profile, _, _, _, _ = project
    model = ModelDouble()
    db = tmp_path / "control.sqlite3"
    http = TestClient(create_app(db, operator_token="owner-" + "x" * 40, model_client=model))
    owner = ControlClient("http://testserver", "owner-" + "x" * 40, client=http)
    code = owner.request("POST", "/v1/pairings", {"project_ids": [profile.project_id]})["code"]
    credentials = http.post("/v1/pairings/consume", json={"code": code}).json()
    runner_api = ControlClient("http://testserver", credentials["token"], client=http)
    runner = LocalRunner(runner_api, tmp_path / "runner", project_ids=[profile.project_id], registry=profile.config_path.parent)
    runner.publish_profiles()
    return owner, runner_api, runner, http, model, db, profile


def report(owner, profile, **extra):
    return owner.request("POST", f"/v1/projects/{profile.project_id}/reports", {"text": "QUOTA_BOUNDARY requestId=quota-001 알아보고 고쳐줘.", "service": "api",
        "context": {"occurred_at": "2026-09-29T12:00:00+09:00"}, **extra}, idempotency_key="report-1")


def test_remote_investigation_then_candidate_runs_real_snapshot_checks(remote):
    owner, _, runner, _, model, _, profile = remote
    queued = report(owner, profile)
    assert queued["state"] == "QUEUED"
    runner.run_once()
    saved = owner.request("GET", "/v1/jobs/" + queued["job_id"])
    assert saved["state"] == "SUCCEEDED" and saved["result"]["correlation"] == "EXACT_ID"
    assert model.calls == 0 and saved["result"]["source_registration"]["profile_sha256"]
    prepared = owner.request("POST", f"/v1/projects/{profile.project_id}/changes", {"text": "prepare candidate", "service": "api",
        "use_nvidia": True, "previous_job_id": queued["job_id"]}, idempotency_key="change-1")
    runner.run_once()
    change = owner.request("GET", "/v1/jobs/" + prepared["job_id"])
    assert change["state"] == "SUCCEEDED", change
    assert change["result"]["job"]["candidate_fix_verified"]
    assert "count <= 5" in change["result"]["diff_preview"]
    assert change["result"]["job"]["model"]["mode"] == "TEST_DOUBLE"
    assert not change["result"]["job"]["original_applied"] and model.calls == 1
    assert str(profile.root) not in json.dumps(change["result"])


@pytest.mark.parametrize("changed", ["service", "registration"])
def test_followup_refuses_changed_service_or_registration(remote, changed):
    owner, runner_api, runner, _, _, _, profile = remote
    queued = report(owner, profile)
    runner.run_once()
    manifest = owner.request("GET", "/v1/projects")[0]
    updated = {key: manifest[key] for key in ("project_id", "environment", "service_ids", "repository_ids", "profile_sha256", "repair_enabled", "repair_policy_ids", "status")}
    updated["service_ids"] = ["api", "frontend"]
    if changed == "registration":
        updated["profile_sha256"] = "0" * 64
    runner_api.request("PUT", "/v1/runner/manifest", updated)
    with pytest.raises(GatewayError, match="binding differs") as denied:
        owner.request("POST", f"/v1/projects/{profile.project_id}/reports", {
            "text": "새 답변", "service": "frontend" if changed == "service" else "api", "previous_job_id": queued["job_id"],
        }, idempotency_key="changed-binding-followup")
    assert denied.value.status_code == 409


def test_credentials_project_scope_pairing_and_revocation(remote):
    owner, api, runner, http, _, _, profile = remote
    assert http.get("/v1/projects").status_code == 401
    with pytest.raises(GatewayError):
        api.request("GET", "/v1/projects")
    with pytest.raises(GatewayError):
        api.request("PUT", "/v1/runner/manifest", {"project_id": "foreign", "environment": "dev", "service_ids": ["api"], "repository_ids": [], "profile_sha256": "a" * 64})
    runner_id = owner.request("GET", "/v1/projects")[0]["runner_id"]
    owner.request("DELETE", "/v1/runners/" + runner_id)
    with pytest.raises(GatewayError):
        runner.run_once()
    assert owner.request("GET", "/v1/projects")[0]["online"] is False


def test_report_and_completion_are_idempotent(remote):
    owner, _, runner, _, _, _, profile = remote
    first = report(owner, profile)
    assert report(owner, profile)["job_id"] == first["job_id"]
    with pytest.raises(GatewayError):
        owner.request("POST", f"/v1/projects/{profile.project_id}/reports", {"text": "different", "service": "api"}, idempotency_key="report-1")
    runner.run_once()
    ack = runner.state_directory / (first["job_id"] + ".ack.json")
    ack.unlink()
    runner.flush_outbox()
    assert ack.exists() and runner.run_once() is None


def test_expired_lease_and_cancel_do_not_accept_late_results(remote):
    owner, api, _, _, _, db, profile = remote
    queued = report(owner, profile)
    job = api.request("POST", "/v1/runner/jobs/claim", {})["job"]
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE jobs SET lease_until=0 WHERE id=?", (job["job_id"],))
    with pytest.raises(GatewayError):
        api.request("POST", f"/v1/runner/jobs/{job['job_id']}/renew", {"epoch": job["epoch"]})
    api.request("POST", "/v1/runner/jobs/claim", {})
    assert owner.request("GET", "/v1/jobs/" + queued["job_id"])["state"] == "RECOVERY_REQUIRED"
    owner.request("POST", "/v1/jobs/" + job["job_id"] + "/resume", {})
    again = api.request("POST", "/v1/runner/jobs/claim", {})["job"]
    assert again["epoch"] > job["epoch"]
    owner.request("POST", "/v1/jobs/" + job["job_id"] + "/cancel", {})
    assert owner.request("GET", "/v1/jobs/" + job["job_id"])["state"] == "CANCEL_REQUESTED"
    api.request("POST", "/v1/runner/jobs/" + job["job_id"] + "/stopped", {"epoch": again["epoch"]})
    assert owner.request("GET", "/v1/jobs/" + job["job_id"])["state"] == "CANCELLED"


def test_model_gateway_caches_steps_and_refuses_unregistered_tools(remote):
    owner, api, _, _, model, _, profile = remote
    queued = report(owner, profile, use_nvidia=True)
    job = api.request("POST", "/v1/runner/jobs/claim", {})["job"]
    payload = {"model": DEFAULT_MODEL, "messages": [{"role": "user", "content": "untrusted"}], "tools": [FINISH_TOOL], "max_tokens": 500}
    request = {"epoch": job["epoch"], "step_id": "model-1", "payload": payload}
    url = f"/v1/runner/jobs/{job['job_id']}/model"
    result = api.request("POST", url, request)
    assert api.request("POST", url, request) == result and model.calls == 1
    with pytest.raises(GatewayError):
        api.request("POST", url, {**request, "payload": {**payload, "messages": [{"role": "user", "content": "different"}]}})
    with pytest.raises(GatewayError):
        api.request("POST", url, {**request, "step_id": "bad", "payload": {**payload, "tools": [{"function": {"name": "shell"}}]}})


def test_pair_token_is_one_time_and_local_secret_protected(remote, tmp_path):
    owner, _, _, http, _, _, profile = remote
    code = owner.request("POST", "/v1/pairings", {"project_ids": [profile.project_id]})["code"]
    path = tmp_path / "paired.json"
    result = pair_runner("http://testserver", code, path, client=http)
    assert "token" not in result and '"token"' not in path.read_text()
    restored = LocalRunner.from_config(path, client=http)
    assert restored.api.request("POST", "/v1/runner/heartbeat", {})["status"] == "ONLINE"
    assert http.post("/v1/pairings/consume", json={"code": code}).status_code == 401


def test_priority_dispatch_is_owner_controlled_and_preserves_fifo_for_ties(remote):
    owner, api, _, http, _, _, profile = remote
    first = report(owner, profile)
    urgent = owner.request("POST", f"/v1/projects/{profile.project_id}/reports", {
        "text": "긴급 제보", "service": "api", "assessment": {"priority": "P0", "difficulty": "LARGE", "risk": "HIGH"}},
        idempotency_key="urgent")
    assignment = api.request("POST", "/v1/runner/jobs/claim", {})["job"]
    assert assignment["job_id"] == urgent["job_id"] and assignment["input"]["investigation_max_seconds"] == 180
    assert api.request("POST", "/v1/runner/jobs/claim", {})["job"] is None
    # A cancellation request must not permit another concurrent local job.
    owner.request("POST", "/v1/jobs/" + urgent["job_id"] + "/cancel", {})
    assert api.request("POST", "/v1/runner/jobs/claim", {})["job"] is None
    api.request("POST", "/v1/runner/jobs/" + urgent["job_id"] + "/stopped", {"epoch": 1})
    assert api.request("POST", "/v1/runner/jobs/claim", {})["job"]["job_id"] == first["job_id"]
    assert http.get(f"/v1/projects/{profile.project_id}/jobs").status_code == 401
    with pytest.raises(GatewayError):
        api.request("GET", "/v1/jobs/" + urgent["job_id"] + "/events")


def test_assessment_changes_are_versioned_and_do_not_break_submission_idempotency(remote):
    owner, api, _, _, _, _, profile = remote
    queued = report(owner, profile)
    update = {"expected_revision": 1, "assessment": {"priority": "P1", "difficulty": "SMALL", "risk": "LOW", "reason": "핵심 기능 영향"}}
    result = owner.request("PUT", "/v1/jobs/" + queued["job_id"] + "/assessment", update)
    assert result["assessment"]["priority"] == "P1" and result["assessment_revision"] == 2
    assert report(owner, profile)["job_id"] == queued["job_id"]
    with pytest.raises(GatewayError) as stale:
        owner.request("PUT", "/v1/jobs/" + queued["job_id"] + "/assessment", update)
    assert stale.value.status_code == 409
    assignment = api.request("POST", "/v1/runner/jobs/claim", {})["job"]
    assert assignment["input"]["investigation_max_seconds"] == 45
    with pytest.raises(GatewayError):
        owner.request("PUT", "/v1/jobs/" + queued["job_id"] + "/assessment", {**update, "expected_revision": 2})
    events = owner.request("GET", "/v1/jobs/" + queued["job_id"] + "/events")["events"]
    assert [event["kind"] for event in events] == ["SUBMITTED", "ASSESSMENT_CHANGED", "STATE_CHANGED"]
    assert events[1]["metadata"]["before"]["priority"] == "P2"
    assert events[1]["metadata"]["after"]["priority"] == "P1"


def test_job_history_survives_reopening_and_lists_partial_diagnoses_separately(remote):
    owner, _, runner, _, _, db, profile = remote
    queued = report(owner, profile)
    runner.run_once()
    # Simulate reopening the server against the existing state, with fresh auth.
    reopened = TestClient(create_app(db, operator_token="new-owner-" + "x" * 40))
    owner = ControlClient("http://testserver", "new-owner-" + "x" * 40, client=reopened)
    listing = owner.request("GET", f"/v1/projects/{profile.project_id}/jobs")
    assert listing["counts"] == {"SUCCEEDED": 1}
    assert listing["jobs"][0]["id"] == queued["job_id"]
    assert listing["jobs"][0]["service_recovery"] == "NOT_VERIFIED"
    events = owner.request("GET", "/v1/jobs/" + queued["job_id"] + "/events")["events"]
    assert [(event["from_state"], event["to_state"]) for event in events if event["kind"] in {"SUBMITTED", "STATE_CHANGED"}] == [(None, "QUEUED"), ("QUEUED", "RUNNING"), ("RUNNING", "SUCCEEDED")]
    assert not any(event["kind"] == "MIGRATED_SNAPSHOT" for event in events)


def test_expiration_recovery_and_model_failures_are_durably_recorded(remote):
    owner, api, _, _, model, db, profile = remote
    queued = report(owner, profile, use_nvidia=True)
    job = api.request("POST", "/v1/runner/jobs/claim", {})["job"]
    payload = {"model": DEFAULT_MODEL, "messages": [{"role": "user", "content": "synthetic"}], "tools": [FINISH_TOOL]}
    url = f"/v1/runner/jobs/{job['job_id']}/model"
    request = {"epoch": job["epoch"], "step_id": "recorded", "payload": payload}
    api.request("POST", url, request)
    api.request("POST", url, request)
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE jobs SET lease_until=0,expires=0 WHERE id=?", (job["job_id"],))
    owner.request("GET", "/v1/jobs/" + queued["job_id"])
    owner.request("POST", "/v1/jobs/" + queued["job_id"] + "/resume", {})
    assert api.request("POST", "/v1/runner/jobs/claim", {})["job"]["epoch"] == 2
    events = owner.request("GET", "/v1/jobs/" + queued["job_id"] + "/events")["events"]
    assert len([event for event in events if event["kind"] == "MODEL_STEP"]) == 2
    assert model.calls == 1
    assert "RECOVERY_REQUIRED" in [event["to_state"] for event in events]
    assert "EXPIRED" not in [event["to_state"] for event in events]


def test_job_and_event_pagination_preserve_equal_timestamp_items(remote):
    owner, api, _, _, _, db, profile = remote
    ids = []
    for i in range(3):
        ids.append(owner.request("POST", f"/v1/projects/{profile.project_id}/reports", {"text": "제보", "service": "api"}, idempotency_key="page-" + str(i))["job_id"])
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE jobs SET created=1")
    first = owner.request("GET", f"/v1/projects/{profile.project_id}/jobs?limit=2")
    second = owner.request("GET", f"/v1/projects/{profile.project_id}/jobs?limit=2&before_id=" + first["next_cursor"])
    assert {row["id"] for row in [*first["jobs"], *second["jobs"]]} == set(ids)
    assert second["next_cursor"] is None
    api.request("POST", "/v1/runner/jobs/claim", {})
    events = owner.request("GET", "/v1/jobs/" + min(ids) + "/events?limit=1")
    next_page = owner.request("GET", "/v1/jobs/" + min(ids) + "/events?after=" + str(events["next_after"]))
    assert len(events["events"]) == 1 and len(next_page["events"]) == 1


def test_lost_cancel_ack_requires_owner_resolution_before_next_dispatch(remote):
    owner, api, _, _, _, db, profile = remote
    first = report(owner, profile)
    second = owner.request("POST", f"/v1/projects/{profile.project_id}/reports", {"text": "다음 제보", "service": "api"}, idempotency_key="next-job")
    active = api.request("POST", "/v1/runner/jobs/claim", {})["job"]
    owner.request("POST", "/v1/jobs/" + first["job_id"] + "/cancel", {})
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE jobs SET lease_until=0 WHERE id=?", (first["job_id"],))
    assert api.request("POST", "/v1/runner/jobs/claim", {})["job"] is None
    assert owner.request("GET", "/v1/jobs/" + first["job_id"])["state"] == "RECOVERY_REQUIRED"
    owner.request("POST", "/v1/jobs/" + first["job_id"] + "/cancel", {})
    assert api.request("POST", "/v1/runner/jobs/claim", {})["job"]["job_id"] == second["job_id"]
    with pytest.raises(GatewayError):
        api.request("POST", f"/v1/runner/jobs/{active['job_id']}/failure", {"epoch": active["epoch"], "error_type": "late"})


def test_failed_model_step_is_retained_and_never_blindly_retried(remote):
    owner, api, _, _, model, _, profile = remote
    queued = report(owner, profile, use_nvidia=True)
    active = api.request("POST", "/v1/runner/jobs/claim", {})["job"]
    def offline(**kwargs):
        raise TimeoutError("synthetic provider failure")
    model.chat.completions.create = offline
    request = {"epoch": active["epoch"], "step_id": "failed", "payload": {"model": DEFAULT_MODEL,
        "messages": [{"role": "user", "content": "synthetic"}], "tools": [FINISH_TOOL]}}
    url = f"/v1/runner/jobs/{active['job_id']}/model"
    with pytest.raises(GatewayError) as failure:
        api.request("POST", url, request)
    assert failure.value.status_code == 502
    with pytest.raises(GatewayError) as retry:
        api.request("POST", url, request)
    assert retry.value.status_code == 409
    events = owner.request("GET", "/v1/jobs/" + queued["job_id"] + "/events")["events"]
    assert [item["metadata"]["state"] for item in events if item["kind"] == "MODEL_STEP"] == ["REQUESTED", "OUTCOME_UNKNOWN"]


def test_server_vector_search_requires_job_consent_and_uses_assigned_scope(remote):
    owner, _, runner, _, _, db, profile = remote
    initial = report(owner, profile)
    runner.run_once()
    run_id = owner.request("GET", "/v1/jobs/" + initial["job_id"])["result"]["run_id"]
    owner.request("POST", f"/v1/memory/{profile.project_id}/{run_id}/review", {"action": "approve"})
    class EmbeddingDouble:
        identity = "synthetic-server-embeddings"
        calls = []
        def embed(self, texts, *, input_type):
            self.calls.append(input_type)
            return [[1.0, 0.0] for text in texts]
    embeddings = EmbeddingDouble()
    http = TestClient(create_app(db, operator_token="owner-" + "x" * 40, embedding_client=embeddings))
    owner = ControlClient("http://testserver", "owner-" + "x" * 40, client=http)
    # Reuse the already-scoped credential without exposing it in test output.
    api = ControlClient("http://testserver", runner.api.token, client=http)
    assert owner.request("POST", f"/v1/memory/{profile.project_id}/index")["indexed"] == 1
    for consent in (False, True):
        queued = owner.request("POST", f"/v1/projects/{profile.project_id}/reports", {"text": "다른 표현", "service": "api", "use_nvidia": consent}, idempotency_key="consent-" + str(consent))
        active = api.request("POST", "/v1/runner/jobs/claim", {})["job"]
        result = api.request("POST", f"/v1/runner/jobs/{active['job_id']}/memory", {"epoch": active["epoch"], "step_id": "memory",
            "payload": {"query": "완전히 다른 표현", "scope_filters": {"service": "foreign"}}})
        assert result["retrieval_scope"] == {"service": "api", "environment": "dev"}
        assert ("semantic" in result) is consent
        owner.request("POST", "/v1/jobs/" + queued["job_id"] + "/cancel", {})
        api.request("POST", f"/v1/runner/jobs/{active['job_id']}/stopped", {"epoch": active["epoch"]})
    assert embeddings.calls == ["passage", "query"]


def test_assessment_is_stored_once_as_redacted_metadata(remote):
    owner, _, _, _, _, db, profile = remote
    queued = owner.request("POST", f"/v1/projects/{profile.project_id}/reports", {
        "text": "제보", "service": "api", "assessment": {"reason": "담당자 person@example.com 확인"}}, idempotency_key="private-assessment")
    with sqlite3.connect(db) as connection:
        body, assessment = connection.execute("SELECT body,assessment_json FROM jobs WHERE id=?", (queued["job_id"],)).fetchone()
    assert "assessment" not in json.loads(body)
    assert "person@example.com" not in assessment
    events = owner.request("GET", "/v1/jobs/" + queued["job_id"] + "/events")
    assert "person@example.com" not in json.dumps(events)
