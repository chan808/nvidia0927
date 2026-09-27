from types import SimpleNamespace

from tracebridge import agent
from tracebridge.triage import analyze, summarize
from tracebridge.repro import demonstrate_red_green
from tracebridge.nat_plugin import _bounded_tool
import pytest


def test_contract_mismatch_and_false_500_claim():
    result = analyze("contract-001", "회원가입에서 500이 납니다")
    assert result["claim_status"] == "CONTRADICTED"
    assert result["finding_status"] == "CONFIRMED_MISMATCH"
    assert result["diagnosis_type"] == "contract_mismatch"
    assert "제보 HTTP 500, 실제 HTTP 422: 불일치" in summarize(result)


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


def test_live_agent_repairs_wrong_scope_tool_call_without_trusting_model_text(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    calls = []
    tool_call = SimpleNamespace(
        function=SimpleNamespace(name="get_contract", arguments='{"trace_id":"migration-002"}')
    )
    completion = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=3, completion_tokens=2),
        choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[tool_call], content="500과 422는 일치"))],
    )

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return completion

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(agent, "OpenAI", lambda **kwargs: fake_client)

    result = agent.run_live("contract-001", "회원가입에서 500이 납니다")
    assert len(calls) == 1
    assert result["verdict"]["claim_status"] == "CONTRADICTED"
    assert "제보 HTTP 500, 실제 HTTP 422: 불일치" in result["agent_message"]
    contract_steps = [step for step in result["steps"] if step["tool"] == "get_contract"]
    assert "error" in contract_steps[0]["result"]
    assert contract_steps[-1]["result"]["openapi"]["required"] == ["userId", "name"]
