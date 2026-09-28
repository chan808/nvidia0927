"""Fixed reproduction/regression commands for later coding-agent comparisons.

Only this small, trusted synthetic fixture is executed. These checks are not an
OS sandbox, a project registration, or permission to execute arbitrary code.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


def run_check(function: str, required: str, candidate: Path | None = None) -> dict:
    root = Path(__file__).resolve().parent
    source = (candidate or root / "client.py").resolve()
    if source.suffix != ".py" or source.is_symlink():
        raise ValueError("Candidate must be a normal Python fixture file")
    spec = importlib.util.spec_from_file_location("evaluation_candidate", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    request = getattr(module, function)({"account_input": "synthetic-account", "name_input": "synthetic-name"})
    passed = set(request) == {required, "displayName"}
    return {"case_kind": "controlled_injection", "check": function, "status": "PASS" if passed else "FAIL",
            "actual_fields": sorted(request), "required_fields": [required, "displayName"]}


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", choices=("registration", "user", "account", "regression"), required=True)
    parser.add_argument("--candidate", type=Path)
    args = parser.parse_args(argv)
    if args.check == "regression":
        rows = [run_check("serialize_correct", "userId", args.candidate)]
    else:
        rows = [run_check("serialize_" + args.check, "accountId" if args.check == "account" else "userId", args.candidate)]
    print(json.dumps({"artifact_status": "DRAFT", "checks": rows}, ensure_ascii=False))
    return 0 if all(row["status"] == "PASS" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
