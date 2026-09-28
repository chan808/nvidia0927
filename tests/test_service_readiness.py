"""Real seed checks and GUI flow; model/OCR doubles are labeled and offline."""

from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

from PIL import Image
import pytest

from tracebridge import change_policy, report_agent, report_service, seed_project
from tracebridge.change_policy import PolicyDenied, read_source
from tracebridge.incident_memory import IncidentStore
from tracebridge.report_agent import confirm_candidate, investigate_submission
from tracebridge.report_contract import ReportContext
from tracebridge.report_intake import mentioned_operations
from tracebridge.report_service import auto_prepare_submission, follow_up_service, prepare_submission
from tracebridge.seed_project import capture_seed_action, registered_seed, seed_catalog


ROOT = Path(__file__).resolve().parents[1]
PROJECT = "tracebridge-seed-signup"


@pytest.fixture
def service(tmp_path, monkeypatch):
    for relative in ("examples/seed_signup", "examples/change_policy"):
        destination = tmp_path / relative
        destination.mkdir(parents=True)
        for original in (ROOT / relative).iterdir():
            if original.is_file():
                shutil.copyfile(original, destination / original.name)
    monkeypatch.setattr(change_policy, "WORKSPACE", tmp_path)
    monkeypatch.setattr(seed_project, "WORKSPACE", tmp_path)
    monkeypatch.setenv("TRACEBRIDGE_DB_PATH", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("TRACEBRIDGE_SERVICE_PROJECT", "seed")
    return tmp_path, tmp_path / "state.sqlite3"


class Proposal:
    def __init__(self):
        self.calls = 0

    def propose(self, context, timeout):
        self.calls += 1
        policy = context["policy"]
        return {"policy_id": policy["policy_id"], "policy_version": policy["version"], "target_id": policy["target_id"],
                "evidence_ids": context["evidence_ids"][:1], "rationale": "관측된 요청 키를 등록 API 계약에 맞춥니다.",
                "check_ids": policy["check_ids"], "edits": [{"path": "client.py", "expected_sha256": policy["files"]["client.py"],
                                                           "old_key": "user_id", "new_key": "userId"}]}


def current_report(db, text="방금 가입이 안 돼요. 알아보고 고쳐줘."):
    return investigate_submission(text, registered_seed=True, db_path=db)


def confirmed_report(db):
    captured = capture_seed_action()
    result = current_report(db)
    return confirm_candidate(result, captured["event"]["trace"]["trace_id"], registered_seed=True, db_path=db)


def test_rough_report_and_current_action_need_human_confirmation_before_work(service):
    _, db = service
    captured = capture_seed_action()
    result = current_report(db)
    assert result["correlation"] == "CONTEXT_CANDIDATE" and result["route"] == "INVESTIGATE"
    assert result["trace_id"] == captured["event"]["trace"]["trace_id"]
    assert result["observed_status"] == 422 and result["model_calls"] == 0
    assert result["questions"] == ["아래 기록 중 문제가 났던 동작을 확인해 주세요."]
    assert result["case_kind"] == "SEEDED_DEVELOPMENT" and not result["deployment_observed"]
    assert not any(item["correlated"] for item in result["observations"])
    with pytest.raises(ValueError):
        prepare_submission(result, db_path=db, proposer=Proposal())


def test_confirm_then_prepare_and_reply_share_one_incident_and_database(service):
    workspace, db = service
    confirmed = confirmed_report(db)
    assert confirmed["route"] == "WORK_CANDIDATE" and confirmed["correlation"] == "EXACT_ID"
    assert confirmed["correlation_confirmation"]["kind"] == "EXPLICIT_HUMAN_SELECTION"
    proposal = Proposal()
    prepared = prepare_submission(confirmed, proposer=proposal)
    assert prepared["persistence"]["status"] == "SAVED" and prepared["job"]["status"] == "CHANGE_PREPARED"
    assert prepared["job"]["model"]["mode"] == "TEST_DOUBLE" and prepared["job"]["model"]["actual_calls"] == 0
    assert [item["exit_code"] for item in prepared["job"]["checks"]] == [1, 0, 0]
    assert prepared["job"]["checks"][2]["result"]["cases"] == [True, True, True]
    assert proposal.calls == 1
    assert prepare_submission(confirmed, proposer=proposal)["persistence"]["status"] == "ALREADY_SAVED"
    assert proposal.calls == 1
    follow = follow_up_service(confirmed, "같은 가입 화면이에요.", registered_seed=True, db_path=db)
    assert follow["incident_id"] == confirmed["incident_id"] and follow["revision"] == 4
    assert follow["persistence"]["status"] == "SAVED" and follow["correlation"] == "EXACT_ID"
    with IncidentStore(db) as store:
        incident = store.get_incident(PROJECT, confirmed["incident_id"])
        assert [run["revision"] for run in incident["runs"]] == [1, 2, 3, 4]
        assert store.get_card(PROJECT, prepared["job"]["result_run_id"])["review"]["status"] == "PENDING"
    policy, _, _ = registered_seed()
    assert read_source(policy, workspace)["client.py"].find(b'"user_id"') != -1


def test_multiple_actions_are_offered_without_choosing_for_the_user(service):
    _, db = service
    first, second = capture_seed_action(), capture_seed_action()
    result = current_report(db)
    assert result["route"] == "REQUEST_CONTEXT" and result["trace_id"] is None
    assert set(result["candidate_trace_ids"]) == {x["event"]["trace"]["trace_id"] for x in (first, second)}
    selected = confirm_candidate(result, second["event"]["trace"]["trace_id"], registered_seed=True, db_path=db)
    assert selected["trace_id"] == second["event"]["trace"]["trace_id"] and selected["route"] == "WORK_CANDIDATE"


def test_invented_or_stale_candidate_selection_is_rejected(service):
    _, db = service
    captured = capture_seed_action()
    previous = current_report(db)
    with pytest.raises(ValueError):
        confirm_candidate(previous, "invented", registered_seed=True, db_path=db)
    follow_up_service(previous, "같은 가입 화면이에요.", registered_seed=True, db_path=db)
    with pytest.raises(ValueError):
        confirm_candidate(previous, captured["event"]["trace"]["trace_id"], registered_seed=True, db_path=db)


def test_restart_after_prepared_change_preserves_context_and_reply_revision(service):
    _, db = service
    confirmed = confirmed_report(db)
    prepare_submission(confirmed, db_path=db, proposer=Proposal())
    with IncidentStore(db) as store:
        restored = store.resume_result(PROJECT, confirmed["incident_id"])
    follow = follow_up_service(restored, "같은 가입 화면이에요.", registered_seed=True, db_path=db)
    assert follow["revision"] == 4 and follow["incident_id"] == confirmed["incident_id"]
    assert follow["correlation"] == "EXACT_ID" and follow["persistence"]["status"] == "SAVED"
    assert not follow["fix_applied"] and not follow["fix_verified"]


@pytest.mark.parametrize("text", ["방금 가입이 안 돼요. 조사만 해줘.", "방금 가입이 안 돼요. 수정하지 마."])
def test_read_only_request_never_auto_prepares_a_change(service, monkeypatch, text):
    _, db = service
    captured = capture_seed_action()
    previous = current_report(db, text)
    result = confirm_candidate(previous, captured["event"]["trace"]["trace_id"], registered_seed=True, db_path=db)
    monkeypatch.setattr(report_service, "prepare_change", lambda *a, **k: pytest.fail("Read-only report cannot prepare"))
    assert auto_prepare_submission(result, enabled=True, live=True, db_path=db) is None


def test_follow_up_does_not_auto_repeat_a_waiting_change(service, monkeypatch):
    _, db = service
    confirmed = confirmed_report(db)
    prepare_submission(confirmed, db_path=db, proposer=Proposal())
    follow = follow_up_service(confirmed, "같은 가입 화면이에요.", registered_seed=True, db_path=db)
    monkeypatch.setattr(report_service, "prepare_change", lambda *a, **k: pytest.fail("No implicit second work attempt"))
    assert auto_prepare_submission(follow, enabled=True, live=True, db_path=db) is None


def test_read_only_preference_survives_restart_and_explicit_follow_up_can_change_it(service, monkeypatch):
    _, db = service
    captured = capture_seed_action()
    initial = current_report(db, "방금 가입 실패. 조사만 해줘.")
    confirmed = confirm_candidate(initial, captured["event"]["trace"]["trace_id"], registered_seed=True, db_path=db)
    with IncidentStore(db) as store:
        restored = store.resume_result(PROJECT, confirmed["incident_id"])
    follow = follow_up_service(restored, "같은 화면이에요.", registered_seed=True, db_path=db)
    assert follow["requested_action"] == "INVESTIGATE_ONLY"
    requests = []
    monkeypatch.setattr(report_service, "prepare_change", lambda *a, **k: requests.append(a) or {"requested": True})
    assert auto_prepare_submission(follow, enabled=True, live=True, db_path=db) is None and not requests
    allowed = follow_up_service(follow, "이제 허용된 수정안을 준비해서 고쳐줘.", registered_seed=True, db_path=db)
    assert allowed["requested_action"] == "PREPARE_ALLOWED"
    assert auto_prepare_submission(allowed, enabled=True, live=True, db_path=db) == {"requested": True}
    assert len(requests) == 1


def test_known_candidate_needs_selection_instead_of_an_unnecessary_model_call(service, monkeypatch):
    _, db = service
    capture_seed_action()
    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("test-key", report_agent.DEFAULT_MODEL))
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: pytest.fail("Only identity confirmation is missing"))))
    result = investigate_submission("방금 가입이 안 돼요. 고쳐줘.", registered_seed=True, use_nvidia=True, client=client, db_path=db)
    assert result["correlation"] == "CONTEXT_CANDIDATE" and result["route"] == "INVESTIGATE"
    assert result["model_calls"] == 0 and result["questions"]


