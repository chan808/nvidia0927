"""Fixed test templates and bounded execution for supported demo cases."""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]
GENERATED = ROOT / "generated"

TEMPLATES = {
    "contract_mismatch": '''from tracebridge.demo_app import frontend_signup_payload, make_connection, signup


def test_signup_request_matches_contract():
    connection = make_connection(apply_v12=True)
    try:
        assert signup(frontend_signup_payload(), connection) == 201
    finally:
        connection.close()
''',
    "migration_missing": '''import os
from tracebridge.demo_app import make_connection, signup


def test_signup_after_schema_is_ready():
    apply_v12 = os.getenv("TRACEBRIDGE_CANDIDATE_FIX") == "1"
    connection = make_connection(apply_v12=apply_v12)
    try:
        payload = {"userId": "demo-43", "name": "Demo User", "phone": "010-0000-0000"}
        assert signup(payload, connection) == 201
    finally:
        connection.close()
''',
}


def generate_test(diagnosis_type: str) -> Path:
    if diagnosis_type not in TEMPLATES:
        raise ValueError("No safe test template for this diagnosis")
    GENERATED.mkdir(exist_ok=True)
    path = GENERATED / f"test_{diagnosis_type}.py"
    path.write_text(TEMPLATES[diagnosis_type], encoding="utf-8")
    return path


def _run(path: Path, candidate_fix: bool) -> dict:
    if path.parent.resolve() != GENERATED.resolve() or path.name not in {f"test_{name}.py" for name in TEMPLATES}:
        raise ValueError("Only generated demo tests can run")
    env = os.environ.copy()
    env["TRACEBRIDGE_CANDIDATE_FIX"] = "1" if candidate_fix else "0"
    env["PYTHONPATH"] = str(ROOT)
    try:
        completed = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(path)],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
        return {"exit_code": completed.returncode, "output": (completed.stdout + completed.stderr)[-2000:]}
    except subprocess.TimeoutExpired:
        return {"exit_code": -1, "output": "Test timed out after 20 seconds"}


def demonstrate_red_green(diagnosis_type: str) -> dict:
    path = generate_test(diagnosis_type)
    before = _run(path, candidate_fix=False)
    after = _run(path, candidate_fix=True)
    return {
        "test_path": str(path),
        "test_source": path.read_text(encoding="utf-8"),
        "before": before,
        "after_candidate_fix": after,
        "verified_in_demo": before["exit_code"] != 0 and after["exit_code"] == 0,
        "note": "This verifies only the disposable sample app/database, not a production fix.",
    }
