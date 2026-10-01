"""Execute a fresh fixed-example demo; output evidence, then remove its workspace."""
import json
import os
from pathlib import Path
import shutil
from uuid import uuid4

import httpx


def run():
    base = (Path(__file__).resolve().parents[1] / "output/reviewer-preview").resolve()
    base.mkdir(parents=True, exist_ok=True)
    directory = base / ("demo-" + uuid4().hex)
    directory.mkdir()
    try:
        registry = directory / "registry"
        os.environ["TRACEBRIDGE_PROJECT_REGISTRY"] = str(registry)
        from tracebridge.reviewer_demo import create_demo, start_demo, DemoPatch
        from tracebridge.report_agent import investigate_submission
        from tracebridge.report_contract import ReportContext
        from tracebridge.project_repair import prepare_project_change, apply_project_change
        from tracebridge.project_repair_ui import candidate_diff
        from tracebridge.project_recovery import verify_project_recovery
        profile, url = create_demo("review-browser", parent=directory, registry=registry)
        process = None
        try:
            process = start_demo(profile, url, directory / "runtime")
            before = httpx.post(url + "/quotas", json={"count": 5}, timeout=3)
            observed = before.json()
            db = directory / "incidents.sqlite3"
            source = investigate_submission("5개까지 신청 가능하지만 5개 신청이 거절돼요. QUOTA_BOUNDARY requestId=" + observed["request_id"],
                context=ReportContext(occurred_at=observed["occurred_at"]), project_profile=profile, db_path=db)
            candidate = prepare_project_change(source, profile, db_path=db, proposer=DemoPatch())
            job = candidate["job"]
            if not job["candidate_fix_verified"]:
                raise RuntimeError("Example candidate did not verify: " + job["status"] + " / " + str(job.get("limitations")))
            diff = candidate_diff(job)
            applied = apply_project_change(profile.project_id, job["work_id"], profile, db_path=db, expected_diff_sha256=job["diff"]["sha256"])
            if applied["persistence"]["status"] not in {"SAVED", "ALREADY_SAVED"}:
                raise RuntimeError("Application receipt was not saved")
            recovery = verify_project_recovery(profile, job["work_id"], db_path=db)
            after = httpx.post(url + "/quotas", json={"count": 5}, timeout=3)
            return {"mode": "DEMO_FIXED_PATCH", "external_model_calls": 0, "scope": "서버의 새 예제 사본에서 실행",
                "before": {"http_status": before.status_code, "accepted": observed["accepted"], "request_id": observed["request_id"]},
                "correlation": source["correlation"], "checks": [{"phase": row["phase"], "status": row["status"], "exit_code": row["exit_code"]} for row in job["checks"]],
                "diff": diff, "original_applied": True, "after": {"http_status": after.status_code, "accepted": after.json()["accepted"]},
                "recovery": recovery["verification"]["status"], "incident_state": recovery["verification"]["incident_state"],
                "snapshot": applied["applied_snapshot_sha256"]}
        finally:
            if process is not None:
                process.terminate()
                process.wait(timeout=10)
    finally:
        assert directory.parent == base and directory.name.startswith("demo-")
        shutil.rmtree(directory)


if __name__ == "__main__":
    print(json.dumps(run(), ensure_ascii=False))
