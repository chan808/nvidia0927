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
