from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from PIL import Image
import pytest

from tracebridge import report_agent, project_sources
from tracebridge.report_agent import follow_up_submission, investigate_submission
from tracebridge.report_contract import ReportContext
from tracebridge.report_intake import LocalEventCatalog


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "tests" / "fixtures" / "agolive_repo"
EVENTS = ROOT / "examples" / "report_events.json"
LOGS = ROOT / "examples" / "scoped_agolive.log"


def image_bytes():
    stream = BytesIO()
    Image.new("RGB", (64, 64), "white").save(stream, "PNG")
    return stream.getvalue()


def message(*, calls=None, final=None):
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(final) if final else None, tool_calls=calls))])


def tool(name, arguments, id_="one"):
    return SimpleNamespace(id=id_, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def final(support=None, **extra):
    return {"intent": "investigate", "symptom_summary": "방 입장 실패", "cause": "정원 검사", "explanation": "정원 검사에서 거절했을 가능성을 확인합니다", "supporting_evidence_ids": support or [], "verification_step": "같은 동작의 관측을 확인", "possible_fix": "정원 계산을 검토", **extra}


def client_for(*responses):
    iterator = iter(responses)

    class Completions:
        def create(self, **kwargs):
            value = next(iterator)
            if isinstance(value, Exception):
                raise value
            return value

    return SimpleNamespace(chat=SimpleNamespace(completions=Completions()))


@pytest.fixture(autouse=True)
def isolated_connections(monkeypatch):
    for name in ("TRACEBRIDGE_EVENTS_FILE", "TRACEBRIDGE_LOG_FILE", "TRACEBRIDGE_LOG_SERVICE", "TRACEBRIDGE_LOG_ENVIRONMENT", "TRACEBRIDGE_LOG_TIMEZONE", "TRACEBRIDGE_DEPLOYED_SHA"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.parametrize("trace_id,route", [("claim-003", "GUIDANCE"), ("contract-001", "WORK_CANDIDATE"), ("migration-002", "WORK_CANDIDATE")])
def test_new_intake_reuses_checked_rules_without_loading_or_calling_a_model(monkeypatch, trace_id, route):
    def forbidden():
        raise AssertionError("A clear event does not need model settings")

    monkeypatch.setattr(report_agent, "nvidia_settings", forbidden)
    result = investigate_submission(f"500 오류를 고쳐줘 requestId={trace_id}", repo=REPO, catalog=LocalEventCatalog.from_file(EVENTS), use_nvidia=True)
    assert result["route"] == route
    assert result["project_id"] == "tracebridge-demo"
    assert result["correlation"] == "EXACT_ID"
    assert result["model_calls"] == 0 and result["run_status"] == "COMPLETED"
    assert result["observations"]
    if trace_id != "migration-002":
        assert result["claim_status"] == "CONTRADICTED"
    assert not result["fix_applied"] and not result["cause_confirmed"]


@pytest.mark.parametrize("trace_id,clock,route,status", [("intake-guidance", "10:00", "GUIDANCE", 422), ("intake-contract", "10:10", "WORK_CANDIDATE", 422), ("intake-server", "10:20", "INVESTIGATE", 500)])
def test_registered_log_is_the_same_routing_path(trace_id, clock, route, status):
    result = investigate_submission(f"500 오류 requestId={trace_id}", repo=REPO, log_file=LOGS, context=ReportContext(environment="dev", service="backend", occurred_at=f"2026-09-28T{clock}:00+09:00"))
    assert result["route"] == route and result["observed_status"] == status
    assert result["log_scope"]["connected"]
    assert result["log_scope"]["sources"][0]["counts"]["matched"] == 1
    assert any(e["kind"] == "log" and e["scope_status"] == "VERIFIED" for e in result["observations"])
    assert not result["deployment_observed"] and result["model_calls"] == 0
    if route == "INVESTIGATE":
        assert result["diagnosis_type"] == "backend_exception_unconfirmed"


def test_zero_and_multiple_candidates_wait_even_if_model_proposes_a_cause(monkeypatch):
    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("test-key", "test-model"))
    for occurred, expected in (("2026-09-28T10:01:00+09:00", {"claim-003", "contract-001"}), ("2026-09-28T12:00:00+09:00", set())):
        result = investigate_submission("회원가입에서 500 오류", repo=REPO, catalog=LocalEventCatalog.from_file(EVENTS), context=ReportContext(environment="dev", occurred_at=occurred), use_nvidia=True, client=client_for(message(final=final(["invented"], missing_information=["커밋 해시를 알려주세요", "비밀번호를 알려주세요"])) ))
        assert result["route"] == "REQUEST_CONTEXT"
        assert set(result["candidate_trace_ids"]) == expected
        assert result["hypotheses"] == [] and result["observations"] == []
        assert all(not any(word in question for word in ("커밋", "해시", "비밀번호")) for question in result["questions"])


def test_photo_and_plain_follow_up_recheck_one_incident_without_repeating_ocr():
    first = investigate_submission(image=image_bytes(), repo=REPO, log_file=LOGS)
    second = follow_up_submission(first, "개발 환경에서 방 입장 버튼을 눌렀고 ROOM_FULL이 떴어요. 2026-09-28 오전 10시 30분쯤이에요.", repo=REPO, log_file=LOGS)
    assert first["route"] == "REQUEST_CONTEXT"
    assert second["incident_id"] == first["incident_id"] and second["revision"] == 2
    assert second["correlation"] == "CONTEXT_CANDIDATE" and second["route"] == "INVESTIGATE"
    assert second["observed_status"] == 409 and second["service_calls"] == []
    assert second["input_modes"] == ["image", "text"] and second["history"]
    assert all(not any(word in q for word in ("HTTP", "trace", "SHA", "SQL", "API")) for q in second["questions"])
    third = follow_up_submission(second, "화면에 requestId=abc12345라고 적혀 있어요.", repo=REPO, log_file=LOGS)
    assert third["incident_id"] == first["incident_id"] and third["correlation"] == "EXACT_ID"


def test_photo_id_is_only_an_intake_clue_and_rules_need_real_observations(monkeypatch):
    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("test-key", "test-model"))

    class OCR:
        def extract(self, raw, *, deadline=None):
            return {"service": "NeMo Retriever OCR NIM", "status": "success", "usable_text": "500 requestId=claim-003", "lines": [{"id": "I1", "text": "500 requestId=claim-003", "confidence": 0.99}]}

    result = investigate_submission(image=image_bytes(), repo=REPO, catalog=LocalEventCatalog.from_file(EVENTS), use_nvidia=True, ocr=OCR(), client=client_for())
    assert result["route"] == "GUIDANCE" and result["observed_status"] == 422
    assert result["model_calls"] == 0 and result["service_calls"][0]["phase"] == "input_processing"
    assert result["report_clues"] and all(item["kind"] != "screenshot" for item in result["observations"])
    follow = follow_up_submission(result, "같은 회원가입 화면이에요.", repo=REPO, catalog=LocalEventCatalog.from_file(EVENTS))
    assert follow["correlation"] == "EXACT_ID" and not follow["service_calls"]


