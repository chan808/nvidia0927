"""Registered seed checks. This file is hash pinned and cannot be edited by proposals."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location("seed_" + name, ROOT / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def check(mode):
    client, api = load("client"), load("api")
    raw = (ROOT / "cases.json").read_bytes()
    inputs = json.loads(raw)
    result = {"case_kind": "SEEDED_DEVELOPMENT", "check_id": "signup-" + mode,
              "input_id": inputs["input_id"], "inputs_sha256": hashlib.sha256(raw).hexdigest()}
    if mode == "contract":
        payload = client.build_signup_request(**inputs["happy"])
        status = api.signup(payload)
        passed = status == 201 and "userId" in payload
        seeded_failure = status == 422 and "user_id" in payload and "userId" not in payload
        result.update(status="PASSED" if passed else "FAILED",
                      failure_kind=None if passed else "contract_field_mismatch" if seeded_failure else "other_failure",
                      failure_signature="seed-user_id-vs-userId" if seeded_failure else None,
                      observation={"response_status": status, "request_fields": sorted(payload)})
    elif mode == "regression":
        results = []
        for case in inputs["regression"]:
            payload = client.build_signup_request(**case)
            expected = {"userId": case["user_id"], "displayName": case["display_name"]}
            results.append(payload == expected and api.signup(payload) == (201 if case["user_id"] else 422))
        result.update(status="PASSED" if all(results) else "FAILED",
                      failure_kind=None if all(results) else "regression_failure", cases=results)
    else:
        raise ValueError("Unregistered seed check")
    return result


if __name__ == "__main__":
    try:
        outcome = check(sys.argv[1])
    except Exception as exc:
        outcome = {"case_kind": "SEEDED_DEVELOPMENT", "status": "EXECUTION_ERROR", "error_type": type(exc).__name__}
    print(json.dumps(outcome, sort_keys=True))
    sys.exit(0 if outcome["status"] == "PASSED" else 1 if outcome["status"] == "FAILED" else 2)
