"""Decisions from current observations; report wording never grants permissions."""
from __future__ import annotations

from .work_management import WorkAssessment


def decide(run: dict, change: dict | None = None) -> dict:
    aggregate = run.get("log_scope", {}).get("aggregate", {})
    complete = aggregate.get("complete") is True and not (
        aggregate.get("conflicts") or aggregate.get("conflicting_trace_ids"))
    exact = run.get("correlation") == "EXACT_ID"
    observed = run.get("observed_status")
    comparison = run.get("version_provenance", {}).get("comparison")
    diagnosis = run.get("diagnosis_type")
    responsibility = run.get("responsibility", {}).get("status")
    refs = [item["id"] for item in run.get("evidence", []) if isinstance(item, dict) and item.get("id")][:8]
    assessment = WorkAssessment(reason="현재 관측이 부족합니다.")
    action, reason = "NEEDS_CONTEXT", "CURRENT_EVIDENCE_REQUIRED"
    if run.get("run_status") in {"TIMED_OUT", "BUDGET_EXHAUSTED", "PARTIAL_FAILURE"}:
        action, reason = "UNRESOLVED", "INVESTIGATION_LIMIT_OR_FAILURE"
    elif comparison == "MISMATCH" or diagnosis == "version_mismatch":
        action, reason = "NEEDS_REVIEW", "RUNTIME_VERSION_MISMATCH"
    elif exact and complete:
        failure = type(observed) is int and observed >= 500
        defect = responsibility == "CALLER_DEFECT" or run.get("product_status") == "DEFECT_OBSERVED"
        security = diagnosis in {"security_suspected", "migration_missing"} or run.get("work_role") in {"Security Review", "Data/Infrastructure"}
        assessment = WorkAssessment(priority="P2" if failure or defect else "P3",
            difficulty="SMALL" if diagnosis == "contract_mismatch" else "MEDIUM" if failure or defect else "UNKNOWN",
            risk="HIGH" if security else "UNKNOWN",
            reason="같은 요청의 관측·계약 비교에 근거한 잠정 평가입니다.")
        if security:
            action, reason = "NEEDS_REVIEW", "SENSITIVE_CHANGE_REQUIRES_OWNER"
        elif run.get("route") == "GUIDANCE" and diagnosis == "expected_validation" and responsibility == "INPUT_OMISSION":
            action, reason = "GUIDANCE", "CHECKED_INPUT_OMISSION"
        elif run.get("route") in {"WORK_CANDIDATE", "INVESTIGATE"} and (failure or defect):
            action, reason = "PREPARE_CHANGE", "REPRODUCE_CURRENT_FAILURE"
        else:
            action, reason = "NEEDS_CONTEXT", "CAUSE_NOT_ESTABLISHED"
    if change is not None:
        action = "WAITING_REVIEW" if change.get("candidate_fix_verified") is True else "UNRESOLVED"
        reason = "CANDIDATE_CHECKS_PASSED" if action == "WAITING_REVIEW" else "CANDIDATE_NOT_VERIFIED"
    # Reasons are fixed, public input/model free; detailed source references stay internal.
    reasons = {
        "CURRENT_EVIDENCE_REQUIRED": "현재 요청을 식별할 자료가 더 필요합니다.",
        "INVESTIGATION_LIMIT_OR_FAILURE": "조사 시간·호출 한도 또는 수집 실패를 기록했습니다.",
        "RUNTIME_VERSION_MISMATCH": "실행 버전과 조사 자료의 버전이 다릅니다.",
        "SENSITIVE_CHANGE_REQUIRES_OWNER": "보안·데이터 변경 위험을 담당자가 검토해야 합니다.",
        "CHECKED_INPUT_OMISSION": "현재 입력 누락과 호출자의 전달 근거를 확인했습니다.",
        "REPRODUCE_CURRENT_FAILURE": "같은 요청의 실패를 등록된 검사로 재현한 뒤 후보를 검증합니다.",
        "CAUSE_NOT_ESTABLISHED": "같은 요청을 찾았지만 원인·책임을 확정하지 않았습니다.",
        "CANDIDATE_CHECKS_PASSED": "수정 전후·회귀 검사 통과 후보를 검토합니다.",
        "CANDIDATE_NOT_VERIFIED": "수정 후보를 검증하지 못했습니다. 실패 이력과 다음 조치를 남겼습니다.",
    }
    assessment = assessment.model_copy(update={"reason": reasons[reason]})
    return {"action": action, "reason": reason, "assessment": assessment.record(),
        "evidence_ids": refs, "source_run_id": run.get("run_id"), "basis": "CURRENT_OBSERVATIONS"}


def knowledge_quality(card: dict) -> str:
    verification = card.get("verification_results", {})
    if verification.get("service_recovery", {}).get("status") == "RECORDED_VERIFIED":
        return "SERVICE_RECOVERY_VERIFIED"
    if verification.get("candidate_validation", {}).get("status") == "VERIFIED":
        return "CANDIDATE_VERIFIED"
    if verification.get("cause_confirmation", {}).get("status") == "RECORDED_CONFIRMED":
        return "CAUSE_CONFIRMED"
    return "CURRENT_RECHECK_REQUIRED"
