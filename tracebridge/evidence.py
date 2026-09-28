"""Read-only evidence sources for one incident at a time."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .fixtures import get_case

if TYPE_CHECKING:
    from .project_profile import ProjectProfile


class EvidenceError(ValueError):
    def __init__(self, *args, details: dict | None = None):
        super().__init__(*args)
        self.details = deepcopy(details) if isinstance(details, dict) else None


class EvidenceSource(Protocol):
    def get_trace(self, trace_id: str) -> dict: ...

    def get_contract(self, trace_id: str) -> dict: ...

    def get_backend_evidence(self, trace_id: str) -> dict: ...

    def get_migration_state(self, trace_id: str) -> dict: ...


def _valid_trace_id(trace_id: str) -> bool:
    return isinstance(trace_id, str) and bool(trace_id) and len(trace_id) <= 64


def verify_synthetic_context(context: dict) -> dict:
    """Recheck explicitly declared demo provenance; never infer it from an ID."""
    result = deepcopy(context)
    provenance = result.get("provenance")
    if not isinstance(provenance, dict) or provenance.get("kind") != "synthetic_fixture":
        return result
    caller = result.get("caller")
    if not isinstance(caller, dict):
        return result
    caller["source_verified"] = False
    root = Path(__file__).resolve().parents[1]
    source = caller.get("source")
    relative = source.split("#", 1)[0] if isinstance(source, str) else ""
    path = root / relative
    try:
        if (not relative or not path.resolve().is_relative_to(root)
                or path.is_symlink() or any(parent.is_symlink() for parent in path.parents if parent.is_relative_to(root))):
            return result
        if path.stat().st_size > 1_000_000:
            return result
        data = path.read_bytes()
        caller["source_bytes_sha256"] = hashlib.sha256(data).hexdigest()
        if caller.get("source_hash_format") == "lf":
            data = data.replace(b"\r\n", b"\n")
        caller["source_verified"] = hashlib.sha256(data).hexdigest() == caller.get("source_sha256")
    except (OSError, ValueError):
        pass
    return result


class FixtureEvidenceSource:
    """The existing synthetic data, exposed through the same source contract."""

    def _case(self, trace_id: str) -> dict:
        if not _valid_trace_id(trace_id):
            raise EvidenceError("Invalid trace ID")
        case = get_case(trace_id)
        if case is None:
            raise EvidenceError("Trace not found. Ask for a valid trace ID, environment, and time.")
        return case

    def get_trace(self, trace_id: str) -> dict:
        return self._case(trace_id)["trace"]

    def get_contract(self, trace_id: str) -> dict:
        case = self._case(trace_id)
        result = {
            "method": case["trace"].get("method"),
            "path": case["trace"].get("path"),
            "openapi": case["contract"],
            "backend_dto": case["dto"],
        }
        # Explicitly synthetic provenance preserves the fixed regression examples.
        # Generic bundle/project sources never infer caller behavior from a case id.
        context_path = Path(__file__).resolve().parents[1] / "examples" / "parallel_b" / "fixture-context.json"
        try:
            contexts = json.loads(context_path.read_text(encoding="utf-8"))
            context = contexts.get(trace_id, {})
        except (OSError, ValueError):
            context = {}
        caller = context.get("caller") if isinstance(context, dict) else None
        if isinstance(caller, dict):
            source_path = Path(__file__).resolve().parents[1] / caller["source"].split("#", 1)[0]
            try:
                source_bytes = source_path.read_bytes()
                caller["source_bytes_sha256"] = hashlib.sha256(source_bytes).hexdigest()
                if caller.get("source_hash_format", "raw") == "lf":
                    source_bytes = source_bytes.replace(b"\r\n", b"\n")
                caller["source_verified"] = hashlib.sha256(source_bytes).hexdigest() == caller.get("source_sha256")
            except OSError:
                caller["source_verified"] = False
        result.update(deepcopy(context))
        return result

    def get_backend_evidence(self, trace_id: str) -> dict:
        case = self._case(trace_id)
        return {"trace_id": trace_id, "logs": case["logs"][:50]}

    def get_migration_state(self, trace_id: str) -> dict:
        case = self._case(trace_id)
        return {"environment": case["trace"]["environment"], "observation_status": "OBSERVED",
                "source_kind": "synthetic_fixture", **case["migration"]}


class LocalBundleEvidenceSource:
    """One bounded JSON incident bundle. This source is for offline CLI use only."""

    MAX_BYTES = 1_000_000
    MAX_LOG_LINES = 200
    MAX_LOG_LENGTH = 4000

    def __init__(self, bundle: dict):
        if not isinstance(bundle, dict) or not isinstance(bundle.get("trace"), dict):
            raise EvidenceError("Bundle must contain a trace object")
        trace = bundle["trace"]
        trace_id = trace.get("trace_id")
        if not _valid_trace_id(trace_id):
            raise EvidenceError("Bundle trace_id must be a nonempty string of at most 64 characters")
        for key in ("environment", "service"):
            if not isinstance(trace.get(key), str) or not trace[key].strip():
                raise EvidenceError(f"Bundle trace.{key} must be a nonempty string")
        status = trace.get("response_status")
        if type(status) is not int or not 100 <= status <= 599:
            raise EvidenceError("Bundle trace.response_status must be an HTTP status")
        for key in ("method", "path", "operation", "version"):
            if key in trace and not isinstance(trace[key], str):
                raise EvidenceError(f"Bundle trace.{key} must be a string")
        if "request" in trace and not isinstance(trace["request"], dict):
            raise EvidenceError("Bundle trace.request must be an object")
        logs = bundle.get("logs", [])
        if (
            not isinstance(logs, list)
            or len(logs) > self.MAX_LOG_LINES
            or any(not isinstance(line, str) or len(line) > self.MAX_LOG_LENGTH for line in logs)
        ):
            raise EvidenceError("Bundle logs must be a bounded array of strings")
        if "contract" in bundle:
            contract = bundle["contract"]
            if (
                not isinstance(contract, dict)
                or not isinstance(contract.get("required"), list)
                or not isinstance(contract.get("properties"), dict)
                or any(not isinstance(name, str) for name in contract["required"])
            ):
                raise EvidenceError("Bundle contract must have required[] and properties{}")
        if "dto" in bundle and not isinstance(bundle["dto"], dict):
            raise EvidenceError("Bundle dto must be an object")
        if "migration" in bundle:
            migration = bundle["migration"]
            if not isinstance(migration, dict) or any(
                not isinstance(migration.get(key), str)
                for key in ("applied", "expected", "related_file")
            ):
                raise EvidenceError("Bundle migration must have applied, expected, related_file strings")
        self._bundle = deepcopy(bundle)
        self.trace_id = trace_id
        self.locator: str | None = None

    @classmethod
    def from_file(cls, path: str | Path) -> LocalBundleEvidenceSource:
        path = Path(path)
        if path.stat().st_size > cls.MAX_BYTES:
            raise EvidenceError("Bundle exceeds 1 MB limit")
        with path.open("rb") as stream:
            data = stream.read(cls.MAX_BYTES + 1)
        if len(data) > cls.MAX_BYTES:
            raise EvidenceError("Bundle exceeds 1 MB limit")
        try:
            source = cls(json.loads(data.decode("utf-8")))
            source.locator = str(path.resolve())
            return source
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvidenceError("Bundle must be valid UTF-8 JSON") from exc

    def _check(self, trace_id: str) -> None:
        if trace_id != self.trace_id:
            raise EvidenceError("Tool scope must match the bundle trace ID")

    def get_trace(self, trace_id: str) -> dict:
        self._check(trace_id)
        return deepcopy(self._bundle["trace"])

    def get_contract(self, trace_id: str) -> dict:
        self._check(trace_id)
        if "contract" not in self._bundle:
            raise EvidenceError("No API contract in this bundle")
        trace = self._bundle["trace"]
        result = {
            "method": trace.get("method"),
            "path": trace.get("path"),
            "openapi": deepcopy(self._bundle["contract"]),
            "backend_dto": deepcopy(self._bundle.get("dto")),
            "provenance": {"kind": "local_bundle", "source": self.locator or "provided_bundle", "code_version": None},
        }
        context = self._bundle.get("contract_context", {})
        if isinstance(context, dict):
            context = verify_synthetic_context(context)
            for key in ("provenance", "versions", "dto_provenance", "caller"):
                if isinstance(context.get(key), dict):
                    result[key] = deepcopy(context[key])
        if isinstance(self._bundle.get("caller"), dict):
            result["caller"] = deepcopy(self._bundle["caller"])
        return result

    def get_backend_evidence(self, trace_id: str) -> dict:
        self._check(trace_id)
        return {"trace_id": trace_id, "logs": deepcopy(self._bundle.get("logs", []))}

    def get_migration_state(self, trace_id: str) -> dict:
        self._check(trace_id)
        if "migration" not in self._bundle:
            raise EvidenceError("No migration state in this bundle")
        return {"environment": self._bundle["trace"]["environment"], "observation_status": "OBSERVED",
                "source_kind": "local_bundle", "source": self.locator or "provided_bundle",
                **deepcopy(self._bundle["migration"])}


class ProjectEvidenceSource:
    """Adapt local registration or an existing scoped source to ``EvidenceSource``.

    The adapter reads evidence only. Policy identifiers are deliberately never
    converted to work execution permission. Create a new adapter for each run.
    """

    def __init__(self, profile: ProjectProfile, *, event_source: EvidenceSource | None = None):
        self.profile, self.event_source = profile, event_source
        self._collection: dict | None = None
        self._version: dict | None = None

    def _events(self) -> dict:
        from .project_profile import read_project_logs

        if self._collection is None:
            self._collection = read_project_logs(self.profile)
        return self._collection

    def _event(self, trace_id: str) -> dict:
        if not _valid_trace_id(trace_id):
            raise EvidenceError("Invalid trace ID")
        collection = self._events()
        if not collection["complete"]:
            raise EvidenceError("Registered log collection is incomplete; do not finalize this incident")
        matches = [event for event in collection["events"] if event["trace"]["trace_id"] == trace_id]
        if not matches:
            raise EvidenceError("Trace not found in the registered project scope")
        merged = deepcopy(matches[0])
        for current in matches[1:]:
            for key in ("method", "path", "occurred_at", "response_status", "operation", "version", "code_version", "request", "request_types"):
                left, right = merged["trace"].get(key), current["trace"].get(key)
                if left is not None and right is not None and left != right:
                    raise EvidenceError("Conflicting registered observations; do not finalize this incident")
            for key in ("dto", "contract", "migration", "caller"):
                if key in merged and key in current and merged[key] != current[key]:
                    raise EvidenceError("Conflicting registered evidence snapshots")
                if key in current:
                    merged[key] = deepcopy(current[key])
            merged["trace"].update({key: value for key, value in current["trace"].items() if value is not None})
            merged["logs"] = [*merged.get("logs", []), *current.get("logs", [])]
        return merged

    def get_trace(self, trace_id: str) -> dict:
        if self.event_source is not None:
            trace = deepcopy(self.event_source.get_trace(trace_id))
        else:
            event = self._event(trace_id)
            trace = deepcopy(event["trace"])
            if "caller" in event:
                trace["caller"] = deepcopy(event["caller"])
        if (trace.get("trace_id") != trace_id or trace.get("project_id", self.profile.project_id) != self.profile.project_id
                or any(trace.get(key) != getattr(self.profile, key) for key in ("service", "environment"))):
            raise EvidenceError("Incident scope does not match project registration")
        trace.setdefault("project_id", self.profile.project_id)
        from .project_profile import observe_project_version

        if self._version is None:
            self._version = observe_project_version(self.profile)
        snapshot = self._version
        if snapshot["runtime_version"]:
            if trace.get("version") and trace["version"] != snapshot["runtime_version"]:
                trace["version_conflict"] = True
            else:
                trace.setdefault("version", snapshot["runtime_version"])
        if snapshot["code_version"]:
            if trace.get("code_version") and trace["code_version"] != snapshot["code_version"]:
                trace["version_conflict"] = True
            else:
                trace.setdefault("code_version", snapshot["code_version"])
        if snapshot["status"] == "CONFLICT":
            trace["version_conflict"] = True
        trace["version_provenance"] = deepcopy(snapshot)
        return trace

    def get_contract(self, trace_id: str) -> dict:
        trace = self.get_trace(trace_id)
        from .project_contracts import load_project_contract

        contract = load_project_contract(self.profile, trace.get("method"), trace.get("path"), runtime_version=trace.get("version"))
        contract["versions"]["code"] = trace.get("code_version")
        if contract["openapi"] is None:
            raise EvidenceError("API contract is unobserved in the registered local source", details=contract)
        caller = contract.get("caller")
        if isinstance(caller, dict):
            captured = trace.get("caller") if isinstance(trace.get("caller"), dict) else trace
            for key in ("input_fields", "input_types"):
                if key in captured:
                    caller[key] = deepcopy(captured[key])
        return contract

    def get_backend_evidence(self, trace_id: str) -> dict:
        self.get_trace(trace_id)
        if self.event_source is not None:
            return self.event_source.get_backend_evidence(trace_id)
        event = self._event(trace_id)
        return {"trace_id": trace_id, "logs": deepcopy(event.get("logs", [])), "source_kind": "registered_local_logs"}

    def get_migration_state(self, trace_id: str) -> dict:
        self.get_trace(trace_id)
        if self.event_source is not None:
            return self.event_source.get_migration_state(trace_id)
        event = self._event(trace_id)
        if "migration" not in event:
            raise EvidenceError("Actual migration state is unobserved; no database connection is registered")
        return {"environment": self.profile.environment, "observation_status": "OBSERVED",
                "source_kind": "registered_local_logs", **deepcopy(event["migration"])}


FIXTURE_SOURCE = FixtureEvidenceSource()


def get_trace(trace_id: str) -> dict:
    return FIXTURE_SOURCE.get_trace(trace_id)


def get_contract(trace_id: str) -> dict:
    return FIXTURE_SOURCE.get_contract(trace_id)


def get_backend_evidence(trace_id: str) -> dict:
    return FIXTURE_SOURCE.get_backend_evidence(trace_id)


def get_migration_state(trace_id: str) -> dict:
    return FIXTURE_SOURCE.get_migration_state(trace_id)


TOOLS = {
    "get_trace": get_trace,
    "get_contract": get_contract,
    "get_backend_evidence": get_backend_evidence,
    "get_migration_state": get_migration_state,
}


def call_tool(name: str, trace_id: str) -> dict:
    if name not in TOOLS:
        raise EvidenceError(f"Tool {name!r} is not allowed")
    return TOOLS[name](trace_id)
