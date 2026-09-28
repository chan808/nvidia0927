"""One A2 preparation attempt on a registered trusted seed copy; never deploys."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
from uuid import uuid4

from . import change_policy
from .change_policy import CASE_KIND, COMMANDS, LIMITATIONS, PolicyDenied, authorize_source, json_bytes, load_policy, read_source, safe_path, sha256, snapshot_hash
from .change_proposal import NemotronProposer, validate_proposal
from .incident_memory import IncidentStore, db_location, persist_result
from .report_intake import LocalEventCatalog, triage_report


DEFAULT_POLICY = "seed-signup-a2-v1"


def _database(workspace: Path, value: str | Path | None) -> Path:
    path = db_location(value if value is not None else workspace / "output/changes/seed-incidents.sqlite3")
    if not path.absolute().is_relative_to(workspace.absolute()):
        raise PolicyDenied("A2 records must stay inside this development workspace")
    return safe_path(workspace, path.absolute().relative_to(workspace.absolute()).as_posix())


def seed_incident(report: str = "씨드 개발 회원가입이 실패해요. requestId=seed-signup-001", *, db_path=None, workspace: Path | None = None) -> dict:
    """Developer-owned synthetic input using the existing intake/routing, not new rules."""
    workspace = workspace or change_policy.WORKSPACE
    policy, digest = load_policy(DEFAULT_POLICY, workspace)
    files = read_source(policy, workspace)
    catalog = LocalEventCatalog(json.loads(files["events.json"]))
    catalog.locator = str(safe_path(workspace, policy.source_root + "/events.json", file=True))
    result = triage_report(report, catalog)
    result.update(case_kind=CASE_KIND, development_target={"target_id": policy.target_id,
                  "snapshot_sha256": policy.snapshot_sha256, "source_origin": policy.source_origin},
                  executed_at=datetime.now(timezone.utc).isoformat())
    return persist_result(result, _database(workspace, db_path))


def _write(path: Path, value) -> None:
    path.write_bytes(json_bytes(value) + b"\n")


def _ref(workspace: Path, path: Path) -> str:
    return path.relative_to(workspace).as_posix()


def _candidate_files(workspace: Path, candidate: Path, expected: dict[str, bytes]) -> dict[str, bytes]:
    safe_path(workspace, _ref(workspace, candidate))
    if {path.name for path in candidate.iterdir()} != set(expected):
        raise PolicyDenied("Candidate contains unregistered files or directories")
    actual = {}
    for name, raw in expected.items():
        actual[name] = safe_path(workspace, _ref(workspace, candidate / name), file=True).read_bytes()
        if actual[name] != raw:
            raise PolicyDenied("Candidate file changed outside validated edits: " + name)
    return actual


def _environment(scratch: Path) -> dict[str, str]:
    env = {name: os.environ[name] for name in ("SystemRoot", "WINDIR") if name in os.environ}
    env.update(TEMP=str(scratch), TMP=str(scratch))
    return env


def run_registered_check(policy, command_id: str, *, workspace: Path, candidate: Path, artifact: Path,
                         expected: dict[str, bytes], env: dict[str, str], phase: str, deadline: float) -> dict:
    if command_id not in policy.check_ids or command_id not in COMMANDS:
        raise PolicyDenied("Check command ID is not registered")
    if phase not in {"before", "after", "regression"}:
        raise PolicyDenied("Unregistered check phase")
    _candidate_files(workspace, candidate, expected)
    argv = [sys.executable, "-I", "-B", "checks.py", COMMANDS[command_id]]
    remaining = deadline - time.monotonic()
    if remaining < policy.check_timeout_seconds:
        raise TimeoutError("Preparation budget exhausted before check")
    started = time.monotonic()
    item = {"phase": phase, "command_id": command_id, "argv": argv, "cwd": _ref(workspace, candidate),
            "input": {"id": "signup-cases-v1", "ref": _ref(workspace, candidate / "cases.json"), "sha256": sha256(expected["cases.json"])},
            "execution_settings_sha256": sha256(json_bytes({"argv": argv, "env": env, "timeout_seconds": policy.check_timeout_seconds})),
            "timeout_seconds": policy.check_timeout_seconds, "started_at": datetime.now(timezone.utc).isoformat()}
    try:
        completed = subprocess.run(argv, cwd=candidate, env=env, capture_output=True, timeout=item["timeout_seconds"], check=False, shell=False)
        stdout, stderr = completed.stdout, completed.stderr
        item.update(exit_code=completed.returncode, status="FAILED")
        try:
            output = json.loads(stdout)
            if (output.get("case_kind") == CASE_KIND and output.get("check_id") == command_id
                    and output.get("inputs_sha256") == item["input"]["sha256"]
                    and output.get("status") in {"PASSED", "FAILED"}
                    and completed.returncode == (0 if output["status"] == "PASSED" else 1)):
                item.update(status=output["status"], result=output)
            else:
                item.update(status="INVALID_RESULT", result={"error_type": output.get("error_type", "UnexpectedCheckResult")})
        except (ValueError, AttributeError, UnicodeError):
            item["status"] = "INVALID_RESULT"
    except subprocess.TimeoutExpired as exc:
        stdout, stderr = exc.stdout or b"", exc.stderr or b""
        item.update(exit_code=None, status="TIMED_OUT")
    except OSError as exc:
        stdout, stderr = b"", type(exc).__name__.encode()
        item.update(exit_code=None, status="EXECUTION_ERROR", error_type=type(exc).__name__)
    item["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    for name, raw in (("stdout", stdout), ("stderr", stderr)):
        raw = raw.encode() if isinstance(raw, str) else raw
        path = artifact / (phase + "." + name + ".txt")
        path.write_bytes(raw[:32_000])
        item[name + "_ref"], item[name + "_sha256"] = _ref(workspace, path), sha256(raw)
        item[name + "_truncated"] = len(raw) > 32_000
    try:
        _candidate_files(workspace, candidate, expected)
        item["candidate_integrity"] = "UNCHANGED"
    except PolicyDenied as exc:
        item.update(status="POLICY_VIOLATION", candidate_integrity="CHANGED", rejection_reason=str(exc))
    _write(artifact / (phase + ".json"), item)
    return item


def _reproduced(check: dict, signature: str) -> bool:
    result = check.get("result", {})
    observed = result.get("observation", {})
    return (check["status"] == "FAILED" and check["exit_code"] == 1
            and result.get("failure_kind") == "contract_field_mismatch" and result.get("failure_signature") == signature
            and observed.get("response_status") == 422
            and "user_id" in observed.get("request_fields", []) and "userId" not in observed.get("request_fields", []))


def _work_run(source: dict, job: dict, revision: int) -> dict:
    result = deepcopy(source)
    result.update(run_id=job["result_run_id"], revision=revision, executed_at=job["finished_at"],
                  run_status="COMPLETED" if job["status"] == "CHANGE_PREPARED" else "TIMED_OUT" if job["status"] == "TIMED_OUT" else "PARTIAL_FAILURE",
                  stop_reason=job["status"].lower(), observations=[], evidence=[], hypotheses=[], report_clues=[],
                  steps=[], service_calls=[], model_trace=[], model_calls=job["model"]["actual_calls"], usage=job["model"].get("usage", {}),
                  cause_confirmed=False, fix_applied=False, fix_verified=False,
                  summary="씨드 격리 후보의 동일 검사와 회귀가 통과했습니다. 원본 적용 없이 검토 대기입니다." if job["candidate_fix_verified"] else "씨드 격리 수정 검증은 완료되지 않았습니다: " + job["status"],
                  next_action="격리 후보 diff와 검증 범위를 담당자가 검토하세요." if job["candidate_fix_verified"] else "확보한 실행 결과와 실패/보류 이유를 검토하세요.")
    result["change"] = {key: deepcopy(job[key]) for key in ("work_id", "source_run_id", "status", "review_status", "case_kind", "policy", "diff",
                        "candidate_fix_verified", "original_applied", "deployment_status", "service_recovery", "artifact_ref")}
    result["change"]["model_mode"] = job["model"]["mode"]
    result["change"]["verification_scope"] = "REGISTERED_SEED_COPY_ONLY"
    return result


def prepare_change(project_id: str, source_run_id: str, *, policy_id: str = DEFAULT_POLICY, db_path=None,
                   live: bool = False, proposer=None, workspace: Path | None = None) -> dict:
    """Internal developer entry point. Injected proposers are always labeled TEST_DOUBLE."""
    workspace = (workspace or change_policy.WORKSPACE).absolute()
    database = _database(workspace, db_path)
    with IncidentStore(database) as store:
        source = store.get_run(project_id, source_run_id)
        existing = store.find_change(project_id, source_run_id)
        if existing:
            return {"job": existing, "run": store.get_run(project_id, existing["result_run_id"]), "persistence": {"status": "ALREADY_SAVED"}}
        incident = store.get_incident(project_id, source["incident_id"])
    work_id, result_run_id = uuid4().hex, uuid4().hex
    relative = "output/changes/" + work_id
    artifact = safe_path(workspace, relative)
    artifact.mkdir(parents=True, exist_ok=False)
    candidate = artifact / "candidate"
    job = {"record_format": "change_job_v1", "work_id": work_id, "project_id": project_id, "incident_id": source["incident_id"],
           "worker_protocol_version": 1, "worker_code_sha256": {name: sha256((Path(__file__).parent / name).read_bytes())
                 for name in ("change_worker.py", "change_proposal.py", "change_policy.py")},
           "source_run_id": source_run_id, "result_run_id": result_run_id, "source_revision": source["revision"],
           "case_kind": source.get("case_kind", "UNCLASSIFIED"), "status": "POLICY_REJECTED", "review_status": "WAITING_REVIEW",
           "candidate_fix_verified": False, "original_applied": False, "deployment_status": "NOT_ATTEMPTED", "service_recovery": "NOT_VERIFIED",
           "started_at": datetime.now(timezone.utc).isoformat(), "policy": {"policy_id": policy_id, "decision": "DENIED"},
           "source_record_sha256": sha256(json_bytes(source)), "artifact_ref": relative + "/result.json",
           "baseline": {}, "candidate": {}, "diff": {}, "checks": [], "attempts": [], "limitations": LIMITATIONS.copy(),
           "model": {"mode": "TEST_DOUBLE" if proposer is not None else "NVIDIA_LIVE" if live else "NOT_REQUESTED",
                     "actual_calls": 0, "test_double_calls": 0, "response_received": False, "status": "NOT_REQUESTED"}}
    started = time.monotonic()
    baseline, policy = None, None
    try:
        policy, digest = load_policy(policy_id, workspace)
        job["policy"].update(version=policy.version, sha256=digest, authorization=policy.authorization)
        if incident["latest_run_id"] != source_run_id:
            raise PolicyDenied("Only the latest unmodified source run can start preparation")
        authorize_source(source, policy)
        baseline = read_source(policy, workspace)
        job["policy"]["decision"] = "ALLOWED"
        deadline = started + policy.total_seconds
        job["baseline"] = {"version": policy.baseline_version, "snapshot_sha256": policy.snapshot_sha256,
                           "file_hashes": dict(policy.files), "source_root": policy.source_root, "source_origin": policy.source_origin}
        # Only the six registered files are copied; no repository walk, .env or credentials.
        candidate.mkdir()
        for name, raw in baseline.items():
            safe_path(workspace, relative + "/candidate/" + name).write_bytes(raw)
        scratch = artifact / "scratch"
        scratch.mkdir()
        env = _environment(scratch)
        job["execution"] = {"mode": policy.execution_mode, "openshell_verified": False, "os_sandbox": False,
                            "environment": env, "check_ids": policy.check_ids, "max_attempts": policy.max_attempts,
                            "total_seconds": policy.total_seconds, "network_enforcement": "NONE_TRUSTED_SOURCE_ONLY"}
        before = run_registered_check(policy, policy.check_ids[0], workspace=workspace, candidate=candidate, artifact=artifact,
                                      expected=baseline, env=env, phase="before", deadline=deadline)
        job["checks"].append(before)
        if before["status"] == "POLICY_VIOLATION":
            job["status"] = "POLICY_REJECTED"
        elif before["status"] == "TIMED_OUT":
            job["status"] = "TIMED_OUT"
        elif not _reproduced(before, policy.failure_signature):
            job["status"] = "NOT_REPRODUCED"
        elif not live and proposer is None:
            job["status"] = "MODEL_NOT_REQUESTED"
        else:
            context = {"case_kind": CASE_KIND, "policy": policy.model_dump(),
                       "source_run_id": source_run_id, "evidence_ids": [item["id"] for item in source["observations"]],
                       "observed_facts": [item.get("content", "") for item in source["observations"]][:4],
                       "source_files": {name: baseline[name].decode() for name in ("client.py", "api.py")},
                       "reproduction_failure": before["result"], "inputs": json.loads(baseline["cases.json"])}
            _write(artifact / "model-context.json", context)
            job["model"]["context_sha256"] = sha256(json_bytes(context))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Preparation budget exhausted before proposal")
            attempt = {"attempt": 1, "status": "REQUESTED"}
            job["attempts"].append(attempt)
            job["status"] = "MODEL_FAILED"
            job["model"]["status"] = "REQUESTED"
            if proposer is not None:
                job["model"]["test_double_calls"] = 1
                raw = proposer.propose(context, min(policy.model_timeout_seconds, remaining))
                job["model"].update(status="RECEIVED", response_received=True)
            else:
                raw = NemotronProposer().propose(context, min(policy.model_timeout_seconds, remaining), job["model"])
            if time.monotonic() >= deadline:
                raise TimeoutError("Preparation budget exhausted after proposal")
            # Persist validated proposals only; rejected response bodies are not durable data.
            job["model"]["proposal_sha256"] = sha256(json_bytes(raw))
            job["status"] = "POLICY_REJECTED"
            job["policy"]["edit_decision"] = "DENIED"
            expected, diff, edit = validate_proposal(raw, policy, baseline, set(context["evidence_ids"]))
            job["policy"]["edit_decision"] = "ALLOWED"
            _candidate_files(workspace, candidate, baseline)
            read_source(policy, workspace)
            _write(artifact / "proposal.json", edit.pop("proposal"))
            (artifact / "candidate.diff").write_bytes(diff.encode())
            job["diff"] = {"ref": relative + "/candidate.diff", "sha256": sha256(diff.encode()), **edit}
            for name in edit["changed_files"]:
                safe_path(workspace, relative + "/candidate/" + name, file=True).write_bytes(expected[name])
            attempt["status"] = "APPLIED_TO_COPY"
            job["candidate"] = {"version": snapshot_hash(expected), "snapshot_sha256": snapshot_hash(expected),
                                "file_hashes": {name: sha256(raw) for name, raw in expected.items()}, "root": relative + "/candidate"}
            job["status"] = "VERIFICATION_FAILED"
            after = run_registered_check(policy, policy.check_ids[0], workspace=workspace, candidate=candidate, artifact=artifact,
                                         expected=expected, env=env, phase="after", deadline=deadline)
            job["checks"].append(after)
            if after["status"] == "POLICY_VIOLATION":
                job["status"] = "POLICY_REJECTED"
            elif after["status"] == "TIMED_OUT":
                job["status"] = "TIMED_OUT"
            else:
                regression = run_registered_check(policy, policy.check_ids[1], workspace=workspace, candidate=candidate, artifact=artifact,
                                                  expected=expected, env=env, phase="regression", deadline=deadline)
                job["checks"].append(regression)
                identical = before["argv"] == after["argv"] and before["input"] == after["input"] and before["execution_settings_sha256"] == after["execution_settings_sha256"]
                job["identical_related_check"] = identical
                if regression["status"] == "POLICY_VIOLATION":
                    job["status"] = "POLICY_REJECTED"
                elif regression["status"] == "TIMED_OUT":
                    job["status"] = "TIMED_OUT"
                elif identical and after["status"] == regression["status"] == "PASSED":
                    read_source(policy, workspace)
                    job.update(status="CHANGE_PREPARED", candidate_fix_verified=True)
                attempt["status"] = job["status"]
    except Exception as exc:
        if isinstance(exc, PolicyDenied) or type(exc).__name__ in {"ValidationError", "JSONDecodeError"}:
            job["status"] = "POLICY_REJECTED"
        elif isinstance(exc, TimeoutError) or "timeout" in type(exc).__name__.lower():
            job["status"] = "TIMED_OUT"
        job.update(candidate_fix_verified=False, error_type=type(exc).__name__)
        if isinstance(exc, PolicyDenied):
            job["policy"]["rejection_reason"] = str(exc)
        if job["attempts"]:
            job["attempts"][-1]["status"] = job["status"]
            if job["model"]["status"] == "REQUESTED":
                job["model"].update(status="TIMED_OUT" if job["status"] == "TIMED_OUT" else "FAILED", error_type=type(exc).__name__)
    if baseline is not None:
        try:
            job["original_unchanged"] = read_source(policy, workspace) == baseline
        except (OSError, ValueError):
            job.update(original_unchanged=False, candidate_fix_verified=False, status="POLICY_REJECTED")
    job.update(finished_at=datetime.now(timezone.utc).isoformat(), elapsed_ms=round((time.monotonic() - started) * 1000))
    result = {"job": job, "run": _work_run(source, job, incident["latest_revision"] + 1)}
    _write(artifact / "result.json", result)
    persist_change_result(result, database)
    _write(artifact / "result.json", result)
    return result


def persist_change_result(result: dict, db_path=None) -> dict:
    """Retry only these obtained records, with no subprocess or model invocation."""
    try:
        with IncidentStore(db_path) as store:
            status = store.save_change_result(result)
        result["persistence"] = {"status": status, "work_id": result["job"]["work_id"]}
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
        result["persistence"] = {"status": "FAILED", "error_type": type(exc).__name__, "retry": "save_obtained_change_result_without_execution"}
    return result
