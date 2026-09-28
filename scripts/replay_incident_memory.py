"""Offline synthetic intake -> reconnect -> review -> repeat/different-cause replay."""

from copy import deepcopy
import argparse
import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

from tracebridge.incident_memory import IncidentStore, export_manual, persist_result, recheck_memory, search_memory
from tracebridge.report_agent import follow_up_submission, investigate_submission
from tracebridge.report_contract import ReportContext, empty_result


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
    # Positive guidance needs explicit input/caller/contract provenance under R2.
    # This C-local source double preserves the original expected route and leaves
    # the shared examples unchanged. The caller file is inspected, never executed.
    caller_path = directory / "synthetic-caller.py"
    caller_bytes = ("\"\"\"C offline source double; not real project code.\"\"\"\n"
        "def build_request(form):\n"
        "    return {field: form[field] for field in ('userId', 'name') if field in form}\n").encode("utf-8")
    caller_path.write_bytes(caller_bytes)
    version = hashlib.sha256(caller_bytes).hexdigest()
    guidance["trace"].update(version=version, code_version=version)
    guidance["contract_context"] = {"provenance": {"kind": "synthetic_c_replay", "source": "synthetic:C/replay-contract", "code_version": version},
        "versions": {key: version for key in ("runtime", "code", "contract", "dto", "caller")}}
    guidance["caller"] = {"source": str(caller_path.resolve()) + "#build_request", "source_kind": "caller_code",
        "source_sha256": version, "source_verified": True, "version": version, "method": "POST", "path": "/api/users",
        "field_mapping": {"userId": "userId", "name": "name"}, "required_inputs": {"userId": "userId", "name": "name"},
        "input_fields": ["userId"], "input_types": {"userId": "string"}}
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
    if repeated["route"] != "GUIDANCE" or repeated["observed_status"] != 422:
        # Preserve a concrete integration failure while A/B own the new routing.
        # Do not quietly weaken the original guidance expectation.
        failure = {"status": "FAILED", "source": "synthetic offline intake replay; not real-incident validation",
            "database": str(db), "external_model_calls": 0,
            "expected": {"route": "GUIDANCE", "observed_status": 422},
            "actual": {key: repeated.get(key) for key in ("run_id", "route", "run_status", "observed_status", "diagnosis_type", "responsibility", "contract_analysis", "log_scope", "memory_search")}}
        (directory / "replay-failure.json").write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding="utf-8")
    assert repeated["memory_search"]["hit_count"] == different["memory_search"]["hit_count"] == 1
    assert repeated["route"] == "GUIDANCE" and repeated["observed_status"] == 422
    # The independently observed guidance remains, but an unknown source/current
    # runtime version cannot validate the applicability of a historical card.
    assert repeated["memory_search"]["rechecks"][0]["status"] == "NOT_REVALIDATED"
    assert repeated["memory_search"]["rechecks"][0]["current_guidance_observed"]
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
        "caller_source_double": {"path": str(caller_path), "sha256": version, "executed": False},
        "external_model_calls": 0, "reconnected_run_count": len(incident["runs"]), "duplicate_save": duplicate,
        "first_run_id": first["run_id"], "follow_up_run_id": follow["run_id"], "follow_up_revision": follow["revision"],
        "repeated": outcome(repeated), "different_current_cause": outcome(different), "conflicting": outcome(conflicting),
        "card_cases": replay_card_cases(directory / "card-cases"),
    }
    manual = export_manual("agolive", db_path=db)
    manual_path = directory / "incident-manual.md"
    manual_path.write_text(manual["markdown"], encoding="utf-8")
    output["manual"] = {"path": str(manual_path), "card_count": manual["card_count"], "card_ids": manual["card_ids"]}
    (directory / "replay.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def replay_card_cases(directory: Path) -> dict:
    """Memory-only synthetic replay, independent of the primary investigation loop."""
    directory.mkdir(parents=True, exist_ok=True)
    db, project, sha = directory / "c-cases.sqlite3", "parallel-c-replay", "a" * 40

    def current(*, version=sha, exception="ValidationException", incident=None, revision=1):
        result = empty_result(project, incident_id=incident)
        result.update(revision=revision, route="GUIDANCE", run_status="COMPLETED", correlation="EXACT_ID",
            observed_status=422, reported_status=500, trace_id="c-synthetic-request", diagnosis_type="expected_validation",
            summary="합성 계약 관측이다. 현재 입력과 호출자 생성 위치를 다시 확인한다.",
            next_action="현재 계약과 로그를 다시 확인한다.", case_kind="SYNTHETIC_OFFLINE",
            executed_at="2026-09-28T01:00:00+00:00", scope={"service": "orders", "environment": "dev", "operation": "주문", "method": "POST", "path": "/api/orders"},
            version_provenance={"runtime": {"sha": version, "status": "OBSERVED" if version else "NOT_OBSERVED", "source": "synthetic:runtime"},
                "local_head": {"sha": version, "source": "synthetic:local"}, "comparison": "MATCH" if version else "UNKNOWN"},
            log_scope={"connected": True, "aggregate": {"complete": True, "conflicts": []}})
        log = {"id": "L1", "kind": "log", "source": "synthetic:c-case", "content": exception,
            "scope_status": "VERIFIED", "correlated": True, "run_id": result["run_id"]}
        result["observations"], result["evidence"] = [deepcopy(log)], [log]
        result["steps"] = [{"tool": "find_logs", "phase": "correlation", "status": "success", "evidence_ids": ["L1"]},
            {"tool": "get_version", "phase": "current", "status": "success", "evidence_ids": []}]
        result["session"] = {"text": "주문 requestId=c-synthetic-request", "answers": [], "answer_count": revision - 1,
            "received_at": result["executed_at"], "context": deepcopy(result["scope"]), "source_binding": "f" * 64}
        return result

    with IncidentStore(db) as store:
        if store.list_incidents(project):
            raise ValueError("Card replay requires a fresh C-specific DB")
        source = current()
        store.save_run(source)
        store.review_card(project, source["run_id"], "approve", reviewer="c-synthetic-owner")

    cases = {}
    repeat = current()
    different = current(exception="IllegalStateException")
    changed = current(version="b" * 40)
    unknown = current(version=None)
    conflict = current()
    conflict["log_scope"]["aggregate"]["conflicts"] = [{"field": "response_status"}]
    for name, result in (("repeat", repeat), ("different_cause_same_http", different), ("changed_version", changed), ("unobserved_version", unknown), ("conflict", conflict)):
        result["memory_search"] = recheck_memory(search_memory(project, "/api/orders", db_path=db, exclude_incident_id=result["incident_id"]), result)
        assert result["memory_search"]["hit_count"] == 1
        cases[name] = {"run_id": result["run_id"], "memory_search": result["memory_search"]}
    assert cases["repeat"]["memory_search"]["rechecks"][0]["status"] == "CURRENT_GUIDANCE_OBSERVED"
    assert cases["different_cause_same_http"]["memory_search"]["rechecks"][0]["status"] == "REJECTED"
    for name in ("changed_version", "unobserved_version", "conflict"):
        assert cases[name]["memory_search"]["rechecks"][0]["status"] == "NOT_REVALIDATED"
    persist_result(repeat, db)

    disabled = current(incident=source["incident_id"], revision=2)
    disabled["memory_search"] = recheck_memory(search_memory(project, "/api/orders", db_path=db, enabled=False), disabled)
    persist_result(disabled, db)
    assert disabled["persistence"]["status"] == "SAVED" and disabled["memory_search"]["cards"] == []
    with IncidentStore(db) as store:
        edited = store.review_card(project, source["run_id"], "edit", reviewer="c-synthetic-owner", changes={
            "check_sequence": ["현재 요청의 로그 범위·충돌·실행 버전을 확인한다.", "현재 계약과 호출자 생성 위치를 확인한다."],
            "invalid_conditions": [{"when": {"environment": "prod"}, "reason": "운영 적용은 검증하지 않았다."}]})
        store.review_card(project, source["run_id"], "reject", reviewer="c-synthetic-owner", note="폐기 이력을 확인하는 합성 검사")
        rejected_count = store.search(project, "/api/orders")["hit_count"]
        assert rejected_count == 0 and store.export_manual(project)["card_count"] == 0
        restored_card = store.review_card(project, source["run_id"], "approve", reviewer="c-synthetic-owner", note="합성 검사의 정정 내용을 재검토")
        duplicate = store.save_run(source)
    with IncidentStore(db) as store:
        resumed = store.resume_result(project, source["incident_id"])
        assert resumed["run_id"] == disabled["run_id"] and len(resumed["history"]) == 1
        assert store.get_card(project, source["run_id"])["review"]["history"] == restored_card["review"]["history"]
    manual = export_manual(project, db_path=db)
    manual_path = directory / "incident-manual.md"
    manual_path.write_text(manual["markdown"], encoding="utf-8")
    output = {"source": "synthetic offline memory API cases; version labels are doubles", "database": str(db),
        "external_model_calls": 0, "cases": cases, "memory_disabled": {"status": disabled["memory_search"]["status"], "persistence": disabled["persistence"]},
        "discard_search_count": rejected_count, "review_history_count": len(restored_card["review"]["history"]),
        "edited_revision": edited["review"]["revision"], "resumed_revision": resumed["revision"], "duplicate_save": duplicate,
        "manual": {"path": str(manual_path), "card_count": manual["card_count"], "card_ids": manual["card_ids"]}}
    (directory / "replay.json").write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def main() -> None:
    import sys
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=ROOT / "output/validation" / ("memory-replay-" + uuid4().hex[:8]))
    parser.add_argument("--cards-only", action="store_true", help="Run C memory/conditions/manual doubles without the primary investigation loop")
    args = parser.parse_args()
    run_replay = replay_card_cases if args.cards_only else replay
    print(json.dumps(run_replay(args.directory.resolve()), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
