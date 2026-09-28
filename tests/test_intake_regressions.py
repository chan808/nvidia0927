from copy import deepcopy
from io import BytesIO
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace

import httpx
from PIL import Image
import pytest

from tracebridge import project_sources, report_agent
from tracebridge.nemo_ocr import NemoRetrieverOCR
from tracebridge.report_agent import investigate_submission
from tracebridge.report_contract import ReportContext
from tracebridge.report_intake import LocalEventCatalog


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "tests" / "fixtures" / "agolive_repo"
RECORDS = [json.loads(line) for line in (ROOT / "examples" / "scoped_agolive.log").read_text(encoding="utf-8").splitlines()]
CATALOG_FILE = ROOT / "examples" / "report_events.json"


class Clock:
    value = 0.0

    def __call__(self):
        return self.value


def photo():
    buffer = BytesIO()
    Image.new("RGB", (32, 32), "white").save(buffer, "PNG")
    return buffer.getvalue()


def finish_client(callback=None):
    def create(**kwargs):
        if callback:
            callback(kwargs)
        call = SimpleNamespace(id="finish", function=SimpleNamespace(name="finish_investigation", arguments=json.dumps({"intent": "investigate", "symptom_summary": "접수 확인", "cause": ""})))
        return SimpleNamespace(usage=None, choices=[SimpleNamespace(message=SimpleNamespace(content=None, tool_calls=[call]))])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    for name in ("TRACEBRIDGE_EVENTS_FILE", "TRACEBRIDGE_LOG_FILE", "TRACEBRIDGE_LOG_SERVICE", "TRACEBRIDGE_LOG_ENVIRONMENT", "TRACEBRIDGE_LOG_TIMEZONE", "TRACEBRIDGE_DEPLOYED_SHA"):
        monkeypatch.delenv(name, raising=False)


def combined(tmp_path, first, second, *, copies=1):
    file = tmp_path / "registered.log"
    file.write_text("\n".join(json.dumps(first) for _ in range(copies)), encoding="utf-8")
    return investigate_submission(
        f"500 requestId={first['trace']['trace_id']}", repo=REPO, log_file=file,
        provided_logs=json.dumps(second), context=ReportContext(occurred_at=first["trace"]["occurred_at"]),
    )


@pytest.mark.parametrize("copies", [1, 100])
def test_conflicting_status_from_another_source_cannot_be_lost_at_the_combined_limit(tmp_path, copies):
    first = deepcopy(RECORDS[0])
    second = deepcopy(first)
    second["trace"]["response_status"] = 500
    result = combined(tmp_path, first, second, copies=copies)
    assert result["route"] == "REQUEST_CONTEXT" and result["correlation"] == "NEEDS_CONTEXT"
    assert result.get("observed_status") is None
    assert "관측" in result["route_reason"]
    aggregate = result["log_scope"]["aggregate"]
    assert aggregate["observation_count"] == copies + 1
    assert first["trace"]["trace_id"] in aggregate["conflicting_trace_ids"]
    assert aggregate["complete"] is (copies < 100)
    assert any('"response_status": 500' in e["content"] for e in result["evidence"] if e["kind"] == "log")


@pytest.mark.parametrize("field,value", [("service", "realtime"), ("environment", "prod"), ("request", {"user_id": "other"})])
def test_cross_source_scope_or_request_conflicts_are_held(tmp_path, field, value):
    first = deepcopy(RECORDS[0])
    second = deepcopy(first)
    second["trace"][field] = value
    result = combined(tmp_path, first, second)
    assert result["route"] == "REQUEST_CONTEXT"
    assert first["trace"]["trace_id"] in result["log_scope"]["aggregate"]["conflicting_trace_ids"]


@pytest.mark.parametrize("record_index,route", [(0, "GUIDANCE"), (1, "WORK_CANDIDATE")])
def test_consistent_sources_preserve_the_normal_fast_routes(tmp_path, record_index, route):
    first = deepcopy(RECORDS[record_index])
    result = combined(tmp_path, first, first)
    assert result["route"] == route and result["observed_status"] == 422
    assert result["log_scope"]["aggregate"]["complete"]
    assert result["model_calls"] == 0


@pytest.mark.parametrize("record_index", [0, 1])
def test_combined_limit_without_a_conflict_still_holds_a_definitive_route(tmp_path, record_index):
    record = deepcopy(RECORDS[record_index])
    result = combined(tmp_path, record, record, copies=100)
    aggregate = result["log_scope"]["aggregate"]
    assert aggregate["observation_count"] == 101 and not aggregate["complete"]
    assert aggregate["conflicting_trace_ids"] == []
    assert result["route"] == "REQUEST_CONTEXT" and result.get("observed_status") is None