@pytest.mark.parametrize("field,value", [("environment", "prod"), ("service", "realtime"), ("occurred_at", "2026-09-27T10:20:00+09:00")])
def test_wrong_scope_is_excluded_even_with_the_same_id(tmp_path, field, value):
    record = json.loads(LOGS.read_text(encoding="utf-8").splitlines()[2])
    record["trace"][field] = value
    path = tmp_path / "wrong.log"
    path.write_text(json.dumps(record), encoding="utf-8")
    result = investigate_submission("주문이 안 돼요 requestId=intake-server", repo=REPO, log_file=path, context=ReportContext(service="backend", environment="dev", occurred_at="2026-09-28T10:20:00+09:00"))
    assert result["route"] == "REQUEST_CONTEXT" and not result["observations"]
    assert result["log_scope"]["sources"][0]["counts"]["excluded"] == 1
    assert not any(item["kind"] == "log" for item in result["evidence"])


def test_missing_fields_and_body_id_are_never_exact_correlations(tmp_path):
    path = tmp_path / "unknown.log"
    path.write_text('ERROR requestId=abc12345 ROOM_FULL\n{"timestamp":"2026-09-28T10:30:00+09:00","environment":"dev","service":"realtime","message":"ERROR requestId=abc12345 ROOM_FULL"}', encoding="utf-8")
    result = investigate_submission("ROOM_FULL requestId=abc12345", repo=REPO, log_file=path, context=ReportContext(service="realtime", environment="dev", occurred_at="2026-09-28T10:30:00+09:00"))
    logs = [e for e in result["evidence"] if e["kind"] == "log"]
    assert len(logs) == 1 and logs[0]["scope_status"] == "UNVERIFIED"
    assert logs[0]["scope_checks"]["time"] == "NOT_OBSERVED"
    assert not logs[0]["correlated"] and result["route"] == "REQUEST_CONTEXT"


