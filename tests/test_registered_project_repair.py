"""Real file snapshots and subprocess checks; proposals are explicitly test doubles."""
from copy import deepcopy
import json
from pathlib import Path
import sys
from uuid import uuid4

import pytest

from tracebridge.change_policy import PolicyDenied, sha256
from tracebridge.incident_memory import IncidentStore
from tracebridge.project_profile import load_project_profile, select_project_service
from tracebridge.project_registry import save_profile, profile_data
from tracebridge.project_repair import prepare_project_change, save_repair_policy, _validate_policy, _apply_proposal, repair_blockers, apply_project_change
from tracebridge.report_agent import investigate_submission
from tracebridge.report_contract import ReportContext


class BoundaryProposer:
    def __init__(self, mutate=None):
        self.calls = 0
        self.mutate = mutate

    def propose(self, context, timeout):
        self.calls += 1
        file = context["source_files"]["app.py"]
        patch = {"rationale": "TEST_DOUBLE: include the supported boundary value", "evidence_ids": context["evidence_ids"][:1],
            "edits": [{"path": "app.py", "expected_sha256": file["sha256"], "content": file["content"].replace("count < 5", "count <= 5")}]}
        if self.mutate:
            self.mutate(patch)
        return patch


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.setenv("TRACEBRIDGE_PROJECT_REGISTRY", str(tmp_path / "registry"))
    for name in ("TRACEBRIDGE_PROJECT_PROFILE", "TRACEBRIDGE_LOG_FILE", "TRACEBRIDGE_EVENTS_FILE", "TRACEBRIDGE_DEPLOYED_SHA"):
        monkeypatch.delenv(name, raising=False)
    root = tmp_path / "external-project"
    root.mkdir()
    (root / "app.py").write_text('ERROR_CODE = "QUOTA_BOUNDARY"\ndef accepted(count):\n    return count < 5\n', encoding="utf-8")
    (root / "checks.py").write_text('import sys\nfrom app import accepted\n'
        'if sys.argv[1] == "boundary":\n'
        '    if not accepted(5):\n        print("QUOTA_BOUNDARY: five items must be accepted")\n        raise SystemExit(1)\n'
        'else:\n    assert accepted(0) and accepted(4) and not accepted(6)\nprint("CHECK_PASSED")\n', encoding="utf-8")
    log = {"trace": {"trace_id": "quota-001", "service": "api", "environment": "dev", "occurred_at": "2026-09-29T12:00:00+09:00", "response_status": 500,
        "method": "POST", "path": "/quotas"}, "message": "QUOTA_BOUNDARY: accepted quota was rejected"}
    (root / "events.jsonl").write_text(json.dumps(log) + "\n", encoding="utf-8")
    path = save_profile({"project_id": "quota-project", "service": "api", "environment": "dev", "root": str(root), "code_roots": ["."],
        "log_sources": [{"id": "requests", "path": "events.jsonl", "format": "jsonl"}], "policy_refs": ["quota-repair"]})
    profile = load_project_profile(path)
    policy = {"policy_id": "quota-repair", "project_id": profile.project_id, "repository_id": "primary", "environment": "dev", "enabled": True,
        "execution_mode": "TRUSTED_LOCAL", "trust_project_code": True, "editable_paths": ["app.py"],
        "allow_apply": True,
        "checks": [{"id": "boundary", "argv": [sys.executable, "-I", "checks.py", "boundary"], "success_marker": "CHECK_PASSED"},
                   {"id": "regression", "argv": [sys.executable, "-I", "checks.py", "regression"], "success_marker": "CHECK_PASSED"}],
        "reproduction_check_id": "boundary", "regression_check_id": "regression", "failure_marker": "QUOTA_BOUNDARY"}
    # -I removes the working directory from imports; use ordinary trusted project Python.
    for check in policy["checks"]:
        check["argv"].remove("-I")
    save_repair_policy(policy, profile)
    db = tmp_path / "incidents.sqlite3"
    source = investigate_submission("다섯 개만 입력했는데 실패해요. QUOTA_BOUNDARY requestId=quota-001 알아보고 고쳐줘.",
        context=ReportContext(occurred_at="2026-09-29T12:00:00+09:00"), project_profile=profile, db_path=db)
    assert source["correlation"] == "EXACT_ID" and source["run_status"] in {"COMPLETED", "WAITING_CONTEXT"}
    return profile, policy, source, db, root


