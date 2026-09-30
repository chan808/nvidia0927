"""Local registration, factual HTTP diagnostics and scoped PC publication."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError
from streamlit.testing.v1 import AppTest

from test_control_plane import remote, project
from tracebridge.control_plane import Manifest
from tracebridge.project_health import assess_connection, connection_checks, inspect_project, observe_service_health
from tracebridge.project_profile import load_project_profile
from tracebridge.project_registry import profile_data, save_profile
from tracebridge.project_repair import save_repair_policy


@pytest.mark.parametrize("status,reason", [(401, "HEALTH_AUTH_REQUIRED"), (403, "HEALTH_AUTH_REQUIRED"),
    (404, "HEALTH_ENDPOINT_NOT_FOUND"), (302, "HEALTH_REDIRECT_NOT_FOLLOWED"), (500, "HTTP_SERVER_ERROR")])
def test_http_response_is_not_misreported_as_connection_failure(monkeypatch, status, reason):
    client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"private": "PRIVATE_BODY"}))
    monkeypatch.setattr("tracebridge.project_health.httpx.Client", lambda **kwargs: client(transport=transport, **kwargs))
    result = observe_service_health("http://127.0.0.1:12345/health")
    assert result["http_status"] == status and result["reason"] == reason
    assert result["status"] == ("DOWN" if status >= 500 else "UNKNOWN")
    assert "PRIVATE_BODY" not in json.dumps(result)


def test_invalid_health_body_retains_http_observation(monkeypatch):
    client = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"invalid", headers={"content-type": "application/json"}))
    monkeypatch.setattr("tracebridge.project_health.httpx.Client", lambda **kwargs: client(transport=transport, **kwargs))
    assert observe_service_health("http://127.0.0.1:12345/health")["reason"] == "HEALTH_RESPONSE_INVALID"


def test_real_http_service_availability_changes_without_model_calls(project):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    state = {"status": "UP"}
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps(state).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/health"
    source = (project[-1] / "app.py").read_bytes()
    try:
        profile = load_project_profile(save_profile({**profile_data(project[0]), "health_url": url}))
        health = inspect_project(profile)
        assert health["services"][0]["service_status"] == "UP"
        state["status"] = "DOWN"
        health = inspect_project(profile)
        assert health["services"][0]["service_status"] == "DOWN"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert observe_service_health(url)["status"] == "UNREACHABLE"
    assert (project[-1] / "app.py").read_bytes() == source


def snapshot():
    return {"online": True, "health_checked_at": datetime.now(timezone.utc).isoformat(), "service_health": {"api": "UP"},
        "connection_checks": {"api": {"code_status": "READY", "log_status": "READY", "observed_requests": 1,
            "contract_status": "REGISTERED", "dto_registered": True, "caller_registered": True,
            "runtime_version_status": "OBSERVED", "http_status": 200, "health_reason": "HTTP_SUCCESS"}}}


@pytest.mark.parametrize("changed", ["offline", "stale", "future", "missing-time"])
def test_old_or_offline_observations_do_not_claim_current_health(changed):
    value = snapshot()
    if changed == "offline": value["online"] = False
    if changed == "stale": value["health_checked_at"] = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
    if changed == "future": value["health_checked_at"] = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
    if changed == "missing-time": value.pop("health_checked_at")
    result = assess_connection(value, "api")
    assert result["availability"] == "UNKNOWN" and result["readiness"] == "UNKNOWN" and result["next_steps"]


def test_service_response_and_investigation_material_are_separate():
    value = snapshot()
    value["connection_checks"]["api"].update(log_status="DEGRADED", contract_status="UNAVAILABLE", runtime_version_status="UNOBSERVED")
    result = assess_connection(value, "api")
    assert result["availability"] == "UP" and result["readiness"] == "PARTIAL"
    assert len(result["next_steps"]) == 3
    assert "RESOLVED" not in json.dumps(result)


def test_data_only_repository_does_not_claim_code_is_connected(project):
    data = profile_data(project[0])
    data["code_roots"] = []
    data["repositories"] = [{"id": "materials", "service": data["service"], "root": data["root"], "code_roots": []}]
    health = inspect_project(load_project_profile(save_profile(data)))
    assert connection_checks(health)[data["service"]]["code_status"] == "UNAVAILABLE"


def test_manifest_publishes_scope_bounded_diagnostics_without_paths(remote):
    current = remote[0].request("GET", "/v1/projects")[0]
    assert current["connection_checks"]["api"]["code_status"] == "READY"
    assert str(remote[-1].root) not in json.dumps(current)
    with pytest.raises(ValidationError):
        Manifest.model_validate({**{key: value for key, value in current.items() if key not in {"runner_id", "online", "semantic_search_enabled", "semantic_index_mode"}},
            "connection_checks": {"foreign": current["connection_checks"]["api"]}})


def test_registration_update_rejects_stale_profile_and_preserves_original(project):
    profile = project[0]
    old = hashlib.sha256(profile.config_path.read_bytes()).hexdigest()
    updated = {**profile_data(profile), "health_url": "http://127.0.0.1:12345/health"}
    save_profile(updated, expected_sha256=old)
    saved = profile.config_path.read_bytes()
    with pytest.raises(ValueError, match="설정이 변경"):
        save_profile(profile_data(profile), expected_sha256=old)
    assert profile.config_path.read_bytes() == saved
    assert load_project_profile(profile.config_path).policy_refs == profile.policy_refs


@pytest.mark.skipif(os.name != "nt", reason="Windows read handles deny atomic replacement")
@pytest.mark.parametrize("kind", ["profile", "policy"])
def test_registration_save_waits_for_real_windows_reader(project, kind):
    import threading
    profile = project[0]
    path = profile.config_path if kind == "profile" else profile.config_path.parent / "repair-policies/quota-repair.json"
    data = {**profile_data(profile), "health_url": "http://127.0.0.1:12345/health"} if kind == "profile" else {**project[1], "version": 2}
    expected = hashlib.sha256(path.read_bytes()).hexdigest()
    reader = path.open("rb")
    release = threading.Event()
    def close_reader():
        release.wait(0.15)
        reader.close()
    thread = threading.Thread(target=close_reader)
    thread.start()
    try:
        if kind == "profile":
            save_profile(data, expected_sha256=expected)
        else:
            save_repair_policy(data, profile)
    finally:
        release.set()
        thread.join(timeout=3)
        reader.close()
    saved = json.loads(path.read_bytes())
    assert saved.get("health_url") == data["health_url"] if kind == "profile" else saved["version"] == 2
    assert not list(path.parent.glob(".registration-*.json")) and not list(path.parent.glob(".policy-*.json"))


@pytest.mark.parametrize("kind", ["profile", "policy"])
def test_permanent_registration_write_denial_preserves_previous_file(project, monkeypatch, kind):
    profile = project[0]
    path = profile.config_path if kind == "profile" else profile.config_path.parent / "repair-policies/quota-repair.json"
    original = path.read_bytes()
    def denied(*args):
        error = PermissionError("persistent synthetic denial")
        error.winerror = 5
        raise error
    monkeypatch.setattr(Path, "replace", denied)
    monkeypatch.setattr("tracebridge.project_registry.time.sleep", lambda delay: None)
    with pytest.raises(PermissionError, match="persistent synthetic denial"):
        if kind == "profile":
            save_profile(profile_data(profile))
        else:
            save_repair_policy(project[1], profile)
    assert path.read_bytes() == original
    assert not list(path.parent.glob(".registration-*.json")) and not list(path.parent.glob(".policy-*.json"))


def test_configuration_change_during_windows_retry_is_not_overwritten(project, monkeypatch):
    profile = project[0]
    original_hash = hashlib.sha256(profile.config_path.read_bytes()).hexdigest()
    other = {**profile_data(profile), "health_url": "http://127.0.0.1:23456/health"}
    def busy(*args):
        error = PermissionError("synthetic reader")
        error.winerror = 32
        raise error
    monkeypatch.setattr(Path, "replace", busy)
    monkeypatch.setattr("tracebridge.project_registry.time.sleep", lambda delay: profile.config_path.write_text(json.dumps(other), encoding="utf-8"))
    with pytest.raises(ValueError, match="설정이 변경"):
        save_profile(profile_data(profile), expected_sha256=original_hash)
    assert load_project_profile(profile.config_path).health_url == other["health_url"]
    assert not list(profile.config_path.parent.glob(".registration-*.json"))


def configure_ui(remote, monkeypatch):
    import tracebridge.local_runner as local
    monkeypatch.setenv("TRACEBRIDGE_CONTROL_URL", "http://testserver")
    monkeypatch.setenv("TRACEBRIDGE_OPERATOR_TOKEN", "synthetic-owner")
    monkeypatch.setenv("TRACEBRIDGE_ALLOW_PROJECT_REGISTRATION", "1")
    monkeypatch.setattr(local, "ControlClient", lambda *args, **kwargs: remote[0])
    return AppTest.from_file(str(Path(__file__).resolve().parents[1] / "pages/3_Remote_Projects.py"), default_timeout=20)


def test_connected_page_edits_local_health_url_and_preserves_repair_policy(remote, monkeypatch):
    profile = remote[-1]
    original_policy = (profile.config_path.parent / "repair-policies/quota-repair.json").read_bytes()
    prefix = "connected:" + profile.project_id + ":" + hashlib.sha256(profile.config_path.read_bytes()).hexdigest()[:12]
    page = configure_ui(remote, monkeypatch).run()
    assert not page.exception
    assert page.text_input(key=prefix + "_primary_root").value == str(profile.root)
    page.text_input(key=prefix + "_health").set_value("http://127.0.0.1:12345/health")
    next(button for button in page.button if button.label == "프로젝트 연결 저장").click().run()
    assert not page.exception
    updated = load_project_profile(profile.config_path)
    assert updated.health_url == "http://127.0.0.1:12345/health" and updated.policy_refs == profile.policy_refs
    assert (profile.config_path.parent / "repair-policies/quota-repair.json").read_bytes() == original_policy


def test_connected_page_local_doctor_explains_missing_service_address(remote, monkeypatch):
    page = configure_ui(remote, monkeypatch).run()
    page.button(key="connection-doctor:" + remote[-1].project_id).click().run()
    assert not page.exception
    assert any("실제 가동 여부를 확인할 수 없습니다" in item.value for item in page.markdown)
    assert remote[4].calls == 0


def test_expired_direct_check_does_not_hide_new_pc_observations(remote, monkeypatch):
    profile = remote[-1]
    key = "connection-local:" + profile.project_id + ":" + hashlib.sha256(profile.config_path.read_bytes()).hexdigest()
    page = configure_ui(remote, monkeypatch).run()
    page.session_state[key] = {"online": True, "health_checked_at": (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat(),
        "service_health": {profile.service: "UP"}, "connection_checks": {}}
    page.run()
    assert not page.exception
    assert next(item for item in page.metric if item.label == "서비스 응답").value == "주소 미등록"
    page.button(key="connection-refresh:" + profile.project_id).click().run()
    assert not page.exception and key not in page.session_state


def test_local_registration_is_hidden_on_remote_deployment(remote, monkeypatch):
    page = configure_ui(remote, monkeypatch)
    monkeypatch.delenv("TRACEBRIDGE_ALLOW_PROJECT_REGISTRATION")
    page.run()
    assert not page.exception and not any("_primary_root" in (field.key or "") for field in page.text_input)


def test_new_runner_falls_back_only_for_old_manifest_schema(remote, monkeypatch):
    request = remote[1].request
    attempted = []
    from tracebridge.local_runner import GatewayError
    def older_server(method, path, payload=None, **kwargs):
        if path == "/v1/runner/manifest":
            attempted.append(deepcopy(payload))
            if "connection_checks" in payload:
                raise GatewayError("connection_checks extra_forbidden", 422)
        return request(method, path, payload, **kwargs)
    monkeypatch.setattr(remote[1], "request", older_server)
    remote[2].publish_profiles()
    assert len(attempted) == 2 and "connection_checks" not in attempted[-1]