def test_an_unreadable_source_holds_guidance_from_the_other_source(tmp_path):
    record = deepcopy(RECORDS[0])
    result = investigate_submission(
        "500 requestId=intake-guidance", repo=REPO, log_file=tmp_path / "missing.log",
        provided_logs=json.dumps(record), context=ReportContext(occurred_at=record["trace"]["occurred_at"]),
    )
    assert not result["log_scope"]["aggregate"]["complete"]
    assert result["route"] == "REQUEST_CONTEXT" and result["run_status"] == "PARTIAL_FAILURE"
    assert result["observations"] and result["log_scope"]["failures"]


def catalog_log(trace_id="claim-003"):
    data = json.loads(CATALOG_FILE.read_text(encoding="utf-8"))
    event = next(event for event in data["events"] if event["trace"]["trace_id"] == trace_id)
    return {"project_id": data["project_id"], **deepcopy(event)}


def investigate_catalog_log(record, *, trace_id="claim-003", context=None):
    catalog = LocalEventCatalog.from_file(CATALOG_FILE)
    trace = catalog.events[trace_id][0].get_trace(trace_id)
    return investigate_submission(
        f"500 requestId={trace_id}", repo=REPO, catalog=catalog, provided_logs=json.dumps(record),
        context=context or ReportContext(service=trace["service"], environment=trace["environment"], occurred_at=trace["occurred_at"]),
    )


@pytest.mark.parametrize("trace_id", ["claim-003", "contract-001"])
def test_catalog_and_current_log_status_conflict_holds_both_fast_routes(trace_id):
    record = catalog_log(trace_id)
    record["trace"]["response_status"] = 500
    result = investigate_catalog_log(record, trace_id=trace_id)
    assert result["route"] == "REQUEST_CONTEXT" and result["correlation"] == "NEEDS_CONTEXT"
    assert result.get("observed_status") is None and result["diagnosis_type"] is None
    assert result["claim_status"] == "UNVERIFIABLE" and result["claim_items"] == []
    assert result["run_status"] == "WAITING_CONTEXT" and result["model_calls"] == 0
    assert "사건 목록" in result["route_reason"] and "현재 로그" in result["route_reason"]
    aggregate = result["log_scope"]["aggregate"]
    assert aggregate["complete"] and aggregate["conflicting_trace_ids"] == [trace_id]
    assert aggregate["conflicts"] == [{
        "trace_id": trace_id, "fields": ["response_status"], "sources": ["local_event_catalog", "provided-log"],
    }]
    assert any(e.get("source_system") == "local_event_catalog" and "HTTP 422" in e["content"] for e in result["observations"])
    assert any(e["kind"] == "log" and e["scope_status"] == "VERIFIED" and '"response_status": 500' in e["content"] for e in result["observations"])


@pytest.mark.parametrize("trace_id,route", [("claim-003", "GUIDANCE"), ("contract-001", "WORK_CANDIDATE")])
def test_matching_catalog_and_current_logs_keep_the_checked_fast_route(trace_id, route):
    result = investigate_catalog_log(catalog_log(trace_id), trace_id=trace_id)
    assert result["route"] == route and result["observed_status"] == 422
    assert result["correlation"] == "EXACT_ID" and result["model_calls"] == 0
    assert result["log_scope"]["aggregate"]["conflicts"] == []


@pytest.mark.parametrize("missing", [True, False])
def test_missing_current_status_is_unknown_instead_of_catalog_counterevidence(missing):
    record = catalog_log()
    if missing:
        del record["trace"]["response_status"]
    else:
        record["trace"]["response_status"] = None
    result = investigate_catalog_log(record)
    assert result["route"] == "GUIDANCE" and result["observed_status"] == 422
    assert result["log_scope"]["aggregate"]["conflicts"] == []
    assert any(e["kind"] == "log" and e["scope_checks"]["response_status"] == "NOT_OBSERVED" for e in result["observations"])


