"""Simple registration, one-screen intake and private leased photo processing."""
from copy import deepcopy
from dataclasses import replace
import hashlib
from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
from openai.types.chat import ChatCompletion
from PIL import Image
import pytest
from streamlit.testing.v1 import AppTest

from test_control_plane import ModelDouble, remote, project
from test_project_connection import configure_ui
from tracebridge.control_plane import create_app
from tracebridge.local_runner import ControlClient, GatewayError, LocalRunner
from tracebridge.project_registry import profile_data, save_profile
from tracebridge.project_profile import load_project_profile
from tracebridge.project_repair import load_repair_policy, repair_blockers
from tracebridge.project_setup_ui import connect_local_project, save_simple_project
from tracebridge.report_photo import encode_report_image
from tracebridge.report_agent import PhotoObservation, VISION_MODEL


def picture():
    stream = BytesIO()
    Image.new("RGB", (120, 80), "white").save(stream, format="PNG")
    return encode_report_image(stream.getvalue())


class PhotoOCR:
    def __init__(self, text="QUOTA_BOUNDARY requestId=quota-001", fail=False):
        self.text, self.fail, self.calls = text, fail, 0

    def extract(self, image, **kwargs):
        self.calls += 1
        assert image[:2] == b"\xff\xd8"
        if self.fail:
            raise RuntimeError("synthetic OCR failure")
        return {"service": "NeMo Retriever OCR NIM", "model": "TEST_OCR", "status": "success",
            "image_sha256": hashlib.sha256(image).hexdigest(), "usable_text": self.text,
            "lines": [{"text": self.text, "confidence": 0.99}] if self.text else []}


class PhotoModel(ModelDouble):
    def __init__(self):
        super().__init__()
        self.vision_requests = []

    def create(self, **kwargs):
        if kwargs["model"] != VISION_MODEL:
            return super().create(**kwargs)
        self.calls += 1
        self.vision_requests.append(kwargs)
        return ChatCompletion.model_validate({"id": "test-photo", "object": "chat.completion", "created": 1, "model": VISION_MODEL,
            "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {"role": "assistant", "content": None,
                "tool_calls": [{"id": "screen", "type": "function", "function": {"name": "describe_screen",
                    "arguments": json.dumps({"visible_symptom": "앱 화면에 오류 문구가 보여요", "has_app_screen": True})}}]}}]})


@pytest.fixture
def photo_stack(project, tmp_path):
    profile = project[0]
    model, ocr = PhotoModel(), PhotoOCR()
    with TestClient(create_app(tmp_path / "photo-control.sqlite3", operator_token="owner-" + "x" * 40, model_client=model, ocr_client=ocr)) as http:
        owner = ControlClient("http://testserver", "owner-" + "x" * 40, client=http)
        code = owner.request("POST", "/v1/pairings", {"project_ids": [profile.project_id]})["code"]
        credentials = http.post("/v1/pairings/consume", json={"code": code}).json()
        worker_api = ControlClient("http://testserver", credentials["token"], client=http)
        runner = LocalRunner(worker_api, tmp_path / "photo-runner", project_ids=[profile.project_id], registry=profile.config_path.parent)
        runner.publish_profiles()
        yield owner, worker_api, runner, http, model, ocr, profile


def send_photo(stack, **extra):
    return stack[0].request("POST", f"/v1/projects/{stack[-1].project_id}/reports", {"image_b64": picture(),
        "context": {"occurred_at": "2026-09-29T12:00:00+09:00"}, **extra}, idempotency_key="photo-intake")


def vision_request(image_url):
    return {"model": VISION_MODEL, "tools": [{"type": "function", "function": {"name": "describe_screen", "parameters": PhotoObservation.model_json_schema()}}],
        "tool_choice": {"type": "function", "function": {"name": "describe_screen"}},
        "messages": [{"role": "user", "content": [{"type": "text", "text": "Describe the app screenshot"},
            {"type": "image_url", "image_url": {"url": image_url}}]}]}


