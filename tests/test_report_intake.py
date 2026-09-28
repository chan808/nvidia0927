from copy import deepcopy
import json
from pathlib import Path

import pytest

from tracebridge.evidence import EvidenceError
from tracebridge.report_intake import LocalEventCatalog, triage_report


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "report_events.json"


def test_exact_id_routes_observed_validation_and_real_work_differently():
    catalog = LocalEventCatalog.from_file(EXAMPLE)
    guidance = triage_report("회원가입 API에서 500 requestId=claim-003", catalog)
    assert guidance["correlation"] == "EXACT_ID"
    assert guidance["route"] == "GUIDANCE"
    assert guidance["diagnosis_type"] == "expected_validation"
    assert "실제 HTTP 422" in guidance["summary"]

    repair = triage_report("회원가입 API에서 500 requestId=contract-001", catalog)
    assert repair["route"] == "WORK_CANDIDATE"
    assert repair["work_role"] == "Frontend/Caller Repair"
    assert repair["diagnosis_type"] == "contract_mismatch"

    infrastructure = triage_report("회원가입 API에서 500 requestId=migration-002", catalog)
    assert infrastructure["route"] == "WORK_CANDIDATE"
    assert infrastructure["work_role"] == "Data/Infrastructure"


def test_context_correlation_holds_multiple_candidates_and_does_not_use_claimed_status():
    catalog = LocalEventCatalog.from_file(EXAMPLE)
    result = triage_report(
        "POST /api/users에서 500 오류",
        catalog,
        environment="dev",
        occurred_at="2026-09-28T10:01:00+09:00",
    )
    assert result["route"] == "REQUEST_CONTEXT"
    assert result["correlation"] == "NEEDS_CONTEXT"
    assert set(result["candidate_trace_ids"]) == {"contract-001", "claim-003"}
    assert result["evidence"] == []


def test_single_context_candidate_is_investigated_before_work_is_dispatched():
    catalog = LocalEventCatalog.from_file(EXAMPLE)
    result = triage_report(
        "POST /api/users에서 500 오류",
        catalog,
        environment="dev",
        occurred_at="2026-09-28T10:10:00+09:00",
    )
    assert result["correlation"] == "CONTEXT_CANDIDATE"
    assert result["trace_id"] == "migration-002"
    assert result["diagnosis_type"] == "migration_missing"
    assert result["route"] == "INVESTIGATE"
    assert result["work_role"] == "Correlation"


def test_missing_or_conflicting_id_requests_context_without_diagnosis():
    catalog = LocalEventCatalog.from_file(EXAMPLE)
    for kwargs in (
        {"report": "500 오류가 납니다"},
        {"report": "requestId=missing 500 오류"},
        {"report": "requestId=claim-003 500 오류", "trace_id": "contract-001"},
        {"report": "requestId=claim-003 500 오류", "environment": "prod"},
    ):
        result = triage_report(catalog=catalog, **kwargs)
        assert result["route"] == "REQUEST_CONTEXT"
        assert result["diagnosis_type"] is None
        assert result["evidence"] == []


def test_unconfirmed_server_error_is_sent_to_diagnosis_without_echoing_raw_logs():
    event = json.loads((EXAMPLE.parent / "backend_incident.json").read_text(encoding="utf-8"))
    event["trace"]["occurred_at"] = "2026-09-28T10:00:00+09:00"
    catalog = LocalEventCatalog({"project_id": "orders", "events": [event]})
    result = triage_report("500 오류 requestId=orders-staging-001", catalog)
    assert result["route"] == "INVESTIGATE"
    assert result["work_role"] == "Diagnosis"
    assert result["diagnosis_type"] == "backend_exception_unconfirmed"
    assert "private@example.invalid" not in str(result)


def test_catalog_rejects_duplicate_ids_and_naive_timestamps():
    catalog = LocalEventCatalog.from_file(EXAMPLE)
    source, _ = catalog.events["contract-001"]
    event = {
        "trace": source.get_trace("contract-001"),
        "contract": source.get_contract("contract-001")["openapi"],
    }
    with pytest.raises(EvidenceError, match="Duplicate trace ID"):
        LocalEventCatalog({"project_id": "demo", "events": [event, deepcopy(event)]})
    event["trace"]["occurred_at"] = "2026-09-28T10:00:00"
    with pytest.raises(EvidenceError, match="timezone"):
        LocalEventCatalog({"project_id": "demo", "events": [event]})


@pytest.mark.parametrize("status, diagnosis", [(200, "unknown"), (500, "backend_exception_unconfirmed")])
def test_field_mismatch_does_not_dispatch_caller_work_for_success_or_server_failure(status, diagnosis):
    event = json.loads(EXAMPLE.read_text(encoding="utf-8"))["events"][0]
    event["trace"]["response_status"] = status
    event["logs"] = ["TimeoutError: dependency did not respond"] if status == 500 else []
    catalog = LocalEventCatalog({"project_id": "demo", "events": [event]})

    result = triage_report("가입이 안돼요 requestId=contract-001", catalog)

    assert result["route"] == "INVESTIGATE"
    assert result["diagnosis_type"] == diagnosis
    assert any("user_id" in item["fact"] for item in result["evidence"])