@pytest.mark.parametrize("field,value", [
    ("trace_id", "another-request"), ("environment", "prod"), ("service", "another-service"),
    ("occurred_at", "2026-09-28T10:09:30+09:00"), ("project_id", "another-project"),
])
def test_logs_from_another_event_scope_do_not_conflict_with_the_selected_catalog_event(field, value):
    record = catalog_log()
    record["trace"].update(response_status=500, **{field: value})
    # Broader input scope accepts known service/environment and two nearby report times;
    # comparison must still check the selected catalog event's own scope.
    context = ReportContext(occurred_at="2026-09-28T10:05:00+09:00" if field == "occurred_at" else record["trace"]["occurred_at"])
    result = investigate_catalog_log(record, context=context)
    assert result["route"] == "GUIDANCE" and result["observed_status"] == 422
    assert result["log_scope"]["aggregate"]["conflicts"] == []
    if field in {"service", "environment", "occurred_at"}:
        assert any(e["kind"] == "log" and e["scope_status"] == "VERIFIED" for e in result["evidence"])


@pytest.mark.parametrize("value", ["[VALUE]", "[REDACTED]", "private-log-value"])
def test_redacted_log_request_values_are_not_comparable_to_catalog_values(value):
    record = catalog_log()
    record["trace"]["request"] = {"userId": value}
    result = investigate_catalog_log(record)
    assert result["route"] == "GUIDANCE" and result["log_scope"]["aggregate"]["conflicts"] == []
    assert "private-log-value" not in str(result)


def test_missing_optional_log_fields_do_not_create_a_catalog_conflict():
    record = catalog_log()
    for field in ("request", "method", "path", "operation"):
        del record["trace"][field]
    result = investigate_catalog_log(record)
    assert result["route"] == "GUIDANCE" and result["log_scope"]["aggregate"]["conflicts"] == []


@pytest.mark.parametrize("field,value", [("path", "/api/other"), ("dto", {"userId": "int", "name": "str"})])
def test_other_known_catalog_log_fields_share_the_conflict_guard(field, value):
    record = catalog_log()
    (record if field == "dto" else record["trace"])[field] = value
    result = investigate_catalog_log(record)
    assert result["route"] == "REQUEST_CONTEXT" and result.get("observed_status") is None
    assert field in result["log_scope"]["aggregate"]["conflicts"][0]["fields"]


def configured_page(monkeypatch):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv("TRACEBRIDGE_AGOLIVE_REPO", str(REPO))
    monkeypatch.setenv("TRACEBRIDGE_EVENTS_FILE", str(ROOT / "examples" / "report_events.json"))
    page = AppTest.from_file(str(ROOT / "pages" / "2_Report_Agent.py")).run()
    page.text_area[0].set_value("500 requestId=claim-003")
    page.button[0].click().run()
    assert not page.exception and page.session_state["report_agent_result"]["route"] == "GUIDANCE"
    return page


def test_failed_new_submission_clears_active_result_and_next_success_has_a_new_incident(monkeypatch):
    page = configured_page(monkeypatch)
    previous_id = page.session_state["report_agent_result"]["incident_id"]
    page.text_area[0].set_value("")
    page.button[0].click().run()
    assert not page.exception and page.error
    assert page.session_state["report_agent_result"] is None
    assert not any(item.value == "입력·동작 안내" for item in page.subheader)
    page.text_area[0].set_value("500 requestId=contract-001")
    page.button[0].click().run()
    result = page.session_state["report_agent_result"]
    assert not page.exception and result["route"] == "WORK_CANDIDATE"
    assert result["incident_id"] != previous_id


def test_failed_same_incident_answer_keeps_secured_result(monkeypatch):
    page = configured_page(monkeypatch)
    previous = deepcopy(page.session_state["report_agent_result"])
    page.text_area[1].set_value("")
    page.button[1].click().run()
    assert not page.exception and page.error
    assert page.session_state["report_agent_result"] == previous


def fake_clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(report_agent.time, "monotonic", clock)
    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("test-key", "test-model"))
    return clock