def test_photo_only_optout_keeps_image_private_and_never_calls_providers(photo_stack):
    receipt = send_photo(photo_stack)
    photo_stack[2].run_once()
    status = photo_stack[0].request("GET", "/v1/jobs/" + receipt["job_id"])
    assert status["state"] == "SUCCEEDED" and status["service"] == photo_stack[-1].service
    assert photo_stack[4].calls == photo_stack[5].calls == 0
    assert "image_b64" not in json.dumps(status) and picture() not in json.dumps(status)
    assert status["result"]["questions"] and not status["result"]["fix_applied"]


def test_photo_ocr_uses_server_gateway_and_current_request_evidence(photo_stack, monkeypatch):
    monkeypatch.setattr("tracebridge.report_agent.nvidia_settings", lambda: (_ for _ in ()).throw(AssertionError("PC loaded a provider key")))
    receipt = send_photo(photo_stack, use_nvidia=True)
    photo_stack[2].run_once()
    result = photo_stack[0].request("GET", "/v1/jobs/" + receipt["job_id"])["result"]
    assert photo_stack[5].calls == 1 and result["correlation"] == "EXACT_ID"
    assert any(item.get("provider_mode") == "TEST_DOUBLE" for item in result["service_calls"])
    assert not result["fix_applied"] and not result["fix_verified"]