def test_unregistered_logs_are_reported_without_claiming_a_collection_limit(project):
    from tracebridge.project_registry import profile_data
    profile, _, _, db, _ = project
    data = {**profile_data(profile), "log_sources": []}
    updated = load_project_profile(save_profile(data))
    result = investigate_submission("QUOTA_BOUNDARY 확인해줘", project_profile=updated, db_path=db)
    assert result["route"] == "REQUEST_CONTEXT" and result["correlation"] != "EXACT_ID"
    assert "로그 파일이 등록되지" in result["summary"]
    assert "한도를 넘었거나" not in result["route_reason"]
    assert any(item["kind"] == "code" for item in result["evidence"])


def test_registered_logs_ask_for_missing_report_time_and_accept_followup(project):
    from tracebridge.report_service import follow_up_service
    profile, _, _, db, _ = project
    result = investigate_submission("QUOTA_BOUNDARY requestId=quota-001 고쳐줘", project_profile=profile, db_path=db)
    assert "발생 시각이 없어" in result["summary"]
    assert result["route"] == "REQUEST_CONTEXT" and "한도를 넘었거나" not in result["summary"]
    followed = follow_up_service(result, "2026-09-29T12:00:00+09:00 개발 환경입니다", project_profile=profile, db_path=db)
    assert followed["incident_id"] == result["incident_id"]
    assert followed["correlation"] == "EXACT_ID" and followed["log_scope"]["aggregate"]["complete"]


def test_gui_model_failure_then_followup_clears_old_candidate_and_retries(project, monkeypatch):
    from streamlit.testing.v1 import AppTest
    from tracebridge import report_agent, report_service
    profile, _, _, db, _ = project
    monkeypatch.setenv("TRACEBRIDGE_PROJECT_PROFILE", str(profile.config_path))
    monkeypatch.setenv("TRACEBRIDGE_DB_PATH", str(db))
    monkeypatch.setattr(report_agent, "nvidia_settings", lambda: ("test-only-key", "test-only-model"))
    class FailedProposer:
        def propose(self, context, timeout):
            raise RuntimeError("test-only provider failure")
    proposer = FailedProposer()
    monkeypatch.setattr(report_service, "prepare_submission", lambda source, **kwargs: prepare_project_change(
        source, kwargs["project_profile"], policy_id=kwargs.get("policy_id"), db_path=db, proposer=proposer))
    page = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "pages/2_Report_Agent.py"), default_timeout=20).run()
    page.text_area[0].set_value("QUOTA_BOUNDARY requestId=quota-001 2026-09-29T12:00:00+09:00 고쳐줘")
    page.button(key="start_report").click().run()
    page.checkbox(key="use_nvidia_analysis").check().run()
    page.button(key="prepare_project_change").click().run()
    failed = page.session_state["report_agent_change"]
    assert not page.exception and failed["job"]["status"] == "MODEL_FAILED"
    page.checkbox(key="use_nvidia_analysis").uncheck().run()
    page.text_area(key="report_follow_up").set_value("2026-09-29T12:00:00+09:00 같은 실패입니다. 다시 고쳐줘")
    page.button(key="reply_report").click().run()
    assert not page.exception and page.session_state["report_agent_change"] is None
    continued = page.session_state["report_agent_result"]
    assert continued["incident_id"] == failed["job"]["incident_id"]
    assert continued["revision"] > failed["run"]["revision"] and continued["persistence"]["status"] == "SAVED"
    proposer = BoundaryProposer()
    page.checkbox(key="use_nvidia_analysis").check().run()
    page.button(key="prepare_project_change").click().run()
    assert not page.exception and page.session_state["report_agent_change"]["job"]["candidate_fix_verified"]
    first_candidate = page.session_state["report_agent_change"]["job"]["work_id"]
    next(item for item in page.checkbox if item.label.startswith("diff와 검사 결과")).check().run()
    page.checkbox(key="use_nvidia_analysis").uncheck().run()
    page.text_area(key="report_follow_up").set_value("2026-09-29T12:00:00+09:00 적용 전에 새 후보를 다시 준비해줘")
    page.button(key="reply_report").click().run()
    page.checkbox(key="use_nvidia_analysis").check().run()
    page.button(key="prepare_project_change").click().run()
    assert not page.exception and page.session_state["report_agent_change"]["job"]["work_id"] != first_candidate
    assert not next(item for item in page.checkbox if item.label.startswith("diff와 검사 결과")).value
    next(item for item in page.checkbox if item.label.startswith("diff와 검사 결과")).check().run()
    page.button(key="apply_project_change").click().run()
    assert not page.exception and page.session_state["report_agent_result"]["fix_applied"]
    assert not page.session_state["report_agent_result"]["fix_verified"]
    assert any(item.value == "수정안 · 원본 적용 완료" for item in page.subheader)
    reopened = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "pages/2_Report_Agent.py"), default_timeout=20).run()
    reopened.selectbox(key="resume_report_incident").set_value(continued["incident_id"])
    reopened.button(key="resume_report").click().run()
    assert not reopened.exception and reopened.session_state["report_agent_result"]["fix_applied"]
    assert any(item.value == "수정안 · 원본 적용 완료" for item in reopened.subheader)


