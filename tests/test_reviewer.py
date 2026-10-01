from io import BytesIO
import json
from pathlib import Path
import time
from zipfile import ZipFile

from fastapi.testclient import TestClient
import pytest
from streamlit.testing.v1 import AppTest

from tracebridge.control_plane import create_app
from tracebridge.local_runner import ControlClient, LocalRunner, pair_runner
from tracebridge.reviewer import companion_zip, read_review, sign_review
from tracebridge.reviewer_demo import create_demo


SECRET = "test-review-operator-" + "x" * 40


@pytest.fixture
def review_server(tmp_path):
    with TestClient(create_app(tmp_path / "control.sqlite3", operator_token=SECRET)) as client:
        yield client


def connection(client):
    response = client.post("/review/download")
    assert response.status_code == 200
    with ZipFile(BytesIO(response.content)) as archive:
        return json.loads(archive.read("TraceBridge-Review/review-connection.json")), archive.namelist()


def test_public_welcome_and_real_fresh_browser_repair(review_server):
    welcome = review_server.get("/welcome")
    assert welcome.status_code == 200 and "로그인 없이" in welcome.text
    result = review_server.post("/review/preview")
    assert result.status_code == 200, result.text
    evidence = result.json()
    assert evidence["before"]["http_status"] == 500 and not evidence["before"]["accepted"]
    assert evidence["after"] == {"http_status": 200, "accepted": True}
    assert evidence["correlation"] == "EXACT_ID"
    assert [row["exit_code"] for row in evidence["checks"]] == [1, 0, 0]
    assert evidence["recovery"] == "PASSED" and evidence["incident_state"] == "RESOLVED"
    assert evidence["mode"] == "DEMO_FIXED_PATCH" and evidence["external_model_calls"] == 0
    assert "+    return 0 <= count <= 5" in evidence["diff"]
    assert not list((Path(__file__).resolve().parents[1] / "output/reviewer-preview").glob("demo-*"))


def test_download_is_scoped_source_only_and_owner_endpoints_stay_protected(review_server):
    config, names = connection(review_server)
    project = read_review(config["token"], SECRET)
    assert project == config["project_id"]
    assert all(not any(part in name for part in (".env", "output/", ".git/", ".venv/", "operator-token")) for name in names)
    assert "TraceBridge-Review/START.cmd" in names
    assert "TraceBridge-Review/examples/reviewer_demo/serve.py" in names
    assert "TraceBridge-Review/review_app.py" in names
    headers = {"Authorization": "Bearer " + config["token"]}
    assert review_server.get("/v1/projects", headers=headers).status_code == 401
    assert review_server.post("/v1/pairings", headers=headers, json={"project_ids": ["owner-project"]}).status_code == 401
    assert review_server.get("/review/project", headers=headers).json() is None
    assert review_server.get("/review/project").status_code == 401


@pytest.mark.parametrize("kind", ["tampered", "expired", "malformed"])
def test_review_credentials_reject_tampering_and_expiry(kind):
    token = sign_review("review-" + "a" * 24, int(time.time() + 60), SECRET)
    if kind == "tampered":
        token = token[:-1] + ("0" if token[-1] != "0" else "1")
    elif kind == "expired":
        token = sign_review("review-" + "a" * 24, int(time.time() - 1), SECRET)
    else:
        token = "bad-data.bad-signature"
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as denied:
        read_review(token, SECRET)
    assert denied.value.status_code == 401


def test_two_reviewers_cannot_read_each_others_project_or_job(review_server, tmp_path, monkeypatch):
    first, _ = connection(review_server)
    second, _ = connection(review_server)
    registry = tmp_path / "registry"
    monkeypatch.setenv("TRACEBRIDGE_PROJECT_REGISTRY", str(registry))
    profile, _ = create_demo(first["project_id"], parent=tmp_path, registry=registry)
    viewer = ControlClient("http://testserver", first["token"], client=review_server)
    second_viewer = ControlClient("http://testserver", second["token"], client=review_server)
    code = viewer.request("POST", "/review/pair")["code"]
    runner_config = tmp_path / "runner/config.json"
    pair_runner("http://testserver", code, runner_config, client=review_server)
    runner = LocalRunner.from_config(runner_config, client=review_server)
    runner.publish_profiles()
    assert viewer.request("GET", "/review/project")["project_id"] == first["project_id"]
    assert second_viewer.request("GET", "/review/project") is None
    headers = {"Authorization": "Bearer " + first["token"]}
    assert review_server.post("/review/pair", headers=headers).status_code == 409
    job = viewer.request("POST", "/review/reports", {"text": "5개 신청이 안돼요"}, idempotency_key="scoped-test")
    second_headers = {"Authorization": "Bearer " + second["token"]}
    assert review_server.get("/review/jobs/" + job["job_id"], headers=second_headers).status_code == 404
    assert viewer.request("GET", "/review/jobs/" + job["job_id"])["state"] == "QUEUED"
    assert review_server.post("/review/changes", headers=headers, json={"text": "수정", "previous_job_id": job["job_id"], "policy_id": profile.policy_refs[0], "use_nvidia": True}).status_code == 422


def test_companion_offline_demo_runs_ui_repair_and_http_recovery(tmp_path, monkeypatch, review_server):
    import tracebridge.local_runner as local
    import tracebridge.reviewer_demo as demo_module
    config, _ = connection(review_server)
    root = tmp_path / "companion"
    root.mkdir()
    with ZipFile(BytesIO(companion_zip("http://testserver", config["project_id"], config["token"]))) as archive:
        for name in archive.namelist():
            target = root / name.removeprefix("TraceBridge-Review/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(name))
    # AppTest uses the current runtime; bind its sample parent to this workspace.
    monkeypatch.setattr(demo_module, "desktop_directory", lambda: tmp_path)
    monkeypatch.setenv("TRACEBRIDGE_PROJECT_REGISTRY", str(root / "output/project-profiles"))
    monkeypatch.setenv("TRACEBRIDGE_ALLOW_PROJECT_REGISTRATION", "1")
    monkeypatch.setattr(local.LocalRunner, "run", lambda self: None)
    original = local.ControlClient
    monkeypatch.setattr(local, "ControlClient", lambda url, token, **kwargs: original(url, token, client=review_server))
    import tracebridge.project_connection_ui as connection_ui
    monkeypatch.setattr(connection_ui, "list_profiles", lambda: ([], []))
    page = AppTest.from_file(str(root / "review_app.py"), default_timeout=45).run()
    assert not page.exception
    page.button(key="start-demo").click().run()
    assert not page.exception
    process = page.session_state["demo_process"]
    try:
        page.button(key="reproduce").click().run()
        assert not page.exception and page.session_state["source"]["correlation"] == "EXACT_ID"
        page.button(key="prepare-demo").click().run()
        assert not page.exception and page.session_state["candidate"]["job"]["candidate_fix_verified"]
        page.checkbox(key="review-diff").check().run()
        page.button(key="apply-demo").click().run()
        assert not page.exception and page.session_state["application"]["persistence"]["status"] == "SAVED"
        page.button(key="verify-demo").click().run()
        assert not page.exception and page.session_state["recovery"]["verification"]["status"] == "PASSED"
        assert "count <= 5" in (tmp_path / ("TraceBridge-Demo-" + config["project_id"].removeprefix("review-")) / "app.py").read_text()
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        page.button(key="stop-demo").click().run()