def test_empty_ocr_falls_back_to_scoped_vision_photo(photo_stack):
    photo_stack[5].text = ""
    receipt = send_photo(photo_stack, use_nvidia=True)
    photo_stack[2].run_once()
    status = photo_stack[0].request("GET", "/v1/jobs/" + receipt["job_id"])
    assert status["state"] == "SUCCEEDED" and len(photo_stack[4].vision_requests) == 1
    message = photo_stack[4].vision_requests[0]["messages"][-1]
    assert message["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
    assert status["result"]["product_status"] == "UNCONFIRMED" and not status["result"]["cause_confirmed"]


@pytest.mark.parametrize("replacement", ["https://example.invalid/private.png", "data:image/jpeg;base64,AAAA"])
def test_vision_cannot_analyze_any_other_picture(photo_stack, replacement):
    send_photo(photo_stack, use_nvidia=True)
    job = photo_stack[1].request("POST", "/v1/runner/jobs/claim", {})["job"]
    with pytest.raises(GatewayError) as denied:
        photo_stack[1].request("POST", f"/v1/runner/jobs/{job['job_id']}/model", {"epoch": job["epoch"], "step_id": "vision",
            "payload": vision_request(replacement)})
    assert denied.value.status_code == 422 and photo_stack[4].calls == 0


def test_photo_ocr_cache_does_not_repeat_or_survive_cancellation(photo_stack):
    receipt = send_photo(photo_stack, use_nvidia=True)
    job = photo_stack[1].request("POST", "/v1/runner/jobs/claim", {})["job"]
    path, body = f"/v1/runner/jobs/{job['job_id']}/ocr", {"epoch": job["epoch"]}
    first = photo_stack[1].request("POST", path, body)
    assert photo_stack[1].request("POST", path, body) == first and photo_stack[5].calls == 1
    photo_stack[0].request("POST", "/v1/jobs/" + receipt["job_id"] + "/cancel", {})
    with pytest.raises(GatewayError) as denied:
        photo_stack[1].request("POST", path, body)
    assert denied.value.status_code == 409 and photo_stack[5].calls == 1


@pytest.mark.parametrize("consent,epoch,code", [(False, 1, 403), (True, 2, 409)])
def test_ocr_requires_current_lease_and_explicit_consent(photo_stack, consent, epoch, code):
    send_photo(photo_stack, use_nvidia=consent)
    job = photo_stack[1].request("POST", "/v1/runner/jobs/claim", {})["job"]
    with pytest.raises(GatewayError) as denied:
        photo_stack[1].request("POST", f"/v1/runner/jobs/{job['job_id']}/ocr", {"epoch": epoch})
    assert denied.value.status_code == code and photo_stack[5].calls == 0


def test_failed_ocr_outcome_is_not_automatically_repeated(photo_stack):
    photo_stack[5].fail = True
    send_photo(photo_stack, use_nvidia=True)
    job = photo_stack[1].request("POST", "/v1/runner/jobs/claim", {})["job"]
    path, body = f"/v1/runner/jobs/{job['job_id']}/ocr", {"epoch": job["epoch"]}
    for expected in [502, 409]:
        with pytest.raises(GatewayError) as denied:
            photo_stack[1].request("POST", path, body)
        assert denied.value.status_code == expected
    assert photo_stack[5].calls == 1


@pytest.mark.parametrize("body", [{"image_b64": "not a picture"}, {"image_b64": "A" * 180000}, {"text": "  "},
    {"image_b64": picture(), "service": "foreign"}])
def test_invalid_photo_or_unregistered_scope_never_enters_queue(photo_stack, body):
    response = photo_stack[3].post(f"/v1/projects/{photo_stack[-1].project_id}/reports", json=body,
        headers={"Authorization": "Bearer owner-" + "x" * 40, "Idempotency-Key": "invalid"})
    assert response.status_code == 422
    assert not photo_stack[0].request("GET", f"/v1/projects/{photo_stack[-1].project_id}/jobs")["jobs"]


def test_default_scope_followup_stays_bound_to_previous_report(photo_stack):
    receipt = send_photo(photo_stack)
    photo_stack[2].run_once()
    next_job = photo_stack[0].request("POST", f"/v1/projects/{photo_stack[-1].project_id}/reports",
        {"text": "가입 화면에서 문제가 났어요", "previous_job_id": receipt["job_id"]}, idempotency_key="photo-answer")
    assert photo_stack[0].request("GET", "/v1/jobs/" + next_job["job_id"])["service"] == photo_stack[-1].service


def test_simple_setup_preserves_existing_policy_and_service_scope(project):
    profile = project[0]
    policy_file = profile.config_path.parent / "repair-policies/quota-repair.json"
    original = policy_file.read_bytes()
    saved = save_simple_project("가입 프로젝트", str(profile.root), "http://127.0.0.1:3000", profile=profile,
        expected_sha256=hashlib.sha256(profile.config_path.read_bytes()).hexdigest())
    assert saved.project_id == profile.project_id and saved.service == profile.service and saved.display_name == "가입 프로젝트"
    assert saved.policy_refs == profile.policy_refs and policy_file.read_bytes() == original


def test_simple_new_project_has_no_implicit_execution_permissions(project):
    saved = save_simple_project("새 프로젝트", str(project[-1]))
    assert saved.display_name == "새 프로젝트" and saved.code_roots == (project[-1],)
    assert saved.service == "app" and not saved.policy_refs and not saved.log_sources


@pytest.mark.parametrize("root", ["relative/path", "https://example.invalid/repository", "\\\\remote\\share"])
def test_simple_setup_requires_an_explicit_local_folder(root):
    with pytest.raises(ValueError):
        save_simple_project("new-project", root)


def test_simple_connection_is_scoped_keeps_credentials_and_omits_provider_keys(remote, project, tmp_path, monkeypatch):
    import tracebridge.project_setup_ui as setup
    saved = save_simple_project("new-demo", str(project[-1]))
    spawned = []
    monkeypatch.setenv("NVIDIA_API_KEY", "synthetic-provider-secret")
    monkeypatch.setenv("TRACEBRIDGE_OPERATOR_TOKEN", "synthetic-owner-secret")
    config = tmp_path / "companions/new-demo/config.json"
    with monkeypatch.context() as patch:
        patch.setattr(setup.subprocess, "Popen", lambda args, **kwargs: spawned.append((args, kwargs)) or SimpleNamespace(pid=321))
        patch.setattr(setup, "_running", lambda pid: True)
        connect_local_project(remote[0], saved, directory=tmp_path / "companions")
        previous = config.read_bytes()
        connect_local_project(remote[0], saved, directory=tmp_path / "companions")
    assert len(spawned) == 1 and config.read_bytes() == previous
    assert json.loads(previous)["project_ids"] == [saved.project_id]
    assert "NVIDIA_API_KEY" not in spawned[0][1]["env"] and "TRACEBRIDGE_OPERATOR_TOKEN" not in spawned[0][1]["env"]
    runner = LocalRunner.from_config(config, client=remote[3])
    runner.publish_profiles()
    row = next(row for row in remote[0].request("GET", "/v1/projects") if row["project_id"] == saved.project_id)
    assert row["online"] and row["display_name"] == "new-demo" and not row["repair_enabled"]


def test_simple_connection_does_not_replace_another_registered_pc(remote, tmp_path):
    with pytest.raises(ValueError, match="이미 연결된"):
        connect_local_project(remote[0], remote[-1], directory=tmp_path / "companions")
    assert not (tmp_path / "companions" / remote[-1].project_id / "config.json").exists()


def test_dashboard_has_no_sidebar_controls_and_keeps_scope_inside_advanced_settings(remote, monkeypatch):
    page = configure_ui(remote, monkeypatch).run()
    assert not page.exception and not page.sidebar.button and not page.sidebar.selectbox
    assert any(item.label == "고급 설정" for item in page.expander)
    assert page.selectbox(key="remote_service").value is None
    assert any(item.proto.label == "오류 화면 사진 (선택)" for item in page.get("file_uploader"))
    page.text_area(key="remote_report_text").set_value("QUOTA_BOUNDARY requestId=quota-001")
    next(item for item in page.button if item.label == "제보 접수").click().run()
    assert not page.exception
    jobs = remote[0].request("GET", f"/v1/projects/{remote[-1].project_id}/jobs")["jobs"]
    assert len(jobs) == 1 and jobs[0]["service"] == remote[-1].service


def test_root_opens_the_single_project_dashboard(remote, monkeypatch):
    configure_ui(remote, monkeypatch)
    page = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=20).run()
    assert not page.exception and page.title[0].value == "연결된 프로젝트"
    assert not page.sidebar.selectbox and not page.sidebar.button
    assert page.text_area(key="remote_report_text") and page.get("file_uploader")


