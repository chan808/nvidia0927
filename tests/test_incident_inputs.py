from pathlib import Path
from types import SimpleNamespace

import pytest

from tracebridge import agent
from tracebridge.agent import run_offline
from tracebridge.demo_app import make_connection, signup
from tracebridge.evidence import EvidenceError, LocalBundleEvidenceSource
from tracebridge.fixtures import get_case
from tracebridge.triage import analyze, summarize


def test_report_facts_distinguish_partial_and_wrong_api():
    partial = analyze("contract-001", "결제 API에서 422가 납니다")
    assert partial["claim_status"] == "PARTIAL"
    assert {item["facet"]: item["status"] for item in partial["claim_items"]} == {
        "http_status": "MATCHED",
        "operation": "CONTRADICTED",
    }

    wrong = analyze("contract-001", "결제 API에서 500이 납니다")
    assert wrong["claim_status"] == "CONTRADICTED"

    mixed = analyze("contract-001", "회원가입 API에서 500이 납니다")
    assert mixed["claim_status"] == "PARTIAL"


def test_no_report_still_investigates_backend_500():
    result = analyze("migration-002", None)
    assert result["claim_status"] == "NOT_PROVIDED"
    assert result["claim_items"] == []
    assert result["diagnosis_type"] == "migration_missing"
    assert "제보 없이" in summarize(result)


def test_multiple_statuses_do_not_select_first():
    result = analyze("migration-002", "회원가입 API에서 500 또는 502")
    status = next(item for item in result["claim_items"] if item["facet"] == "http_status")
    assert status["status"] == "UNVERIFIABLE"
    assert result["reported_status"] is None


def test_explicit_method_path_and_cause_remain_separate_claims():
    result = analyze("migration-002", "GET /api/payments에서 500, 원인은 DB 장애")
    facets = {item["facet"]: item["status"] for item in result["claim_items"]}
    assert facets == {
        "http_status": "MATCHED",
        "method": "CONTRADICTED",
        "path": "CONTRADICTED",
        "suspected_cause": "UNVERIFIABLE",
    }
    assert result["claim_status"] == "PARTIAL"


def test_offline_bundle_exposes_exception_hint_without_confirming_cause():
    path = Path(__file__).resolve().parents[1] / "examples" / "backend_incident.json"
    source = LocalBundleEvidenceSource.from_file(path)

    report = run_offline(source.trace_id, None, source=source)
    verdict = report["verdict"]
    assert report["mode"] == "offline_bundle"
    assert verdict["claim_status"] == "NOT_PROVIDED"
    assert verdict["finding_status"] == "INCONCLUSIVE"
    assert verdict["diagnosis_type"] == "backend_exception_unconfirmed"
    assert not verdict["repro_eligible"]
    assert any("ValueError" in item["fact"] for item in verdict["evidence"])
    assert "ValueError" in summarize(verdict)
    assert "private@example.invalid" not in summarize(verdict)
    assert "private@example.invalid" not in str(report)


def test_bundle_is_scoped_and_rejects_bad_status():
    data = {
        "trace": {
            "trace_id": "one",
            "environment": "staging",
            "service": "api",
            "response_status": 500,
        },
        "logs": [],
    }
    source = LocalBundleEvidenceSource(data)
    with pytest.raises(EvidenceError, match="scope"):
        source.get_trace("another")
    data["trace"]["response_status"] = True
    with pytest.raises(EvidenceError, match="HTTP status"):
        LocalBundleEvidenceSource(data)


def test_validation_fixture_matches_disposable_app():
    case = get_case("claim-003")
    connection = make_connection(apply_v12=True)
    try:
        assert signup(case["trace"]["request"], connection) == case["trace"]["response_status"]
    finally:
        connection.close()


def test_live_fixture_path_accepts_no_report(monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key")
    calls = []

    class FakeCompletions:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                usage=None,
                choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[]))],
            )

    client = SimpleNamespace(chat=SimpleNamespace(completions=FakeCompletions()))
    monkeypatch.setattr(agent, "OpenAI", lambda **kwargs: client)
    result = agent.run_live("migration-002", None)
    assert result["verdict"]["claim_status"] == "NOT_PROVIDED"
    assert "(none; investigate" in calls[0]["messages"][1]["content"]
