from tracebridge.triage import analyze
from tracebridge.repro import demonstrate_red_green
from tracebridge.nat_plugin import _bounded_tool
import pytest


def test_contract_mismatch_and_false_500_claim():
    result = analyze("contract-001", "회원가입에서 500이 납니다")
    assert result["claim_status"] == "CONTRADICTED"
    assert result["finding_status"] == "CONFIRMED_MISMATCH"
    assert result["diagnosis_type"] == "contract_mismatch"


def test_migration_missing():
    result = analyze("migration-002", "회원가입에서 500이 납니다")
    assert result["claim_status"] == "MATCHED"
    assert result["diagnosis_type"] == "migration_missing"
    assert result["repro_eligible"]


def test_wrong_report_does_not_invent_500():
    result = analyze("claim-003", "회원가입에서 500이 납니다")
    assert result["claim_status"] == "CONTRADICTED"
    assert result["observed_status"] == 400
    assert result["diagnosis_type"] == "expected_validation"
    assert not result["repro_eligible"]


def test_missing_trace_abstains():
    result = analyze("missing", "500이 납니다")
    assert result["claim_status"] == "UNVERIFIABLE"
    assert result["finding_status"] == "INCONCLUSIVE"


def test_repro_templates_show_red_then_green():
    for kind in ("contract_mismatch", "migration_missing"):
        result = demonstrate_red_green(kind)
        assert result["verified_in_demo"], result


def test_tool_scope_rejects_other_trace_before_read(monkeypatch):
    monkeypatch.setenv("TRACEBRIDGE_ALLOWED_TRACE_ID", "contract-001")
    accessed = []

    def provider(trace_id):
        accessed.append(trace_id)
        return {}

    with pytest.raises(ValueError, match="scope"):
        _bounded_tool("trace", "migration-002", provider)
    assert accessed == []
