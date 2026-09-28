"""Real filesystem reads across separate registered roots; no model network calls."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

import pytest

from tracebridge.evidence import EvidenceError
from tracebridge.project_profile import load_project_profile, read_project_logs, registered_path
from tracebridge.project_registry import save_profile, list_profiles
from tracebridge.project_sources import code_evidence
from tracebridge.project_contracts import load_project_contract
from tracebridge.report_agent import investigate_submission, ProjectTools
from tracebridge.report_contract import ReportContext
from tracebridge.incident_memory import IncidentStore

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def connected(tmp_path, monkeypatch):
    for name in ("TRACEBRIDGE_PROJECT_PROFILE", "TRACEBRIDGE_EVENTS_FILE", "TRACEBRIDGE_LOG_FILE",
                 "TRACEBRIDGE_LOG_SERVICE", "TRACEBRIDGE_LOG_ENVIRONMENT", "TRACEBRIDGE_LOG_TIMEZONE",
                 "TRACEBRIDGE_DEPLOYED_SHA"):
        monkeypatch.delenv(name, raising=False)
    frontend, backend, logs = (tmp_path / name for name in ("separate-ui", "separate-api", "observations"))
    for path in (frontend, backend):
        (path / "src").mkdir(parents=True)
    logs.mkdir()
    (frontend / "src/client.py").write_text("raise RuntimeError('MULTI_ROOT_FAILURE: frontend payload')\n", encoding="utf-8")
    (backend / "src/client.py").write_text("raise RuntimeError('MULTI_ROOT_FAILURE: backend dependency')\n", encoding="utf-8")
    event = {"trace": {"trace_id": "multi-001", "service": "backend", "environment": "dev",
             "occurred_at": "2026-09-29T10:00:00+09:00", "response_status": 500,
             "method": "POST", "path": "/accounts"}, "message": "MULTI_ROOT_FAILURE"}
    (logs / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    data = {"project_id": "multi-real-files", "service": "backend", "environment": "dev",
            "root": str(backend), "code_roots": [], "repositories": [
                {"id": "ui", "service": "frontend", "root": str(frontend), "code_roots": ["src"]},
                {"id": "api", "service": "backend", "root": str(backend), "code_roots": ["src"]},
                {"id": "logs", "service": "backend", "root": str(logs), "code_roots": []}],
            "log_sources": [{"id": "requests", "repository": "logs", "path": "events.jsonl", "format": "jsonl"}]}
    config = tmp_path / "profile.json"
    config.write_text(json.dumps(data), encoding="utf-8")
    return data, config, frontend, backend, logs


def test_actual_search_reads_same_named_files_from_separate_directories(connected):
    _, config, frontend, backend, _ = connected
    profile = load_project_profile(config)
    assert not frontend.is_relative_to(backend)
    evidence = code_evidence(profile.root, ["MULTI_ROOT_FAILURE"], code_roots=profile.code_roots, repositories=profile.repositories)
    assert {item.repository_id for item in evidence} == {"ui", "api"}
    assert {item.service for item in evidence} == {"frontend", "backend"}
    assert any("frontend payload" in item.content for item in evidence)
    assert any("backend dependency" in item.content for item in evidence)


def test_live_file_change_is_read_again_and_nonregistered_sibling_is_excluded(connected):
    _, config, frontend, backend, _ = connected
    outside = backend.parent / "not-registered"
    outside.mkdir()
    (outside / "secret.py").write_text("MULTI_ROOT_FAILURE unregistered-secret", encoding="utf-8")
    profile = load_project_profile(config)
    tools = ProjectTools(profile.root, "MULTI_ROOT_FAILURE", profile=profile, project_id=profile.project_id,
                         log_file=None, provided_logs="", include_docker=False, since_minutes=30)
    first = tools.call("search_code", json.dumps({"terms": ["MULTI_ROOT_FAILURE"]}))
    (frontend / "src/client.py").write_text("raise RuntimeError('MULTI_ROOT_FAILURE: changed-live-file')\n", encoding="utf-8")
    second = tools.call("search_code", json.dumps({"terms": ["MULTI_ROOT_FAILURE"]}))
    assert any("changed-live-file" in item["content"] for item in second["evidence"])
    assert "unregistered-secret" not in str(first) + str(second)
    assert {item["repository_id"] for item in second["evidence"]} == {"ui", "api"}


def test_current_independent_log_and_code_flow_saves_repository_provenance(connected, tmp_path):
    _, config, _, _, _ = connected
    db = tmp_path / "incidents.sqlite3"
    result = investigate_submission("MULTI_ROOT_FAILURE requestId=multi-001", project_profile=config,
        context=ReportContext(occurred_at="2026-09-29T10:00:00+09:00"), db_path=db)
    assert result["trace_id"] == "multi-001" and result["observed_status"] == 500
    assert result["model_calls"] == 0 and result["persistence"]["status"] == "SAVED"
    assert {item["repository_id"] for item in result["evidence"] if item["kind"] == "code"} == {"ui", "api"}
    with IncidentStore(db) as store:
        record = store.get_run(result["project_id"], result["run_id"])
    assert {item["repository_id"] for item in record["evidence"] if item["kind"] == "code"} == {"ui", "api"}
    assert not result["cause_confirmed"] and not result["fix_applied"]


def test_registered_plain_application_log_has_known_scope_and_real_id(connected):
    data, config, _, _, logs = connected
    (logs / "backend.log").write_text("2026-09-29 10:00:00.000 ERROR [req12345] [?] app.Logger - MULTI_ROOT_FAILURE\n", encoding="utf-8")
    data["log_sources"] = [{"id": "console", "repository": "logs", "path": "backend.log", "format": "text", "timezone": "+09:00"}]
    config.write_text(json.dumps(data), encoding="utf-8")
    collection = read_project_logs(load_project_profile(config))
    assert collection["complete"] and collection["events"][0]["trace"]["trace_id"] == "req12345"
    result = investigate_submission("MULTI_ROOT_FAILURE requestId=req12345", project_profile=config,
        context=ReportContext(occurred_at="2026-09-29T10:00:00+09:00"))
    assert result["log_scope"]["aggregate"]["complete"]
    assert any(item["kind"] == "log" and item["correlated"] for item in result["observations"])


@pytest.mark.parametrize("mutation", ["escape", "unknown-repository", "duplicate", "url", "missing-directory", "command"])
def test_registration_cannot_expand_into_unregistered_paths_or_commands(connected, mutation):
    data, config, _, backend, _ = connected
    data = deepcopy(data)
    if mutation == "escape": data["repositories"][0]["code_roots"] = ["../not-registered"]
    if mutation == "unknown-repository": data["log_sources"][0]["repository"] = "unknown"
    if mutation == "duplicate": data["repositories"].append(data["repositories"][0])
    if mutation == "url": data["repositories"][0]["root"] = "https://example.invalid/project"
    if mutation == "missing-directory": data["repositories"][0]["root"] = str(backend.parent / "absent")
    if mutation == "command": data["repositories"][0]["command"] = "shell"
    config.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(EvidenceError): load_project_profile(config)


def test_registered_artifact_paths_need_an_explicit_root(connected):
    _, config, frontend, backend, _ = connected
    profile = load_project_profile(config)
    assert registered_path(profile, {"repository": "ui", "path": "src/client.py"}) == frontend / "src/client.py"
    with pytest.raises(EvidenceError): registered_path(profile, str(backend.parent / "private.json"))
    with pytest.raises(EvidenceError): registered_path(profile, {"repository": "ui", "path": "../private.json"})


def test_caller_contract_can_reference_registered_separate_frontend_code(tmp_path):
    backend = tmp_path / "backend"
    shutil.copytree(ROOT / "examples/parallel_b/ledger_demo", backend)
    frontend = tmp_path / "frontend"
    (frontend / "src").mkdir(parents=True)
    shutil.copyfile(backend / "src/client.py", frontend / "src/client.py")
    data = json.loads((backend / "profile.json").read_text(encoding="utf-8"))
    data["repositories"] = [{"id": "ui", "service": "frontend", "root": str(frontend), "code_roots": ["src"]}]
    (backend / "profile.json").write_text(json.dumps(data), encoding="utf-8")
    caller = json.loads((backend / "caller.json").read_text(encoding="utf-8"))
    caller["operations"]["POST /accounts"]["repository"] = "ui"
    (backend / "caller.json").write_text(json.dumps(caller), encoding="utf-8")
    contract = load_project_contract(load_project_profile(backend / "profile.json"), "POST", "/accounts")
    assert contract["caller"]["source_verified"]
    assert str(frontend) in contract["caller"]["source"]


def test_registration_survives_reload_and_failed_update_keeps_previous(connected, tmp_path):
    data, _, _, _, _ = connected
    directory = tmp_path / "registry"
    saved = save_profile(data, directory)
    before = saved.read_bytes()
    profiles, errors = list_profiles(directory)
    assert not errors and profiles[0].project_id == data["project_id"]
    assert len(profiles[0].repositories) == 3
    invalid = deepcopy(data)
    invalid["repositories"][0]["root"] = "https://example.invalid/project"
    with pytest.raises(EvidenceError): save_profile(invalid, directory)
    assert saved.read_bytes() == before


def test_separate_repository_revision_is_not_overwritten_by_primary(connected):
    _, config, _, _, _ = connected
    profile = load_project_profile(config)
    tools = ProjectTools(profile.root, "MULTI_ROOT_FAILURE", profile=profile, project_id=profile.project_id,
                         log_file=None, provided_logs="", include_docker=False, since_minutes=30)
    tools.revision = "primary-only-head"
    result = tools.call("search_code", json.dumps({"terms": ["MULTI_ROOT_FAILURE"]}))
    assert all(item["source_revision"] == "unknown" for item in result["evidence"])


@pytest.mark.parametrize("with_empty_rows", [False, True])
def test_registration_form_saves_paths_and_reopened_ui_reads_real_sources(connected, tmp_path, monkeypatch, with_empty_rows):
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    data, _, frontend, backend, logs = connected
    directory = tmp_path / "registry"
    monkeypatch.setenv("TRACEBRIDGE_PROJECT_REGISTRY", str(directory))
    monkeypatch.setenv("TRACEBRIDGE_ALLOW_PROJECT_REGISTRATION", "1")
    original = st.data_editor
    def edited_rows(value, **kwargs):
        if kwargs.get("key") == "registration_repositories":
            rows = [{**row, "code_roots": ",".join(row["code_roots"])} for row in data["repositories"]]
            return rows + ([{"id": None, "service": None, "root": None, "code_roots": None, "git_root": None}] if with_empty_rows else [])
        if kwargs.get("key") == "registration_logs":
            return data["log_sources"] + ([{"id": None, "path": None}] if with_empty_rows else [])
        return original(value, **kwargs)
    monkeypatch.setattr(st, "data_editor", edited_rows)
    page = AppTest.from_file(str(ROOT / "pages/2_Report_Agent.py")).run()
    page.text_input(key="registration_project_id").set_value(data["project_id"])
    page.text_input(key="registration_primary_root").set_value(str(backend))
    next(button for button in page.button if button.label == "프로젝트 연결 저장").click().run()
    assert not page.exception
    assert page.selectbox(key="report_project").value == data["project_id"] + " (등록 프로젝트)"
    profile = load_project_profile(directory / (data["project_id"] + ".json"))
    assert {item.root for item in profile.repositories} == {frontend, backend, logs}
    reopened = AppTest.from_file(str(ROOT / "pages/2_Report_Agent.py")).run()
    reopened.selectbox(key="report_project").set_value(data["project_id"] + " (등록 프로젝트)").run()
    reopened.text_area[0].set_value("MULTI_ROOT_FAILURE requestId=multi-001 2026-09-29T10:00:00+09:00")
    reopened.button(key="start_report").click().run()
    assert not reopened.exception
    result = reopened.session_state["report_agent_result"]
    assert result["trace_id"] == "multi-001" and result["model_calls"] == 0
    assert {item["repository_id"] for item in result["evidence"] if item["kind"] == "code"} == {"ui", "api"}


def test_foreign_repository_code_is_not_promoted_by_primary_runtime_version(connected):
    from tracebridge.report_agent import Conclusion, ConciseHypothesis, _checked_current_hypotheses
    from tracebridge.project_sources import Evidence
    _, config, _, _, _ = connected
    profile = load_project_profile(config)
    tools = ProjectTools(profile.root, "MULTI_ROOT_FAILURE", profile=profile, project_id=profile.project_id,
                         log_file=None, provided_logs="", include_docker=False, since_minutes=30)
    tools.revision, tools.dirty = "a" * 12, False
    tools.runtime_revision = {"status": "OBSERVED", "sha": "a" * 40, "service": "backend"}
    tools.evidence = [Evidence("C1", "code", "repository:ui/src/client.py:1", "MULTI_ROOT_FAILURE", service="frontend", repository_id="ui", source_revision="b" * 40, source_tree_dirty=False),
                      Evidence("L1", "log", "current-log:1", "MULTI_ROOT_FAILURE", correlated=True, scope_status="VERIFIED", trace_id="multi-001")]
    proposal = Conclusion(intent="investigate", symptom_summary="failure", missing_information=[], next_steps=[], hypotheses=[ConciseHypothesis(cause="candidate", explanation="source and log", supporting_evidence_ids=["C1", "L1"], contradicting_evidence_ids=[], verification_step="reproduce", possible_fix="inspect")])
    result = _checked_current_hypotheses(proposal, tools, {"observations": [], "trace_id": "multi-001", "correlation": "EXACT_ID"})
    assert result[0]["status"] == "LOG_CANDIDATE" and result[0]["limitations"] and not result[0]["cause_confirmed"]
