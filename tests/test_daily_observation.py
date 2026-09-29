import hashlib
import json
from pathlib import Path

import httpx
import pytest

from tracebridge.daily_observation import configure_daily, CONTROLLER, LOGIN_PATH
from tracebridge.project_profile import load_project_profile, select_project_service, observe_project_version, _health_url
from tracebridge.project_registry import profile_data
from tracebridge.project_contracts import load_project_contract
from tracebridge.project_health import observe_service_health
from tracebridge.control_plane import Manifest


@pytest.fixture
def daily(tmp_path):
    root = tmp_path / "daily"
    controller = root / CONTROLLER
    controller.parent.mkdir(parents=True)
    controller.write_text('''data class DevLoginRequest(
    @field:Email val email: String,
    @field:NotBlank val name: String,
)
@PostMapping("/dev-login")
@RequestMapping("/api/v1/auth")
@Profile("local & !google")
''')
    caller = root / "frontend/src/lib/api.ts"
    caller.parent.mkdir(parents=True)
    caller.write_text('''export const devLogin = (email: string, name: string) =>
  api<{ userId: string }>("/api/v1/auth/dev-login", {
    method: "POST", body: JSON.stringify({ email, name }),
  });''')
    (root / "backend/app/src/main").mkdir(parents=True)
    (root / "backend/domain").mkdir()
    observation = tmp_path / "observations"
    observation.mkdir()
    return root, tmp_path / "registry", tmp_path / "artifacts", observation


def configure(daily):
    root, registry, artifacts, observation = daily
    return configure_daily(root, registry=registry, artifacts=artifacts, observation=observation)


def test_registration_links_scope_without_inventing_runtime_or_permissions(daily):
    result = configure(daily)
    profile = load_project_profile(result["profile"])
    backend = select_project_service(profile, "backend")
    assert backend.log_sources[0].id == "daily-http-requests"
    assert backend.version_observation.path == daily[3] / "runtime.json"
    assert profile.policy_refs == ()
    contract = load_project_contract(backend, "POST", LOGIN_PATH)
    assert contract["openapi"]["required"] == ["email", "name"]
    assert contract["caller"]["source_verified"]
    assert contract["versions"]["contract"] is None
    assert contract["versions"]["caller"] is None
    assert profile_data(profile)["services"][0]["health_url"] == backend.health_url


def test_runtime_contract_version_requires_embedded_matching_source_bytes(daily):
    controller = daily[0] / CONTROLLER
    snapshot = {"project_id": "daily-local", "service": "backend", "environment": "dev",
        "runtime_version": "a" * 40, "build": {"source": "spring_boot_build_info", "source_dirty": "true",
        "dev_login_source_sha256": hashlib.sha256(controller.read_bytes()).hexdigest()}}
    (daily[3] / "runtime.json").write_text(json.dumps(snapshot))
    assert configure(daily)["contract_runtime_version"] == "a" * 40
    profile = select_project_service(load_project_profile(daily[1] / "daily-local.json"), "backend")
    assert observe_project_version(profile)["source_tree_dirty"] is True
    controller.write_text(controller.read_text() + "// changed after build\n")
    assert configure(daily)["contract_runtime_version"] is None


def test_reregistration_preserves_owner_repair_permissions(daily):
    result = configure(daily)
    path = Path(result["profile"])
    data = json.loads(path.read_text())
    data["policy_refs"] = ["owner-policy"]
    path.write_text(json.dumps(data))
    configure(daily)
    assert load_project_profile(path).policy_refs == ("owner-policy",)


def test_conflicting_registration_is_rejected_before_overwriting_evidence(daily):
    result = configure(daily)
    path = Path(result["profile"])
    data = json.loads(path.read_text())
    another = daily[0] / "other-logs"
    another.mkdir()
    next(item for item in data["repositories"] if item["id"] == "daily-observations")["root"] = str(another)
    path.write_text(json.dumps(data))
    artifact = daily[2] / "openapi.json"
    artifact.write_text('{"owner":"retained"}')
    with pytest.raises(ValueError, match="another location"):
        configure(daily)
    assert artifact.read_text() == '{"owner":"retained"}'


@pytest.mark.parametrize("url", ["https://example.com/health", "http://127.0.0.1/health", "http://127.0.0.1:80/a?token=x",
    "http://user:secret@localhost:8081/health", "http://127.0.0.1:8081/health#x", "http://169.254.169.254:80/latest/meta-data/", "http://localhost:bad/health"])
def test_health_registration_rejects_external_or_credential_urls(url):
    with pytest.raises(ValueError):
        _health_url(url)


@pytest.mark.parametrize("status,body,expected", [(200, {"status": "UP", "secret": "discard"}, "UP"),
    (200, {"status": "DOWN"}, "DOWN"), (503, {}, "UNREACHABLE"), (302, {}, "UNREACHABLE"),
    (200, {"status": ["invalid"]}, "REACHABLE")])
def test_health_observation_uses_status_only_and_does_not_follow_redirects(monkeypatch, status, body, expected):
    original = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json=body, headers={"location": "http://external.invalid/"}))
    def factory(**kwargs):
        assert kwargs["follow_redirects"] is False and kwargs["trust_env"] is False
        return original(transport=transport, **kwargs)
    monkeypatch.setattr("tracebridge.project_health.httpx.Client", factory)
    result = observe_service_health("http://127.0.0.1:8081/health")
    assert result["status"] == expected
    assert "discard" not in json.dumps(result)


def test_service_manifest_does_not_promote_another_service_or_untimed_state():
    data = {"project_id": "daily-local", "environment": "dev", "service_ids": ["backend"],
        "repository_ids": ["backend"], "profile_sha256": "a" * 64}
    assert Manifest(**data).service_health == {}
    with pytest.raises(ValueError):
        Manifest(**data, service_health={"another": "UP"})
    with pytest.raises(ValueError):
        Manifest(**data, service_health={"backend": "invented"})
    with pytest.raises(ValueError):
        Manifest(**data, health_checked_at="2026-09-29T12:00:00")


def test_persisted_metadata_is_correlated_with_actual_time_and_remains_held_without_it(daily):
    from tracebridge.report_agent import investigate_submission
    from tracebridge.report_contract import ReportContext
    result = configure(daily)
    occurred = "2026-09-29T07:00:00+00:00"
    trace = {"project_id": "daily-local", "service": "backend", "environment": "dev", "occurred_at": occurred,
        "trace_id": "daily-test-001", "method": "POST", "path": LOGIN_PATH, "response_status": 400,
        "request_fields": ["email"], "request_types": {"email": "string"}}
    (daily[3] / "requests.jsonl").write_text(json.dumps({"trace": trace, "message": "HTTP 400 code=VALIDATION_FAILED"}) + "\n")
    profile = select_project_service(load_project_profile(result["profile"]), "backend")
    report = "개발 로그인에서 실패해요 requestId=daily-test-001"
    held = investigate_submission(report, project_profile=profile, use_nvidia=False, db_path=daily[2] / "incidents.sqlite3")
    assert held["route"] == "REQUEST_CONTEXT"
    observed = investigate_submission(report, project_profile=profile, use_nvidia=False, db_path=daily[2] / "incidents.sqlite3",
        context=ReportContext(environment="dev", service="backend", occurred_at=occurred))
    assert observed["correlation"] == "EXACT_ID"
    assert observed["trace_id"] == "daily-test-001"
    assert observed["model_calls"] == 0
    evidence = next(item for item in observed["evidence"] if item["kind"] == "log")
    assert json.loads(evidence["content"])["request_fields"] == ["email"]