def test_general_project_candidate_checks_and_persistence(project):
    profile, policy, source, db, root = project
    original = (root / "app.py").read_bytes()
    proposer = BoundaryProposer()
    result = prepare_project_change(source, profile, db_path=db, proposer=proposer)
    job = result["job"]
    assert result["persistence"]["status"] == "SAVED", result
    assert job["status"] == "CHANGE_PREPARED" and job["candidate_fix_verified"]
    assert [item["exit_code"] for item in job["checks"]] == [1, 0, 0]
    assert job["original_unchanged"] and (root / "app.py").read_bytes() == original
    assert job["execution"]["os_sandbox"] is False
    assert job["model"]["mode"] == "TEST_DOUBLE" and job["model"]["actual_calls"] == 0
    assert not job["original_applied"] and not result["run"]["fix_verified"]
    with IncidentStore(db) as store:
        assert store.get_change(profile.project_id, job["work_id"])["candidate_fix_verified"]
        assert store.get_card(profile.project_id, job["result_run_id"])["review"]["status"] == "PENDING"
        assert not store.get_run(profile.project_id, source["run_id"])["fix_applied"]
    repeated = prepare_project_change(source, profile, db_path=db, proposer=proposer)
    assert repeated["persistence"]["status"] == "ALREADY_SAVED" and proposer.calls == 1


def test_reviewed_application_is_explicit_and_idempotent(project):
    profile, _, source, db, root = project
    job = prepare_project_change(source, profile, db_path=db, proposer=BoundaryProposer())["job"]
    with pytest.raises(PolicyDenied):
        apply_project_change(profile.project_id, job["work_id"], profile, expected_diff_sha256="0" * 64, db_path=db)
    application = apply_project_change(profile.project_id, job["work_id"], profile, expected_diff_sha256=job["diff"]["sha256"], db_path=db)
    assert application["persistence"]["status"] == "SAVED"
    assert "count <= 5" in (root / "app.py").read_text()
    assert application["run"]["fix_applied"] and not application["run"]["fix_verified"]
    repeated = apply_project_change(profile.project_id, job["work_id"], profile, expected_diff_sha256=job["diff"]["sha256"], db_path=db)
    assert repeated["persistence"]["status"] == "ALREADY_SAVED"


@pytest.mark.parametrize("changed", ["original", "candidate", "policy"])
def test_application_preserves_newer_edits(project, changed):
    profile, policy, source, db, root = project
    job = prepare_project_change(source, profile, db_path=db, proposer=BoundaryProposer())["job"]
    original = (root / "app.py").read_bytes()
    if changed == "original":
        (root / "app.py").write_text("OWNER_NEW_EDIT = 1\n")
        newer_edit = (root / "app.py").read_bytes()
    elif changed == "candidate":
        (Path(job["candidate"]["root"]) / "app.py").write_text("UNVERIFIED_EDIT = 1\n")
    else:
        policy["enabled"] = False
        save_repair_policy(policy, profile)
    with pytest.raises(PolicyDenied):
        apply_project_change(profile.project_id, job["work_id"], profile, expected_diff_sha256=job["diff"]["sha256"], db_path=db)
    assert (root / "app.py").read_bytes() == (newer_edit if changed == "original" else original)


