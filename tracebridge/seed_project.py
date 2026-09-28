"""Registered development observations and bounded source reads for the local service."""

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from uuid import uuid4

from .change_policy import CASE_KIND, WORKSPACE, PolicyDenied, load_policy, read_source, safe_path, sha256
from .project_sources import Evidence, redact
from .report_intake import LocalEventCatalog


POLICY_ID = "seed-signup-a2-v1"
OBSERVATIONS = "output/seed-observations"


def registered_seed(workspace: Path | None = None):
    workspace = workspace or WORKSPACE
    policy, digest = load_policy(POLICY_ID, workspace)
    files = read_source(policy, workspace)
    return policy, digest, files


def seed_catalog(workspace: Path | None = None) -> LocalEventCatalog:
    """Read actual captured seed checks; use the labeled snapshot before any capture."""
    workspace = workspace or WORKSPACE
    policy, _, files = registered_seed(workspace)
    folder = safe_path(workspace, OBSERVATIONS)
    paths = sorted(folder.glob("*/event.json")) if folder.exists() else []
    if len(paths) > LocalEventCatalog.MAX_EVENTS:
        raise PolicyDenied("Registered seed observation limit exceeded")
    events = []
    for path in paths:
        path = safe_path(workspace, path.relative_to(workspace).as_posix(), file=True)
        if path.stat().st_size > 16_000:
            raise PolicyDenied("Seed observation exceeds its size limit")
        item = json.loads(path.read_bytes())
        if (item.get("case_kind") != CASE_KIND or item.get("baseline_sha256") != policy.snapshot_sha256
                or item.get("project_id") != policy.project_id or item.get("capture", {}).get("status") != "FAILED"
                or item.get("capture", {}).get("exit_code") != 1
                or item.get("capture", {}).get("failure_signature") != policy.failure_signature):
            raise PolicyDenied("Seed observation registration or reproduction differs")
        capture, event = item["capture"], item["event"]
        stdout = safe_path(workspace, capture["check_ref"], file=True)
        if stdout.stat().st_size > 16_000 or sha256(stdout.read_bytes()) != capture["stdout_sha256"]:
            raise PolicyDenied("Captured check evidence changed")
        check = json.loads(stdout.read_bytes())
        observed, trace = check.get("observation", {}), event["trace"]
        if (check.get("status") != "FAILED" or check.get("failure_signature") != policy.failure_signature
                or check.get("inputs_sha256") != policy.files["cases.json"]
                or trace.get("response_status") != observed.get("response_status")
                or set(trace.get("request", {})) != set(observed.get("request_fields", []))
                or trace.get("version") != policy.baseline_version
                or any(trace.get(key) != value for key, value in policy.environment.items())):
            raise PolicyDenied("Seed observation does not match its executed check")
        events.append(item["event"])
    data = {"project_id": policy.project_id, "events": events} if events else json.loads(files["events.json"])
    catalog = LocalEventCatalog(data)
    catalog.locator = "registered-seed:" + policy.policy_id
    catalog.source_kind = "executed_seed_observation" if events else "registered_seed_snapshot"
    return catalog


def seed_code(terms: list[str], *, workspace: Path | None = None) -> list[Evidence]:
    """Only the policy's two trusted source files can enter investigation context."""
    policy, _, files = registered_seed(workspace)
    terms = [term.strip().casefold() for term in terms if 2 <= len(term.strip()) <= 80][:8]
    hits = []
    for name in ("client.py", "api.py"):
        lines = files[name].decode("utf-8").splitlines()
        for index, line in enumerate(lines):
            if any(term in line.casefold() or term in name for term in terms):
                content = "\n".join(lines[max(0, index - 2):index + 3])
                hits.append(Evidence("", "code", f"{name}:{index + 1}", redact(content),
                                     "seed-api" if name == "api.py" else "seed-client",
                                     source_revision=policy.snapshot_sha256))
                break
    return hits[:3]


def capture_seed_action(workspace: Path | None = None) -> dict:
    """Execute the immutable seed check on a copy and record its current observation."""
    from .change_worker import _candidate_files, _environment, run_registered_check

    workspace = workspace or WORKSPACE
    policy, _, files = registered_seed(workspace)
    root = safe_path(workspace, OBSERVATIONS)
    if root.exists() and len(list(root.glob("*/event.json"))) >= LocalEventCatalog.MAX_EVENTS:
        raise PolicyDenied("Seed capture is limited to 100 observations")
    execution_id = uuid4().hex
    artifact = safe_path(workspace, OBSERVATIONS + "/" + execution_id)
    artifact.mkdir(parents=True)
    candidate, scratch = artifact / "candidate", artifact / "scratch"
    candidate.mkdir()
    scratch.mkdir()
    for name, raw in files.items():
        safe_path(workspace, (candidate / name).relative_to(workspace).as_posix()).write_bytes(raw)
    when = datetime.now(timezone.utc).isoformat()
    check = run_registered_check(policy, "signup-contract", workspace=workspace, candidate=candidate,
                                 artifact=artifact, expected=files, env=_environment(scratch),
                                 phase="before", deadline=time.monotonic() + policy.total_seconds)
    output = check.get("result", {})
    if (check["status"] != "FAILED" or check["exit_code"] != 1
            or output.get("failure_signature") != policy.failure_signature):
        raise PolicyDenied("The registered signup failure was not reproduced")
    _candidate_files(workspace, candidate, files)
    read_source(policy, workspace)
    event = deepcopy(json.loads(files["events.json"])["events"][0])
    trace = event["trace"]
    trace.update(trace_id="seed-signup-" + execution_id[:12], occurred_at=when,
                 response_status=output["observation"]["response_status"],
                 request={name: "[VALUE]" for name in output["observation"]["request_fields"]})
    event["logs"] = [f"Registered seed check: {policy.failure_signature}; response_status={trace['response_status']}"]
    record = {"case_kind": CASE_KIND, "project_id": policy.project_id,
              "baseline_sha256": policy.snapshot_sha256, "event": event,
              "capture": {"execution_id": execution_id, "status": check["status"], "exit_code": check["exit_code"],
                          "failure_signature": output["failure_signature"], "check_ref": check["stdout_ref"],
                          "stdout_sha256": check["stdout_sha256"], "input_sha256": check["input"]["sha256"],
                          "origin": "REGISTERED_SEED_COPY_IN_PROCESS_API", "deployment_observed": False}}
    destination = artifact / "event.json"
    temporary = artifact / "event.tmp"
    temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(destination)
    return record
