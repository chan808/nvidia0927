"""A's full-loop doubles plus public B/C/D and CLI/UI consumer checks."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tracebridge import report_agent, report_service
from tracebridge.incident_memory import IncidentStore, export_manual
from tracebridge.report_contract import ReportContext, presentation_status


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "tests/fixtures/agolive_repo"
SHA = "a" * 40


@pytest.fixture
def workspace():
    path = ROOT / "output/parallel-a" / ("investigation-" + uuid4().hex)
    path.mkdir(parents=True)
    return path


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    for name in ("TRACEBRIDGE_EVENTS_FILE", "TRACEBRIDGE_LOG_FILE", "TRACEBRIDGE_LOG_SERVICE", "TRACEBRIDGE_LOG_ENVIRONMENT",
                 "TRACEBRIDGE_LOG_TIMEZONE", "TRACEBRIDGE_DEPLOYED_SHA", "TRACEBRIDGE_PROJECT_PROFILE", "TRACEBRIDGE_METRICS_DIR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("TEST_DOUBLE", "test-model"))


def project(workspace, *, status=500, format_="jsonl", conflict=False):
    # No Agolive folder, embedded log contract, commands or policy authority.
    (workspace / "src").mkdir()
    code = 'def serialize(accountId, name):\n    return {"account_id": accountId, "name": name}\n'
    (workspace / "src/client.py").write_text(code, encoding="utf-8")
    source_hash = hashlib.sha256((workspace / "src/client.py").read_bytes()).hexdigest()
    trace = {"trace_id": "account-001", "project_id": "account-project", "service": "account-api", "environment": "dev",
             "occurred_at": "2026-09-28T10:00:00+09:00", "operation": "계정 생성", "method": "POST", "path": "/api/accounts",
             "version": SHA, "code_version": SHA, "response_status": status,
             "request": {"account_id": "[VALUE]", "name": "[VALUE]"}, "request_types": {"account_id": "string", "name": "string"},
             "input_fields": ["accountId", "name"], "input_types": {"accountId": "string", "name": "string"}}
    event = {"trace": trace, "level": "ERROR", "message": "SerializationException while creating account"}
    events = [event]
    if conflict:
        opposing = deepcopy(event)
        opposing["trace"]["response_status"] = 422 if status == 500 else 500
        events.append(opposing)
    log = workspace / ("events.jsonl" if format_ == "jsonl" else "events.json")
    log.write_text("\n".join(json.dumps(item) for item in events) if format_ == "jsonl" else json.dumps({"project_id": "account-project", "events": events}), encoding="utf-8")
    schema = {"type": "object", "required": ["accountId", "name"], "properties": {"accountId": {"type": "string"}, "name": {"type": "string"}}}
    documents = {
        "openapi.json": {"openapi": "3.0.3", "info": {"title": "TEST_DOUBLE", "version": "api-v1"}, "x-code-version": SHA,
                         "paths": {"/api/accounts": {"post": {"requestBody": {"content": {"application/json": {"schema": schema}}}}}}},
        "dto.json": {"method": "POST", "path": "/api/accounts", "code_version": SHA, "schema": schema},
        "caller.json": {"method": "POST", "path": "/api/accounts", "code_version": SHA, "source": "src/client.py#serialize",
                        "source_sha256": source_hash, "field_mapping": {"account_id": "accountId", "name": "name"},
                        "required_inputs": {"accountId": "accountId", "name": "name"}},
        "version.json": {"project_id": "account-project", "service": "account-api", "environment": "dev", "runtime_version": SHA, "code_version": SHA},
        "profile.json": {"project_id": "account-project", "service": "account-api", "environment": "dev", "root": ".", "code_roots": ["src"],
                         "log_sources": [{"id": "account-log", "path": log.name, "format": format_}], "openapi_path": "openapi.json",
                         "dto_path": "dto.json", "caller_evidence_path": "caller.json", "version_observation": {"method": "json_file", "path": "version.json"},
                         "policy_refs": ["seed-signup-a2-v1"]},
    }
    for name, document in documents.items():
        (workspace / name).write_text(json.dumps(document), encoding="utf-8")
    return workspace / "profile.json", log, trace


def call(name, arguments):
    return SimpleNamespace(id=uuid4().hex, function=SimpleNamespace(name=name, arguments=json.dumps(arguments)))


def response(*calls):
    return SimpleNamespace(id="TEST_DOUBLE-response", usage=SimpleNamespace(prompt_tokens=12, completion_tokens=3),
                           choices=[SimpleNamespace(finish_reason="tool_calls", message=SimpleNamespace(content="", tool_calls=list(calls)))])


def finish(**changes):
    fields = {"intent": "investigate", "symptom_summary": "계정 생성 실패", "cause": "직렬화 경로 조사 후보", "explanation": "현재 로그와 생성 코드 단서를 대조했습니다.",
              "supporting_evidence_ids": ["C1", "L1"], "contradicting_evidence_ids": [], "verification_step": "같은 요청의 재현 검사 필요",
              "possible_fix": "검토 후 호출자 전달 확인", "missing_information": [], "next_steps": ["현재 근거로 재현 계획 검토"],
              "previous_hypothesis_outcome": "not_rechecked", "counter_evidence_ids": []}
    fields.update(changes)
    return call("finish_investigation", fields)


def client(create):
    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def run(workspace, profile, create=None, **kwargs):
    return report_agent.investigate_submission("계정 생성이 실패해요. 500 requestId=account-001", project_profile=profile,
        context=ReportContext(occurred_at="2026-09-28T10:00:00+09:00"), db_path=workspace / "incidents.sqlite3",
        memory_enabled=False, use_nvidia=create is not None, client=client(create) if create else None,
        observer_output_dir=workspace / "metrics", **kwargs)


def test_full_adaptive_loop_changes_query_after_no_match_and_returns_validated_schema(workspace, monkeypatch):
    profile, _, _ = project(workspace)
    monkeypatch.setattr(report_agent, "repository_revision", lambda *a, **k: SHA)
    monkeypatch.setattr(report_agent, "source_tree_dirty", lambda *a, **k: False)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return response(call("search_code", {"terms": ["NoSuchInitialHypothesis"], "purpose": "초기 가설의 코드 단서 확인"}))
        if len(calls) == 2:
            assert json.loads(kwargs["messages"][-1]["content"])["lookup_status"] == "NO_MATCH"
            return response(call("search_code", {"terms": ["serialize"], "services": ["account-api"], "purpose": "조회 실패 후 호출자 생성 경로로 전환"}), call("get_contract", {}))
        assert any(item["kind"] == "contract" for item in json.loads(kwargs["messages"][-1]["content"])["evidence"])
        return response(finish())

    result = run(workspace, profile, create)
    searches = [step for step in result["steps"] if step["tool"] == "search_code"]
    assert [step["query"]["terms"] for step in searches] == [["NoSuchInitialHypothesis"], ["serialize"]]
    assert [step["lookup_status"] for step in searches] == ["NO_MATCH", "OBSERVED"]
    assert result["log_scope"]["investigation"]["final_return_status"] == "VALIDATED"
    assert result["run_status"] == "COMPLETED" and result["model_calls"] == 3
    assert result["hypotheses"][0]["status"] == "SUPPORTED_HYPOTHESIS" and not result["cause_confirmed"]
    assert result["observability"]["coverage_status"] == "MATCHED_REPORTED_COUNTS"
    assert result["observability"]["observed_calls"]["model"] == 3
    assert result["observability"]["nat"]["status"] == "NOT_CONFIGURED" and not result["observability"]["main_flow_verified"]
    with IncidentStore(workspace / "incidents.sqlite3") as store:
        saved = store.get_run(result["project_id"], result["run_id"])
    assert saved["log_scope"]["investigation"]["lookup_records"] == result["steps"]


def test_lookup_transport_failure_can_change_tool_but_remains_partial(workspace, monkeypatch):
    profile, _, _ = project(workspace)
    real_search = report_agent.code_evidence
    calls = []

    def search(repo, terms, *args, **kwargs):
        if terms == ["failed-first-query"]:
            raise OSError("private source failure")
        return real_search(repo, terms, *args, **kwargs)

    monkeypatch.setattr(report_agent, "code_evidence", search)

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return response(call("search_code", {"terms": ["failed-first-query"]}))
        if len(calls) == 2:
            assert "error" in json.loads(kwargs["messages"][-1]["content"])
            return response(call("get_contract", {}))
        return response(finish(cause="", supporting_evidence_ids=[]))

    result = run(workspace, profile, create)
    assert result["run_status"] == "PARTIAL_FAILURE"
    assert result["log_scope"]["investigation"]["final_return_status"] == "VALIDATED"
    assert [step["tool"] for step in result["steps"]][-2:] == ["search_code", "get_contract"]
    assert "private source failure" not in str(result)


def test_5xx_gets_one_final_summary_keeps_failure_and_missing_usage(workspace):
    profile, _, _ = project(workspace)
    calls = []

    class ServingError(RuntimeError):
        status_code = 500

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return response(call("search_code", {"terms": ["serialize"]}))
        if len(calls) == 2:
            raise ServingError("private serving response")
        assert len(kwargs["tools"]) == 1 and kwargs["tool_choice"]["function"]["name"] == "finish_investigation"
        return response(finish(cause="", supporting_evidence_ids=[]))

    result = run(workspace, profile, create)
    assert result["model_calls"] == 3 and result["run_status"] == "PARTIAL_FAILURE"
    assert result["log_scope"]["investigation"]["final_return_status"] == "VALIDATED"
    assert result["usage"]["prompt_tokens"] is None
    assert result["observability"]["usage"]["prompt_tokens_missing_calls"] == 1
    assert result["observability"]["coverage_status"] == "MATCHED_REPORTED_COUNTS"
    assert any(item.get("http_status") == 500 for item in result["model_trace"])
    assert result["observations"] and "private serving response" not in str(result)


@pytest.mark.parametrize("failure", ["timeout", "invalid-final", "intermediate-only"])
def test_intermediate_success_is_not_whole_investigation_success(workspace, failure):
    profile, _, _ = project(workspace)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if failure == "timeout":
            raise TimeoutError("private response")
        if failure == "invalid-final":
            return response(finish(intent="unsupported"))
        return response(call("search_code", {"terms": ["query-" + str(len(calls))]}))

    result = run(workspace, profile, create)
    assert result["run_status"] != "COMPLETED" and result["model_calls"] <= report_agent.MAX_MODEL_CALLS
    assert result["log_scope"]["investigation"]["final_return_status"] == "NOT_RETURNED"
    assert len(result["model_trace"]) == result["model_calls"]
    assert result["observability"]["coverage_status"] == "MATCHED_REPORTED_COUNTS"
    assert result["observations"] and not result["fix_verified"]
    if failure == "timeout":
        assert result["run_status"] == "TIMED_OUT" and len(calls) == 1


def test_later_log_query_cannot_erase_old_conflict_or_completeness(workspace):
    profile, log, trace = project(workspace)
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            changed = deepcopy(trace)
            changed["response_status"] = 422
            log.write_text(json.dumps({"trace": changed, "message": "different current response"}), encoding="utf-8")
            return response(call("find_logs", {"terms": ["different"]}))
        return response(finish(cause="", supporting_evidence_ids=[]))

    result = run(workspace, profile, create)
    assert result["route"] == "REQUEST_CONTEXT" and not result["log_scope"]["aggregate"]["complete"]
    assert result["log_scope"]["aggregate"]["conflicting_trace_ids"] == ["account-001"]
    assert {event["response_status"] for event in result["log_scope"]["retained_observations"]} == {500, 422}


@pytest.mark.parametrize("format_", ["jsonl", "json"])
def test_generic_profile_reads_separate_contract_memory_off_still_saves(workspace, format_):
    profile, _, _ = project(workspace, status=422, format_=format_)
    result = run(workspace, profile)
    assert result["project_id"] == "account-project" and result["route"] == "WORK_CANDIDATE"
    assert result["responsibility"]["status"] == "CALLER_DEFECT"
    assert result["contract_analysis"]["missing_fields"] == ["accountId"]
    assert result["product_status"] == "CALLER_DEFECT_OBSERVED" and result["claim_status"] == "CONTRADICTED"
    status = presentation_status(result)
    assert status["report_status_verification"] == "CONTRADICTED" and status["actual_symptom_status"] == "REQUEST_REJECTED"
    assert result["memory_search"]["status"] == "DISABLED" and result["memory_search"]["cards"] == []
    assert not result["deployment_observed"]  # Local version snapshots are not a live deployment check.
    assert result["persistence"]["status"] == "SAVED"
    assert "등록된 수정 대상 없음" in report_service.preparation_blockers(result)
    with IncidentStore(workspace / "incidents.sqlite3") as store:
        saved = store.get_run(result["project_id"], result["run_id"])
        store.review_card(result["project_id"], result["run_id"], "approve", reviewer="parallel-a-double")
    assert presentation_status(saved)["product_status"] == "CALLER_DEFECT_OBSERVED"
    manual = export_manual(result["project_id"], db_path=workspace / "incidents.sqlite3")
    assert manual["card_count"] == 1 and result["run_id"] in manual["markdown"]


def test_hosted_final_schema_has_every_flat_field_and_local_validation_limits():
    schema = report_agent.FINISH_TOOL["function"]["parameters"]
    assert set(schema["required"]) == set(schema["properties"])
    assert "$defs" not in schema and schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(report_agent.FinalArguments.model_fields)


def test_injected_event_sink_tracks_same_run_without_bodies(workspace):
    from tracebridge.nat_observability import InvestigationObserver

    profile, _, _ = project(workspace)
    events = []
    observer = InvestigationObserver(uuid4().hex, workspace / "sink-metrics", origin="evaluation_double", nat_sink=events.append)
    result = report_agent.investigate_submission("계정 생성 실패 requestId=account-001", project_profile=profile,
        context=ReportContext(occurred_at="2026-09-28T10:00:00+09:00"), db_path=workspace / "sink.sqlite3",
        use_nvidia=True, client=client(lambda **kwargs: response(finish(cause="", supporting_evidence_ids=[]))),
        memory_enabled=False, observer=observer)
    assert result["run_id"] == observer.run_id
    assert result["observability"]["coverage_status"] == "MATCHED_REPORTED_COUNTS"
    assert result["observability"]["nat"]["status"] == "EVENTS_DELIVERED"
    assert not result["observability"]["main_flow_verified"]
    assert len(events) == len(result["steps"]) + result["model_calls"]
    assert all(event["run_id"] == result["run_id"] and event["origin"] == "evaluation_double" for event in events)
    assert all(not ({"prompt", "content", "arguments", "logs", "code"} & event.keys()) for event in events)


def test_profile_log_format_does_not_depend_on_filename_suffix(workspace):
    profile, log, _ = project(workspace, status=422)
    replacement = workspace / "account-observations.ndjson"
    replacement.write_bytes(log.read_bytes())
    configuration = json.loads(profile.read_bytes())
    configuration["log_sources"][0]["path"] = replacement.name
    profile.write_text(json.dumps(configuration), encoding="utf-8")
    assert run(workspace, profile)["route"] == "WORK_CANDIDATE"


def test_cli_profile_no_memory_metrics_manual_and_statuses(workspace):
    profile, _, _ = project(workspace, status=422)
    output = workspace / "cli.json"
    result = subprocess.run([sys.executable, "-m", "scripts.investigate_report", "--profile", str(profile),
        "--report", "계정 생성 실패 500 requestId=account-001", "--occurred-at", "2026-09-28T10:00:00+09:00",
        "--no-memory", "--db", str(workspace / "cli.sqlite3"), "--metrics-dir", str(workspace / "cli-metrics"),
        "--manual-output", str(workspace / "manual.md"), "--output", str(output)], cwd=ROOT, capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    record = json.loads(output.read_bytes())
    assert record["memory_search"]["status"] == "DISABLED" and record["model_calls"] == 0
    assert record["presentation"]["product_status"] == "CALLER_DEFECT_OBSERVED"
    assert record["observability"]["coverage_status"] == "MATCHED_REPORTED_COUNTS"
    assert (workspace / "manual.md").is_file()


def test_ui_profile_claim_symptom_memory_and_manual_are_connected(workspace, monkeypatch):
    import tempfile

    def local_temp_dir(suffix=None, prefix=None, dir=None):
        path = workspace / "ui-scratch" / ((prefix or "tmp") + uuid4().hex + (suffix or ""))
        assert path.resolve().is_relative_to(workspace.resolve())
        path.mkdir(parents=True)
        return str(path)

    monkeypatch.setattr(tempfile, "mkdtemp", local_temp_dir)
    from streamlit.testing.v1 import AppTest

    profile, _, _ = project(workspace, status=422)
    monkeypatch.setenv("TRACEBRIDGE_PROJECT_PROFILE", str(profile))
    monkeypatch.setenv("TRACEBRIDGE_DB_PATH", str(workspace / "ui.sqlite3"))
    app = AppTest.from_file(str(ROOT / "pages/2_Report_Agent.py")).run()
    assert not app.exception
    app.checkbox(key="report_memory_enabled").uncheck()
    app.text_area[0].set_value("500 계정 생성 실패 requestId=account-001")
    app.text_input[0].set_value("2026-09-28T10:00:00+09:00")
    app.button(key="start_report").click().run()
    assert not app.exception
    record = app.session_state["report_agent_result"]
    assert record["route"] == "WORK_CANDIDATE" and record["memory_search"]["status"] == "DISABLED"
    displayed = "\n".join(item.value for item in app.markdown)
    assert "제보의 HTTP 상태" in displayed and "실제 증상" in displayed and "제품 판단" in displayed
    assert "호출자 결함 근거 확보" in displayed and "현재 응답과 다름" in displayed