def test_registration_can_scope_mdc_but_missing_status_stays_unobserved(tmp_path):
    path = tmp_path / "console.log"
    path.write_text("2026-09-28 10:30:00.000 ERROR [abc12345] [192.0.2.1] ws - ROOM_FULL\n    at handler.joinRoom(ws.go:2)", encoding="utf-8")
    result = investigate_submission("ROOM_FULL requestId=abc12345", repo=REPO, log_file=path, context=ReportContext(occurred_at="2026-09-28T10:30:00+09:00"), registered_log_scope={"service": "realtime", "environment": "dev", "timezone": "+09:00"})
    assert result["correlation"] == "EXACT_ID" and result["route"] == "INVESTIGATE"
    assert result["observed_status"] is None
    logs = [e for e in result["observations"] if e["kind"] == "log"]
    assert len(logs) == 2 and logs[0]["scope_checks"]["response_status"] == "NOT_OBSERVED"
    assert logs[0]["scope_checks"]["service"] == "REGISTERED_SOURCE"
    assert "192.0.2.1" not in str(result)


def test_manual_sha_is_not_a_running_deployment_observation(monkeypatch):
    monkeypatch.setenv("TRACEBRIDGE_DEPLOYED_SHA", "a" * 40)
    result = investigate_submission("방 입장이 안 돼요", repo=REPO)
    assert result["configured_deployed_revision"] == "a" * 40
    assert not result["deployment_observed"] and result["deployed_revision"] is None
    assert result["version_provenance"]["runtime"]["status"] == "NOT_OBSERVED"


@pytest.mark.parametrize("runtime_sha,status", [("a" * 40, "SUPPORTED_HYPOTHESIS"), ("b" * 40, "CONTESTED_HYPOTHESIS")])
def test_running_version_comparison_limits_code_hypotheses(monkeypatch, runtime_sha, status):
    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("test-key", "test-model"))
    monkeypatch.setattr(report_agent, "repository_revision", lambda repo, **kwargs: "a" * 12)
    monkeypatch.setattr(report_agent, "source_tree_dirty", lambda repo, **kwargs: False)
    monkeypatch.setattr(report_agent, "docker_compose_logs", lambda *a, **k: ("", None))
    monkeypatch.setattr(report_agent, "running_service_revision", lambda *a, **k: {"sha": runtime_sha, "source": "running-docker:test-label", "status": "OBSERVED"})
    result = investigate_submission("ROOM_FULL requestId=abc12345", repo=REPO, log_file=LOGS, include_docker_logs=True, context=ReportContext(environment="dev", service="realtime", occurred_at="2026-09-28T10:30:00+09:00"), use_nvidia=True, client=client_for(message(calls=[tool("search_code", {"terms": ["ROOM_FULL"]})]), message(calls=[tool("finish_investigation", final(["C1", "L1"]))])))
    assert result["deployment_observed"] and result["deployed_revision"] == runtime_sha
    assert result["hypotheses"][0]["status"] == status
    assert not result["cause_confirmed"] and not result["fix_verified"]
    if status == "SUPPORTED_HYPOTHESIS":
        assert result["route"] == "INVESTIGATE" and result["run_status"] == "COMPLETED"


