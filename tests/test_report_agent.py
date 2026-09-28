from io import BytesIO
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest

from tracebridge import report_agent
from tracebridge.report_agent import ProjectTools, investigate_submission
from tracebridge.nemo_ocr import NemoRetrieverOCR


REPO = Path(__file__).resolve().parent / "fixtures" / "agolive_repo"
LOG = Path(__file__).resolve().parents[1] / "examples" / "agolive_error.log"


def image_bytes():
    stream = BytesIO()
    Image.new("RGB", (100, 100), "white").save(stream, format="PNG")
    return stream.getvalue()


def response(content=None, tool_calls=None):
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])


def tool(name, arguments, id_):
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def conclusion(support=None, opposing=None):
    return json.dumps({
        "intent": "investigate", "symptom_summary": "방 입장 실패",
        "hypotheses": [{
            "cause": "정원 검사", "explanation": "정원 검사에서 요청을 거절했을 수 있습니다",
            "supporting_evidence_ids": support or [], "contradicting_evidence_ids": opposing or [],
            "verification_step": "같은 정원에서 재현", "possible_fix": "정원 계산 확인",
        }], "missing_information": [], "next_steps": ["배포 버전과 실제 정원을 확인하세요"],
    }, ensure_ascii=False)


class FakeOCR:
    def extract(self, raw, *, deadline=None):
        return {
            "service": "NeMo Retriever OCR NIM", "model": "test-ocr", "status": "success",
            "lines": [{"id": "I1", "text": "ROOM_FULL requestId=abc12345", "confidence": 0.99}],
            "usable_text": "ROOM_FULL requestId=abc12345",
        }


def test_image_only_agent_selects_tools_across_rounds_and_checks_citations(monkeypatch):
    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("test-key", "test-model"))
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return response(tool_calls=[tool("search_code", {"terms": ["ROOM_FULL"], "services": ["realtime"]}, "one")])
            if len(calls) == 2:
                assert any(message.get("role") == "tool" for message in kwargs["messages"])
                return response(tool_calls=[tool("find_logs", {"terms": ["ROOM_FULL"]}, "two"), tool("get_version", {}, "three")])
            final = json.loads(conclusion(["L1", "C1", "invented"]))
            final["missing_information"] = ["실시간 서버 부하 현황", "당시 방의 사용자 수"]
            hypothesis = final.pop("hypotheses")[0]
            return response(tool_calls=[tool("finish_investigation", {**final, **hypothesis}, "final")])

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    result = investigate_submission(image=image_bytes(), repo=REPO, log_file=LOG, use_nvidia=True, client=client, ocr=FakeOCR())
    assert result["input_modes"] == ["image"]
    assert result["route"] == "REQUEST_CONTEXT"
    assert [step["tool"] for step in result["steps"] if step["phase"] == "investigation"] == ["search_code", "find_logs", "get_version"]
    assert result["hypotheses"][0]["supporting_evidence_ids"] == ["L1", "C1"]
    assert result["hypotheses"][0]["status"] == "LOG_CANDIDATE"
    assert not any(item.get("correlated") for item in result["evidence"] if item["kind"] == "log")
    assert result["model_calls"] == 3
    assert not result["fix_applied"] and not result["deployment_observed"]
    assert all("서버" not in question and "부하" not in question for question in result["questions"])
    assert any("몇 명" in question for question in result["questions"])


def test_model_cannot_create_log_correlation_by_inventing_id_or_run_shell():
    tools = ProjectTools(REPO, "방 입장이 안돼요", log_file=LOG, provided_logs="", include_docker=False, since_minutes=30)
    output = tools.call("find_logs", json.dumps({"terms": ["ROOM_FULL", "requestId=abc12345"]}))
    assert output["evidence"]
    assert not any(item["correlated"] for item in output["evidence"])
    with pytest.raises(ValueError):
        tools.call("run_shell", '{"command": "read .env"}')
    with pytest.raises(ValueError):
        tools.call("search_code", '{"terms": ["ROOM_FULL"], "path": ".env"}')


def test_ocr_label_correction_never_changes_identifier_values():
    assert report_agent._ocr_labels("requestld=abc1lI45 trace1d=one1") == "requestId=abc1lI45 traceId=one1"


def test_rough_text_and_image_without_external_inference_ask_plain_question(monkeypatch):
    def forbidden():
        raise AssertionError("Offline intake must not read model keys")

    monkeypatch.setattr(report_agent, "nvidia_settings", forbidden)
    for submission in ({"text": "방에 들어가면 자꾸 튕겨요"}, {"image": image_bytes()}):
        result = investigate_submission(repo=REPO, **submission)
        assert result["route"] == "REQUEST_CONTEXT"
        assert result["model_calls"] == 0
        assert result["questions"]
        assert all("trace" not in question and "HTTP" not in question for question in result["questions"])


def test_photo_without_text_uses_visual_observation_and_keeps_cause_unverified(monkeypatch):
    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("test-key", "test-model"))

    class EmptyOCR:
        def extract(self, raw, *, deadline=None):
            return {"service": "NeMo Retriever OCR NIM", "status": "success", "lines": [], "usable_text": ""}

    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return response(tool_calls=[tool("describe_screen", {"visible_symptom": "빈 화면에 로딩 표시만 보입니다", "has_app_screen": True}, "photo")])
            return response(conclusion(["I-visual"]))

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    result = investigate_submission(image=image_bytes(), repo=REPO, use_nvidia=True, client=client, ocr=EmptyOCR())
    assert calls[0]["model"] == report_agent.VISION_MODEL
    assert result["hypotheses"] == []
    assert result["route"] == "REQUEST_CONTEXT"
    assert any(item["id"] == "I-visual" for item in result["evidence"])


