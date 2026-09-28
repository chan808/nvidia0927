from pathlib import Path
from types import SimpleNamespace

from tracebridge.project_gpt import ProjectGPT, ProposedHypothesis, ProposedReport, SearchPlan
from tracebridge import project_investigation
from tracebridge.project_investigation import investigate_agolive_report
from tracebridge.project_sources import (
    code_evidence,
    Evidence,
    log_evidence,
    redact,
    stack_profile,
    validate_agolive_repo,
)


REPO = Path(__file__).resolve().parent / "fixtures" / "agolive_repo"


class FakeGPT:
    def search_plan(self, report):
        return SearchPlan(services=["realtime", "backend"], terms=["ROOM_FULL", "joinRoom"])

    def hypotheses(self, report, evidence, revision):
        assert any(item.id == "L1" for item in evidence)
        assert any(item.id == "C1" for item in evidence)
        return ProposedReport(
            hypotheses=[ProposedHypothesis(
                cause="room capacity check",
                explanation="The request may have reached the capacity guard.",
                supporting_evidence_ids=["L1", "C1", "invented-id"],
                contradicting_evidence_ids=[],
                verification_step="Reproduce the join at the same capacity.",
                possible_fix="Review the capacity calculation.",
            )],
            missing_information=["deployed revision"],
            next_steps=["Check the room occupancy at the reported time."],
        )


def test_agolive_profile_and_code_evidence_are_bounded():
    repo = validate_agolive_repo(REPO)
    profile = stack_profile(repo)
    assert len(profile) == 5
    code = code_evidence(repo, ["ROOM_FULL"], ["realtime"])
    assert code
    assert all(item.source.endswith(".go:2") or item.source.endswith(".kt:2") for item in code)
    assert all(item.source.startswith(("realtime/", "backend/")) for item in code)


def test_report_uses_correlated_redacted_log_and_rejects_fake_citation():
    logs = (
        "2026-09-27 14:00:00 ERROR [abc12345] [192.0.2.2] ws - ROOM_FULL "
        "email=user@example.invalid token=sk-abcdefghijklmnopqrstuvwxyz\n"
        "    at handler.joinRoom(ws.go:2)\n"
    )
    result = investigate_agolive_report(
        "ROOM_FULL requestId=abc12345 방 입장 실패",
        repo=REPO,
        provided_logs=logs,
        use_gpt=True,
        gpt=FakeGPT(),
    )
    hypothesis = result["hypotheses"][0]
    assert result["gpt_used"]
    assert hypothesis["status"] == "SUPPORTED_HYPOTHESIS"
    assert hypothesis["supporting_evidence_ids"] == ["L1", "C1"]
    assert not result["fix_applied"]
    visible = str(result)
    for secret in ("user@example.invalid", "192.0.2.2", "sk-abcdefghijklmnopqrstuvwxyz"):
        assert secret not in visible
    assert any(item["correlated"] for item in result["evidence"] if item["kind"] == "log")


def test_code_only_result_keeps_cause_unconfirmed():
    result = investigate_agolive_report("ROOM_FULL 방 입장 실패", repo=REPO, use_gpt=False)
    assert not result["gpt_used"]
    assert result["hypotheses"] == []
    assert any("로그" in item for item in result["missing_information"])
    assert any(item["kind"] == "code" for item in result["evidence"])


def test_default_offline_path_does_not_read_gpt_key(monkeypatch):
    def forbidden(_repo):
        raise AssertionError("GPT key must not be read by default")

    monkeypatch.setattr(project_investigation, "agolive_openai_settings", forbidden)
    result = investigate_agolive_report("ROOM_FULL 방 입장 실패", repo=REPO)
    assert result["gpt_used"] is False


def test_redaction_keeps_request_id_and_removes_sensitive_values():
    text = "requestId=abc12345 userId=123 email=a@example.invalid clientIp=192.0.2.2 Bearer abc.def nvapi-abcdefghijklmnopqrstuvwxyz"
    cleaned = redact(text)
    assert "abc12345" in cleaned
    assert "userId=123" not in cleaned
    assert "a@example.invalid" not in cleaned
    assert "192.0.2.2" not in cleaned
    assert "Bearer abc.def" not in cleaned
    assert "nvapi-abcdefghijklmnopqrstuvwxyz" not in cleaned


def test_log_without_request_id_is_not_called_correlated():
    evidence, notes = log_evidence("ERROR ROOM_FULL", "방 입장 ROOM_FULL")
    assert evidence and not evidence[0].correlated
    assert any("requestId" in note for note in notes)


def test_log_id_must_be_a_request_field_or_registered_mdc_prefix():
    logs = (
        "ERROR requestId=other note=abc12345 ROOM_FULL\n"
        "ERROR arbitrary=abc12345 ROOM_FULL\n"
        '{"level":"ERROR","requestId":"abc12345","message":"ROOM_FULL"}\n'
        "2026-09-28 10:00:00.000 ERROR [abc12345] [192.0.2.2] ws - ROOM_FULL\n"
        "2026-09-28 10:00:00.000 ERROR [other] [192.0.2.2] ws - requestId=abc12345\n"
        "2026-09-28 10:00:00.000 ERROR [?] [192.0.2.2] ws - requestId=abc12345\n"
    )
    evidence, _ = log_evidence(logs, "requestId=abc12345 ROOM_FULL")
    assert {item.source for item in evidence} == {"provided-log:3", "provided-log:4"}
    assert all(item.correlated for item in evidence)


def test_stack_continuation_cannot_inherit_correlation_from_another_request():
    logs = (
        "ERROR requestId=abc12345 ROOM_FULL\n"
        "    at handler.joinRoom(ws.go:2)\n"
        "    ERROR requestId=other dependency failure\n"
        "    at other.handler(other.go:3)\n"
    )
    evidence, _ = log_evidence(logs, "requestId=abc12345 ROOM_FULL")
    assert {item.source for item in evidence} == {"provided-log:1", "provided-log:2"}


def test_gpt_wrapper_uses_structured_output_and_redacted_evidence():
    calls = []

    class Completions:
        def parse(self, **kwargs):
            calls.append(kwargs)
            parsed = (
                SearchPlan(services=["realtime"], terms=["ROOM_FULL"])
                if kwargs["response_format"] is SearchPlan
                else ProposedReport(hypotheses=[], missing_information=[], next_steps=[])
            )
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(parsed=parsed))])

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    gpt = ProjectGPT("unused-test-key", "gpt-4o-mini", client=client)
    gpt.search_plan("room error user@example.invalid")
    gpt.hypotheses(
        "room error user@example.invalid",
        [Evidence("L1", "log", "log:1", "ERROR token=sk-abcdefghijklmnopqrstuvwxyz", correlated=True)],
        "r42",
    )
    assert [call["response_format"] for call in calls] == [SearchPlan, ProposedReport]
    assert all(call["store"] is False for call in calls)
    assert all("user@example.invalid" not in str(call["messages"]) for call in calls)
    assert "sk-abcdefghijklmnopqrstuvwxyz" not in str(calls[1]["messages"])
