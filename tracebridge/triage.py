"""Deterministic claims and evidence checks; model text is never the source of truth."""

from __future__ import annotations

import re
from typing import Any

from .evidence import get_backend_evidence, get_contract, get_migration_state, get_trace, EvidenceError


def _reported_status(claim: str) -> int | None:
    match = re.search(r"(?<!\d)([45]\d{2})(?!\d)", claim or "")
    return int(match.group(1)) if match else None


def analyze(trace_id: str, claim: str) -> dict[str, Any]:
    result: dict[str, Any] = {
        "trace_id": trace_id,
        "claim": claim,
        "claim_status": "UNVERIFIABLE",
        "finding_status": "INCONCLUSIVE",
        "repro_status": "NOT_ATTEMPTED",
        "diagnosis_type": "unknown",
        "evidence": [],
        "next_action": "trace_id, 환경, 발생 시각과 실제 요청 정보를 확인하세요.",
        "repro_eligible": False,
    }
    try:
        trace = get_trace(trace_id)
    except EvidenceError:
        return result

    observed = trace["response_status"]
    reported = _reported_status(claim)
    if reported is not None:
        result["claim_status"] = "MATCHED" if reported == observed else "CONTRADICTED"
    result["observed_status"] = observed
    result["reported_status"] = reported
    result["scope"] = {key: trace[key] for key in ("environment", "service", "method", "path")}
    result["evidence"].append({"source": f"trace:{trace_id}", "fact": f"실제 응답 HTTP {observed}"})

    contract_data = get_contract(trace_id)
    required = set(contract_data["openapi"]["required"])
    properties = set(contract_data["openapi"]["properties"])
    request_fields = set(trace["request"])
    missing = sorted(required - request_fields)
    unexpected = sorted(request_fields - properties)
    if missing:
        result["evidence"].append({"source": f"openapi:{trace['method']} {trace['path']}", "fact": f"필수 필드 누락: {', '.join(missing)}"})
    if unexpected:
        result["evidence"].append({"source": f"trace:{trace_id}", "fact": f"계약에 없는 요청 필드: {', '.join(unexpected)}"})

    if "userId" in missing and "user_id" in unexpected:
        result.update(
            finding_status="CONFIRMED_MISMATCH",
            diagnosis_type="contract_mismatch",
            next_action="샘플 프론트 요청 생성기의 user_id를 계약의 userId로 수정하고 같은 테스트를 다시 실행하세요.",
            repro_eligible=True,
        )
        result["evidence"].append({"source": "backend_dto:SignupRequest", "fact": "백엔드 DTO도 userId를 요구"})
        return result

    if observed == 500:
        backend = get_backend_evidence(trace_id)
        migration = get_migration_state(trace_id)
        matching_log = next((line for line in backend["logs"] if "no column named phone" in line), None)
        if matching_log and migration["applied"] != migration["expected"]:
            result.update(
                finding_status="CONFIRMED_MISMATCH",
                diagnosis_type="migration_missing",
                next_action=f"{migration['related_file']} 적용 여부를 개발 DB에서 확인하고 격리 환경의 재현 테스트를 실행하세요.",
                repro_eligible=True,
            )
            result["evidence"].extend(
                [
                    {"source": f"backend-log:{trace_id}", "fact": matching_log},
                    {"source": "migration-history:dev", "fact": f"적용 {migration['applied']}, 코드 기대 {migration['expected']}"},
                    {"source": f"migration-file:{migration['related_file']}", "fact": "phone 컬럼 추가 파일 존재"},
                ]
            )
            return result

    if observed == 400 and missing:
        result.update(
            finding_status="CONFIRMED_MISMATCH",
            diagnosis_type="expected_validation",
            next_action="제보한 500은 이 요청에서 확인되지 않았습니다. 누락 필드를 보완한 요청과 새 trace_id로 다시 확인하세요.",
        )
        return result

    result["next_action"] = "현재 증거만으로 원인을 정할 수 없습니다. 같은 요청의 상세 로그 또는 재현 절차를 추가하세요."
    return result


def summarize(result: dict[str, Any]) -> str:
    """Use verified verdict fields, never unvalidated model prose, for the user summary."""
    if "observed_status" not in result:
        return "요청을 식별하지 못했습니다. trace ID, 환경, 발생 시각을 확인해 주세요."

    reported = result["reported_status"]
    observed = result["observed_status"]
    if reported is None:
        claim_line = f"실제 응답은 HTTP {observed}입니다. 제보에 상태 코드가 없어 일치 여부는 판단할 수 없습니다."
    else:
        agreement = "일치" if result["claim_status"] == "MATCHED" else "불일치"
        claim_line = f"제보 HTTP {reported}, 실제 HTTP {observed}: {agreement}."

    finding = {
        "contract_mismatch": "요청의 user_id와 계약·DTO의 userId가 다릅니다.",
        "migration_missing": "백엔드의 phone 컬럼 오류와 DB V11 / 코드 V12 상태가 확인됐습니다.",
        "expected_validation": "이 요청에서는 필수 필드 누락에 따른 HTTP 400이 확인됐습니다.",
    }.get(result["diagnosis_type"], "현재 자료로는 원인을 확정할 수 없습니다.")
    return f"{claim_line} {finding} 재현 테스트는 아직 실행하지 않았습니다."