def test_non_app_photo_and_invalid_image_do_not_invent_a_report(monkeypatch):
    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("test-key", "test-model"))

    class EmptyOCR:
        def extract(self, raw, *, deadline=None):
            return {"service": "NeMo Retriever OCR NIM", "status": "success", "lines": [], "usable_text": ""}

    class Completions:
        def create(self, **kwargs):
            return response('{"visible_symptom": "풍경 사진", "has_app_screen": false}')

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    result = investigate_submission(image=image_bytes(), repo=REPO, use_nvidia=True, client=client, ocr=EmptyOCR())
    assert result["route"] == "REQUEST_CONTEXT" and result["hypotheses"] == []
    assert result["steps"] == []
    with pytest.raises(ValueError, match="사진 파일"):
        investigate_submission(image=b"not an image", repo=REPO, use_nvidia=True, client=client)


def test_report_page_accepts_a_rough_symptom_in_offline_mode(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("TRACEBRIDGE_AGOLIVE_REPO", str(REPO))
    page = AppTest.from_file(str(REPO.parents[2] / "pages" / "2_Report_Agent.py")).run()
    assert not page.exception
    page.text_area[0].set_value("방에 들어가면 자꾸 튕겨요")
    page.button[0].click().run()
    assert not page.exception
    assert page.session_state["report_agent_result"]["questions"]


@pytest.mark.parametrize("batch_size, expected_model_calls", [(1, 4), (7, 2)])
def test_agent_enforces_read_and_model_budgets_and_forces_a_final_result(monkeypatch, batch_size, expected_model_calls):
    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("test-key", "test-model"))
    calls = []

    class Completions:
        def create(self, **kwargs):
            calls.append(kwargs)
            if len(kwargs["tools"]) == 1:
                assert kwargs["tool_choice"]["function"]["name"] == "finish_investigation"
                return response(tool_calls=[tool("finish_investigation", {
                    "intent": "investigate", "symptom_summary": "입장 실패", "cause": "",
                }, "final")])
            return response(tool_calls=[tool("get_version", {}, f"version-{len(calls)}-{index}") for index in range(batch_size)])

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    result = investigate_submission("방에 못 들어가요", repo=REPO, use_nvidia=True, client=client)

    assert result["model_calls"] == expected_model_calls <= report_agent.MAX_MODEL_CALLS
    assert sum(step["status"] == "success" for step in result["steps"]) <= report_agent.MAX_TOOL_CALLS
    assert len([item for item in result["evidence"] if item["kind"] == "version"]) == 1
    assert result["hypotheses"] == [] and not result["fix_applied"]
    if batch_size == 7:
        assert result["steps"][-1]["status"] == "rejected"


def test_model_timeout_preserves_observations_without_a_cause_or_success_claim(monkeypatch):
    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("test-key", "test-model"))
    count = 0

    class Completions:
        def create(self, **kwargs):
            nonlocal count
            count += 1
            if count == 1:
                return response(tool_calls=[
                    tool("search_code", {"terms": ["ROOM_FULL"]}, "code"),
                    tool("find_logs", {}, "logs"),
                ])
            raise TimeoutError("private model response must not be echoed")

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    result = investigate_submission("ROOM_FULL requestId=abc12345", repo=REPO, log_file=LOG, use_nvidia=True, client=client)

    assert {item["kind"] for item in result["evidence"]} >= {"code", "log"}
    assert result["model_trace"][-1]["status"] == "TimeoutError"
    assert result["model_calls"] == 2 and result["hypotheses"] == []
    assert not result["fix_applied"] and not result["deployment_observed"]
    assert "private model response" not in str(result)


def test_malformed_ocr_and_failed_vision_preserve_the_supplied_text(monkeypatch):
    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("test-key", "test-model"))
    model_count = 0

    def handler(request):
        return httpx.Response(200, json={"data": [{"text_detections": [{"text_prediction": None}]}]})

    class Completions:
        def create(self, **kwargs):
            nonlocal model_count
            if kwargs["model"] == report_agent.VISION_MODEL:
                raise TimeoutError()
            model_count += 1
            assert "ROOM_FULL requestId=abc12345" in str(kwargs["messages"])
            if model_count == 1:
                return response(tool_calls=[tool("search_code", {"terms": ["ROOM_FULL"]}, "code")])
            if model_count == 2:
                return response(tool_calls=[tool("find_logs", {}, "logs")])
            return response(conclusion(["C1", "L1"]))

    client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        result = investigate_submission(
            "ROOM_FULL requestId=abc12345", image=image_bytes(), repo=REPO, log_file=LOG,
            use_nvidia=True, client=client, ocr=NemoRetrieverOCR("test-key", client=transport),
        )
    assert result["input_modes"] == ["text", "image"]
    assert result["hypotheses"][0]["status"] == "LOG_CANDIDATE"
    assert result["run_status"] == "TIMED_OUT" and "vision" in result["timeout_reasons"]
    assert result["service_calls"][1]["status"] == "failed"
    assert result["model_calls"] == report_agent.MAX_MODEL_CALLS