def test_timeout_and_partial_failure_preserve_observed_server_error(monkeypatch, tmp_path):
    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("test-key", "test-model"))
    context = ReportContext(environment="dev", service="backend", occurred_at="2026-09-28T10:20:00+09:00")
    timed = investigate_submission("500 requestId=intake-server", repo=REPO, log_file=LOGS, context=context, use_nvidia=True, client=client_for(message(calls=[tool("search_code", {"terms": ["ROOM_FULL"]})]), TimeoutError("private response token=never-print")))
    assert timed["route"] == "INVESTIGATE" and timed["run_status"] == "TIMED_OUT"
    assert timed["observed_status"] == 500 and timed["observations"]
    assert not timed["hypotheses"] and "never-print" not in str(timed)
    event = json.loads((ROOT / "examples" / "backend_incident.json").read_text(encoding="utf-8"))
    event["trace"]["occurred_at"] = "2026-09-28T10:20:00+09:00"
    partial = investigate_submission("500 requestId=orders-staging-001", repo=REPO, catalog=LocalEventCatalog({"project_id": "agolive", "events": [event]}), log_file=tmp_path / "not-there.log")
    assert partial["route"] == "INVESTIGATE" and partial["run_status"] == "PARTIAL_FAILURE"
    assert partial["observed_status"] == 500 and partial["observations"]


def test_budget_exhaustion_is_distinct_from_routing_and_keeps_observations(monkeypatch):
    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("test-key", "test-model"))
    result = investigate_submission("500 requestId=intake-server", repo=REPO, log_file=LOGS, context=ReportContext(environment="dev", service="backend", occurred_at="2026-09-28T10:20:00+09:00"), use_nvidia=True, client=client_for(message(calls=[tool("get_version", {}, str(i)) for i in range(7)]), message(calls=[tool("finish_investigation", final(cause=""))])))
    assert result["route"] == "INVESTIGATE" and result["run_status"] == "BUDGET_EXHAUSTED"
    assert result["observed_status"] == 500 and result["observations"]
    assert sum(step["status"] == "success" for step in result["steps"]) <= report_agent.MAX_TOOL_CALLS


def test_follow_up_counter_evidence_retires_a_prior_hypothesis_and_keeps_history(monkeypatch, tmp_path):
    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("test-key", "test-model"))
    path = tmp_path / "incident.log"
    record = json.loads(LOGS.read_text(encoding="utf-8").splitlines()[3])
    path.write_text(json.dumps(record), encoding="utf-8")
    context = ReportContext(environment="dev", service="realtime", occurred_at="2026-09-28T10:30:00+09:00")
    first = investigate_submission("ROOM_FULL requestId=abc12345", repo=REPO, log_file=path, context=context, use_nvidia=True, client=client_for(message(calls=[tool("search_code", {"terms": ["ROOM_FULL"]})]), message(calls=[tool("finish_investigation", final(["L1", "C1"]))])))
    assert first["hypotheses"]
    record["trace"]["response_status"] = 200
    record["message"] = "capacity=4 occupancy=1; room join succeeded; previous failure was transient dependency"
    path.write_text(json.dumps(record), encoding="utf-8")
    second = follow_up_submission(first, "그 방은 한 명만 있었고 입장에 성공했어요. 같은 사건을 다시 확인해 주세요.", repo=REPO, log_file=path, use_nvidia=True, client=client_for(message(calls=[tool("finish_investigation", final(cause="", previous_hypothesis_outcome="rejected", counter_evidence_ids=["L1", "invented"]))])))
    assert first["incident_id"] == second["incident_id"]
    assert second["observed_status"] == 200 and second["hypotheses"] == []
    assert second["hypothesis_updates"][0]["status"] == "REJECTED"
    assert second["hypothesis_updates"][0]["counter_evidence_ids"] == ["L1"]
    assert second["history"][0]["hypotheses"] == first["hypotheses"]
    assert any("409" in item["content"] for item in second["history"][0]["observations"])
    assert not second["cause_confirmed"]


