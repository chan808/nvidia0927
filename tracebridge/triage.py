"""Conservative incident triage; model text is never the source of truth."""

from __future__ import annotations

import re
from typing import Any

from .claims import assess_claim
from .contract_analysis import analyze_contract
from .evidence import EvidenceError, EvidenceSource, FIXTURE_SOURCE


EXCEPTION_PATTERN = re.compile(r"\b(?:[A-Za-z_]\w*\.)*[A-Za-z_]\w*(?:Error|Exception|Fault)\b")
MISSING_COLUMN_PATTERNS = (
    re.compile(r"no column named\s+([A-Za-z_]\w*)", re.I),
    re.compile(r'column\s+[\"\x27]?([A-Za-z_]\w*)[\"\x27]?\s+does not exist', re.I),
    re.compile(r'unknown column\s+[\"\x27]?([A-Za-z_]\w*)', re.I),
)


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
    analysis = verdict.get("contract_analysis") or {}
    responsibility = (verdict.get("responsibility") or {}).get("status", "UNCONFIRMED")
    if analysis.get("versions", {}).get("status") == "MISMATCH" or responsibility == "VERSION_MISMATCH":
        return {"route": "INVESTIGATE", "work_role": "Diagnosis", "route_reason": "실행 버전과 계약·코드 근거의 버전이 달라 책임과 수정 대상을 보류합니다",
                "next_action": verdict["next_action"]}
    decisions = {
        "expected_validation": ("GUIDANCE", None, "현재 입력 누락과 호출자의 정상 필드 전달을 함께 확인했습니다. 서비스 전체 정상 여부는 미확정입니다"),
        "contract_mismatch": ("WORK_CANDIDATE", "Frontend/Caller Repair", "현재 입력·요청 생성 코드·계약·DTO·실행 버전으로 호출자 결함을 확인했습니다"),
        "migration_missing": ("WORK_CANDIDATE", "Data/Infrastructure", "백엔드 오류와 마이그레이션 상태 불일치를 확인했습니다"),
    }
    if diagnosis == "expected_validation" and responsibility != "INPUT_OMISSION":
        diagnosis = "unknown"
    if diagnosis == "contract_mismatch" and responsibility != "CALLER_DEFECT":
        diagnosis = "unknown"
    route, role, reason = decisions.get(diagnosis, ("INVESTIGATE", "Diagnosis", "현재 관측으로 원인과 조치 대상을 확정할 수 없어 추가 조사합니다"))
    return {"route": route, "work_role": role, "route_reason": reason, "next_action": verdict["next_action"]}