def test_three_field_registration_connects_and_becomes_reportable(remote, project, tmp_path, monkeypatch):
    registered = []
    def connect(api, profile):
        registered.append(profile)
        code = api.request("POST", "/v1/pairings", {"project_ids": [profile.project_id]})["code"]
        credentials = remote[3].post("/v1/pairings/consume", json={"code": code}).json()
        worker_api = ControlClient("http://testserver", credentials["token"], client=remote[3])
        LocalRunner(worker_api, tmp_path / "new-runner", project_ids=[profile.project_id], registry=profile.config_path.parent).publish_profiles()
    monkeypatch.setattr("tracebridge.project_setup_ui.connect_local_project", connect)
    page = configure_ui(remote, monkeypatch).run()
    page.button(key="project-add").click().run()
    page.text_input(key="simple:new:name").set_value("new-dashboard")
    page.text_input(key="simple:new:root").set_value(str(project[-1]))
    next(item for item in page.button if item.label == "프로젝트 등록").click().run()
    assert not page.exception and len(registered) == 1
    assert page.selectbox(key="remote_project").value == "new-dashboard"
    assert not next(item for item in page.button if item.label == "제보 접수").disabled
    assert not registered[0].policy_refs


def test_api_does_not_choose_another_components_check(remote):
    project_id = remote[-1].project_id
    current = remote[0].request("GET", "/v1/projects")[0]
    manifest = {key: value for key, value in current.items() if key not in {"runner_id", "online", "semantic_search_enabled", "semantic_index_mode"}}
    manifest.update(service_ids=["api", "other"], repair_policy_services={"quota-repair": "other"})
    remote[1].request("PUT", "/v1/runner/manifest", manifest)
    report = remote[0].request("POST", f"/v1/projects/{project_id}/reports", {"text": "QUOTA_BOUNDARY requestId=quota-001", "service": "api"}, idempotency_key="scope")
    remote[2].run_once()
    with pytest.raises(GatewayError) as denied:
        remote[0].request("POST", f"/v1/projects/{project_id}/changes", {"text": "수정 후보", "service": "api", "previous_job_id": report["job_id"]}, idempotency_key="different-component")
    assert denied.value.status_code == 422 and remote[4].calls == 0


def test_default_check_never_targets_a_different_component(project):
    profile = project[0]
    policy, _ = load_repair_policy(profile)
    blockers = repair_blockers(project[2], replace(profile, service="other-app"), policy)
    assert "조사한 앱과 수정 검사 대상이 다릅니다" in blockers
