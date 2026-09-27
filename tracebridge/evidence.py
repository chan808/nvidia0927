"""Read-only evidence sources for one incident at a time."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import Protocol

from .fixtures import get_case


class EvidenceError(ValueError):
    pass


class EvidenceSource(Protocol):
    def get_trace(self, trace_id: str) -> dict: ...

    def get_contract(self, trace_id: str) -> dict: ...

    def get_backend_evidence(self, trace_id: str) -> dict: ...

    def get_migration_state(self, trace_id: str) -> dict: ...


def _valid_trace_id(trace_id: str) -> bool:
    return isinstance(trace_id, str) and bool(trace_id) and len(trace_id) <= 64


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
        return {
            "method": case["trace"].get("method"),
            "path": case["trace"].get("path"),
            "openapi": case["contract"],
            "backend_dto": case["dto"],
        }

    def get_backend_evidence(self, trace_id: str) -> dict:
        case = self._case(trace_id)
        return {"trace_id": trace_id, "logs": case["logs"][:50]}

    def get_migration_state(self, trace_id: str) -> dict:
        case = self._case(trace_id)
        return {"environment": case["trace"]["environment"], **case["migration"]}


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
            return cls(json.loads(data.decode("utf-8")))
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
        return {
            "method": trace.get("method"),
            "path": trace.get("path"),
            "openapi": deepcopy(self._bundle["contract"]),
            "backend_dto": deepcopy(self._bundle.get("dto")),
        }

    def get_backend_evidence(self, trace_id: str) -> dict:
        self._check(trace_id)
        return {"trace_id": trace_id, "logs": deepcopy(self._bundle.get("logs", []))}

    def get_migration_state(self, trace_id: str) -> dict:
        self._check(trace_id)
        if "migration" not in self._bundle:
            raise EvidenceError("No migration state in this bundle")
        return {"environment": self._bundle["trace"]["environment"], **deepcopy(self._bundle["migration"])}


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