@pytest.mark.parametrize("mutation", ["unknown-evidence", "wrong-hash", "edit-test", "escape", "no-change"])
def test_model_cannot_expand_files_or_forge_refs(project, mutation):
    profile, _, source, db, root = project
    def mutate(patch):
        if mutation == "unknown-evidence": patch["evidence_ids"] = ["historical:foreign"]
        if mutation == "wrong-hash": patch["edits"][0]["expected_sha256"] = "0" * 64
        if mutation == "edit-test": patch["edits"][0]["path"] = "checks.py"
        if mutation == "escape": patch["edits"][0]["path"] = "../outside.py"
        if mutation == "no-change": patch["edits"][0]["content"] = (root / "app.py").read_text()
    result = prepare_project_change(source, profile, db_path=db, proposer=BoundaryProposer(mutate))
    assert result["job"]["status"] == "POLICY_REJECTED" and not result["job"]["candidate_fix_verified"]
    assert len(result["job"]["checks"]) == 1
    assert "count < 5" in (root / "app.py").read_text()


def test_reproduction_required_before_model(project):
    profile, policy, source, db, root = project
    policy["failure_marker"] = "UNRELATED_FAILURE"
    save_repair_policy(policy, profile)
    proposer = BoundaryProposer()
    result = prepare_project_change(source, profile, db_path=db, proposer=proposer)
    assert result["job"]["status"] == "NOT_REPRODUCED" and proposer.calls == 0


def test_offline_only_reproduces_without_proposal(project):
    profile, _, source, db, _ = project
    result = prepare_project_change(source, profile, db_path=db)
    assert result["job"]["status"] == "MODEL_NOT_REQUESTED" and len(result["job"]["checks"]) == 1
    assert not result["job"]["candidate_fix_verified"]


def test_stale_registration_and_guidance_never_run_checks(project):
    profile, policy, source, db, root = project
    (profile.config_path).write_text(profile.config_path.read_text() + "\n")
    with pytest.raises(PolicyDenied, match="設定|설정"):
        prepare_project_change(source, profile, db_path=db, proposer=BoundaryProposer())
    policy_model = _validate_policy(policy)
    assert any("안내" in text for text in repair_blockers({**source, "route": "GUIDANCE"}, profile, policy_model))


def test_store_rejects_forged_generic_verification(project):
    profile, _, source, db, _ = project
    result = prepare_project_change(source, profile, db_path=db, proposer=BoundaryProposer())
    forged = deepcopy(result)
    forged["job"]["checks"][0]["failure_marker_observed"] = False
    with IncidentStore(db) as store, pytest.raises(ValueError):
        store.save_project_change(forged["job"], forged["run"])


@pytest.mark.parametrize("change", [{"environment": "prod"}, {"trust_project_code": False}, {"checks": [{"id": "boundary", "argv": ["cmd.exe", "/c", "anything"]}]}])
def test_policy_requires_owner_scope_and_actual_executable(project, change):
    profile, policy, _, _, _ = project
    with pytest.raises(ValueError):
        save_repair_policy({**policy, **change}, profile)


def test_service_scopes_are_independent_and_relocation_preserves_paths(project, tmp_path):
    profile, _, _, _, root = project
    event = {"trace": {"trace_id": "ui-001", "service": "web", "environment": "dev", "occurred_at": "2026-09-29T12:00:00+09:00", "response_status": 500}, "message": "QUOTA_BOUNDARY"}
    (root / "ui.jsonl").write_text(json.dumps(event) + "\n")
    data = profile_data(profile)
    data["log_sources"][0]["service"] = "api"
    data["log_sources"].append({"id": "ui", "path": str(root / "ui.jsonl"), "format": "jsonl", "service": "web"})
    data["services"] = [{"id": "api", "log_source_ids": ["requests"]}, {"id": "web", "log_source_ids": ["ui"]}]
    path = save_profile(data, tmp_path / "moved")
    loaded = load_project_profile(path)
    selected = select_project_service(loaded, "web")
    assert selected.root == root and [item.id for item in selected.log_sources] == ["ui"]
    result = investigate_submission("QUOTA_BOUNDARY requestId=ui-001", project_profile=loaded, db_path=tmp_path / "ui.sqlite3",
        context=ReportContext(service="web", occurred_at="2026-09-29T12:00:00+09:00"))
    assert result["scope"]["service"] == "web" and result["trace_id"] == "ui-001"
    assert "quota-001" not in result["candidate_trace_ids"]