@pytest.mark.parametrize("preparation_seconds", [0, 0.6])
def test_one_second_photo_budget_reaches_the_ocr_transport(monkeypatch, preparation_seconds):
    clock = fake_clock(monkeypatch)
    prepare = report_agent.prepare_image

    def prepare_elapsed(raw):
        output = prepare(raw)
        clock.value = preparation_seconds
        return output

    monkeypatch.setattr(report_agent, "prepare_image", prepare_elapsed)
    timeouts = []

    def handler(request):
        timeouts.append(request.extensions["timeout"])
        return httpx.Response(200, json={"data": [{"text_detections": [{"text_prediction": {"text": "오류 화면의 문구를 제보했습니다. 추가 확인이 필요합니다.", "confidence": 0.99}}]}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        result = investigate_submission(image=photo(), repo=REPO, use_nvidia=True, ocr=NemoRetrieverOCR("test-key", client=transport), client=finish_client(), max_seconds=1)
    assert len(timeouts) == 1 and all(0 < value <= 1 for value in timeouts[0].values())
    assert list(timeouts[0].values()) == pytest.approx([1 - preparation_seconds] * 4)
    assert result["service_calls"][0]["status"] == "success"


def test_expired_photo_budget_blocks_ocr_and_other_io(monkeypatch):
    clock = fake_clock(monkeypatch)
    prepare = report_agent.prepare_image

    def prepare_expiring(raw):
        output = prepare(raw)
        clock.value = 2
        return output

    monkeypatch.setattr(report_agent, "prepare_image", prepare_expiring)
    http_calls = []

    def handler(request):
        http_calls.append(request)
        return httpx.Response(200, json={"data": [{"text_detections": []}]})

    process_calls = []

    def process(command, **kwargs):
        process_calls.append(command)
        return SimpleNamespace(returncode=1, stdout="")

    monkeypatch.setattr(project_sources.subprocess, "run", process)
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        result = investigate_submission(image=photo(), repo=REPO, use_nvidia=True, ocr=NemoRetrieverOCR("test-key", client=transport), client=finish_client(), max_seconds=1)
    assert http_calls == [] and process_calls == [] and result["model_calls"] == 0
    assert result["run_status"] == "TIMED_OUT" and result["timeout_reasons"]


def test_expiry_before_a_registered_read_cannot_leave_the_event_snapshot_as_guidance(monkeypatch):
    clock = fake_clock(monkeypatch)

    def revision(*args, **kwargs):
        clock.value = 2
        return "unknown"

    def forbidden(*args, **kwargs):
        raise AssertionError("No log read may start after expiry")

    monkeypatch.setattr(report_agent, "repository_revision", revision)
    monkeypatch.setattr(report_agent, "_log_tail", forbidden)
    result = investigate_submission(
        "500 requestId=claim-003", repo=REPO, catalog=LocalEventCatalog.from_file(ROOT / "examples" / "report_events.json"),
        log_file=ROOT / "examples" / "scoped_agolive.log", use_nvidia=True, client=finish_client(), max_seconds=1,
    )
    assert result["route"] == "REQUEST_CONTEXT" and result["run_status"] == "TIMED_OUT"
    assert not result["log_scope"]["aggregate"]["complete"] and result["model_calls"] == 0


def test_expiry_during_scope_parsing_keeps_the_observed_prefix(monkeypatch):
    clock = fake_clock(monkeypatch)
    parse = project_sources._log_record
    parsed = []

    def expiring_parse(*args, **kwargs):
        parsed.append(args[0])
        event = parse(*args, **kwargs)
        clock.value = 1.1
        return event

    monkeypatch.setattr(project_sources, "_log_record", expiring_parse)
    record = deepcopy(RECORDS[2])
    result = investigate_submission(
        "500 requestId=intake-server", repo=REPO, provided_logs="\n".join([json.dumps(record)] * 2),
        context=ReportContext(occurred_at=record["trace"]["occurred_at"]), use_nvidia=True, client=finish_client(), max_seconds=1,
    )
    assert len(parsed) == 1 and result["model_calls"] == 0
    assert result["run_status"] == "TIMED_OUT" and "provided-log" in result["timeout_reasons"]
    assert not result["log_scope"]["aggregate"]["complete"]
    assert any('"response_status": 500' in e["content"] for e in result["observations"])


def test_ocr_transport_timeout_reason_survives_a_later_service_failure(monkeypatch):
    fake_clock(monkeypatch)

    def handler(request):
        raise httpx.ReadTimeout("private response must not be copied", request=request)

    def model_response(kwargs):
        if kwargs["model"] == report_agent.VISION_MODEL:
            raise RuntimeError("private vision response")

    record = deepcopy(RECORDS[2])
    with httpx.Client(transport=httpx.MockTransport(handler)) as transport:
        result = investigate_submission(
            "500 requestId=intake-server", image=photo(), repo=REPO, provided_logs=json.dumps(record),
            context=ReportContext(occurred_at=record["trace"]["occurred_at"]), use_nvidia=True,
            ocr=NemoRetrieverOCR("test-key", client=transport), client=finish_client(model_response), max_seconds=1,
        )
    assert result["run_status"] == "TIMED_OUT" and "ocr_http" in result["timeout_reasons"]
    assert result["service_calls"][0]["status"] == "timed_out"
    assert result["observed_status"] == 500 and result["observations"]
    assert "private" not in str(result)


def test_git_reads_receive_remaining_time_and_expiry_blocks_a_new_process(monkeypatch):
    clock = fake_clock(monkeypatch)
    received = []

    def process(command, **kwargs):
        received.append(kwargs["timeout"])
        output = str(REPO.resolve()) if "--show-toplevel" in command else "a" * 12
        clock.value += 0.6 if len(received) == 1 else 0.2
        return SimpleNamespace(returncode=0, stdout=output)

    monkeypatch.setattr(project_sources.subprocess, "run", process)
    assert project_sources.repository_revision(REPO, deadline=1) == "a" * 12
    assert received == pytest.approx([1, 0.4])
    clock.value = 1
    with pytest.raises(TimeoutError):
        project_sources.repository_revision(REPO, deadline=1)
    assert len(received) == 2


@pytest.mark.parametrize("expire_after_first", [False, True])
def test_docker_revision_shares_the_budget_between_ps_and_inspect(monkeypatch, expire_after_first):
    clock = fake_clock(monkeypatch)
    received = []

    def process(command, **kwargs):
        received.append(kwargs["timeout"])
        if "compose" in command:
            clock.value = 1.1 if expire_after_first else 0.75
            return SimpleNamespace(returncode=0, stdout="b" * 64)
        return SimpleNamespace(returncode=0, stdout='true "' + 'a' * 40 + '"')

    monkeypatch.setattr(project_sources.subprocess, "run", process)
    if expire_after_first:
        with pytest.raises(TimeoutError):
            project_sources.running_service_revision(REPO, service="backend", environment="dev", deadline=1)
        assert received == [1]
    else:
        assert project_sources.running_service_revision(REPO, service="backend", environment="dev", deadline=1)["status"] == "OBSERVED"
        assert received == pytest.approx([1, 0.25])


def test_docker_timeout_preserves_file_observations_and_blocks_model_calls(monkeypatch, tmp_path):
    clock = fake_clock(monkeypatch)
    file = tmp_path / "server.log"
    record = deepcopy(RECORDS[2])
    file.write_text(json.dumps(record), encoding="utf-8")
    timeouts = []

    def process(command, **kwargs):
        if command[0] == "git":
            return SimpleNamespace(returncode=0, stdout=str(REPO.resolve()) if "--show-toplevel" in command else "a" * 12 if "rev-parse" in command else "")
        assert "logs" in command
        timeouts.append(kwargs["timeout"])
        clock.value = 1.1
        raise subprocess.TimeoutExpired(command, kwargs["timeout"])

    monkeypatch.setattr(project_sources.subprocess, "run", process)
    result = investigate_submission("500 requestId=intake-server", repo=REPO, log_file=file, include_docker_logs=True, context=ReportContext(occurred_at=record["trace"]["occurred_at"]), use_nvidia=True, client=finish_client(), max_seconds=1)
    assert timeouts == [1] and result["run_status"] == "TIMED_OUT"
    assert result["model_calls"] == 0 and "docker_logs" in result["timeout_reasons"]
    assert any('"response_status": 500' in e["content"] for e in result["observations"])


def test_model_receives_the_budget_left_by_reads_and_preserves_server_observations(monkeypatch, tmp_path):
    clock = fake_clock(monkeypatch)
    file = tmp_path / "server.log"
    record = deepcopy(RECORDS[2])
    file.write_text(json.dumps(record), encoding="utf-8")

    def revision(*args, **kwargs):
        clock.value = 0.6
        return "unknown"

    monkeypatch.setattr(report_agent, "repository_revision", revision)
    monkeypatch.setattr(report_agent, "source_tree_dirty", lambda *args, **kwargs: None)
    timeouts = []

    def response(kwargs):
        timeouts.append(kwargs["timeout"])
        clock.value = 1.1

    result = investigate_submission("500 requestId=intake-server", repo=REPO, log_file=file, context=ReportContext(occurred_at=record["trace"]["occurred_at"]), use_nvidia=True, client=finish_client(response), max_seconds=1)
    assert timeouts == pytest.approx([0.4]) and result["run_status"] == "TIMED_OUT"
    assert result["observed_status"] == 500 and result["observations"]
