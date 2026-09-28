"""A2 tests use explicit TEST_DOUBLE proposals; no external model calls."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tracebridge import change_policy, change_proposal, change_worker
from tracebridge.change_policy import PolicyDenied, json_bytes, load_policy, read_source, safe_path, sha256, snapshot_hash
from tracebridge.change_worker import DEFAULT_POLICY, prepare_change, persist_change_result, seed_incident
from tracebridge.incident_memory import IncidentStore, RunConflict


ROOT = Path(__file__).resolve().parents[1]
PROJECT = "tracebridge-seed-signup"


@pytest.fixture
def lab(tmp_path):
    for relative in ("examples/seed_signup", "examples/change_policy"):
        destination = tmp_path / relative
        destination.mkdir(parents=True)
        for original in (ROOT / relative).iterdir():
            if original.is_file():
                shutil.copyfile(original, destination / original.name)
    return tmp_path, tmp_path / "state.sqlite3"


class ProposalStub:
    """Deliberately fake for deterministic safety/failure checks."""

    def __init__(self, transform=None, error=None):
        self.calls, self.contexts, self.transform, self.error = 0, [], transform, error

    def propose(self, context, timeout):
        self.calls += 1
        self.contexts.append(deepcopy(context))
        if self.error:
            raise self.error
        policy = context["policy"]
        raw = {"policy_id": policy["policy_id"], "policy_version": policy["version"], "target_id": policy["target_id"],
               "evidence_ids": context["evidence_ids"][:1], "rationale": "TEST_DOUBLE: align the seeded caller key with its contract",
               "check_ids": policy["check_ids"], "edits": [{"path": "client.py", "expected_sha256": policy["files"]["client.py"],
                                                            "old_key": 'user_id', "new_key": 'userId'}]}
        if self.transform:
            self.transform(raw)
        return raw


def run(lab, stub=None, **kwargs):
    workspace, db = lab
    source = seed_incident(workspace=workspace, db_path=db)
    assert source["persistence"]["status"] == "SAVED"
    result = prepare_change(PROJECT, source["run_id"], workspace=workspace, db_path=db, proposer=stub, **kwargs)
    return source, result


def register_test_baseline(lab, monkeypatch, *, client=None, checks=None, limits=None):
    """Test owner registration, never a report/model policy update."""
    workspace, _ = lab
    source = workspace / "examples/seed_signup"
    for name, value in (("client.py", client), ("checks.py", checks)):
        if value is not None:
            (source / name).write_bytes(value.encode())
    path = workspace / "examples/change_policy/seed-signup-a2-v1.json"
    policy = json.loads(path.read_bytes())
    files = {name: (source / name).read_bytes() for name in policy["files"]}
    policy.update(files={name: sha256(data) for name, data in files.items()}, snapshot_sha256=snapshot_hash(files))
    policy.update(limits or {})
    raw = json_bytes(policy)
    path.write_bytes(raw)
    monkeypatch.setattr(change_policy, "REGISTRATIONS", {DEFAULT_POLICY: (path.relative_to(workspace).as_posix(), sha256(raw))})


def test_actual_diff_same_check_regression_original_and_review_link(lab, monkeypatch):
    workspace, db = lab
    monkeypatch.setenv("NVIDIA_API_KEY", "must-not-enter-child")
    monkeypatch.setenv("TRACEBRIDGE_CANDIDATE_FIX", "1")
    monkeypatch.setenv("PYTHONPATH", "must-not-enter-child")
    policy, _ = load_policy(DEFAULT_POLICY, workspace)
    original = read_source(policy, workspace)
    source = seed_incident(workspace=workspace, db_path=db)
    with IncidentStore(db) as store:
        stored_source = store.get_run(PROJECT, source["run_id"])
        old_card = store.review_card(PROJECT, source["run_id"], "approve", reviewer="test-owner")
    stub = ProposalStub()
    result = prepare_change(PROJECT, source["run_id"], workspace=workspace, db_path=db, proposer=stub)
    job = result["job"]
    assert job["status"] == "CHANGE_PREPARED" and job["candidate_fix_verified"] and job["original_unchanged"]
    assert result["persistence"]["status"] == "SAVED" and stub.calls == 1
    assert job["review_status"] == "WAITING_REVIEW" and not job["original_applied"]
    assert job["deployment_status"] == "NOT_ATTEMPTED" and job["service_recovery"] == "NOT_VERIFIED"
    assert job["model"]["mode"] == "TEST_DOUBLE" and job["model"]["actual_calls"] == 0
    assert job["model"]["test_double_calls"] == 1 and job["case_kind"] == "SEEDED_DEVELOPMENT"
    before, after, regression = job["checks"]
    assert [check["exit_code"] for check in job["checks"]] == [1, 0, 0]
    assert before["argv"] == after["argv"] and before["input"] == after["input"]
    assert before["execution_settings_sha256"] == after["execution_settings_sha256"]
    assert before["timeout_seconds"] == after["timeout_seconds"] == regression["timeout_seconds"]
    assert "TRACEBRIDGE_CANDIDATE_FIX" not in job["execution"]["environment"]
    assert "NVIDIA_API_KEY" not in job["execution"]["environment"] and "PYTHONPATH" not in job["execution"]["environment"]
    assert not job["execution"]["os_sandbox"] and not job["execution"]["openshell_verified"]
    candidate = workspace / job["candidate"]["root"]
    assert (candidate / "client.py").read_bytes() != original["client.py"]
    assert '"userId"' in (candidate / "client.py").read_text()
    assert read_source(policy, workspace) == original
    assert (workspace / job["diff"]["ref"]).read_text().startswith("--- baseline/client.py")
    assert not any(result["run"][key] for key in ("fix_applied", "fix_verified", "cause_confirmed"))
    assert "report" not in stub.contexts[0] and "memory_search" not in stub.contexts[0]
    with IncidentStore(db) as store:
        assert store.get_run(PROJECT, source["run_id"]) == stored_source
        assert store.get_card(PROJECT, source["run_id"]) == old_card
        card = store.get_card(PROJECT, job["result_run_id"])
        assert card["review"]["status"] == "PENDING" and card["prepared_change"]["candidate_fix_verified"]
        assert not card["verification"]["fix_verified"]
        assert store.get_incident(PROJECT, source["incident_id"])["changes"][0]["work_id"] == job["work_id"]
        assert [item["revision"] for item in store.get_incident(PROJECT, source["incident_id"])["runs"]] == [1, 2]
        hits = store.search(PROJECT, signals={"paths": ["/api/users"]})["cards"]
        assert [card["card_id"] for card in hits] == [source["run_id"]]
        assert store.get_change(PROJECT, job["work_id"])["source_run_id"] == source["run_id"]
        with pytest.raises(ValueError):
            store.get_change("other-project", job["work_id"])
        store.review_card(PROJECT, job["result_run_id"], "approve", reviewer="test-owner")
        reviewed = store.get_card(PROJECT, job["result_run_id"])
    duplicate = prepare_change(PROJECT, source["run_id"], workspace=workspace, db_path=db, proposer=stub)
    assert duplicate["persistence"]["status"] == "ALREADY_SAVED" and stub.calls == 1
    with IncidentStore(db) as store:
        assert store.get_card(PROJECT, job["result_run_id"]) == reviewed


@pytest.mark.parametrize("variant", ["already_passed", "different_failure", "environment_error"])
def test_unrelated_or_passing_baseline_never_requests_a_fix(lab, monkeypatch, variant):
    text = (lab[0] / "examples/seed_signup/client.py").read_text()
    text = text.replace('"user_id"', '"userId"' if variant == "already_passed" else '"customerId"')
    if variant == "environment_error":
        text = "raise RuntimeError('test environment unavailable')\n" + text
    register_test_baseline(lab, monkeypatch, client=text)
    original = (lab[0] / "examples/seed_signup/client.py").read_bytes()
    stub = ProposalStub()
    _, result = run(lab, stub)
    job = result["job"]
    # Current caller evidence already contradicts this registered repair target.
    # Eligibility must stop before executing the baseline or asking a proposer.
    assert job["status"] == "POLICY_REJECTED" and not job["candidate_fix_verified"] and not job["diff"]
    assert stub.calls == 0 and not job["checks"]
    assert (lab[0] / "examples/seed_signup/client.py").read_bytes() == original


@pytest.mark.parametrize("mutation", [
    {"route": "REQUEST_CONTEXT", "run_status": "WAITING_CONTEXT"},
    {"route": "INVESTIGATE"}, {"correlation": "CONTEXT_CANDIDATE"}, {"run_status": "TIMED_OUT"},
    {"case_kind": "REAL_INCIDENT"}, {"diagnosis_type": "migration_missing"},
    {"log_scope": {"aggregate": {"complete": False}}},
    {"log_scope": {"aggregate": {"conflicts": [{"fields": ["response_status"]}]}}},
    {"scope": {"environment": "prod"}},
])
def test_held_conflicting_out_of_scope_incidents_are_blocked(lab, mutation):
    workspace, db = lab
    source = seed_incident(workspace=workspace, db_path=db)
    source.update(mutation, run_id=uuid4().hex, incident_id=uuid4().hex)
    with IncidentStore(db) as store:
        store.save_run(source)
    stub = ProposalStub()
    result = prepare_change(PROJECT, source["run_id"], workspace=workspace, db_path=db, proposer=stub)
    assert result["job"]["status"] == "POLICY_REJECTED" and not result["job"]["checks"] and stub.calls == 0
    assert result["persistence"]["status"] == "SAVED"


def test_missing_policy_blocks_before_any_execution(lab):
    _, result = run(lab, ProposalStub(), policy_id="unregistered-from-report")
    assert result["job"]["status"] == "POLICY_REJECTED" and not result["job"]["checks"]
    assert result["job"]["model"]["test_double_calls"] == 0


def test_real_project_has_no_registered_a2_authority(lab):
    workspace, db = lab
    source = seed_incident(workspace=workspace, db_path=db)
    source.update(project_id="agolive", case_kind="REAL_INCIDENT", run_id=uuid4().hex, incident_id=uuid4().hex)
    with IncidentStore(db) as store:
        store.save_run(source)
    stub = ProposalStub()
    result = prepare_change("agolive", source["run_id"], workspace=workspace, db_path=db, proposer=stub)
    assert result["job"]["status"] == "POLICY_REJECTED" and not result["job"]["checks"] and stub.calls == 0
    assert not result["job"]["original_applied"]


@pytest.mark.parametrize("path", ["checks.py", "api.py", "cases.json", ".env", "../client.py", "C:/client.py", "client.py:stream", "sub/client.py"])
def test_forbidden_paths_rejected_before_apply(lab, path):
    def mutate(raw):
        raw["edits"][0]["path"] = path
    _, result = run(lab, ProposalStub(mutate))
    assert result["job"]["status"] == "POLICY_REJECTED" and not result["job"]["diff"]
    assert len(result["job"]["checks"]) == 1
    candidate = lab[0] / Path(result["job"]["artifact_ref"]).parent / "candidate"
    assert (candidate / "client.py").read_bytes() == (lab[0] / "examples/seed_signup/client.py").read_bytes()


@pytest.mark.parametrize("kind", ["command", "shell", "policy", "hash", "evidence", "expression", "size", "duplicate", "whole_file"])
def test_invalid_model_commands_and_edits_never_execute(lab, kind):
    def mutate(raw):
        edit = raw["edits"][0]
        if kind == "command": raw["check_ids"] = ["curl-model-url", "signup-regression"]
        elif kind == "shell": raw["shell"] = "arbitrary SQL URL shell"
        elif kind == "policy": raw["policy_version"] = 999
        elif kind == "hash": edit["expected_sha256"] = "0" * 64
        elif kind == "evidence": raw["evidence_ids"] = ["historical:old:R1"]
        elif kind == "expression": edit["new_key"] = "__import__('os').system('bad')"
        elif kind == "size": edit["new_key"] = "x" * 100
        elif kind == "duplicate": raw["edits"].append(deepcopy(edit))
        elif kind == "whole_file": edit["old_key"] = "return"
    _, result = run(lab, ProposalStub(mutate))
    assert result["job"]["status"] == "POLICY_REJECTED" and not result["job"]["diff"]
    assert len(result["job"]["checks"]) == 1 and not result["job"]["candidate_fix_verified"]


@pytest.mark.parametrize("limit", ["max_changed_bytes", "max_changed_lines", "max_diff_bytes"])
def test_registered_diff_size_limit_blocks_before_edit(lab, monkeypatch, limit):
    register_test_baseline(lab, monkeypatch, limits={limit: 1})
    _, result = run(lab, ProposalStub())
    assert result["job"]["status"] == "POLICY_REJECTED" and not result["job"]["diff"]
    assert len(result["job"]["checks"]) == 1


def test_candidate_link_cannot_write_back_to_original(lab):
    workspace, _ = lab
    original = workspace / "examples/seed_signup/client.py"
    raw = original.read_bytes()
    def inject_link(proposal):
        copies = list((workspace / "output/changes").glob("*/candidate/client.py"))
        assert len(copies) == 1 and copies[0].absolute().is_relative_to(workspace.absolute())
        copies[0].unlink()
        os.link(original, copies[0])
    _, result = run(lab, ProposalStub(inject_link))
    assert result["job"]["status"] == "POLICY_REJECTED" and not result["job"]["diff"]
    assert original.read_bytes() == raw


@pytest.mark.parametrize("failure", ["related", "regression"])
def test_candidate_check_failure_never_becomes_verified(lab, failure):
    def mutate(raw):
        if failure == "related":
            raw["edits"][0]["new_key"] = 'customerId'
        else:
            extra = deepcopy(raw["edits"][0])
            extra.update(old_key='displayName', new_key='screenName')
            raw["edits"].append(extra)
    _, result = run(lab, ProposalStub(mutate))
    job = result["job"]
    assert job["status"] == "VERIFICATION_FAILED" and not job["candidate_fix_verified"]
    assert job["checks"][1]["status"] == ("FAILED" if failure == "related" else "PASSED")
    assert job["checks"][2]["status"] == "FAILED" and job["diff"] and job["original_unchanged"]


@pytest.mark.parametrize("phase", ["before", "after", "regression"])
def test_real_check_timeout_preserves_partial_stdout_and_prior_results(lab, monkeypatch, phase):
    text = (lab[0] / "examples/seed_signup/checks.py").read_text()
    condition = "True" if phase == "before" else "outcome['status'] == 'PASSED'" if phase == "after" else "sys.argv[1] == 'regression'"
    text = text.replace("print(json.dumps(outcome, sort_keys=True))", "print(json.dumps(outcome, sort_keys=True), flush=True)\n    import time\n    if " + condition + ": time.sleep(3)")
    register_test_baseline(lab, monkeypatch, checks=text, limits={"check_timeout_seconds": 1})
    stub = ProposalStub()
    _, result = run(lab, stub)
    job = result["job"]
    assert job["status"] == "TIMED_OUT" and not job["candidate_fix_verified"] and job["original_unchanged"]
    assert len(job["checks"]) == {"before": 1, "after": 2, "regression": 3}[phase]
    last = job["checks"][-1]
    assert last["status"] == "TIMED_OUT" and last["exit_code"] is None
    assert '"case_kind": "SEEDED_DEVELOPMENT"' in (lab[0] / last["stdout_ref"]).read_text()
    assert stub.calls == (0 if phase == "before" else 1)
    assert result["persistence"]["status"] == "SAVED"


@pytest.mark.parametrize("error", [ConnectionError("test service unavailable"), TimeoutError("test model timeout")])
def test_failed_model_is_not_retried_and_preserves_reproduction(lab, error):
    stub = ProposalStub(error=error)
    _, result = run(lab, stub)
    assert stub.calls == 1 and result["job"]["status"] == ("TIMED_OUT" if isinstance(error, TimeoutError) else "MODEL_FAILED")
    assert result["job"]["checks"][0]["status"] == "FAILED" and not result["job"]["diff"]


def test_no_live_or_test_provider_leaves_work_unverified(lab):
    _, result = run(lab)
    assert result["job"]["status"] == "MODEL_NOT_REQUESTED"
    assert result["job"]["model"]["actual_calls"] == 0 and not result["job"]["candidate_fix_verified"]


def test_missing_live_connection_is_distinct_from_a_test_double(lab, monkeypatch):
    monkeypatch.setattr(change_proposal, "nvidia_settings", lambda: (None, "nvidia/nemotron-unavailable"))
    _, result = run(lab, live=True)
    assert result["job"]["status"] == "MODEL_FAILED" and len(result["job"]["checks"]) == 1
    assert result["job"]["model"]["mode"] == "NVIDIA_LIVE"
    assert result["job"]["model"]["actual_calls"] == result["job"]["model"]["test_double_calls"] == 0
    assert not result["job"]["model"]["response_received"]


def test_unregistered_secret_files_are_not_copied_or_sent(lab):
    workspace, _ = lab
    (workspace / "examples/seed_signup/.env").write_text("SECRET=seed-secret-canary")
    (workspace / "examples/seed_signup/credentials.json").write_text('{"password":"seed-secret-canary"}')
    stub = ProposalStub()
    _, result = run(lab, stub)
    candidate = workspace / result["job"]["candidate"]["root"]
    assert {item.name for item in candidate.iterdir()} == {"README.md", "client.py", "api.py", "checks.py", "cases.json", "events.json"}
    assert "seed-secret-canary" not in json.dumps(stub.contexts)


def test_check_output_is_preserved_when_candidate_integrity_changes(lab, monkeypatch):
    workspace, _ = lab
    original_run = change_worker.subprocess.run
    def corrupt_after_check(*args, **kwargs):
        completed = original_run(*args, **kwargs)
        (Path(kwargs["cwd"]) / "checks.py").write_text("# test corruption")
        return completed
    monkeypatch.setattr(change_worker.subprocess, "run", corrupt_after_check)
    stub = ProposalStub()
    _, result = run(lab, stub)
    assert result["job"]["status"] == "POLICY_REJECTED" and stub.calls == 0
    assert result["job"]["checks"][0]["status"] == "POLICY_VIOLATION"
    assert result["job"]["checks"][0]["exit_code"] == 1
    assert (workspace / result["job"]["checks"][0]["stdout_ref"]).is_file()


@pytest.mark.parametrize("changed", ["policy", "source"])
def test_policy_or_baseline_hash_drift_blocks_execution(lab, changed):
    workspace, db = lab
    source = seed_incident(workspace=workspace, db_path=db)
    path = workspace / ("examples/change_policy/seed-signup-a2-v1.json" if changed == "policy" else "examples/seed_signup/client.py")
    path.write_bytes(path.read_bytes() + b"\n")
    stub = ProposalStub()
    result = prepare_change(PROJECT, source["run_id"], workspace=workspace, db_path=db, proposer=stub)
    assert result["job"]["status"] == "POLICY_REJECTED" and not result["job"]["checks"] and stub.calls == 0


def test_hardlinked_source_is_rejected(lab):
    workspace, db = lab
    source = seed_incident(workspace=workspace, db_path=db)
    original = workspace / "examples/seed_signup/client.py"
    os.link(original, workspace / "linked-client.py")
    result = prepare_change(PROJECT, source["run_id"], workspace=workspace, db_path=db, proposer=ProposalStub())
    assert result["job"]["status"] == "POLICY_REJECTED" and not result["job"]["checks"]


def test_junction_or_symlink_path_is_rejected(lab):
    workspace, _ = lab
    destination, alias = workspace / "real-directory", workspace / "alias-directory"
    destination.mkdir()
    if os.name == "nt":
        created = subprocess.run(["cmd", "/c", "mklink", "/J", str(alias), str(destination)], capture_output=True, check=False)
        if created.returncode:
            pytest.skip("Junction creation unavailable on this host")
    else:
        alias.symlink_to(destination, target_is_directory=True)
    with pytest.raises(PolicyDenied, match="Links"):
        safe_path(workspace, "alias-directory/new-file.py")


def test_check_tampering_and_unknown_command_rejected_before_process(lab, monkeypatch):
    workspace, _ = lab
    policy, _ = load_policy(DEFAULT_POLICY, workspace)
    expected = read_source(policy, workspace)
    candidate, artifact = workspace / "candidate", workspace / "artifact"
    candidate.mkdir()
    artifact.mkdir()
    for name, raw in expected.items():
        (candidate / name).write_bytes(raw)
    def no_process(*args, **kwargs):
        raise AssertionError("A rejected check must not execute")
    monkeypatch.setattr(change_worker.subprocess, "run", no_process)
    kwargs = dict(workspace=workspace, candidate=candidate, artifact=artifact, expected=expected, env={}, phase="before", deadline=10**20)
    with pytest.raises(PolicyDenied, match="command ID"):
        change_worker.run_registered_check(policy, "model-supplied-shell", **kwargs)
    (candidate / "checks.py").write_text("print('PASSED')")
    with pytest.raises(PolicyDenied, match="outside validated edits"):
        change_worker.run_registered_check(policy, "signup-contract", **kwargs)


def test_storage_failure_save_only_retry_is_atomic_and_keeps_review(lab, monkeypatch):
    workspace, db = lab
    original_save = IncidentStore.save_change_result
    def fail_save(*args, **kwargs):
        raise sqlite3.OperationalError("test disk failure")
    monkeypatch.setattr(IncidentStore, "save_change_result", fail_save)
    stub = ProposalStub()
    source, result = run(lab, stub)
    assert result["job"]["candidate_fix_verified"] and result["persistence"]["status"] == "FAILED"
    assert (workspace / result["job"]["artifact_ref"]).is_file()
    with IncidentStore(db) as store:
        assert len(store.get_incident(PROJECT, source["incident_id"])["runs"]) == 1
        old_card = store.review_card(PROJECT, source["run_id"], "approve", reviewer="owner")
    monkeypatch.setattr(IncidentStore, "save_change_result", original_save)
    def no_execution(*args, **kwargs):
        raise AssertionError("Save retry may not execute tools")
    monkeypatch.setattr(change_worker.subprocess, "run", no_execution)
    persist_change_result(result, db)
    assert result["persistence"]["status"] == "SAVED" and stub.calls == 1
    with IncidentStore(db) as store:
        assert store.get_card(PROJECT, source["run_id"]) == old_card
        store.review_card(PROJECT, result["job"]["result_run_id"], "reject", reviewer="owner")
        reviewed = store.get_card(PROJECT, result["job"]["result_run_id"])
    persist_change_result(result, db)
    assert result["persistence"]["status"] == "ALREADY_SAVED"
    with IncidentStore(db) as store:
        assert store.get_card(PROJECT, result["job"]["result_run_id"]) == reviewed
        changed = deepcopy(result)
        changed["job"]["elapsed_ms"] += 1
        with pytest.raises(RunConflict):
            store.save_change_result(changed)


def test_sql_failure_rolls_back_new_run_card_and_incident_projection(lab, monkeypatch):
    workspace, db = lab
    original_save = IncidentStore.save_change_result
    monkeypatch.setattr(IncidentStore, "save_change_result", lambda *args: (_ for _ in ()).throw(sqlite3.OperationalError()))
    source, result = run(lab, ProposalStub())
    monkeypatch.setattr(IncidentStore, "save_change_result", original_save)
    with IncidentStore(db) as store:
        before = store.get_incident(PROJECT, source["incident_id"])
        store.connection.execute("CREATE TRIGGER reject_change BEFORE INSERT ON change_jobs BEGIN SELECT RAISE(ABORT,'test failure'); END")
        with pytest.raises(RunConflict):
            store.save_change_result(result)
        assert store.get_incident(PROJECT, source["incident_id"]) == before
        with pytest.raises(ValueError):
            store.get_card(PROJECT, result["job"]["result_run_id"])


def test_existing_v1_records_and_review_survive_additive_schema_upgrade(lab):
    _, db = lab
    source = seed_incident(workspace=lab[0], db_path=db)
    with IncidentStore(db) as store:
        old_run = store.get_run(PROJECT, source["run_id"])
        old_card = store.review_card(PROJECT, source["run_id"], "approve", reviewer="owner")
        store.connection.execute("DROP TABLE change_jobs")
        store.connection.execute("PRAGMA user_version=1")
    with IncidentStore(db) as store:
        assert store.connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert store.get_run(PROJECT, source["run_id"]) == old_run
        assert store.get_card(PROJECT, source["run_id"]) == old_card


def test_live_adapter_forces_proposal_and_disables_retries_with_mock_http(monkeypatch):
    """Mock HTTP adapter check; this is not actual Nemotron evidence."""
    seen = {}
    def create(**kwargs):
        seen.update(kwargs)
        call = SimpleNamespace(function=SimpleNamespace(name="propose_change", arguments='{"mock":true}'))
        return SimpleNamespace(id="mock-response", usage=SimpleNamespace(prompt_tokens=5, completion_tokens=2),
            choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[call]), finish_reason="tool_calls")])
    def client(**kwargs):
        seen["client"] = kwargs
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(change_proposal, "nvidia_settings", lambda: ("mock-key", "nvidia/nemotron-mock"))
    monkeypatch.setattr(change_proposal, "OpenAI", client)
    audit = {}
    assert change_proposal.NemotronProposer().propose({"case_kind": "SEEDED_DEVELOPMENT"}, 3, audit) == {"mock": True}
    assert seen["client"]["max_retries"] == 0 and seen["timeout"] == 3
    assert seen["tool_choice"]["function"]["name"] == "propose_change"
    assert audit["response_id"] == "mock-response" and audit["response_received"]


def test_cli_intake_prepare_lookup_and_seed_labels(lab):
    # CLI uses the real pinned workspace target; all execution still uses a copy.
    workspace_db = ROOT / "output/validation" / ("stage4-cli-test-" + uuid4().hex + ".sqlite3")
    def cli(*arguments):
        process = subprocess.run([sys.executable, "-m", "scripts.prepare_change", "--db", str(workspace_db), *arguments],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8", check=False, timeout=20)
        assert process.returncode == 0, process.stderr
        return json.loads(process.stdout)
    source = cli("seed")
    result = cli("prepare", "--source-run", source["run_id"])
    job = cli("show", result["job"]["work_id"])
    assert source["route"] == "WORK_CANDIDATE" and source["case_kind"] == "SEEDED_DEVELOPMENT"
    assert job["source_run_id"] == source["run_id"] and job["status"] == "MODEL_NOT_REQUESTED"
    assert job["model"]["actual_calls"] == 0 and result["persistence"]["status"] == "SAVED"
