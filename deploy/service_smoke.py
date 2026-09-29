"""Actual HTTP/PG smoke with synthetic intake; never asks for a model call."""
import argparse
import json
from pathlib import Path
import secrets
import urllib.error
import urllib.request


def request(url, method="GET", body=None, token=None, key=None, expected=200):
    headers = {}
    if token:
        headers["Authorization"] = "Bearer " + token
    if key:
        headers["Idempotency-Key"] = key
    raw = None
    if body is not None:
        headers["Content-Type"] = "application/json"
        raw = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=raw, headers=headers, method=method), timeout=20) as r:
            code, data = r.status, r.read()
    except urllib.error.HTTPError as error:
        code, data = error.code, error.read()
    if code != expected:
        raise RuntimeError("HTTP smoke failed: " + method + " expected " + str(expected) + " got " + str(code))
    return json.loads(data) if data else {}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://control:8765")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    base = args.url.rstrip("/")
    owner = Path("/app/output/control-plane/operator-token").read_text().strip()
    marker = Path("/app/output/deployment-smoke/http-check.json")
    health = request(base + "/health")
    assert health["storage"] == "POSTGRESQL"
    request(base + "/v1/projects", expected=401)
    if args.resume:
        saved = json.loads(marker.read_text())
        status = request(base + saved["path"], token=saved["token"])
        assert status["report_id"] == saved["report_id"] and status["status"] == "QUEUED"
        request(base + saved["path"], token="wrong-receipt-token", expected=404)
        print(json.dumps({"http_pg_reopen": "PASSED", "external_model_calls": 0}))
        return
    project = "deployment-smoke"
    pairing = request(base + "/v1/pairings", "POST", {"project_ids": [project]}, owner)
    credential = request(base + "/v1/pairings/consume", "POST", {"code": pairing["code"]})["token"]
    manifest = {"project_id": project, "environment": "test", "service_ids": ["api"],
        "repository_ids": ["synthetic"], "profile_sha256": "0" * 64, "status": "READY"}
    request(base + "/v1/runner/manifest", "PUT", manifest, credential)
    request(base + "/v1/projects/" + project + "/service-policy", "PUT",
        {"expected_revision": 0, "policy": {"enabled": True, "services": ["api"], "use_nvidia": False,
        "automation": "INVESTIGATE", "hourly_limit": 5, "pending_limit": 2}}, owner)
    prefix = "/v1/public/projects/" + project + "/reports"
    body = {"text": "Synthetic deployment verification", "service": "api", "allow_external_analysis": False}
    key = secrets.token_hex(24)
    report = request(base + prefix, "POST", body, key=key, expected=202)
    replay = request(base + prefix, "POST", body, key=key, expected=202)
    assert report["report_id"] == replay["report_id"] and report["receipt_token"] == replay["receipt_token"]
    assert owner not in json.dumps(report) and credential not in json.dumps(report)
    path = prefix + "/" + report["report_id"]
    state = request(base + path, token=report["receipt_token"])
    assert state["status"] == "QUEUED"
    request(base + path, token="wrong-receipt-token", expected=404)
    request(base + prefix, "POST", {**body, "text": "Different input"}, key=key, expected=409)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({"path": path, "token": report["receipt_token"], "report_id": report["report_id"]}))
    marker.chmod(0o600)
    print(json.dumps({"http_intake": "PASSED", "receipt_scope": "PASSED", "idempotency": "PASSED", "external_model_calls": 0}))


if __name__ == "__main__":
    main()
