"""Conservative comparison of explicitly stated report facts with one observation."""

from __future__ import annotations

import re
from typing import Any


STATUS_PATTERN = re.compile(r"(?<!\d)([45]\d{2})(?!\d)")
METHOD_PATTERN = re.compile(r"(?<![A-Za-z])(GET|POST|PUT|PATCH|DELETE)(?![A-Za-z])", re.IGNORECASE)
PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9])(/(?:[A-Za-z0-9._~{}-]+/?)+)")
OPERATION_PATTERN = re.compile(r"([가-힣A-Za-z0-9_-]+)\s*API", re.IGNORECASE)
CAUSE_PATTERN = re.compile(r"원인(?:은|이|:)?\s*(.+)")


def _values(pattern: re.Pattern[str], claim: str) -> list[str]:
    return list(dict.fromkeys(match.group(1) for match in pattern.finditer(claim)))


def assess_claim(claim: str | None, trace: dict | None, trace_id: str) -> tuple[str, list[dict[str, Any]]]:
    """Return an aggregate and checked items; unknown prose is not treated as verified."""
    if not claim or not claim.strip():
        return "NOT_PROVIDED", []

    items: list[dict[str, Any]] = []

    def add(facet: str, reported: Any, observed: Any) -> None:
        if isinstance(reported, list) or observed is None:
            status = "UNVERIFIABLE"
        else:
            status = "MATCHED" if reported == observed else "CONTRADICTED"
        items.append(
            {
                "facet": facet,
                "reported": reported,
                "observed": observed,
                "status": status,
                "source": f"trace:{trace_id}" if observed is not None else None,
            }
        )

    statuses = _values(STATUS_PATTERN, claim)
    if statuses:
        add("http_status", int(statuses[0]) if len(statuses) == 1 else [int(v) for v in statuses],
            trace.get("response_status") if trace else None)

    methods = [value.upper() for value in _values(METHOD_PATTERN, claim)]
    if methods:
        add("method", methods[0] if len(methods) == 1 else methods,
            trace.get("method", "").upper() or None if trace else None)

    paths = [value.rstrip("/.,;:)") for value in _values(PATH_PATTERN, claim)]
    if paths:
        add("path", paths[0] if len(paths) == 1 else paths,
            trace.get("path") if trace else None)

    operations = _values(OPERATION_PATTERN, claim)
    if operations:
        add("operation", operations[0] if len(operations) == 1 else operations,
            trace.get("operation") if trace else None)

    cause_match = CAUSE_PATTERN.search(claim)
    if cause_match:
        add("suspected_cause", cause_match.group(1).strip(), None)

    if not items or all(item["status"] == "UNVERIFIABLE" for item in items):
        return "UNVERIFIABLE", items
    statuses_seen = {item["status"] for item in items}
    if statuses_seen == {"MATCHED"}:
        return "MATCHED", items
    if statuses_seen == {"CONTRADICTED"}:
        return "CONTRADICTED", items
    return "PARTIAL", items