def test_same_id_with_conflicting_observations_is_held(tmp_path):
    first = json.loads(LOGS.read_text(encoding="utf-8").splitlines()[2])
    second = deepcopy(first)
    second["trace"]["path"] = "/different-request"
    path = tmp_path / "conflict.log"
    path.write_text("\n".join(json.dumps(record) for record in (first, second)), encoding="utf-8")
    result = investigate_submission("500 requestId=intake-server", repo=REPO, log_file=path, context=ReportContext(environment="dev", service="backend", occurred_at="2026-09-28T10:20:00+09:00"))
    assert result["route"] == "REQUEST_CONTEXT" and result["correlation"] == "NEEDS_CONTEXT"


@pytest.mark.parametrize("trace_id,route", [("claim-003", "GUIDANCE"), ("contract-001", "WORK_CANDIDATE"), ("unknown", "REQUEST_CONTEXT"), ("migration-002", "WORK_CANDIDATE")])
def test_page_uses_common_routes(monkeypatch, trace_id, route):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("TRACEBRIDGE_AGOLIVE_REPO", str(REPO))
    monkeypatch.setenv("TRACEBRIDGE_EVENTS_FILE", str(EVENTS))
    page = AppTest.from_file(str(ROOT / "pages" / "2_Report_Agent.py")).run()
    page.text_area[0].set_value(f"500 requestId={trace_id}")
    page.button[0].click().run()
    assert not page.exception and page.session_state["report_agent_result"]["route"] == route


def test_page_reply_is_the_same_incident(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest

    catalog = json.loads(EVENTS.read_text(encoding="utf-8"))
    catalog["events"] = [catalog["events"][1]]
    path = tmp_path / "one.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    monkeypatch.setenv("TRACEBRIDGE_AGOLIVE_REPO", str(REPO))
    monkeypatch.setenv("TRACEBRIDGE_EVENTS_FILE", str(path))
    page = AppTest.from_file(str(ROOT / "pages" / "2_Report_Agent.py")).run()
    page.text_area[0].set_value("가입이 안 돼요")
    page.button[0].click().run()
    incident = page.session_state["report_agent_result"]["incident_id"]
    page.text_area[1].set_value("개발 환경의 회원가입 화면에서 2026-09-28 오전 10시쯤 발생했어요.")
    page.button[1].click().run()
    result = page.session_state["report_agent_result"]
    assert not page.exception and result["incident_id"] == incident
    assert result["revision"] == 2 and result["correlation"] == "CONTEXT_CANDIDATE"


@pytest.mark.parametrize("trace_id,clock,route", [("intake-guidance", "10:00", "GUIDANCE"), ("intake-contract", "10:10", "WORK_CANDIDATE"), ("intake-server", "10:20", "INVESTIGATE")])
def test_cli_replays_the_same_registered_log_routes(trace_id, clock, route):
    process = subprocess.run([sys.executable, "-m", "scripts.investigate_report", "--repo", str(REPO), "--logs-file", str(LOGS), "--report", f"500 requestId={trace_id}", "--environment", "dev", "--service", "backend", "--occurred-at", f"2026-09-28T{clock}:00+09:00"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=20)
    assert process.returncode == 0, process.stderr
    result = json.loads(process.stdout)
    assert result["route"] == route and result["model_calls"] == 0


def test_visual_model_cannot_invent_an_exact_id_including_on_follow_up(monkeypatch):
    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("test-key", "test-model"))

    class EmptyOCR:
        def extract(self, raw, *, deadline=None):
            return {"service": "NeMo Retriever OCR NIM", "status": "success", "usable_text": "", "lines": []}

    context = ReportContext(environment="dev", service="realtime", occurred_at="2026-09-28T10:30:00+09:00")
    result = investigate_submission(image=image_bytes(), repo=REPO, log_file=LOGS, context=context, use_nvidia=True, ocr=EmptyOCR(), client=client_for(message(calls=[tool("describe_screen", {"visible_symptom": "ROOM_FULL requestId=abc12345", "has_app_screen": True})]), message(calls=[tool("finish_investigation", final(["L1"]))])))
    assert result["correlation"] == "CONTEXT_CANDIDATE"
    assert not any(item.get("correlated") for item in result["evidence"] if item["kind"] == "log")
    follow = follow_up_submission(result, "같은 방 입장 화면이에요.", repo=REPO, log_file=LOGS)
    assert follow["correlation"] == "CONTEXT_CANDIDATE" and follow["incident_id"] == result["incident_id"]


def test_docker_timestamp_mdc_and_stack_use_the_registered_scope():
    text = (
        "api-1 | 2026-09-28T01:20:00Z 2026-09-28 10:20:00.000 ERROR [abc12345] [192.0.2.1] app - IllegalStateException\n"
        "api-1 | 2026-09-28T01:20:00Z     at app.handle(Room.kt:2)\n"
        "realtime-1 | 2026-09-28T01:20:00Z     at unrelated.handle(ws.go:2)\n"
    )
    evidence, _, events, _ = project_sources.collect_scoped_logs(text, "requestId=abc12345", source="local-docker", scope=ReportContext(service="backend", environment="dev", occurred_at="2026-09-28T10:20:00+09:00"), source_metadata={"environment": "dev"})
    assert len(evidence) == 2 and len(events) == 2
    assert all(item.correlated for item in evidence)
    assert evidence[0].scope_checks["timezone"] == "DOCKER_TIMESTAMP"
    assert not any("unrelated" in item.content for item in evidence)


@pytest.mark.parametrize("inspect_output,observed", [('true "' + 'a' * 40 + '"', True), ('false "' + 'a' * 40 + '"', False), ('true ""', False)])
def test_runtime_revision_reads_only_a_running_container_label(monkeypatch, inspect_output, observed):
    commands = []

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout=("b" * 64 + "\n") if "compose" in command else inspect_output)

    monkeypatch.setattr(project_sources.subprocess, "run", run)
    version = project_sources.running_service_revision(REPO, service="agolive-api", environment="dev")
    assert (version["status"] == "OBSERVED") is observed
    assert len(commands) == 2 and all(".Config.Env" not in " ".join(cmd) for cmd in commands)
    if observed:
        assert version["source"].startswith("running-docker:api/") and version["sha"] == "a" * 40