def test_current_rule_observations_can_support_a_hypothesis_without_becoming_runtime_logs(service):
    _, db = service
    result = confirmed_report(db)
    tools = report_agent.ProjectTools(ROOT / "tests/fixtures/agolive_repo", "관측 확인", log_file=None,
                                     provided_logs="", include_docker=False, since_minutes=30)
    proposal = report_agent.FinalArguments(intent="investigate", symptom_summary="관측 확인", cause="요청 키 불일치",
        explanation="현재 관측에 요청 키와 계약이 다릅니다.", supporting_evidence_ids=[result["observations"][0]["id"], "invented"],
        verification_step="같은 입력 검사", possible_fix="요청 키 확인").conclusion()
    checked = report_agent._checked_current_hypotheses(proposal, tools, result)
    assert checked[0]["supporting_evidence_ids"] == [result["observations"][0]["id"]]
    assert checked[0]["status"] == "OBSERVATION_CANDIDATE" and checked[0]["cause_confirmed"] is False


def test_captured_observation_must_match_its_check_artifact(service):
    workspace, _ = service
    record = capture_seed_action()
    event_file = workspace / seed_project.OBSERVATIONS / record["capture"]["execution_id"] / "event.json"
    record["event"]["trace"]["response_status"] = 500
    event_file.write_text(json.dumps(record), encoding="utf-8")
    with pytest.raises(PolicyDenied):
        seed_catalog()


