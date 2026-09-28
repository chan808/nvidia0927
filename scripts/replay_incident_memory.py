"""Offline synthetic intake -> reconnect -> review -> repeat/different-cause replay."""

from copy import deepcopy
import argparse
import json
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tracebridge.incident_memory import IncidentStore
from tracebridge.report_agent import follow_up_submission, investigate_submission
from tracebridge.report_contract import ReportContext


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "tests/fixtures/agolive_repo"


def replay(directory: Path) -> dict:
    with patch.dict(os.environ):
        for name in ("TRACEBRIDGE_EVENTS_FILE", "TRACEBRIDGE_LOG_FILE", "TRACEBRIDGE_LOG_SERVICE", "TRACEBRIDGE_LOG_ENVIRONMENT", "TRACEBRIDGE_LOG_TIMEZONE", "TRACEBRIDGE_DEPLOYED_SHA"):
            os.environ.pop(name, None)
        return _replay(directory)


def _replay(directory: Path) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    db, log = directory / "incidents.sqlite3", directory / "current.log"
    with IncidentStore(db) as store:
        if store.list_incidents("agolive"):
            raise ValueError("Replay requires a directory with no existing agolive incidents")
    records = [json.loads(line) for line in (ROOT / "examples/scoped_agolive.log").read_text(encoding="utf-8").splitlines()]
    guidance = deepcopy(records[0])
    guidance["trace"]["trace_id"] = "memory-first"
    context = ReportContext(environment="dev", service="backend", occurred_at=guidance["trace"]["occurred_at"])
    options = {"repo": REPO, "log_file": log, "db_path": db}

    def write(*items):
        log.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in items), encoding="utf-8")

    write(guidance)
    first = investigate_submission("회원가입 500 requestId=memory-first", context=context, **options)
    with IncidentStore(db) as store:
        restored = store.resume_result("agolive", first["incident_id"])
    follow = follow_up_submission(restored, "같은 회원가입 화면입니다. 다시 확인해 주세요.", **options)
    with IncidentStore(db) as store:
        duplicate = store.save_run(follow)
        card = store.review_card("agolive", follow["run_id"], "approve", reviewer="synthetic-reviewer", note="관측 안내 검토; 원인·수정 검증 아님")
    with IncidentStore(db) as store:
        incident = store.get_incident("agolive", first["incident_id"])
        assert store.get_run("agolive", first["run_id"])["observed_status"] == 422
        assert len(incident["runs"]) == 2 and follow["incident_id"] == first["incident_id"]
        assert duplicate == "ALREADY_SAVED" and card["verification"]["fix_verified"] is False

    repeat_record = deepcopy(guidance)
    repeat_record["trace"]["trace_id"] = "memory-repeat"
    write(repeat_record)
    repeated = investigate_submission("회원가입 500 requestId=memory-repeat", context=context, **options)
    different_record = deepcopy(records[2])
    different_record["trace"].update(trace_id="memory-different", path="/api/users", operation="회원가입", occurred_at=context.occurred_at)
    write(different_record)
    different = investigate_submission("회원가입 실패 requestId=memory-different", context=context, **options)

    conflict_a, conflict_b = deepcopy(guidance), deepcopy(different_record)
    conflict_a["trace"]["trace_id"] = conflict_b["trace"]["trace_id"] = "memory-conflict"
    write(conflict_a, conflict_b)
    conflicting = investigate_submission("회원가입 /api/users requestId=memory-conflict", context=context, **options)
    assert repeated["memory_search"]["hit_count"] == different["memory_search"]["hit_count"] == 1
    assert repeated["route"] == "GUIDANCE" and repeated["observed_status"] == 422
    assert repeated["memory_search"]["rechecks"][0]["status"] == "CURRENT_GUIDANCE_OBSERVED"
    assert different["route"] == "INVESTIGATE" and different["observed_status"] == 500
    assert different["diagnosis_type"] == "backend_exception_unconfirmed"
    assert different["memory_search"]["rechecks"][0]["status"] == "REJECTED"
    assert conflicting["route"] == "REQUEST_CONTEXT"
    assert conflicting["memory_search"]["rechecks"][0]["status"] == "NOT_REVALIDATED"
    for result in (first, follow, repeated, different, conflicting):
        assert result["persistence"]["status"] == "SAVED"
        assert result["model_calls"] == 0 and not result["cause_confirmed"] and not result["fix_verified"]
        assert not result["hypotheses"]
        assert all(item["run_id"] == result["run_id"] for item in result["evidence"])

    def outcome(result):
        return {key: result.get(key) for key in ("incident_id", "run_id", "route", "run_status", "diagnosis_type", "observed_status", "model_calls", "cause_confirmed", "fix_verified", "persistence", "memory_search")}

    output = {
        "source": "synthetic, offline; not real-incident validation", "database": str(db),
        "external_model_calls": 0, "reconnected_run_count": len(incident["runs"]), "duplicate_save": duplicate,
        "first_run_id": first["run_id"], "follow_up_run_id": follow["run_id"], "follow_up_revision": follow["revision"],
        "repeated": outcome(repeated), "different_current_cause": outcome(different), "conflicting": outcome(conflicting),
    }
    (directory / "replay.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def main() -> None:
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "output/validation" / ("memory-replay-" + uuid4().hex[:8]))
    args = parser.parse_args()
    print(json.dumps(replay(args.directory.resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
