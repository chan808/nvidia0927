"""Correlate bounded event observations, then reuse the single checked verdict."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
import re
from typing import Any

from .claims import METHOD_PATTERN, PATH_PATTERN
from .evidence import EvidenceError, EvidenceSource, LocalBundleEvidenceSource
from .report_contract import TIME_WINDOW, empty_result, event_time, plain_questions
from .project_sources import canonical_service, redact
from .triage import analyze, route_verdict, summarize


REQUEST_ID_PATTERN = re.compile(
    r"\b(?:request[_ -]?id|trace[_ -]?id)\s*[:=#]\s*([A-Za-z0-9._-]{1,64})(?![A-Za-z0-9._-])", re.I,
)


def _event_time(value: Any) -> datetime:
    try:
        return event_time(value)
    except ValueError as exc:
        raise EvidenceError(str(exc)) from exc


def _one_report_value(pattern: re.Pattern[str], report: str) -> str | None:
    values = list(dict.fromkeys(match.group(1) for match in pattern.finditer(report)))
    return values[0] if len(values) == 1 else None


def _known_value_conflict(left: Any, right: Any) -> bool:
    markers = {"[VALUE]", "[REDACTED]", "[MASKED]", "[API_KEY]", "[EMAIL]", "[IP]"}
    if any(value is None or isinstance(value, str) and value.upper() in markers for value in (left, right)):
        return False
    if isinstance(left, dict) and isinstance(right, dict):
        return any(_known_value_conflict(left[key], right[key]) for key in left.keys() & right.keys())
    return left != right


def observation_conflicts(previous: dict, current: dict) -> set[str]:
    """Compare observed fields only; absence and masked values are not opposition."""
    before, after = previous["trace"], current["trace"]
    before = {**before, "service": canonical_service(before.get("service"))}
    after = {**after, "service": canonical_service(after.get("service"))}
    keys = ("service", "environment", "method", "path", "response_status", "operation", "version")
    conflicts = {key for key in keys if _known_value_conflict(before.get(key), after.get(key))}
    if before.get("occurred_at") and after.get("occurred_at") and abs(_event_time(before["occurred_at"]) - _event_time(after["occurred_at"])) > TIME_WINDOW:
        conflicts.add("occurred_at")
    first, second = before.get("request"), after.get("request")
    if isinstance(first, dict) and isinstance(second, dict):
        # Captured request field names remain comparable after values are masked.
        if first.keys() != second.keys() or _known_value_conflict(first, second):
            conflicts.add("request")
    for key in ("contract", "dto", "migration"):
        if _known_value_conflict(previous.get(key), current.get(key)):
            conflicts.add(key)
    return conflicts


class LocalEventCatalog:
    """Explicitly supplied events from one project; no production connector."""

    MAX_BYTES = 1_000_000
    MAX_EVENTS = 100

    def __init__(self, data: dict[str, Any]):
        if not isinstance(data, dict):
            raise EvidenceError("Event catalog must be an object")
        project_id, events = data.get("project_id"), data.get("events")
        if not isinstance(project_id, str) or not project_id.strip():
            raise EvidenceError("Event catalog needs a project_id")
        if not isinstance(events, list) or not 1 <= len(events) <= self.MAX_EVENTS:
            raise EvidenceError("Event catalog needs 1 to 100 events")
        self.project_id, self.source_kind = project_id.strip(), "local_event_catalog"
        self.locator: str | None = None
        self.events: dict[str, tuple[EvidenceSource, datetime]] = {}
        for event in events:
            source = LocalBundleEvidenceSource(event)
            if source.trace_id in self.events:
                raise EvidenceError(f"Duplicate trace ID: {source.trace_id}")
            when = _event_time(source.get_trace(source.trace_id).get("occurred_at"))
            self.events[source.trace_id] = (source, when)

    @classmethod
    def from_file(cls, path: str | Path) -> LocalEventCatalog:
        path = Path(path)
        if path.stat().st_size > cls.MAX_BYTES:
            raise EvidenceError("Event catalog exceeds 1 MB limit")
        with path.open("rb") as stream:
            raw = stream.read(cls.MAX_BYTES + 1)
        if len(raw) > cls.MAX_BYTES:
            raise EvidenceError("Event catalog exceeds 1 MB limit")
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise EvidenceError("Event catalog must be valid UTF-8 JSON") from exc
        catalog = cls(data)
        catalog.locator = str(path.resolve())
        return catalog


class ObservedEventSource:
    """Only already scoped log records; absent response/contract data stays absent."""

    def __init__(self, event: dict):
        self.event = deepcopy(event)

    def _check(self, trace_id):
        if trace_id != self.event["trace"]["trace_id"]:
            raise EvidenceError("다른 사건의 자료를 조회할 수 없습니다")

    def get_trace(self, trace_id):
        self._check(trace_id)
        return deepcopy(self.event["trace"])

    def get_backend_evidence(self, trace_id):
        self._check(trace_id)
        return {"trace_id": trace_id, "logs": deepcopy(self.event.get("logs", []))[:20]}

    def get_contract(self, trace_id):
        self._check(trace_id)
        if "contract" not in self.event:
            raise EvidenceError("관측된 계약 자료가 없습니다")
        trace = self.get_trace(trace_id)
        return {"openapi": deepcopy(self.event["contract"]), "backend_dto": deepcopy(self.event.get("dto")), "method": trace.get("method"), "path": trace.get("path")}

    def get_migration_state(self, trace_id):
        self._check(trace_id)
        if "migration" not in self.event:
            raise EvidenceError("관측된 마이그레이션 자료가 없습니다")
        return {"environment": self.event["trace"]["environment"], **deepcopy(self.event["migration"])}


class ObservedEventCatalog:
    """Combined scoped observations; completeness and consistency are separate facts."""

    MAX_OBSERVATIONS = 100

    def __init__(self, project_id: str, events: list[dict], *, complete: bool = True):
        self.project_id, self.source_kind = project_id, "registered_log"
        self.observation_count = len(events)
        self.complete = complete and self.observation_count <= self.MAX_OBSERVATIONS
        self.observations = deepcopy(events)
        self.events: dict[str, tuple[ObservedEventSource, datetime]] = {}
        self.conflicting_ids: set[str] = set()
        self.conflicts: dict[str, set[str]] = {}
        # Inspect every supplied record, including those beyond the decision limit.
        for event in events:
            trace = event["trace"]
            trace_id = trace["trace_id"]
            old = self.events.get(trace_id)
            if old:
                conflicts = observation_conflicts(old[0].event, event)
                if conflicts:
                    self.conflicting_ids.add(trace_id)
                    self.conflicts.setdefault(trace_id, set()).update(conflicts)
                    del self.events[trace_id]
                    continue
                merged = deepcopy(old[0].event)
                merged["trace"].update({k: v for k, v in trace.items() if v is not None})
                merged["logs"] = [*merged.get("logs", []), *event.get("logs", [])][:20]
                for key in ("contract", "dto", "migration"):
                    if key in event:
                        merged[key] = deepcopy(event[key])
                event = merged
            if trace_id in self.conflicting_ids:
                self.events.pop(trace_id, None)
            else:
                self.events[trace_id] = (ObservedEventSource(event), _event_time(event["trace"]["occurred_at"]))

    def completeness(self, *, additional_conflicts: list[dict] | None = None) -> dict:
        conflicts = {id_: {"trace_id": id_, "fields": set(fields), "sources": set()} for id_, fields in self.conflicts.items()}
        for item in additional_conflicts or []:
            target = conflicts.setdefault(item["trace_id"], {"trace_id": item["trace_id"], "fields": set(), "sources": set()})
            target["fields"].update(item["fields"])
            target["sources"].update(item["sources"])
        return {
            "observation_count": self.observation_count, "observation_limit": self.MAX_OBSERVATIONS,
            "complete": self.complete, "conflicting_trace_ids": sorted(self.conflicting_ids | conflicts.keys())[:5],
            "conflicts": [{"trace_id": id_, "fields": sorted(item["fields"]), **({"sources": sorted(item["sources"])} if item["sources"] else {})} for id_, item in sorted(conflicts.items())[:5]],
        }


def selected_catalog_conflicts(catalog: LocalEventCatalog, trace_id: str | None, current: ObservedEventCatalog) -> list[dict]:
    """Compare the selected snapshot to every scoped current record of that event."""
    selected = catalog.events.get(trace_id)
    if not selected or catalog.project_id != current.project_id or not current.observations:
        return []
    source, when = selected
    snapshot = {"trace": source.get_trace(trace_id)}
    if (snapshot["trace"].get("project_id") or catalog.project_id) != current.project_id:
        return []
    try:
        contract = source.get_contract(trace_id)
        snapshot.update(contract=contract["openapi"], dto=contract.get("backend_dto"))
    except EvidenceError:
        pass
    try:
        snapshot["migration"] = source.get_migration_state(trace_id)
    except EvidenceError:
        pass
    trace = snapshot["trace"]
    conflicts: dict[str, set[str]] = {}
    for event in current.observations:
        observed = event["trace"]
        if (
            observed.get("trace_id") != trace_id
            or (observed.get("project_id") or current.project_id) != catalog.project_id
            or canonical_service(observed.get("service")) != canonical_service(trace.get("service"))
            or observed.get("environment") != trace.get("environment")
            or not observed.get("occurred_at") or abs(_event_time(observed["occurred_at"]) - when) > TIME_WINDOW
        ):
            continue
        fields = observation_conflicts(snapshot, event)
        if fields:
            conflicts.setdefault(event.get("observation_source", current.source_kind), set()).update(fields)
    return [{"trace_id": trace_id, "fields": sorted(fields), "sources": [catalog.source_kind, name]} for name, fields in sorted(conflicts.items())]


def triage_report(
    report: str,
    catalog: LocalEventCatalog | ObservedEventCatalog,
    *,
    trace_id: str | None = None,
    environment: str | None = None,
    occurred_at: str | None = None,
    method: str | None = None,
    path: str | None = None,
    service: str | None = None,
    operation: str | None = None,
    incident_id: str | None = None,
) -> dict[str, Any]:
    """ID or bounded context selects one event; all diagnosis rules stay in triage."""
    if not isinstance(report, str) or not report.strip() or len(report) > 12000:
        raise ValueError("Report must contain 1 to 12000 characters including follow-up answers")
    when = _event_time(occurred_at) if occurred_at else None
    result = empty_result(catalog.project_id, incident_id=incident_id)

    def candidate(id_):
        trace = catalog.events[id_][0].get_trace(id_)
        return {"trace_id": id_, "scope": {k: redact(trace[k]) for k in ("occurred_at", "environment", "service", "method", "path", "operation") if k in trace}}

    def needs_context(reason: str, candidates: list[str] | None = None) -> dict[str, Any]:
        result.update(summary=reason, route_reason=reason, next_action=reason, candidate_trace_ids=(candidates or [])[:5])
        result["candidates"] = [candidate(id_) for id_ in (candidates or [])[:5]]
        return result

    if not getattr(catalog, "complete", True):
        return needs_context("등록 소스의 합산 관측이 한도를 넘었거나 일부 조회가 중단되어 전체 관측을 확인하지 못했습니다. 범위를 좁혀 다시 확인해 주세요.")
    reported_ids = list(dict.fromkeys(match.group(1) for match in REQUEST_ID_PATTERN.finditer(report)))
    if len(reported_ids) > 1 or trace_id and reported_ids and trace_id != reported_ids[0]:
        return needs_context("제보 단서가 서로 다른 요청을 가리킵니다. 문제가 난 화면·동작·시각을 다시 확인해 주세요.")
    selected_id = trace_id or (reported_ids[0] if reported_ids else None)
    if not selected_id and getattr(catalog, "conflicting_ids", set()):
        return needs_context("같은 식별 값에 응답·서비스·환경 등의 관측이 충돌합니다. 화면·동작·발생 시각을 확인해 주세요.")
    if selected_id:
        if selected_id in getattr(catalog, "conflicting_ids", set()):
            return needs_context("같은 식별 값에 서로 다른 관측이 있습니다. 화면·동작·발생 시각을 확인해 주세요.")
        selected = catalog.events.get(selected_id)
        if selected is None:
            return needs_context("제보에 맞는 관측 요청이 없습니다. 화면·동작·발생 시각을 알려주세요.")
        source, selected_time = selected
        trace = source.get_trace(selected_id)
        if environment and trace["environment"] != environment:
            return needs_context("제보한 환경과 관측 환경이 다릅니다. 어느 화면에서 사용했는지 확인해 주세요.")
        if service and trace["service"] != service or when and abs(selected_time - when) > TIME_WINDOW:
            return needs_context("관측의 서비스 또는 발생 시각이 이번 제보 범위와 다릅니다. 화면과 대략적인 시각을 확인해 주세요.")
        correlation, basis = "EXACT_ID", "request/trace ID exact match within scoped observations"
    else:
        selected_method = method or _one_report_value(METHOD_PATTERN, report)
        selected_path = path or _one_report_value(PATH_PATTERN, report)
        known_operations = {s.get_trace(id_).get("operation") for id_, (s, _) in catalog.events.items()}
        mentioned = [name for name in known_operations if name and name in report]
        selected_operation = operation or (mentioned[0] if len(mentioned) == 1 else None)
        if not all((environment, when)) or not (service or selected_operation or selected_method and selected_path):
            return needs_context("어떤 화면에서 무엇을 하려던 중이었고, 대략 언제 발생했나요?")
        matches = []
        for candidate_id, (candidate_source, candidate_time) in catalog.events.items():
            observed = candidate_source.get_trace(candidate_id)
            if (
                observed["environment"] == environment
                and (not service or observed.get("service") == service)
                and (not selected_operation or observed.get("operation") == selected_operation)
                and (not selected_method or observed.get("method", "").upper() == selected_method.upper())
                and (not selected_path or observed.get("path") == selected_path)
                and abs(candidate_time - when) <= TIME_WINDOW
            ):
                matches.append(candidate_id)
        if len(matches) != 1:
            message = "같은 범위의 요청이 여러 개입니다. 문제가 난 화면·동작·오류 문구를 조금 더 알려주세요." if matches else "이번 제보 범위와 맞는 요청이 없습니다. 화면·동작·대략적인 시각을 확인해 주세요."
            return needs_context(message, matches)
        selected_id = matches[0]
        source, _ = catalog.events[selected_id]
        trace = source.get_trace(selected_id)
        correlation = "CONTEXT_CANDIDATE"
        basis = "one candidate: environment, time ±5m" + (", service" if service else "") + (", operation" if selected_operation else "") + (", method/path" if selected_method and selected_path else "")

    verdict = analyze(selected_id, report, source=source)
    decision = route_verdict(verdict, correlation)
    summary = summarize(verdict)
    if correlation == "CONTEXT_CANDIDATE":
        summary = "제보와 연결이 미확정인 후보의 관측입니다. " + summary
    scope = {key: redact(trace[key]) for key in ("occurred_at", "environment", "service", "method", "path", "operation", "version") if key in trace}
    evidence = [{"source": redact(item["source"]), "fact": redact(item["fact"]), "id": f"R{index}", "kind": "rule_observation", "content": redact(item["fact"]), "scope": scope, "correlated": correlation == "EXACT_ID", "source_system": catalog.source_kind} for index, item in enumerate(verdict["evidence"], 1)]
    result.update(
        **decision, correlation=correlation, correlation_basis=basis, trace_id=selected_id,
        candidates=[candidate(selected_id)], diagnosis_type=verdict["diagnosis_type"],
        finding_status=verdict["finding_status"], claim_status=verdict["claim_status"],
        claim_items=verdict["claim_items"], observed_status=verdict.get("observed_status"),
        reported_status=verdict.get("reported_status"), summary=summary,
        observations=evidence, evidence=evidence, scope=scope,
        questions=[] if decision["route"] in {"GUIDANCE", "WORK_CANDIDATE"} else plain_questions(),
        run_status="COMPLETED" if decision["route"] in {"GUIDANCE", "WORK_CANDIDATE"} else "WAITING_CONTEXT",
        stop_reason="rule_decision" if decision["route"] in {"GUIDANCE", "WORK_CANDIDATE"} else "investigation_needed",
        version_provenance={**result["version_provenance"], "event_snapshot": {"version": scope.get("version"), "source": catalog.source_kind, "deployment_observed": False}},
    )
    return result
