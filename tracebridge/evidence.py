"""Narrow, read-only tools exposed to the agent."""

from __future__ import annotations

from .fixtures import get_case


class EvidenceError(ValueError):
    pass


def _case(trace_id: str) -> dict:
    if not isinstance(trace_id, str) or len(trace_id) > 64:
        raise EvidenceError("Invalid trace ID")
    case = get_case(trace_id)
    if case is None:
        raise EvidenceError("Trace not found. Ask for a valid trace ID, environment, and time.")
    return case


def get_trace(trace_id: str) -> dict:
    return _case(trace_id)["trace"]


def get_contract(trace_id: str) -> dict:
    case = _case(trace_id)
    return {"method": case["trace"]["method"], "path": case["trace"]["path"], "openapi": case["contract"], "backend_dto": case["dto"]}


def get_backend_evidence(trace_id: str) -> dict:
    case = _case(trace_id)
    return {"trace_id": trace_id, "logs": case["logs"][:50]}


def get_migration_state(trace_id: str) -> dict:
    case = _case(trace_id)
    return {"environment": case["trace"]["environment"], **case["migration"]}


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
