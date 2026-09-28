"""Public synthetic project: HTTP report -> PC snapshot -> optional NVIDIA patch -> checks.

No real project is modified. Without --live, only the registered checks run.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import secrets
import sys
from uuid import uuid4

from fastapi.testclient import TestClient

from tracebridge.control_plane import create_app
from tracebridge.local_runner import ControlClient, LocalRunner
from tracebridge.project_profile import load_project_profile
from tracebridge.project_registry import save_profile
from tracebridge.project_repair import save_repair_policy, run_project_checks
from tracebridge.report_agent import nvidia_settings


@contextmanager
def synthetic_registration(directory):
    name = "TRACEBRIDGE_PROJECT_REGISTRY"
    previous = os.environ.get(name)
    os.environ[name] = str(directory)
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Send only this public synthetic source and failure to the server's NVIDIA gateway")
    parser.add_argument("--output", type=Path, default=Path("output/project-repair-live"))
    args = parser.parse_args()
    directory = args.output.resolve() / uuid4().hex
    root = directory / "external-project"
    root.mkdir(parents=True)
    (root / "app.py").write_bytes(b'ERROR_CODE = "QUOTA_BOUNDARY"\ndef accepted(count):\n    return count < 5\n')
    (root / "checks.py").write_bytes(b'import sys\nfrom app import accepted\nif sys.argv[1] == "boundary":\n    if not accepted(5):\n        print("QUOTA_BOUNDARY: five items must be accepted")\n        raise SystemExit(1)\nelse:\n    assert accepted(0) and accepted(4) and not accepted(6)\nprint("CHECK_PASSED")\n')
    event = {"trace": {"trace_id": "quota-public-001", "service": "api", "environment": "dev", "occurred_at": "2026-09-29T12:00:00+09:00", "response_status": 500,
        "method": "POST", "path": "/quotas"}, "message": "QUOTA_BOUNDARY: accepted quota was rejected"}
    (root / "events.jsonl").write_text(json.dumps(event) + "\n")
    with synthetic_registration(directory / "registry"):
        profile = load_project_profile(save_profile({"project_id": "public-quota-validation", "root": str(root), "code_roots": ["."], "service": "api", "environment": "dev",
            "log_sources": [{"id": "requests", "path": "events.jsonl", "format": "jsonl"}], "policy_refs": ["public-quota-repair"]}))
        save_repair_policy({"policy_id": "public-quota-repair", "project_id": profile.project_id, "repository_id": "primary", "environment": "dev", "enabled": True,
            "execution_mode": "TRUSTED_LOCAL", "trust_project_code": True, "editable_paths": ["app.py"],
            "checks": [{"id": id_, "argv": [sys.executable, "checks.py", id_], "success_marker": "CHECK_PASSED"} for id_ in ("boundary", "regression")],
            "reproduction_check_id": "boundary", "regression_check_id": "regression", "failure_marker": "QUOTA_BOUNDARY"}, profile)
        if not args.live:
            result = run_project_checks(profile, artifact_root=directory / "baseline-checks")
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        key, model = nvidia_settings()
        if not key:
            parser.error("Server NVIDIA connection is required")
        operator = secrets.token_urlsafe(48)
        http = TestClient(create_app(directory / "server.sqlite3", operator_token=operator, nvidia_key=key, model=model))
        owner = ControlClient("http://testserver", operator, client=http)
        code = owner.request("POST", "/v1/pairings", {"project_ids": [profile.project_id]})["code"]
        credential = http.post("/v1/pairings/consume", json={"code": code}).json()
        runner = LocalRunner(ControlClient("http://testserver", credential["token"], client=http), directory / "runner", project_ids=[profile.project_id], registry=profile.config_path.parent)
        runner.publish_profiles()
        first = owner.request("POST", f"/v1/projects/{profile.project_id}/reports", {"text": "다섯 개 입력을 거절합니다. QUOTA_BOUNDARY requestId=quota-public-001 고쳐줘.", "service": "api",
            "context": {"occurred_at": "2026-09-29T12:00:00+09:00"}}, idempotency_key="public-report")
        runner.run_once()
        change = owner.request("POST", f"/v1/projects/{profile.project_id}/changes", {"text": "검증할 후보를 준비해 주세요.", "service": "api", "use_nvidia": True,
            "previous_job_id": first["job_id"]}, idempotency_key="public-change")
        runner.run_once()
        result = owner.request("GET", "/v1/jobs/" + change["job_id"])
        (directory / "validation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        job = result["result"]["job"]
        print(json.dumps({"output": str(directory / "validation.json"), "job_status": job["status"], "model": job["model"],
            "candidate_fix_verified": job["candidate_fix_verified"], "original_unchanged": job["original_unchanged"],
            "checks": [{key: item[key] for key in ("phase", "status", "exit_code")} for item in job["checks"]]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