def test_cut_log_tail_and_json_secrets_are_not_used_as_complete_records(tmp_path, monkeypatch):
    path = tmp_path / "tail.log"
    path.write_text("x" * 200 + " forged requestId=abc12345\nERROR actual tail\n", encoding="utf-8")
    monkeypatch.setattr(report_agent, "MAX_LOG_BYTES", 100)
    assert report_agent._log_tail(path).splitlines() == ["ERROR actual tail"]
    assert "private-value" not in project_sources.redact('{"token":"private-value","userId":123,"requestId":"abc12345"}')


def test_registered_source_rejects_a_record_declared_for_another_project(tmp_path):
    record = json.loads(LOGS.read_text(encoding="utf-8").splitlines()[2])
    record["project_id"] = "another-project"
    path = tmp_path / "another.log"
    path.write_text(json.dumps(record), encoding="utf-8")
    result = investigate_submission("500 requestId=intake-server", repo=REPO, log_file=path, context=ReportContext(environment="dev", service="backend", occurred_at="2026-09-28T10:20:00+09:00"))
    assert result["route"] == "REQUEST_CONTEXT" and not result["observations"]


def test_page_investigates_a_scoped_server_error(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("TRACEBRIDGE_AGOLIVE_REPO", str(REPO))
    monkeypatch.setenv("TRACEBRIDGE_LOG_FILE", str(LOGS))
    page = AppTest.from_file(str(ROOT / "pages" / "2_Report_Agent.py")).run()
    page.text_area[0].set_value("주문에서 500 requestId=intake-server")
    page.text_input[0].set_value("2026-09-28 오전 10시 20분")
    page.selectbox[0].set_value("개발 환경")
    page.button[0].click().run()
    result = page.session_state["report_agent_result"]
    assert not page.exception and result["route"] == "INVESTIGATE"
    assert result["observed_status"] == 500 and result["correlation"] == "EXACT_ID"
