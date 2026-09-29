"""Reproduce a real local HTTP validation failure and investigate its persisted request."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

import httpx

from tracebridge.daily_observation import LOGIN_PATH
from tracebridge.project_profile import _health_url, load_project_profile, read_project_logs, select_project_service
from tracebridge.project_health import observe_service_health
from tracebridge.report_agent import investigate_submission
from tracebridge.report_contract import ReportContext


def verify(profile_path: Path, *, backend_url: str, output: Path) -> dict:
    url = _health_url(backend_url.rstrip("/") + LOGIN_PATH)
    profile = select_project_service(load_project_profile(profile_path), "backend")
    request_id = "daily-check-" + uuid4().hex
    occurred = datetime.now(timezone.utc).isoformat()
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
        capabilities = client.get(backend_url.rstrip("/") + "/api/v1/auth/capabilities")
        if capabilities.status_code != 200 or capabilities.json().get("data", {}).get("devLogin") is not True:
            raise ValueError("The endpoint is not a local development-login service")
        response = client.post(url, json={}, headers={"X-Request-Id": request_id})
    body = response.json()
    if (response.status_code != 400 or body.get("error", {}).get("code") != "VALIDATION_FAILED"
            or response.headers.get("X-Request-Id") != request_id):
        raise ValueError("The registered local failure did not reproduce; no diagnosis was submitted")
    deadline = time.monotonic() + 5
    event = None
    while time.monotonic() < deadline:
        collected = read_project_logs(profile, max_bytes=4_000_000, max_records=50_000)
        matches = [item for item in collected["events"] if item["trace"]["trace_id"] == request_id]
        if len(matches) == 1 and collected["complete"]:
            event = matches[0]
            break
        time.sleep(0.1)
    if event is None or event["trace"].get("response_status") != 400 or event["trace"].get("request_fields") != []:
        raise ValueError("The same request, status and observed empty input were not found in the persistent log")
    report = f"개발 로그인 요청이 400으로 실패합니다. requestId={request_id}"
    output.mkdir(parents=True, exist_ok=True)
    investigation = investigate_submission(report, project_profile=profile, use_nvidia=False, db_path=output / "incidents.sqlite3",
        context=ReportContext(environment=profile.environment, service="backend", occurred_at=occurred, trace_id=request_id))
    if investigation["correlation"] != "EXACT_ID" or investigation["trace_id"] != request_id:
        raise ValueError("TraceBridge did not correlate the reproduced HTTP request")
    result = {
        "recorded_at": datetime.now(timezone.utc).isoformat(), "project_id": profile.project_id,
        "scenario": "real_local_http_missing_required_input", "received_at": occurred,
        "request_id": request_id, "report": report, "http_status": response.status_code,
        "error_code": body["error"]["code"], "response_build_sha": response.headers.get("X-Daily-Build-Sha"),
        "trace": event["trace"], "correlation": investigation["correlation"],
        "incident_id": investigation["incident_id"], "run_id": investigation["run_id"], "route": investigation["route"],
        "version_provenance": investigation["version_provenance"], "model_calls": investigation["model_calls"],
        "service_health": observe_service_health(profile.health_url),
        "limitations": ["Controlled local input-validation failure, not a reported production defect",
            "No account or task was created; no source patch, external model, deployment or service recovery was performed"],
    }
    (output / "investigation.json").write_text(json.dumps(investigation, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "verification.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, default=Path("output/project-profiles/daily-local.json"))
    parser.add_argument("--backend-url", default="http://127.0.0.1:8081")
    parser.add_argument("--output", type=Path, default=Path("output/daily-observation/verification"))
    args = parser.parse_args()
    try:
        result = verify(args.profile, backend_url=args.backend_url, output=args.output)
    except (OSError, ValueError, httpx.HTTPError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