def test_other_project_configuration_cannot_enter_registered_seed_context(service, monkeypatch):
    _, db = service
    monkeypatch.setenv("TRACEBRIDGE_EVENTS_FILE", "must-not-be-read.json")
    monkeypatch.setenv("TRACEBRIDGE_LOG_FILE", "must-not-be-read.log")
    monkeypatch.setenv("TRACEBRIDGE_DEPLOYED_SHA", "a" * 40)
    result = current_report(db, "가입이 안 돼요.")
    assert result["project_id"] == PROJECT and result["version_provenance"]["configured"]["sha"] is None
    with pytest.raises(ValueError):
        investigate_submission("가입 오류", registered_seed=True, repo=ROOT / "tests/fixtures/agolive_repo", db_path=db)


def test_photo_time_and_action_can_find_a_candidate_without_a_request_id(service):
    _, db = service
    capture_seed_action()
    stream = BytesIO()
    Image.new("RGB", (100, 100), "white").save(stream, "PNG")

    class OCR:
        def extract(self, raw, *, deadline=None):
            return {"service": "NeMo Retriever OCR NIM", "status": "success", "usable_text": "방금 가입 실패",
                    "lines": [{"text": "방금 가입 실패", "confidence": 0.99}]}

    result = investigate_submission(image=stream.getvalue(), registered_seed=True, use_nvidia=True,
                                    ocr=OCR(), client=finish_client(), db_path=db)
    assert result["correlation"] == "CONTEXT_CANDIDATE" and result["observed_status"] == 422
    assert result["report_clues"] and all(item["kind"] != "screenshot" for item in result["observations"])


