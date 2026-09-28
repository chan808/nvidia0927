"""Conservative incident triage; model text is never the source of truth."""

from __future__ import annotations

import re
from typing import Any

from .claims import assess_claim
from .evidence import EvidenceError, EvidenceSource, FIXTURE_SOURCE


EXCEPTION_PATTERN = re.compile(r"\b(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?:Error|Exception|Fault)\b")


def route_verdict(verdict: dict[str, Any], correlation: str) -> dict[str, Any]:
    """The single routing policy for both intake paths; never grants execution rights."""
    if correlation not in {"EXACT_ID", "CONTEXT_CANDIDATE"}:
        return {"route": "REQUEST_CONTEXT", "work_role": None, "route_reason": "관측과 제보의 연결이 확인되지 않았습니다", "next_action": "문제가 난 화면·동작·대략적인 시각을 알려주세요."}
    if correlation == "CONTEXT_CANDIDATE":
        return {
            "route": "INVESTIGATE", "work_role": "Correlation",
            "route_reason": "범위가 맞는 한 요청 후보를 찾았지만 같은 사건인지 확인이 필요합니다",
            "next_action": "같은 화면과 동작에서 발생한 문제인지 확인해 주세요.",
        }
    diagnosis = verdict["diagnosis_type"]
    decisions = {
        "expected_validation": ("GUIDANCE", None, "필수 입력 누락에 따른 응답을 확인했습니다. 입력 주체의 책임은 미확정입니다"),
        "contract_mismatch": ("WORK_CANDIDATE", "Frontend/Caller Repair", "실제 요청 필드와 API 계약·DTO의 불일치를 확인했습니다"),
        "migration_missing": ("WORK_CANDIDATE", "Data/Infrastructure", "백엔드 오류와 마이그레이션 상태 불일치를 확인했습니다"),
    }
    route, role, reason = decisions.get(diagnosis, ("INVESTIGATE", "Diagnosis", "현재 관측으로 원인과 조치 대상을 확정할 수 없어 추가 조사합니다"))
    return {"route": route, "work_role": role, "route_reason": reason, "next_action": verdict["next_action"]}


def _exception_hint(logs: list[str]) -> str | None:
    for line in logs:
        match = EXCEPTION_PATTERN.search(line)
        if match:
            return match.group(0)
    return None


def analyze(trace_id: str, claim: str | None = None, source: EvidenceSource | None = None) -> dict[str, Any]:
    """Assess one observed incident from a scoped evidence source."""
    evidence_source = source or FIXTURE_SOURCE
    result: dict[str, Any] = {
        "trace_id": trace_id,
        "claim": claim,
        "claim_status": "NOT_PROVIDED" if not claim or not claim.strip() else "UNVERIFIABLE",
        "claim_items": [],
        "claim_coverage": "explicit_facts_only",
        "finding_status": "INCONCLUSIVE",
        "repro_status": "NOT_ATTEMPTED",
        "diagnosis_type": "unknown",
        "evidence": [],
        "next_action": "trace_id, 환경, 발생 시각과 실제 요청 정보를 확인하세요.",
        "repro_eligible": False,
    }
    try:
        trace = evidence_source.get_trace(trace_id)
    except EvidenceError:
        result["claim_status"], result["claim_items"] = assess_claim(claim, None, trace_id)
        return result

    result["claim_status"], result["claim_items"] = assess_claim(claim, trace, trace_id)
    observed = trace.get("response_status")
    result["observed_status"] = observed
    result["reported_status"] = next(
        (item["reported"] for item in result["claim_items"]
         if item["facet"] == "http_status" and isinstance(item["reported"], int)),
        None,
    )
    result["scope"] = {
        key: trace[key]
        for key in ("environment", "service", "method", "path", "operation", "version")
        if key in trace
    }
    if observed is not None:
        result["evidence"].append({"source": f"trace:{trace_id}", "fact": f"실제 응답 HTTP {observed}"})

    contract_data = None
    try:
        contract_data = evidence_source.get_contract(trace_id)
    except EvidenceError:
        pass
    missing: list[str] = []
    unexpected: list[str] = []
    if contract_data and isinstance(trace.get("request"), dict):
        openapi = contract_data["openapi"]
        required = set(openapi["required"])
        properties = set(openapi["properties"])
        request_fields = set(trace["request"])
        missing = sorted(required - request_fields)
        unexpected = sorted(request_fields - properties)
        contract_source = f"openapi:{trace.get('method', '?')} {trace.get('path', '?')}"
        if missing:
            result["evidence"].append({"source": contract_source, "fact": f"필수 필드 누락: {', '.join(missing)}"})
        if unexpected:
            result["evidence"].append({"source": f"trace:{trace_id}", "fact": f"계약에 없는 요청 필드: {', '.join(unexpected)}"})

        dto = contract_data.get("backend_dto")
        if "userId" in missing and "user_id" in unexpected and isinstance(dto, dict) and "userId" in dto:
            result["evidence"].append({"source": "backend_dto:SignupRequest", "fact": "백엔드 DTO도 userId를 요구"})
            # The structural difference alone does not explain a success or a 5xx.
            if observed in (400, 422):
                result.update(
                    finding_status="CONFIRMED_MISMATCH",
                    diagnosis_type="contract_mismatch",
                    next_action="요청 생성기의 user_id를 계약의 userId로 수정하고 같은 테스트를 다시 실행하세요.",
                    repro_eligible=source is None or source is FIXTURE_SOURCE,
                )
                return result

    logs: list[str] = []
    if isinstance(observed, int) and observed >= 500:
        try:
            backend = evidence_source.get_backend_evidence(trace_id)
            logs = backend.get("logs", [])
        except EvidenceError:
            pass
        matching_log = next((line for line in logs if "no column named phone" in line), None)
        if matching_log:
            try:
                migration = evidence_source.get_migration_state(trace_id)
            except EvidenceError:
                migration = None
            if migration and migration["applied"] != migration["expected"]:
                result.update(
                    finding_status="CONFIRMED_MISMATCH",
                    diagnosis_type="migration_missing",
                    next_action=f"{migration['related_file']} 적용 여부를 해당 환경에서 확인하고 격리된 재현을 실행하세요.",
                    repro_eligible=source is None or source is FIXTURE_SOURCE,
                )
                result["evidence"].extend(
                    [
                        {"source": f"backend-log:{trace_id}", "fact": "no column named phone 오류 발견"},
                        {"source": f"migration-history:{trace.get('environment', '?')}",
                         "fact": f"적용 {migration['applied']}, 코드 기대 {migration['expected']}"},
                        {"source": f"migration-file:{migration['related_file']}", "fact": "phone 컬럼 추가 파일 존재"},
                    ]
                )
                return result

        hint = _exception_hint(logs)
        if hint:
            result["evidence"].append(
                {"source": f"backend-log:{trace_id}", "fact": f"{hint} 예외 단서 발견 (원인 미확정)"}
            )
            result["diagnosis_type"] = "backend_exception_unconfirmed"
            result["exception_type"] = hint
            result["next_action"] = "해당 예외의 첫 애플리케이션 실패 지점과 배포 버전, 관련 요청을 확인하세요."
            return result

    if observed in (400, 422) and missing:
        result.update(
            finding_status="OBSERVED_VALIDATION",
            diagnosis_type="expected_validation",
            next_action="누락 필드를 보완한 요청과 새 trace_id로 다시 확인하세요.",
        )
        return result

    result["next_action"] = "현재 증거만으로 원인을 정할 수 없습니다. 같은 사건의 상세 로그 또는 재현 절차를 추가하세요."
    return result


