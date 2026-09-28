"""Registered development observations and bounded source reads for the local service."""

from copy import deepcopy
import ast
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


class SeedEvidenceSource:
    """The registered seed's checked code/input/version attribution for B's API."""

    def __init__(self, source, policy, files, workspace):
        self.source, self.policy, self.files, self.workspace = source, policy, files, workspace
        function = next(node for node in ast.parse(files["client.py"]).body
                        if isinstance(node, ast.FunctionDef) and node.name == policy.editable_function)
        returned = next(node.value for node in ast.walk(function) if isinstance(node, ast.Return))
        if not isinstance(returned, ast.Dict) or any(not isinstance(key, ast.Constant) or not isinstance(key.value, str)
                                                   or not isinstance(value, ast.Name) for key, value in zip(returned.keys, returned.values)):
            raise PolicyDenied("Registered seed caller mapping is unsupported")
        self.mapping = {key.value: value.id for key, value in zip(returned.keys, returned.values)}
        self.inputs = json.loads(files["cases.json"])["happy"]

    def get_trace(self, trace_id):
        trace = deepcopy(self.source.get_trace(trace_id))
        trace.setdefault("project_id", self.policy.project_id)
        trace.setdefault("code_version", self.policy.baseline_version)
        # Both snapshot and captured check use the registered happy input. Values
        # are omitted; the captured check/input hash is checked by seed_catalog.
        names = set(trace.get("request", {}))
        trace["request_types"] = {name: "string" for name, input_name in self.mapping.items()
                                  if name in names and isinstance(self.inputs.get(input_name), str)}
        return trace

    def get_contract(self, trace_id):
        result = deepcopy(self.source.get_contract(trace_id))
        trace = self.get_trace(trace_id)
        result.update(
            provenance={"kind": "registered_seed_contract", "source": self.policy.source_root + "/api.py",
                        "sha256": self.policy.files["api.py"], "code_version": self.policy.baseline_version},
            versions={key: self.policy.baseline_version for key in ("contract", "dto", "caller", "code")},
            caller={"source": self.policy.source_root + "/client.py#" + self.policy.editable_function,
                    "source_kind": "caller_code", "source_verified": True, "source_sha256": self.policy.files["client.py"],
                    "version": self.policy.baseline_version, "method": trace.get("method"), "path": trace.get("path"),
                    "project_id": self.policy.project_id, "service": trace.get("service"), "environment": trace.get("environment"),
                    "field_mapping": self.mapping, "required_inputs": {"userId": "user_id"},
                    "input_fields": list(self.inputs), "input_types": {name: "string" for name, value in self.inputs.items() if isinstance(value, str)}},
        )
        result["versions"]["runtime"] = trace.get("version")
        return result

    def get_backend_evidence(self, trace_id):
        return self.source.get_backend_evidence(trace_id)

    def get_migration_state(self, trace_id):
        return self.source.get_migration_state(trace_id)


def bind_seed_catalog(catalog, policy, files, workspace):
    """Attach only this owner-registered seed's provenance; no generic shortcut."""
    catalog.events = {trace_id: (SeedEvidenceSource(source, policy, files, workspace), when)
                      for trace_id, (source, when) in catalog.events.items()}
    return catalog


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
    return bind_seed_catalog(catalog, policy, files, workspace)


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
