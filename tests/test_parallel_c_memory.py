"""Session C: explicit temporary DBs, synthetic observations and no external calls."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
from uuid import uuid4

import pytest

from tracebridge import incident_memory as memory
from tracebridge.incident_memory import IncidentStore, RunConflict, export_manual, persist_result, recheck_memory, search_memory
from tracebridge.report_contract import empty_result
from scripts.replay_incident_memory import replay, replay_card_cases


ROOT = Path(__file__).resolve().parents[1]
PROJECT = "parallel-c-project"
SHA = "a" * 40
MANUAL_FIELDS = {"applicability", "observed_features", "check_sequence", "disproof_conditions", "invalid_conditions",
    "limitations", "source", "last_checked", "verification_results", "field_sources", "card_format",
    "contract_analysis", "responsibility", "symptom_status", "product_status", "claim_verification"}


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    monkeypatch.setenv("TRACEBRIDGE_DB_PATH", str(tmp_path / "c-default.sqlite3"))


def record(*, project=PROJECT, incident=None, revision=1, exception="ValidationException", sha=SHA,
        route="GUIDANCE", observed_status=422):
    run = empty_result(project, incident_id=incident)
    run.update(revision=revision, route=route, run_status="COMPLETED", correlation="EXACT_ID", trace_id="c-current-request",
        diagnosis_type="expected_validation" if route == "GUIDANCE" else "backend_exception_unconfirmed",
        observed_status=observed_status, reported_status=500, summary="입력 계약을 현재 로그에서 확인했다.",
        next_action="현재 계약과 요청 생성 위치를 다시 확인한다.", next_steps=["현재 로그 범위를 확인한다."],
        executed_at="2026-09-28T01:00:00+00:00", case_kind="SYNTHETIC_OFFLINE",
        scope={"service": "orders", "environment": "dev", "operation": "주문", "method": "POST", "path": "/api/orders"},
        log_scope={"connected": True, "aggregate": {"complete": True, "conflicts": []}},
        version_provenance={"local_head": {"sha": sha, "source": "synthetic:local", "source_tree_dirty": False},
            "configured": {"sha": sha, "source": "manual_configuration", "deployment_observed": False},
            "runtime": {"status": "OBSERVED" if sha else "NOT_OBSERVED", "sha": sha, "source": "synthetic:runtime"},
            "comparison": "MATCH" if sha else "UNKNOWN"})
    rule = {"id": "R1", "kind": "rule_observation", "source": "synthetic:c-rule", "content": f"실제 응답 HTTP {observed_status}", "run_id": run["run_id"]}
    log = {"id": "L1", "kind": "log", "source": "synthetic:c-log", "content": exception, "run_id": run["run_id"], "scope_status": "VERIFIED", "correlated": True}
    run["observations"] = [deepcopy(rule), deepcopy(log)]
    run["evidence"] = [rule, log]
    run["steps"] = [{"tool": "find_logs", "phase": "correlation", "status": "success", "evidence_ids": ["L1"]},
        {"tool": "get_version", "phase": "current", "status": "success", "evidence_ids": []}]
    run["session"] = {"text": "주문 requestId=c-current-request", "answers": [], "received_at": run["executed_at"],
        "context": deepcopy(run["scope"]), "source_binding": "f" * 64, "answer_count": 0}
    return run


def approved(db, run=None):
    run = run or record()
    with IncidentStore(db) as store:
        store.save_run(run)
        store.review_card(run["project_id"], run["run_id"], "approve", reviewer="c-owner")
    return run


def rechecked(db, current):
    search = search_memory(current["project_id"], "/api/orders", db_path=db, exclude_incident_id=current["incident_id"])
    assert search["status"] == "OK" and search["hit_count"] == 1
    return recheck_memory(search, current)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def candidate_result(source, stored_source):
    """Persisted change-job double; does not execute project code or alter a baseline."""
    work_id, result_id = uuid4().hex, uuid4().hex
    checks = []
    for phase, status, exit_code, command in (("before", "FAILED", 1, "signup-contract"), ("after", "PASSED", 0, "signup-contract"), ("regression", "PASSED", 0, "signup-regression")):
        checks.append({"phase": phase, "status": status, "exit_code": exit_code, "command_id": command,
            "argv": ["synthetic-python", "checks.py", command], "input": {"sha256": "b" * 64},
            "execution_settings_sha256": "c" * 64, "stdout_ref": f"synthetic/{phase}.txt", "stdout_sha256": "d" * 64,
            "result": {"failure_signature": "seed-user_id-vs-userId"} if phase == "before" else {"status": "PASSED"}})
    job = {"record_format": "change_job_v1", "work_id": work_id, "project_id": source["project_id"], "incident_id": source["incident_id"],
        "source_run_id": source["run_id"], "result_run_id": result_id, "source_record_sha256": digest(stored_source),
        "source_revision": source["revision"], "status": "CHANGE_PREPARED", "review_status": "WAITING_REVIEW", "case_kind": "SYNTHETIC_OFFLINE",
        "candidate_fix_verified": True, "original_applied": False, "deployment_status": "NOT_ATTEMPTED", "service_recovery": "NOT_VERIFIED",
        "original_unchanged": True, "identical_related_check": True, "diff": {"sha256": "e" * 64, "ref": "synthetic/change.diff"},
        "checks": checks, "artifact_ref": "synthetic/candidate", "limitations": ["자료는 C 전용 검사 double이다."]}
    run = deepcopy(source)
    run.update(run_id=result_id, revision=source["revision"] + 1, observations=[], evidence=[], hypotheses=[], steps=[],
        executed_at="2026-09-28T01:10:00+00:00", cause_confirmed=False, fix_applied=False, fix_verified=False)
    run["change"] = {key: deepcopy(job[key]) for key in ("work_id", "source_run_id", "status", "review_status", "candidate_fix_verified", "original_applied", "deployment_status", "service_recovery", "artifact_ref", "diff")}
    run["change"]["verification_scope"] = "SYNTHETIC_COPY_ONLY"
    return {"job": job, "run": run}


def cli(module, *args, json_output=True):
    completed = subprocess.run([sys.executable, "-m", module, *map(str, args)], cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", timeout=20)
    assert completed.returncode == 0, completed.stderr
    return json.loads(completed.stdout) if json_output else completed.stdout


def test_repeat_uses_order_with_current_refs_and_keeps_source_last_check(tmp_path):
    db = tmp_path / "repeat.sqlite3"
    past, current = approved(db), record()
    current["executed_at"] = "2026-09-29T01:00:00+00:00"
    checked = rechecked(db, current)
    match = checked["rechecks"][0]
    assert match["status"] == "CURRENT_GUIDANCE_OBSERVED" and match["applicability_status"] == "MATCH"
    assert match["current_guidance_observed"] and not match["usable_as_current_evidence"]
    assert all(ref["run_id"] == current["run_id"] for ref in match["current_evidence_refs"])
    assert all(ref.startswith(f"historical:{past['run_id']}:") for ref in checked["cards"][0]["evidence_refs"])
    assert [item["tool"] for item in checked["cards"][0]["check_sequence"][:2]] == ["find_logs", "get_version"]
    with IncidentStore(db) as store:
        assert store.get_card(PROJECT, past["run_id"])["last_checked"]["at"] == past["executed_at"]


def test_same_path_and_500_with_other_exception_rejects_cause_and_separates_ids(tmp_path):
    db = tmp_path / "cause.sqlite3"
    past = record(exception="SQLiteException", route="INVESTIGATE", observed_status=500)
    past["hypotheses"] = [{"cause": "스키마 후보", "supporting_evidence_ids": ["L1"], "status": "LOG_CANDIDATE"}]
    approved(db, past)
    current = record(exception="IllegalStateException", route="INVESTIGATE", observed_status=500)
    checked = rechecked(db, current)
    assert checked["rechecks"][0]["status"] == "REJECTED"
    assert checked["cards"][0]["hypotheses"][0]["supporting_evidence_refs"] == [f"historical:{past['run_id']}:L1"]
    assert {item["run_id"] for item in checked["current_recheck"]["evidence_refs"]} == {current["run_id"]}
    assert current["hypotheses"] == [] and not current["cause_confirmed"]


@pytest.mark.parametrize("past_sha,current_sha", [(SHA, "b" * 40), (SHA, None), (None, SHA), (None, None)])
def test_version_change_or_missing_version_is_clue_only(tmp_path, past_sha, current_sha):
    db = tmp_path / "versions.sqlite3"
    approved(db, record(sha=past_sha))
    checked = rechecked(db, record(sha=current_sha))["rechecks"][0]
    assert checked["status"] == "NOT_REVALIDATED" and checked["applicability_status"] != "MATCH"
    assert checked["current_guidance_observed"] and not checked["usable_as_current_evidence"]
    assert any("버전" in reason or "sha" in reason for reason in checked["reasons"])


def test_short_and_long_observed_sha_are_compatible_but_configured_sha_is_not_observation(tmp_path):
    db = tmp_path / "sha.sqlite3"
    approved(db)
    current = record(sha=SHA[:7])
    assert rechecked(db, current)["rechecks"][0]["applicability_status"] == "MATCH"
    current["version_provenance"]["runtime"] = {"status": "NOT_OBSERVED", "sha": SHA}
    assert rechecked(db, current)["rechecks"][0]["status"] == "NOT_REVALIDATED"


def test_different_cause_remains_rejected_when_local_runtime_version_mismatches(tmp_path):
    db = tmp_path / "reject.sqlite3"
    approved(db, record(exception="SQLiteException", route="INVESTIGATE", observed_status=500))
    current = record(exception="IllegalStateException", route="INVESTIGATE", observed_status=500)
    current["version_provenance"]["comparison"] = "MISMATCH"
    checked = rechecked(db, current)["rechecks"][0]
    assert checked["status"] == "REJECTED" and checked["applicability_status"] == "MISMATCH"


@pytest.mark.parametrize("field,value", [("service", "payments"), ("environment", "prod"), ("operation", "환불"), ("method", "PUT"), ("path", "/api/refunds"), ("environment", None), ("operation", None), ("method", None)])
def test_different_or_unobserved_current_scope_is_not_revalidated(tmp_path, field, value):
    db = tmp_path / "scope.sqlite3"
    approved(db)
    current = record()
    current["scope"][field] = value
    checked = rechecked(db, current)["rechecks"][0]
    assert checked["status"] == "NOT_REVALIDATED" and checked["applicability_status"] != "MATCH"


@pytest.mark.parametrize("complete,conflicts", [(False, []), (None, []), (True, [{"field": "response_status"}])])
def test_incomplete_unknown_or_conflicting_observations_cannot_validate_old_card(tmp_path, complete, conflicts):
    db = tmp_path / "conflicts.sqlite3"
    approved(db)
    current = record()
    current["log_scope"]["aggregate"] = {"complete": complete, "conflicts": conflicts}
    checked = rechecked(db, current)["rechecks"][0]
    assert checked["status"] == "NOT_REVALIDATED" and not checked["current_guidance_observed"]


def test_source_conflict_missing_scope_and_interrupted_current_remain_clues(tmp_path):
    db = tmp_path / "source.sqlite3"
    past = record()
    past["log_scope"]["aggregate"]["conflicts"] = [{"field": "response_status"}]
    past["scope"]["environment"] = None
    approved(db, past)
    current = record()
    current["run_status"] = "BUDGET_EXHAUSTED"
    checked = rechecked(db, current)["rechecks"][0]
    assert checked["status"] == "NOT_REVALIDATED"
    assert checked["applicability_status"] == "MISMATCH"
    assert any("중단" in item for item in checked["reasons"])


def test_review_correction_invalid_conditions_and_discard_preserve_history(tmp_path):
    db = tmp_path / "review.sqlite3"
    past = approved(db)
    with IncidentStore(db) as store:
        original = store.get_run(PROJECT, past["run_id"])
        before = store.get_card(PROJECT, past["run_id"])
        edited = store.review_card(PROJECT, past["run_id"], "edit", reviewer="c-owner", changes={
            "finding": "correctedword", "check_sequence": ["currentcheckword 계약 출처를 확인한다."],
            "disproof_conditions": ["현재 예외가 다르면 적용하지 않는다."],
            "invalid_conditions": [{"when": {"environment": "dev"}, "reason": "이 버전의 dev 조건은 부적합하다."}],
            "limitations": ["등록된 범위에서만 조사 단서로 사용한다."]})
        assert edited["review"]["history"][-1]["before"]["check_sequence"] == before["check_sequence"]
        assert store.search(PROJECT, "currentcheckword")["cards"][0]["field_sources"]["check_sequence"]["kind"] == "REVIEW_EDIT"
        assert store.get_run(PROJECT, past["run_id"]) == original
        assert edited["verification"] == before["verification"]
        assert store.export_manual(PROJECT)["card_count"] == 1
    checked = rechecked(db, record())["rechecks"][0]
    assert checked["status"] == "NOT_REVALIDATED" and any("부적합 조건" in reason for reason in checked["reasons"])
    with IncidentStore(db) as store:
        store.review_card(PROJECT, past["run_id"], "reject", reviewer="c-owner", note="폐기 근거 보존")
        assert store.search(PROJECT, "/api/orders")["cards"] == []
        assert store.export_manual(PROJECT)["card_count"] == 0
    with IncidentStore(db) as store:
        card = store.get_card(PROJECT, past["run_id"])
        assert card["review"]["status"] == "REJECTED" and len(card["invalid_conditions"]) == 1
        assert [item["action"] for item in card["review"]["history"]] == ["approve", "edit", "reject"]
        assert store.save_run(past) == "ALREADY_SAVED" and store.get_card(PROJECT, past["run_id"])["review"] == card["review"]
        restored = store.review_card(PROJECT, past["run_id"], "approve", reviewer="c-second-reviewer")
        assert restored["review"]["history"][-1]["before_status"] == "REJECTED"
        assert store.export_manual(PROJECT)["card_count"] == 1


@pytest.mark.parametrize("changes", [
    {"fix_verified": True}, {"verification_results": {}}, {"source_run_id": "other"},
    {"applicability": {"project_id": "other"}}, {"applicability": {"runtime_sha": "version-string"}},
    {"check_sequence": [{"step": "invented", "source_run_id": "other"}]},
    {"invalid_conditions": [{"when": {}, "reason": "unbounded"}]}, {"limitations": "not-a-list"},
])
def test_review_cannot_change_factual_state_scope_identity_or_invent_sources(tmp_path, changes):
    db = tmp_path / "immutable.sqlite3"
    past = approved(db)
    with IncidentStore(db) as store:
        before = store.get_card(PROJECT, past["run_id"])
        with pytest.raises(ValueError):
            store.review_card(PROJECT, past["run_id"], "edit", reviewer="c-owner", changes=changes)
        assert store.get_card(PROJECT, past["run_id"]) == before


def test_reviewed_version_constraint_does_not_replace_source_observation(tmp_path):
    db = tmp_path / "review-version.sqlite3"
    past = approved(db)
    with IncidentStore(db) as store:
        card = store.review_card(PROJECT, past["run_id"], "edit", reviewer="c-owner", changes={"applicability": {"runtime_sha": "b" * 40}})
        assert card["version_provenance"]["runtime"]["sha"] == SHA
    checked = rechecked(db, record(sha="b" * 40))["rechecks"][0]
    assert checked["status"] == "NOT_REVALIDATED" and checked["applicability_status"] == "MISMATCH"


@pytest.mark.parametrize("contract_version", ["release-v2", None])
def test_contract_dto_caller_versions_are_preserved_and_rechecked_separately(tmp_path, contract_version):
    db = tmp_path / "contract-versions.sqlite3"
    past = record()
    past["contract_analysis"] = {"status": "COMPARED", "complete": True, "provenance": {"source": "synthetic:registered-contract"},
        "versions": {"status": "MATCHED", **{name: "release-v1" for name in ("runtime", "code", "contract", "dto", "caller")}}}
    approved(db, past)
    current = record()
    current["contract_analysis"] = deepcopy(past["contract_analysis"])
    current["contract_analysis"]["versions"]["contract"] = contract_version
    checked = rechecked(db, current)["rechecks"][0]
    assert checked["status"] == "NOT_REVALIDATED" and checked["applicability_status"] != "MATCH"
    with IncidentStore(db) as store:
        saved = store.get_run(PROJECT, past["run_id"])
        assert saved["contract_analysis"]["versions"]["contract"] == "release-v1"
        assert "synthetic:registered" in store.export_manual(PROJECT)["markdown"]


def test_claim_symptom_product_and_responsibility_remain_distinct_in_manual(tmp_path):
    db = tmp_path / "claim.sqlite3"
    past = record(route="INVESTIGATE")
    past.update(claim_status="CONFIRMED_MISMATCH", symptom_status="REQUEST_REJECTED", product_status="CALLER_DEFECT_OBSERVED",
        responsibility={"status": "CALLER_DEFECT", "evidence_sources": ["synthetic:caller"], "reason": "Synthetic caller evidence"})
    approved(db, past)
    with IncidentStore(db) as store:
        card = store.get_card(PROJECT, past["run_id"])
        assert card["claim_verification"]["reported_status"] == 500 and card["claim_verification"]["observed_status"] == 422
        assert card["symptom_status"] == "REQUEST_REJECTED" and card["responsibility"]["status"] == "CALLER_DEFECT"
        assert not card["verification"]["cause_confirmed"]
    markdown = export_manual(PROJECT, db_path=db)["markdown"]
    assert "제보 HTTP: 500; 실제 HTTP: 422" in markdown and "CALLER\\_DEFECT" in markdown


def test_reviewed_free_text_disproof_requires_a_current_check(tmp_path):
    db = tmp_path / "disproof.sqlite3"
    past = approved(db)
    with IncidentStore(db) as store:
        store.review_card(PROJECT, past["run_id"], "edit", reviewer="c-owner", changes={"disproof_conditions": ["등록 계약의 필드 생성 동작이 다르면 기각한다."]})
    checked = rechecked(db, record())["rechecks"][0]
    assert checked["status"] == "NOT_REVALIDATED" and any("반증 문구" in item for item in checked["reasons"])


def test_candidate_success_has_persisted_checks_without_original_application_or_recovery(tmp_path):
    db = tmp_path / "candidate.sqlite3"
    source = record(route="WORK_CANDIDATE")
    with IncidentStore(db) as store:
        store.save_run(source)
        stored = store.get_run(PROJECT, source["run_id"])
        work = candidate_result(source, stored)
        assert store.save_change_result(work) == "SAVED"
        assert store.save_change_result(work) == "ALREADY_SAVED"
        card = store.review_card(PROJECT, work["run"]["run_id"], "approve", reviewer="c-owner")
        assert all(value is False for value in card["verification"].values())
        facts = card["verification_results"]
        assert facts["candidate_validation"]["status"] == "VERIFIED"
        assert [check["status"] for check in facts["candidate_validation"]["checks"]] == ["FAILED", "PASSED", "PASSED"]
        assert facts["original_application"]["status"] == "NOT_APPLIED"
        assert facts["service_recovery"]["status"] == "NOT_VERIFIED" and facts["cause_confirmation"]["status"] == "NOT_CONFIRMED"
        assert card["last_checked"]["source_run_id"] == source["run_id"]
        assert card["source"]["investigation_run_id"] == source["run_id"]
        assert store.get_run(PROJECT, source["run_id"]) == stored
        job = store.get_change(PROJECT, work["job"]["work_id"])
    with IncidentStore(db) as store:
        assert store.get_change(PROJECT, work["job"]["work_id"]) == job
        assert store.find_change(PROJECT, source["run_id"])["work_id"] == job["work_id"]
        assert len(store.get_incident(PROJECT, source["incident_id"])["runs"]) == 2
        markdown = store.export_manual(PROJECT)["markdown"]
        assert "VERIFIED" in markdown and "NOT\\_APPLIED" in markdown and "NOT\\_VERIFIED" in markdown
        assert work["job"]["work_id"] in markdown and "before" in markdown and "regression" in markdown


def test_unpersisted_candidate_claim_and_legacy_fix_flag_do_not_confirm_recovery(tmp_path):
    db = tmp_path / "unverified.sqlite3"
    run = record()
    run["fix_verified"] = True
    run["cause_confirmed"] = True
    run["change"] = {"candidate_fix_verified": True, "original_applied": False, "service_recovery": "NOT_VERIFIED"}
    past = approved(db, run)
    with IncidentStore(db) as store:
        facts = store.get_card(PROJECT, past["run_id"])["verification_results"]
        assert facts["candidate_validation"]["status"] == "REPORTED_UNVERIFIED"
        assert facts["cause_confirmation"]["status"] == "REPORTED_UNVERIFIED"
        assert facts["service_recovery"]["status"] == "NOT_VERIFIED" and facts["service_recovery"]["legacy_fix_verified"]


def test_search_disabled_never_opens_db_and_discards_any_cards(monkeypatch, tmp_path):
    def forbidden(*args, **kwargs):
        raise AssertionError("Disabled search must not open a DB")

    monkeypatch.setattr(memory, "IncidentStore", forbidden)
    path = tmp_path / "absent" / "not-a-db.json"
    disabled = search_memory(PROJECT, "sensitive query", db_path=path, enabled=False)
    assert set(disabled) == {"status", "strategy", "hit_count", "cards", "query_signals", "query_hash", "elapsed_ms"}
    assert disabled["status"] == "DISABLED" and disabled["hit_count"] == 0 and disabled["cards"] == []
    assert not path.parent.exists() and disabled["elapsed_ms"] >= 0
    checked = recheck_memory({**disabled, "cards": [{"finding": "must not be delivered"}]}, {})
    assert checked["cards"] == [] and checked["current_recheck"] == {} and checked["rechecks"] == []
    assert "must not be delivered" not in json.dumps(checked)


def test_disabled_search_still_saves_runs_resumes_and_retains_option_in_record(tmp_path):
    db = tmp_path / "disabled.sqlite3"
    first = approved(db)
    next_run = record(incident=first["incident_id"], revision=2)
    next_run["session"]["answer_count"] = 1
    next_run["memory_search"] = recheck_memory(search_memory(PROJECT, "/api/orders", db_path=db, enabled=False), next_run)
    assert persist_result(next_run, db)["persistence"]["status"] == "SAVED"
    with IncidentStore(db) as store:
        saved = store.get_run(PROJECT, next_run["run_id"])
        assert saved["memory_search"]["status"] == "DISABLED" and saved["memory_search"]["current_recheck"] == {}
        restored = store.resume_result(PROJECT, first["incident_id"])
        assert restored["revision"] == 2 and restored["history"][0]["run_id"] == first["run_id"]
        assert restored["session"]["answer_count"] == 1
        assert store.save_run(next_run) == "ALREADY_SAVED"
    assert search_memory(PROJECT, "/api/orders", db_path=db)["hit_count"] == 1


def test_search_failure_keeps_same_empty_shape_and_source_result_is_unchanged(tmp_path):
    query = "/api/orders"
    failed = search_memory(PROJECT, query, db_path=tmp_path / "bad-name.json")
    disabled = search_memory(PROJECT, query, db_path=tmp_path / "bad-name.json", enabled=False)
    assert failed["status"] == "FAILED" and failed["cards"] == []
    assert set(failed) - {"error_type"} == set(disabled)
    current = record()
    original = deepcopy(current)
    assert recheck_memory(failed, current)["rechecks"] == [] and current == original


def test_run_duplicates_revision_conflicts_and_reconnection_preserve_review_and_history(tmp_path):
    db = tmp_path / "history.sqlite3"
    first = approved(db)
    with IncidentStore(db) as store:
        card = store.get_card(PROJECT, first["run_id"])
        assert store.save_run(first) == "ALREADY_SAVED"
        same_id = deepcopy(first)
        same_id["private_input"] = "different discarded input"
        with pytest.raises(RunConflict):
            store.save_run(same_id)
        with pytest.raises(RunConflict):
            store.save_run(record(incident=first["incident_id"]))
        assert store.get_incident(PROJECT, first["incident_id"])["latest_run_id"] == first["run_id"]
        assert store.get_card(PROJECT, first["run_id"]) == card
        second = record(incident=first["incident_id"], revision=2)
        store.save_run(second)
        third = record(incident=first["incident_id"], revision=3)
        third["session"]["answer_count"] = 2
        store.save_run(third)
    with IncidentStore(db) as store:
        resumed = store.resume_result(PROJECT, first["incident_id"])
        assert resumed["run_id"] == third["run_id"] and [item["revision"] for item in resumed["history"]] == [1, 2]
        assert resumed["session"]["answer_count"] == 2
        assert store.get_evidence(PROJECT, first["run_id"], "L1")["run_id"] != store.get_evidence(PROJECT, third["run_id"], "L1")["run_id"]


def test_legacy_card_json_projects_additive_defaults_without_rewriting_source_rows(tmp_path):
    db = tmp_path / "legacy.sqlite3"
    past = approved(db)
    with IncidentStore(db) as store:
        raw = json.loads(store.connection.execute("SELECT card_json FROM cards WHERE card_id=?", (past["run_id"],)).fetchone()[0])
        for key in MANUAL_FIELDS:
            raw.pop(key, None)
        encoded = json.dumps(raw, ensure_ascii=False)
        store.connection.execute("UPDATE cards SET card_json=? WHERE card_id=?", (encoded, past["run_id"]))
        store.connection.execute("PRAGMA user_version=1")
        store.connection.commit()
        rows = tuple(store.connection.execute("SELECT content_hash,record_json FROM runs WHERE run_id=?", (past["run_id"],)).fetchone())
    with IncidentStore(db) as store:
        card = store.get_card(PROJECT, past["run_id"])
        assert MANUAL_FIELDS <= set(card) and card["review"]["status"] == "APPROVED"
        assert store.connection.execute("SELECT card_json FROM cards WHERE card_id=?", (past["run_id"],)).fetchone()[0] == encoded
        assert store.export_manual(PROJECT)["card_count"] == 1
        assert tuple(store.connection.execute("SELECT content_hash,record_json FROM runs WHERE run_id=?", (past["run_id"],)).fetchone()) == rows
        edited = store.review_card(PROJECT, past["run_id"], "edit", reviewer="c-owner", changes={"limitations": ["레거시 자료의 확인 범위를 유지한다."]})
        assert len(edited["review"]["history"]) == 2
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert store.save_run(past) == "ALREADY_SAVED"


def test_manual_selects_only_reviewed_project_cards_and_has_action_verification_sources(tmp_path):
    db = tmp_path / "manual.sqlite3"
    past = approved(db)
    approved(db, record(project="other-c-project"))
    pending = record()
    with IncidentStore(db) as store:
        store.save_run(pending)
        result = store.export_manual(PROJECT)
        assert result["card_ids"] == [past["run_id"]] and result["card_count"] == 1
        assert store.export_manual(PROJECT, card_ids=[pending["run_id"]])["card_count"] == 0
        with pytest.raises(ValueError):
            store.export_manual("other-c-project", card_ids=[past["run_id"]])
    manual = export_manual(PROJECT, db_path=db)
    markdown = manual["markdown"]
    for label in ("적용 조건과 버전", "확인 순서", "반증", "기록된 다음 조치", "원인 확인", "후보 사본 검증", "원본 적용", "회복 확인", "마지막 자료 확인"):
        assert label in markdown
    assert past["run_id"] in markdown and "synthetic:c" in markdown
    assert f"historical:{past['run_id']}:L1" in markdown
    assert "편집·실행·배포 권한을 부여하지 않는다" in markdown
    for line in markdown.splitlines():
        if line.startswith("- 기록된 다음 조치:") or any(line.startswith("- " + label + ":") for label in ("원인 확인", "후보 사본 검증", "원본 적용", "회복 확인")):
            assert "출처 실행" in line
    assert export_manual(PROJECT, db_path=db, card_ids=[])["card_count"] == 0
    with pytest.raises(FileNotFoundError):
        export_manual(PROJECT, db_path=tmp_path / "no-db.sqlite3")
    assert not (tmp_path / "no-db.sqlite3").exists()


def test_reviewed_untrusted_text_is_redacted_and_rendered_without_html_or_command_execution(tmp_path):
    db = tmp_path / "text.sqlite3"
    past = record()
    past["evidence"][1]["content"] = "ValidationException raw-private-body password=private-value"
    past["observations"][1]["content"] = past["evidence"][1]["content"]
    approved(db, past)
    with IncidentStore(db) as store:
        store.review_card(PROJECT, past["run_id"], "edit", reviewer="c-owner", changes={
            "next_action": "<script>alert(1)</script> [run_shell](https://invalid.example) password=private-value",
            "check_sequence": ["```run_shell``` token=private-value"]})
    markdown = export_manual(PROJECT, db_path=db)["markdown"]
    assert "<script>" not in markdown and "[run_shell](" not in markdown and "```run_shell```" not in markdown
    assert "private-value" not in markdown and "raw-private-body" not in markdown
    assert "private-value" not in db.read_bytes().decode("utf-8", errors="ignore")


def test_historical_observation_cannot_be_renamed_current_during_recheck(tmp_path):
    db = tmp_path / "refs.sqlite3"
    past = approved(db)
    current = record()
    current["observations"].append({"id": "OLD1", "kind": "log", "scope_status": "VERIFIED", "content": "SQLiteException", "run_id": past["run_id"]})
    checked = rechecked(db, current)
    assert checked["rechecks"][0]["status"] == "CURRENT_GUIDANCE_OBSERVED"
    assert all(ref["evidence_id"] != "OLD1" for ref in checked["current_recheck"]["evidence_refs"])
    with IncidentStore(db) as store, pytest.raises(ValueError, match="another run"):
        store.save_run(current)


def test_cli_disable_store_review_extended_conditions_and_export_survive_restart(tmp_path):
    db = tmp_path / "cli.sqlite3"
    manage = ("--db", db, "--project", PROJECT)
    disabled = cli("scripts.incident_memory", *manage, "search", "--disable-memory", "--query", "/api/orders")
    assert disabled["status"] == "DISABLED" and not db.exists()
    run = record()
    saved_file = tmp_path / "run.json"
    saved_file.write_text(json.dumps(run, ensure_ascii=False), encoding="utf-8")
    assert cli("scripts.incident_memory", *manage, "save", "--file", saved_file)["status"] == "SAVED"
    cli("scripts.incident_memory", *manage, "review", run["run_id"], "--action", "approve", "--reviewer", "c-cli-owner")
    edits = tmp_path / "edits.json"
    edits.write_text(json.dumps({"applicability": {"environment": "dev"}, "check_sequence": ["CLI currentcheckword 계약 출처 확인"],
        "invalid_conditions": [{"when": {"environment": "prod"}, "reason": "운영 적용 불가"}]}), encoding="utf-8")
    changed = cli("scripts.incident_memory", *manage, "review", run["run_id"], "--action", "edit", "--reviewer", "c-cli-owner", "--changes-file", edits)
    assert changed["review"]["revision"] == 2 and changed["verification"]["fix_verified"] is False
    assert cli("scripts.incident_memory", *manage, "search", "--query", "currentcheckword")["hit_count"] == 1
    output = tmp_path / "incident-manual.md"
    metadata = cli("scripts.export_incident_manual", *manage, "--output", output)
    assert metadata["card_count"] == 1 and run["run_id"] in output.read_text(encoding="utf-8")
    stdout = cli("scripts.export_incident_manual", *manage, "--card", run["run_id"], json_output=False)
    assert stdout == output.read_text(encoding="utf-8")
    assert cli("scripts.incident_memory", *manage, "run", run["run_id"])["run_id"] == run["run_id"]


def test_cards_only_replay_checks_versions_discard_restart_save_and_manual(tmp_path):
    result = replay_card_cases(tmp_path / "replay")
    assert result["external_model_calls"] == 0 and result["resumed_revision"] == 2
    assert result["duplicate_save"] == "ALREADY_SAVED" and result["review_history_count"] == 4
    assert result["discard_search_count"] == 0 and result["memory_disabled"]["status"] == "DISABLED"
    assert result["memory_disabled"]["persistence"]["status"] == "SAVED"
    expected = {"repeat": "CURRENT_GUIDANCE_OBSERVED", "different_cause_same_http": "REJECTED", "changed_version": "NOT_REVALIDATED", "unobserved_version": "NOT_REVALIDATED", "conflict": "NOT_REVALIDATED"}
    for name, status in expected.items():
        assert result["cases"][name]["memory_search"]["rechecks"][0]["status"] == status
    assert Path(result["manual"]["path"]).read_text(encoding="utf-8").startswith("# parallel")


def test_intake_replay_with_explicit_synthetic_input_provenance_retains_guidance_expectation(tmp_path):
    result = replay(tmp_path / "intake-replay")
    assert result["external_model_calls"] == 0 and result["caller_source_double"]["executed"] is False
    assert result["repeated"]["route"] == "GUIDANCE" and result["repeated"]["observed_status"] == 422
    match = result["repeated"]["memory_search"]["rechecks"][0]
    assert match["status"] == "NOT_REVALIDATED" and match["current_guidance_observed"]
    assert result["different_current_cause"]["memory_search"]["rechecks"][0]["status"] == "REJECTED"
    assert result["conflicting"]["memory_search"]["rechecks"][0]["status"] == "NOT_REVALIDATED"
    assert result["manual"]["card_count"] == 1 and result["reconnected_run_count"] == 2