def summarize(result: dict[str, Any]) -> str:
    """Build the visible summary only from checked verdict fields."""
    if "observed_status" in result and result["observed_status"] is None:
        return "요청의 로그는 찾았지만 실제 응답 상태가 기록되지 않았습니다. 원인을 확인하려면 같은 사건의 관측을 더 확인해야 합니다."
    if not isinstance(result.get("observed_status"), int):
        return "요청을 식별하지 못했습니다. trace ID, 환경, 발생 시각을 확인해 주세요."

    observed = result["observed_status"]
    reported = result["reported_status"]
    if result["claim_status"] == "NOT_PROVIDED":
        claim_line = f"제보 없이 관측된 HTTP {observed} 사건을 조사했습니다."
    elif reported is None:
        claim_line = f"실제 응답은 HTTP {observed}입니다. 제보의 HTTP 상태는 확인할 수 없습니다."
    else:
        status_item = next(item for item in result["claim_items"] if item["facet"] == "http_status")
        agreement = "일치" if status_item["status"] == "MATCHED" else "불일치"
        claim_line = f"제보 HTTP {reported}, 실제 HTTP {observed}: {agreement}."

    other_items = [item for item in result["claim_items"] if item["facet"] != "http_status"]
    if other_items:
        labels = {"method": "메서드", "path": "경로", "operation": "API 작업", "suspected_cause": "원인 추측"}
        status_labels = {"MATCHED": "일치", "CONTRADICTED": "불일치", "UNVERIFIABLE": "확인 불가"}
        claim_line += " " + "; ".join(
            f"{labels.get(item['facet'], item['facet'])} {status_labels[item['status']]}"
            for item in other_items
        ) + "."

    finding = {
        "contract_mismatch": "요청의 user_id와 계약·DTO의 userId가 다릅니다.",
        "migration_missing": "백엔드의 phone 컬럼 오류와 DB 마이그레이션 상태 불일치가 확인됐습니다.",
        "expected_validation": f"이 요청에서는 필수 필드 누락에 따른 HTTP {observed}이 확인됐습니다.",
        "backend_exception_unconfirmed": f"{result.get('exception_type', '백엔드')} 로그 단서는 찾았지만 원인은 확인하지 못했습니다.",
    }.get(result["diagnosis_type"], "현재 자료로는 원인을 확정할 수 없습니다.")
    return f"{claim_line} {finding} 재현 테스트는 아직 실행하지 않았습니다."