def _exception_hint(logs: list[str]) -> str | None:
    for line in logs:
        if not isinstance(line, str):
            continue
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
        "contract_analysis": analyze_contract({}),
        "responsibility": {"status": "UNCONFIRMED", "evidence_sources": []},
        "symptom_status": "UNOBSERVED",
        "product_status": "UNCONFIRMED",
    }
    try:
        trace = evidence_source.get_trace(trace_id)
    except EvidenceError as exc:
        result["claim_status"], result["claim_items"] = assess_claim(claim, None, trace_id)
        result["source_status"] = "UNAVAILABLE"
        result["source_limitations"] = [str(exc)]
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
    if type(observed) is int:
        result["symptom_status"] = "SERVER_ERROR_OBSERVED" if observed >= 500 else "REQUEST_REJECTED" if observed >= 400 else "HTTP_SUCCESS_OBSERVED" if 200 <= observed < 400 else "UNOBSERVED"

    contract_data = None
    try:
        contract_data = evidence_source.get_contract(trace_id)
    except EvidenceError as exc:
        if isinstance(exc.details, dict) and "openapi" in exc.details:
            contract_data = exc.details
    analysis = analyze_contract(trace, contract_data)
    result["contract_analysis"] = analysis
    result["responsibility"] = analysis["responsibility"]
    missing = analysis["missing_fields"]
    unexpected = analysis["unexpected_fields"]
    contract_source = analysis.get("provenance", {}).get("source") or f"openapi:{trace.get('method', '?')} {trace.get('path', '?')}"
    if missing:
        result["evidence"].append({"source": contract_source, "fact": f"필수 필드 누락: {', '.join(missing)}"})
    if unexpected:
        result["evidence"].append({"source": f"trace:{trace_id}", "fact": f"계약에 없는 요청 필드: {', '.join(unexpected)}"})
    for mismatch in analysis["type_mismatches"]:
        result["evidence"].append({"source": contract_source, "fact": f"타입 차이 {mismatch['field']}: 계약 {'/'.join(mismatch['expected'])}, 관측 {mismatch['observed']}"})
    for candidate in analysis["similarity_candidates"]:
        result["evidence"].append({"source": contract_source, "fact": f"표기 유사 후보 {candidate['observed_field']} / {candidate['required_field']} (자동 이름 변경 근거 아님)"})

    if analysis["versions"]["status"] == "MISMATCH":
        result.update(diagnosis_type="version_mismatch", next_action="현재 실행 버전의 계약·DTO와 호출자 생성 코드를 다시 확인하세요.")
        result["evidence"].append({"source": "version_observations", "fact": "실행 버전과 비교 자료의 코드 버전 불일치"})
        return result
    if analysis["responsibility"]["status"] == "CALLER_DEFECT":
        fields = ", ".join(analysis["responsibility"].get("fields", missing))
        result.update(
            finding_status="CONFIRMED_MISMATCH", diagnosis_type="contract_mismatch", product_status="CALLER_DEFECT_OBSERVED",
            next_action=f"호출자 요청 생성기의 {fields} 전달을 계약·DTO와 대조한 수정안으로 준비하고 같은 검사로 검증하세요. 표기 유사 후보만으로 이름을 바꾸지 마세요.",
            repro_eligible=source is None or source is FIXTURE_SOURCE,
        )
        result["evidence"].extend({"source": item, "fact": "현재 입력·요청 생성·계약·DTO·실행 버전의 책임 근거 확인"}
                                  for item in analysis["responsibility"]["evidence_sources"])
        return result

    logs: list[str] = []
    if type(observed) is int and observed >= 500:
        try:
            backend = evidence_source.get_backend_evidence(trace_id)
            logs = backend.get("logs", [])
            if not isinstance(logs, list):
                logs = []
        except EvidenceError:
            pass
        column = next((match.group(1) for line in logs if isinstance(line, str) for pattern in MISSING_COLUMN_PATTERNS
                       if (match := pattern.search(line))), None)
        if column:
            try:
                migration = evidence_source.get_migration_state(trace_id)
            except EvidenceError:
                migration = None
            actual_state = (isinstance(migration, dict) and migration.get("observation_status", "OBSERVED") == "OBSERVED"
                            and migration.get("environment", trace.get("environment")) == trace.get("environment")
                            and all(isinstance(migration.get(key), str) and migration[key] for key in ("applied", "expected", "related_file")))
            relates = actual_state and (column in migration.get("related_fields", [])
                                       or re.search(rf"(?<![A-Za-z0-9]){re.escape(column)}(?![A-Za-z0-9])", migration["related_file"], re.I))
            if actual_state and relates and migration["applied"] != migration["expected"]:
                result.update(
                    finding_status="CONFIRMED_MISMATCH",
                    diagnosis_type="migration_missing",
                    product_status="MIGRATION_MISMATCH_OBSERVED",
                    next_action=f"{migration['related_file']} 적용 여부를 해당 환경에서 확인하고 격리된 재현을 실행하세요.",
                    repro_eligible=source is None or source is FIXTURE_SOURCE,
                )
                result["evidence"].extend(
                    [
                        {"source": f"backend-log:{trace_id}", "fact": f"{column} 컬럼 부재 오류 발견"},
                        {"source": f"migration-history:{trace.get('environment', '?')}",
                         "fact": f"적용 {migration['applied']}, 코드 기대 {migration['expected']}"},
                        {"source": f"migration-file:{migration['related_file']}", "fact": f"{column} 관련 마이그레이션 자료 관측"},
                    ]
                )
                return result
            result["hypotheses"] = [{"cause": "migration_missing", "status": "UNVERIFIED",
                                     "reason": "컬럼 부재 단서는 있으나 해당 환경의 관련 마이그레이션 미적용은 확인하지 못했습니다"}]

        hint = _exception_hint(logs)
        if hint:
            result["evidence"].append(
                {"source": f"backend-log:{trace_id}", "fact": f"{hint} 예외 단서 발견 (원인 미확정)"}
            )
            result["diagnosis_type"] = "backend_exception_unconfirmed"
            result["exception_type"] = hint
            result["next_action"] = "해당 예외의 첫 애플리케이션 실패 지점과 배포 버전, 관련 요청을 확인하세요."
            return result

    if observed in (400, 422) and analysis["responsibility"]["status"] == "INPUT_OMISSION":
        result.update(
            finding_status="OBSERVED_VALIDATION",
            diagnosis_type="expected_validation",
            next_action=f"현재 입력에서 누락된 {', '.join(missing)}를 보완한 요청으로 다시 확인하세요. 서비스 전체의 정상 여부는 별도 확인이 필요합니다.",
        )
        return result

    if analysis["status"] == "COMPARED" and (missing or analysis["type_mismatches"] or analysis["forbidden_fields"]):
        result.update(finding_status="OBSERVED_CONTRACT_DIFFERENCE",
                      diagnosis_type="contract_difference_unconfirmed" if observed in (400, 422) else "unknown",
                      next_action="필드·타입 차이는 관측됐지만 원인과 책임은 미확정입니다. 현재 입력, 요청 생성 코드, DTO와 실행 버전을 확인하세요.")
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
        "contract_mismatch": "현재 입력은 제공됐지만 호출자가 계약·DTO와 다른 요청을 만든 근거를 확인했습니다.",
        "contract_difference_unconfirmed": "요청과 계약의 차이는 관측됐지만 입력 주체와 호출자의 책임은 미확정입니다.",
        "migration_missing": "백엔드 컬럼 부재 오류와 관련 DB 마이그레이션 상태 불일치가 확인됐습니다.",
        "expected_validation": f"현재 입력 누락과 호출자의 필드 전달을 확인했습니다. HTTP {observed}만으로 제품 전체 정상을 확정하지 않습니다.",
        "version_mismatch": "실행 버전과 계약·코드 자료의 버전이 달라 원인과 조치 대상을 보류합니다.",
        "backend_exception_unconfirmed": f"{result.get('exception_type', '백엔드')} 로그 단서는 찾았지만 원인은 확인하지 못했습니다.",
    }.get(result["diagnosis_type"], "현재 자료로는 원인을 확정할 수 없습니다.")
    return f"{claim_line} {finding} 재현 테스트는 아직 실행하지 않았습니다."
