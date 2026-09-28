"""Create disposable public source/log fixtures for manual GUI verification.

This prepares files only. Registration, investigation, review and application
must be performed through the real app to verify its user flow.
"""
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys
from uuid import uuid4


def main():
    workspace = Path(__file__).resolve().parents[1]
    project_id = "gui-local-" + uuid4().hex[:8]
    root = workspace / "output/gui-validation" / project_id
    frontend, backend = root / "frontend", root / "backend"
    (frontend / "src").mkdir(parents=True)
    (backend / "src").mkdir(parents=True)
    (frontend / "src/app.py").write_text('ERROR_CODE = "QUOTA_BOUNDARY"\ndef accepted(count):\n    return count < 5\n', encoding="utf-8")
    (frontend / "checks.py").write_text(
        'import sys\nfrom src.app import accepted\n'
        'if sys.argv[1] == "boundary":\n'
        '    if not accepted(5):\n'
        '        print("QUOTA_BOUNDARY: five items must be accepted")\n'
        '        raise SystemExit(1)\n'
        'else:\n'
        '    assert accepted(0) and accepted(4) and not accepted(6)\n'
        'print("CHECK_PASSED")\n', encoding="utf-8")
    (backend / "src/handler.py").write_text('ERROR_CODE = "QUOTA_BOUNDARY"\n# Caller-provided quotas from zero through five are valid.\n', encoding="utf-8")
    received = datetime.now(timezone(timedelta(hours=9))).isoformat()
    event = {"trace": {"trace_id": "gui-quota-001", "service": "backend", "environment": "dev", "occurred_at": received,
        "response_status": 500, "method": "POST", "path": "/quotas"}, "message": "QUOTA_BOUNDARY: accepted quota was rejected"}
    (backend / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    metadata = {"project_id": project_id, "root": str(root), "frontend": str(frontend), "backend": str(backend),
        "received_at": received, "reproduction_argv": [sys.executable, "checks.py", "boundary"],
        "regression_argv": [sys.executable, "checks.py", "regression"], "public_synthetic_material": True}
    output = workspace / "output/gui-validation/latest-fixture.json"
    output.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(metadata, ensure_ascii=False))


if __name__ == "__main__":
    main()