@pytest.mark.parametrize("text", ["가입이 안 돼요", "계정을 만들다 실패했어요", "회원 가입 버튼 오류"])
def test_ordinary_action_phrases_only_match_registered_operations(text):
    assert mentioned_operations(text, {"씨드 회원가입", "방 입장"}) == ["씨드 회원가입"]
    assert mentioned_operations(text, {"방 입장"}) == []


def test_fractional_iso_keeps_timezone_and_now_requires_an_occurrence_phrase():
    context = ReportContext().with_answer("발생 2026-09-28T11:00:00.123456+00:00")
    assert context.occurred_at == "2026-09-28T11:00:00.123456+00:00"
    assert ReportContext().with_answer("지금 고쳐줘", received_at="2026-09-28T11:00:00Z").occurred_at is None
    assert ReportContext().with_answer("방금 가입 실패", received_at="2026-09-28T11:00:00Z").occurred_at == "2026-09-28T20:00:00+09:00"


def finish_client():
    final = {"intent": "fix_request", "symptom_summary": "가입 동작 실패", "cause": "", "explanation": "",
             "supporting_evidence_ids": [], "contradicting_evidence_ids": [], "verification_step": "", "possible_fix": "",
             "missing_information": [], "next_steps": [], "previous_hypothesis_outcome": "not_rechecked", "counter_evidence_ids": []}
    call = SimpleNamespace(id="finish", function=SimpleNamespace(name="finish_investigation", arguments=json.dumps(final)))
    response = SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call], content=None))])
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response)))


def test_serving_error_keeps_observations_and_allows_only_a_summary_fallback(tmp_path, monkeypatch):
    from tracebridge.report_intake import LocalEventCatalog

    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("test-key", report_agent.DEFAULT_MODEL))
    event = {"trace": {"trace_id": "server-one", "occurred_at": "2026-09-28T10:00:00+09:00", "environment": "dev",
                       "service": "backend", "response_status": 500}, "logs": ["TimeoutException"]}
    calls = []
    class ServingError(RuntimeError):
        status_code = 500
    def create(**kwargs):
        calls.append(deepcopy(kwargs))
        if len(calls) == 1:
            assert "get_version" not in [tool["function"]["name"] for tool in kwargs["tools"]]
            call = SimpleNamespace(id="search", function=SimpleNamespace(name="search_code", arguments='{"terms":["TimeoutException"]}'))
            return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]))])
        if len(calls) == 2:
            assistant = next(message for message in kwargs["messages"] if message["role"] == "assistant")
            tool = next(message for message in kwargs["messages"] if message["role"] == "tool")
            assert assistant["content"] == "" and tool["name"] == "search_code"
            raise ServingError("Provider body must not be copied")
        assert len(calls) == 3
        assert kwargs["tool_choice"]["function"]["name"] == "finish_investigation"
        assert len(kwargs["messages"]) == 2 and "현재 관측" in kwargs["messages"][1]["content"]
        schema = kwargs["tools"][0]["function"]["parameters"]
        assert set(schema["required"]) == set(schema["properties"])
        assert all("default" not in value for value in schema["properties"].values())
        return finish_client().chat.completions.create()
    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = investigate_submission("먹통이에요", repo=ROOT / "tests/fixtures/agolive_repo",
        catalog=LocalEventCatalog({"project_id": "readiness-test", "events": [event]}), context=ReportContext(trace_id="server-one"),
        use_nvidia=True, client=client, db_path=tmp_path / "state.sqlite3")
    assert len(calls) == result["model_calls"] == 3 and result["observations"]
    assert result["run_status"] == "PARTIAL_FAILURE" and not result["cause_confirmed"] and not result["fix_verified"]
    assert result["model_trace"][1]["http_status"] == 500 and "Provider body" not in str(result)


