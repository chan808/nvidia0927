"""An explicitly scripted patch exercised through the real repair/recovery pipeline."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time

import httpx

from .project_profile import load_project_profile
from .project_registry import ROOT, save_profile
from .project_repair import save_repair_policy


class DemoPatch:
    metadata = {"mode": "DEMO_FIXED_PATCH", "actual_calls": 0, "test_double_calls": 0}

    def propose(self, context, timeout):
        file = context["source_files"].get("app.py")
        template = (ROOT / "examples/reviewer_demo/app.py").read_bytes()
        if not file or file["sha256"] != hashlib.sha256(template).hexdigest():
            raise ValueError("기본 예제와 코드가 다릅니다. 준비된 예제 패치를 적용하지 않습니다.")
        return {"rationale": "DEMO_FIXED_PATCH: 예제의 허용 경계값 5를 포함하는 준비된 패치. 모델 호출 없음.",
                "evidence_ids": context["evidence_ids"][:1],
                "edits": [{"path": "app.py", "expected_sha256": file["sha256"],
                           "content": file["content"].replace("count < 5", "count <= 5")} ]}


def desktop_directory():
    if os.name == "nt":
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
            return Path(os.path.expandvars(winreg.QueryValueEx(key, "Desktop")[0])).resolve()
    return Path.home() / "Desktop"


def create_demo(project_id, *, parent=None, registry=None):
    parent = (Path(parent) if parent is not None else desktop_directory()).resolve()
    root = parent / ("TraceBridge-Demo-" + project_id.removeprefix("review-"))
    root.mkdir(parents=True, exist_ok=False)
    for source in (ROOT / "examples/reviewer_demo").glob("*.py"):
        shutil.copyfile(source, root / source.name)
    (root / "output").mkdir()
    (root / "output/events.jsonl").touch()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    policy_id = "review-demo-repair-" + project_id.removeprefix("review-")
    profile = load_project_profile(save_profile({"project_id": project_id, "display_name": "5개 신청 버그 예제",
        "service": "api", "environment": "dev", "root": str(root), "code_roots": ["."],
        "log_sources": [{"id": "requests", "path": "output/events.jsonl", "format": "jsonl"}],
        "version_observation": {"method": "json_file", "path": "output/version.json", "field": "snapshot_sha256"},
        "health_url": url + "/health", "policy_refs": [policy_id]}, directory=registry))
    save_repair_policy({"policy_id": policy_id, "project_id": project_id, "repository_id": "primary", "environment": "dev",
        "enabled": True, "execution_mode": "TRUSTED_LOCAL", "trust_project_code": True, "allow_apply": True,
        "editable_paths": ["app.py"], "checks": [
            {"id": "boundary", "argv": [sys.executable, "checks.py", "boundary"], "success_marker": "CHECK_PASSED"},
            {"id": "regression", "argv": [sys.executable, "checks.py", "regression"], "success_marker": "CHECK_PASSED"}],
        "reproduction_check_id": "boundary", "regression_check_id": "regression", "failure_marker": "QUOTA_BOUNDARY",
        "recovery": {"samples": 2, "interval_seconds": 1, "checks": [
            {"id": "journey", "method": "POST", "read_only": True, "url": url + "/quotas", "json_body": {"count": 5}, "expected_json": {"accepted": True}},
            {"id": "regression", "read_only": True, "url": url + "/limits", "expected_json": {"ok": True}}]}}, profile, directory=profile.config_path.parent / "repair-policies")
    return profile, url


def start_demo(profile, url, runtime):
    try:
        response = httpx.get(url + "/health", timeout=1)
        if response.status_code == 200 and response.json().get("demo") == "tracebridge-quota":
            return None
    except (httpx.HTTPError, ValueError):
        pass
    # The process can import the bundled runtime, without receiving cloud secrets.
    env = {key: value for key, value in os.environ.items() if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "LANG", "APPDATA", "LOCALAPPDATA", "USERPROFILE"}}
    env.update(PYTHONPATH=str(ROOT), PYTHONDONTWRITEBYTECODE="1")
    runtime = Path(runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    with (runtime / "demo.log").open("ab") as log:
        process = subprocess.Popen([sys.executable, str(profile.root / "serve.py"), "--port", url.rsplit(":", 1)[1]],
            cwd=profile.root, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    for _ in range(40):
        if process.poll() is not None:
            raise RuntimeError("예제 앱을 시작하지 못했습니다. " + str(runtime / "demo.log"))
        try:
            response = httpx.get(url + "/health", timeout=0.5)
            if response.status_code == 200 and response.json().get("demo") == "tracebridge-quota":
                return process
        except (httpx.HTTPError, ValueError):
            pass
        time.sleep(0.1)
    process.terminate()
    raise RuntimeError("예제 앱 실행을 확인하지 못했습니다. 다시 실행해 주세요.")