def test_delayed_report_keeps_original_relative_date(project):
    profile, _, _, db, _ = project
    result = investigate_submission("어제 오후 12시 QUOTA_BOUNDARY requestId=quota-001", project_profile=profile, db_path=db,
        message_received_at="2026-09-30T01:00:00+09:00")
    assert result["message_received_at"] == "2026-09-29T16:00:00+00:00"
    assert result["relative_date_basis"]["date"] == "2026-09-30"
    assert result["session"]["context"]["occurred_at"].startswith("2026-09-29T12:00")


def test_owner_registered_reproduction_can_investigate_without_log_connection(project):
    profile, policy, _, db, root = project
    data = profile_data(profile)
    data["log_sources"] = []
    profile = load_project_profile(save_profile(data))
    policy["allow_reproduction_without_logs"] = True
    save_repair_policy(policy, profile)
    source = investigate_submission("다섯 개 입력을 거절해요. QUOTA_BOUNDARY 확인해서 고쳐줘.", project_profile=profile, db_path=db)
    assert source["correlation"] != "EXACT_ID"
    result = prepare_project_change(source, profile, db_path=db, proposer=BoundaryProposer())
    assert result["job"]["candidate_fix_verified"]
    assert not result["run"]["cause_confirmed"] and not result["run"]["fix_applied"]
    assert any("같은 요청" in text for text in result["job"]["limitations"])


def test_registered_navigation_reads_sources_and_refuses_secrets_and_escape(project):
    from tracebridge.report_agent import ProjectTools
    profile, _, _, _, root = project
    (root / "view.vue").write_text('<script>const QUOTA_BOUNDARY = "limit"</script>\n')
    (root / ".env").write_text("SECRET=private-value\n")
    tools = ProjectTools(root, "QUOTA_BOUNDARY", log_file=None, provided_logs="", include_docker=False, since_minutes=30,
        context=ReportContext(), project_id=profile.project_id, profile=profile)
    inventory = tools.call("list_code_files", json.dumps({"terms": []}))
    assert {"app.py", "view.vue"}.issubset({item["path"] for item in inventory["files"]})
    assert not inventory["evidence"]
    read = tools.call("read_code", json.dumps({"repository_id": "primary", "path": "view.vue"}))
    assert "QUOTA_BOUNDARY" in read["evidence"][0]["content"]
    for path in (".env", "../other.py"):
        with pytest.raises(ValueError):
            tools.call("read_code", json.dumps({"repository_id": "primary", "path": path}))


def test_model_cannot_forge_success_by_terminating_checks(project):
    profile, _, source, db, _ = project
    def terminate(patch):
        patch["edits"][0]["content"] = "import sys\nsys.exit(0)\n"
    result = prepare_project_change(source, profile, db_path=db, proposer=BoundaryProposer(terminate))
    assert result["job"]["status"] == "POLICY_REJECTED" and len(result["job"]["checks"]) == 1


def test_monorepo_git_root_is_explicit_and_does_not_expand_source_scope(tmp_path):
    import subprocess
    from tracebridge.project_sources import code_evidence
    root = tmp_path / "mono"
    (root / "web/src").mkdir(parents=True)
    (root / "web/src/view.js").write_text('const MONO_FAILURE = true;\n')
    subprocess.run(["git", "init", str(root)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "add", "web/src/view.js"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "fixture"], check=True, capture_output=True)
    data = {"project_id": "mono", "root": str(root), "service": "web", "environment": "dev", "code_roots": [],
        "repositories": [{"id": "web", "service": "web", "root": str(root / "web"), "code_roots": ["src"], "git_root": str(root)}]}
    profile = load_project_profile(save_profile(data, tmp_path / "registry"))
    evidence = code_evidence(root, ["MONO_FAILURE"], code_roots=(), repositories=profile.repositories)
    assert evidence[0].source_revision != "unknown" and evidence[0].source_tree_dirty is False
    data["repositories"][0]["git_root"] = str(tmp_path)
    with pytest.raises(ValueError):
        save_profile(data, tmp_path / "registry")


def test_multiple_repair_policies_require_explicit_selection(project):
    from tracebridge.project_repair import load_repair_policy
    profile, policy, _, _, _ = project
    data = profile_data(profile)
    data["policy_refs"].append("second-repair")
    updated = load_project_profile(save_profile(data))
    save_repair_policy({**policy, "policy_id": "second-repair"}, updated)
    with pytest.raises(PolicyDenied):
        load_repair_policy(updated)
    assert load_repair_policy(updated, "second-repair")[0].policy_id == "second-repair"