def test_failing_summary_fallback_is_not_retried_again(tmp_path, monkeypatch):
    from tracebridge.report_intake import LocalEventCatalog

    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("test-key", report_agent.DEFAULT_MODEL))
    calls = []
    class ServingError(RuntimeError):
        status_code = 500
    def fail(**kwargs):
        calls.append(kwargs)
        raise ServingError()
    event = {"trace": {"trace_id": "server-one", "occurred_at": "2026-09-28T10:00:00+09:00", "environment": "dev",
                       "service": "backend", "response_status": 500}, "logs": ["TimeoutException"]}
    result = investigate_submission("먹통이에요", repo=ROOT / "tests/fixtures/agolive_repo",
        catalog=LocalEventCatalog({"project_id": "readiness-test", "events": [event]}), context=ReportContext(trace_id="server-one"),
        use_nvidia=True, client=SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fail))), db_path=tmp_path / "state.sqlite3")
    assert len(calls) == result["model_calls"] == 2 and result["run_status"] == "PARTIAL_FAILURE"
    assert calls[-1]["tool_choice"]["function"]["name"] == "finish_investigation"


def test_gui_rough_intake_confirmation_change_reply_and_review(service, monkeypatch):
    from streamlit.testing.v1 import AppTest

    _, db = service
    original, worker = report_agent.investigate_submission, report_service.prepare_change
    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("test-key", report_agent.DEFAULT_MODEL))
    monkeypatch.setattr(report_agent, "investigate_submission", lambda *a, **k: original(*a, client=finish_client(), **k))
    proposal = Proposal()
    monkeypatch.setattr(report_service, "prepare_change", lambda *a, **k: worker(*a, **{**k, "proposer": proposal}))
    page = AppTest.from_file(str(ROOT / "pages/2_Report_Agent.py"), default_timeout=20).run()
    page.checkbox(key="use_nvidia_analysis").set_value(True).run()
    page.checkbox(key="automatic_seed_change").set_value(True).run()
    page.button(key="capture_seed_action").click().run()
    page.text_area[0].set_value("방금 가입이 안 돼요. 알아보고 고쳐줘.")
    page.button(key="start_report").click().run()
    assert not page.exception and page.session_state["report_agent_result"]["correlation"] == "CONTEXT_CANDIDATE"
    assert page.session_state["report_agent_change"] is None
    page.button(key="confirm_report_candidate").click().run()
    assert not page.exception
    result, prepared = page.session_state["report_agent_result"], page.session_state["report_agent_change"]
    assert prepared["job"]["status"] == "CHANGE_PREPARED" and prepared["persistence"]["status"] == "SAVED"
    assert result["incident_id"] == prepared["job"]["incident_id"] and proposal.calls == 1
    page.text_area(key="report_follow_up").set_value("같은 가입 화면이에요.")
    page.button(key="reply_report").click().run()
    assert not page.exception and page.session_state["report_agent_result"]["revision"] == 4 and proposal.calls == 1
    page.selectbox(key="memory_review_action").set_value("승인")
    page.button(key="review_memory_card").click().run()
    assert not page.exception
    with IncidentStore(db) as store:
        assert store.get_card(PROJECT, prepared["job"]["result_run_id"])["review"]["status"] == "APPROVED"
    assert not page.session_state["report_agent_result"]["fix_applied"]


def test_gui_project_change_clears_the_previous_incident(service, monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("TRACEBRIDGE_AGOLIVE_REPO", str(ROOT / "tests/fixtures/agolive_repo"))
    page = AppTest.from_file(str(ROOT / "pages/2_Report_Agent.py")).run()
    page.text_area[0].set_value("가입이 안 돼요")
    page.button(key="start_report").click().run()
    assert page.session_state["report_agent_result"]
    page.selectbox(key="report_project").set_value("Agolive").run()
    assert not page.exception and page.session_state["report_agent_result"] is None and page.session_state["report_agent_change"] is None
    assert page.checkbox(key="use_nvidia_analysis").value is False and page.checkbox(key="automatic_seed_change").value is False
