"""Registered caller preparation, freshness and save-only retry checks for A."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

import pytest

from tracebridge import change_policy, change_worker, report_agent, report_service, seed_project
from tracebridge.change_policy import load_policy, read_source
from tracebridge.incident_memory import IncidentStore, persist_result
from tracebridge.report_contract import presentation_status


ROOT = Path(__file__).resolve().parents[1]
PROJECT = "tracebridge-seed-signup"


@pytest.fixture
def lab():
    root = ROOT / "output/parallel-a" / ("worker-" + uuid4().hex)
    for relative in ("examples/seed_signup", "examples/change_policy"):
        destination = root / relative
        destination.mkdir(parents=True)
        for original in (ROOT / relative).iterdir():
            if original.is_file():
                shutil.copyfile(original, destination / original.name)
    return root, root / "incidents.sqlite3"


class Proposer:
    def __init__(self, callback=None):
        self.calls = 0
        self.callback = callback

    def propose(self, context, timeout):
        self.calls += 1
        if self.callback:
            self.callback(context)
        policy = context["policy"]
        return {"policy_id": policy["policy_id"], "policy_version": policy["version"], "target_id": policy["target_id"],
                "evidence_ids": context["evidence_ids"][:1], "rationale": "TEST_DOUBLE registered caller key alignment",
                "check_ids": policy["check_ids"], "edits": [{"path": "client.py", "expected_sha256": policy["files"]["client.py"],
                "old_key": "user_id", "new_key": "userId"}]}


def source_run(lab, **changes):
    workspace, db = lab
    source = change_worker.seed_incident(workspace=workspace, db_path=db)
    assert source["route"] == "WORK_CANDIDATE" and source["log_scope"]["aggregate"]["complete"]
    if changes:
        source.update(run_id=uuid4().hex, incident_id=uuid4().hex, **changes)
        persist_result(source, db)
    return source


def test_supported_worker_produces_real_diff_same_checks_without_original_apply(lab):
    workspace, db = lab
    source = source_run(lab)
    policy, digest = load_policy(change_worker.DEFAULT_POLICY, workspace)
    baseline = read_source(policy, workspace)
    proposer = Proposer()
    result = change_worker.prepare_change(PROJECT, source["run_id"], workspace=workspace, db_path=db, proposer=proposer)
    job = result["job"]
    assert job["status"] == "CHANGE_PREPARED" and job["candidate_fix_verified"]
    assert proposer.calls == job["model"]["test_double_calls"] == 1 and job["model"]["actual_calls"] == 0
    assert [check["status"] for check in job["checks"]] == ["FAILED", "PASSED", "PASSED"]
    assert job["identical_related_check"] and job["diff"]["changed_files"] == ["client.py"]
    assert read_source(policy, workspace) == baseline and load_policy(change_worker.DEFAULT_POLICY, workspace)[1] == digest
    status = presentation_status(result["run"], job)
    assert status["candidate_validation"] == "VERIFIED"
    assert status["original_application"] == "NOT_APPLIED" and status["service_recovery"] == "NOT_VERIFIED"
    repeated = change_worker.prepare_change(PROJECT, source["run_id"], workspace=workspace, db_path=db, proposer=proposer)
    assert repeated["persistence"]["status"] == "ALREADY_SAVED" and proposer.calls == 1


@pytest.mark.parametrize("changes", [
    {"requested_action": "INVESTIGATE_ONLY"}, {"work_role": "Data/Infrastructure"},
    {"log_scope": {"aggregate": {"complete": False}}},
    {"log_scope": {"aggregate": {"complete": True, "conflicts": [{"trace_id": "seed-signup-001", "fields": ["response_status"]}]}}},
    {"log_scope": {}},
], ids=["read-only", "unsupported-role", "partial", "conflict", "unknown-completeness"])
def test_worker_gate_rejects_before_any_check_or_proposal(lab, changes):
    workspace, db = lab
    source = source_run(lab, **changes)
    proposer = Proposer()
    result = change_worker.prepare_change(PROJECT, source["run_id"], workspace=workspace, db_path=db, proposer=proposer)
    assert result["job"]["status"] == "POLICY_REJECTED"
    assert result["job"]["checks"] == [] and proposer.calls == 0
    assert not result["job"]["candidate_fix_verified"]


def test_new_answer_during_proposal_blocks_patch_and_further_checks(lab):
    workspace, db = lab
    source = source_run(lab)

    def new_answer(context):
        current = deepcopy(source)
        with IncidentStore(db) as store:
            current.update(run_id=uuid4().hex, revision=current["revision"] + 1, requested_action="INVESTIGATE_ONLY")
            store.save_run(current)

    result = change_worker.prepare_change(PROJECT, source["run_id"], workspace=workspace, db_path=db, proposer=Proposer(new_answer))
    assert result["job"]["status"] == "POLICY_REJECTED" and result["job"]["diff"] == {}
    assert len(result["job"]["checks"]) == 1
    assert result["persistence"]["status"] == "FAILED" and result["persistence"]["error_type"] == "RunConflict"
    assert result["run"]["requested_action"] == "INVESTIGATE_ONLY"
    assert (workspace / result["job"]["artifact_ref"]).is_file()
    with IncidentStore(db) as store:
        latest = store.get_incident(PROJECT, source["incident_id"])
        assert store.get_run(PROJECT, latest["latest_run_id"])["requested_action"] == "INVESTIGATE_ONLY"


def test_policy_replacement_during_proposal_blocks_copy_edit(lab):
    workspace, db = lab
    source = source_run(lab)

    def replace_copied_policy(context):
        # Only this isolated test copy changes; public registered files are preserved.
        path = workspace / "examples/change_policy/seed-signup-a2-v1.json"
        path.write_bytes(path.read_bytes() + b" ")

    result = change_worker.prepare_change(PROJECT, source["run_id"], workspace=workspace, db_path=db, proposer=Proposer(replace_copied_policy))
    assert result["job"]["status"] == "POLICY_REJECTED" and result["job"]["diff"] == {}
    assert len(result["job"]["checks"]) == 1 and not result["job"]["candidate_fix_verified"]


def test_storage_failure_entry_retry_saves_obtained_job_without_execution(lab, monkeypatch):
    workspace, db = lab
    source = source_run(lab)
    actual_save = IncidentStore.save_change_result
    attempts = []

    def fail_once(store, result):
        attempts.append(result["job"]["work_id"])
        if len(attempts) == 1:
            raise sqlite3.OperationalError("double write failure")
        return actual_save(store, result)

    monkeypatch.setattr(IncidentStore, "save_change_result", fail_once)
    proposer = Proposer()
    obtained = change_worker.prepare_change(PROJECT, source["run_id"], workspace=workspace, db_path=db, proposer=proposer)
    assert obtained["persistence"]["status"] == "FAILED" and obtained["job"]["candidate_fix_verified"]

    def forbidden_execution(*args, **kwargs):
        raise AssertionError("A save retry executed a check")

    monkeypatch.setattr(change_worker, "run_registered_check", forbidden_execution)
    saved = change_worker.prepare_change(PROJECT, source["run_id"], workspace=workspace, db_path=db, proposer=proposer)
    assert saved["persistence"]["status"] == "SAVED"
    assert saved["job"]["work_id"] == obtained["job"]["work_id"] and proposer.calls == 1


def test_manual_service_and_auto_service_share_holds(lab, monkeypatch):
    source = source_run(lab)
    source["requested_action"] = "INVESTIGATE_ONLY"
    monkeypatch.setattr(report_service, "prepare_change", lambda *a, **k: pytest.fail("Blocked worker called"))
    with pytest.raises(ValueError, match="조사만"):
        report_service.prepare_submission(source, db_path=lab[1], proposer=Proposer())
    assert report_service.auto_prepare_submission(source, enabled=True, live=True, db_path=lab[1]) is None


def test_followup_candidate_cannot_repeat_an_incident_job(lab, monkeypatch):
    workspace, db = lab
    source = source_run(lab)
    proposer = Proposer()
    first = change_worker.prepare_change(PROJECT, source["run_id"], workspace=workspace, db_path=db, proposer=proposer)
    with IncidentStore(db) as store:
        latest = store.get_incident(PROJECT, source["incident_id"])
    followup = deepcopy(source)
    followup.update(run_id=uuid4().hex, revision=latest["latest_revision"] + 1)
    persist_result(followup, db)
    monkeypatch.setattr(report_service, "prepare_change", lambda *a, **k: pytest.fail("Duplicate incident execution"))
    recovered = report_service.prepare_submission(followup, db_path=db, proposer=proposer)
    assert recovered["job"]["work_id"] == first["job"]["work_id"] and proposer.calls == 1


def test_seed_intake_work_followup_resume_keeps_restriction_and_one_job(lab, monkeypatch):
    workspace, db = lab
    monkeypatch.setattr(change_policy, "WORKSPACE", workspace)
    monkeypatch.setattr(seed_project, "WORKSPACE", workspace)
    original = report_agent.investigate_submission("가입 실패를 고쳐줘. requestId=seed-signup-001", registered_seed=True,
                                                 db_path=db, memory_enabled=False)
    assert original["route"] == "WORK_CANDIDATE" and original["run_status"] == "COMPLETED"
    proposer = Proposer()
    job = report_service.prepare_submission(original, db_path=db, proposer=proposer)
    assert job["job"]["candidate_fix_verified"]
    reply = report_service.follow_up_service(original, "원본 수정하지 마. 조사만 해줘.", registered_seed=True,
                                            db_path=db, memory_enabled=False)
    assert reply["revision"] == 3 and reply["requested_action"] == "INVESTIGATE_ONLY" and reply["model_calls"] == 0
    assert report_service.auto_prepare_submission(reply, enabled=True, live=True, db_path=db) is None
    with IncidentStore(db) as store:
        resumed = store.resume_result(PROJECT, original["incident_id"])
        changes = store.list_changes(PROJECT, original["incident_id"])
    assert resumed["requested_action"] == "INVESTIGATE_ONLY" and len(changes) == proposer.calls == 1
    with pytest.raises(ValueError, match="조사만"):
        report_service.prepare_submission(reply, db_path=db, proposer=proposer)
