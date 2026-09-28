"""Small, optional NAT bridge for measured investigation events.

No prompts, tool arguments, source bodies or credentials are accepted here.
Importing this module does not load NAT or create a network client.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import time
from typing import Callable
from uuid import uuid4


ORIGINS = {"main_investigation", "nat_fixture", "evaluation_double"}
STATUSES = {"success", "failed", "timeout", "rejected", "cancelled"}


def _label(value: str, name: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:/ -]{1,160}", value):
        raise ValueError(f"{name} must be a short metadata label")
    return value


def _number(value, name: str):
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
        raise ValueError(f"{name} must be a measured nonnegative number or None")
    return value


def observed_usage(usage: dict | None) -> dict:
    """Keep missing counters missing; a failed response can still return usage."""
    usage = usage or {}
    result = {}
    for key in ("prompt_tokens", "completion_tokens"):
        value = usage.get(key)
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"{key} must be a returned nonnegative integer or None")
        result[key] = value
    return result


class InvestigationObserver:
    """One isolated run; callers emit real tool/model completions, including failures."""

    def __init__(self, run_id: str, output_dir: str | Path, *, origin: str = "main_investigation",
                 nat_sink: Callable[[dict], None] | None = None):
        if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", run_id):
            raise ValueError("Invalid run_id")
        if origin not in ORIGINS:
            raise ValueError("Unknown event origin")
        self.run_id, self.origin, self.nat_sink = run_id, origin, nat_sink
        self.output_dir = Path(output_dir) / run_id
        # Do not append a second execution to an existing run's measurements.
        self.output_dir.mkdir(parents=True, exist_ok=False)
        self.events_path = self.output_dir / "events.jsonl"
        self.events: list[dict] = []
        self.sink_failures: list[str] = []
        self.sink_delivered = 0
        self.started = time.perf_counter()
        self.closed = False

    def _record(self, kind: str, name: str, *, phase: str, status: str, elapsed_ms: float | None,
                usage: dict | None = None, response_id: str | None = None,
                error_type: str | None = None, http_status: int | None = None) -> dict:
        if self.closed:
            raise ValueError("Observer is already finished")
        if status not in STATUSES:
            raise ValueError("Unknown completion status")
        if http_status is not None and (type(http_status) is not int or not 100 <= http_status <= 599):
            raise ValueError("Invalid HTTP status")
        event = {
            "schema_version": 1, "run_id": self.run_id, "origin": self.origin,
            "sequence": len(self.events) + 1, "kind": kind,
            "name": _label(name, "name"), "phase": _label(phase, "phase"), "status": status,
            "elapsed_ms": _number(elapsed_ms, "elapsed_ms"), "timestamp_s": time.time(),
            "span_id": uuid4().hex,
            "error_type": _label(error_type, "error_type") if error_type else None,
            "http_status": http_status,
        }
        if kind == "model":
            event.update(usage=observed_usage(usage),
                         response_id=_label(response_id, "response_id") if response_id else None)
        # A telemetry failure must not turn a failed investigation into a success.
        if self.nat_sink is not None:
            try:
                self.nat_sink(dict(event))
                self.sink_delivered += 1
            except Exception as exc:
                self.sink_failures.append(type(exc).__name__)
        self.events.append(event)
        with self.events_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
        return event

    def record_tool(self, name: str, *, phase: str = "investigation", status: str,
                    elapsed_ms: float | None, error_type: str | None = None,
                    http_status: int | None = None) -> dict:
        return self._record("tool", name, phase=phase, status=status, elapsed_ms=elapsed_ms,
                            error_type=error_type, http_status=http_status)

    def record_model(self, model: str, *, phase: str = "investigation", status: str,
                     elapsed_ms: float | None, usage: dict | None = None,
                     response_id: str | None = None, error_type: str | None = None,
                     http_status: int | None = None) -> dict:
        return self._record("model", model, phase=phase, status=status, elapsed_ms=elapsed_ms,
                            usage=usage, response_id=response_id, error_type=error_type,
                            http_status=http_status)

    @contextmanager
    def tool(self, name: str, *, phase: str = "investigation"):
        started = time.perf_counter()
        try:
            yield
        except Exception as exc:
            self.record_tool(name, phase=phase, status="timeout" if isinstance(exc, TimeoutError) else "failed",
                             elapsed_ms=(time.perf_counter() - started) * 1000,
                             error_type=type(exc).__name__)
            raise
        else:
            self.record_tool(name, phase=phase, status="success",
                             elapsed_ms=(time.perf_counter() - started) * 1000)

    def finish(self, run_status: str, *, stop_reason: str | None = None,
               expected_tool_calls: int | None = None, expected_model_calls: int | None = None) -> dict:
        if self.closed:
            raise ValueError("Observer is already finished")
        for value in (expected_tool_calls, expected_model_calls):
            if value is not None and (type(value) is not int or value < 0):
                raise ValueError("Expected counts must be nonnegative integers or None")
        models = [event for event in self.events if event["kind"] == "model"]
        tools = [event for event in self.events if event["kind"] == "tool"]
        expected = {"tool": expected_tool_calls, "model": expected_model_calls}
        counts = {"tool": len(tools), "model": len(models)}
        coverage = ("NOT_CHECKED" if any(value is None for value in expected.values())
                    else "MATCHED_REPORTED_COUNTS" if counts == expected else "PARTIAL")
        usage = {}
        for key in ("prompt_tokens", "completion_tokens"):
            known = [event["usage"][key] for event in models if event["usage"][key] is not None]
            complete = expected_model_calls is not None and len(known) == len(models) == expected_model_calls
            usage[key] = sum(known) if complete else None
            usage[key + "_observed_sum"] = sum(known) if known else (0 if complete else None)
            usage[key + "_missing_calls"] = max(expected_model_calls, len(models)) - len(known) if expected_model_calls is not None else None
        summary = {
            "schema_version": 1, "artifact_status": "DRAFT", "run_id": self.run_id,
            "origin": self.origin, "run_status": _label(run_status, "run_status"),
            "stop_reason": _label(stop_reason, "stop_reason") if stop_reason else None,
            "observed_calls": counts, "reported_calls": expected, "coverage_status": coverage,
            "failed_model_calls": sum(event["status"] != "success" for event in models),
            "failed_tool_calls": sum(event["status"] != "success" for event in tools),
            "usage": usage, "observer_elapsed_ms": round((time.perf_counter() - self.started) * 1000, 3),
            "nat": {"configured": self.nat_sink is not None, "delivered_completions": self.sink_delivered,
                    "failure_types": self.sink_failures,
                    "status": "NOT_CONFIGURED" if self.nat_sink is None else "FAILED" if self.sink_failures
                    else "EVENTS_DELIVERED" if self.sink_delivered else "NO_EVENTS"},
            "main_flow_verified": False,
        }
        (self.output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.closed = True
        return summary


class NATEventSink:
    """Publish completion metadata using the installed NAT 1.8 public payload API.

    manager is an active NAT Context.intermediate_step_manager (or a double).
    Paired start timestamps are derived from the caller's measured duration;
    neither prompt content nor NAT's default zero token counters are invented.
    """

    def __init__(self, manager):
        self.manager = manager

    def __call__(self, event: dict) -> None:
        from nat.data_models.intermediate_step import IntermediateStepPayload, IntermediateStepType

        prefix = "LLM" if event["kind"] == "model" else "TOOL"
        metadata = {"tracebridge": event, "completion_adapter": True}
        elapsed = event["elapsed_ms"]
        if elapsed is not None:
            self.manager.push_intermediate_step(IntermediateStepPayload(
                event_type=IntermediateStepType[prefix + "_START"],
                event_timestamp=event["timestamp_s"] - elapsed / 1000,
                name=event["name"], UUID=event["span_id"], metadata=metadata, usage_info=None))
        self.manager.push_intermediate_step(IntermediateStepPayload(
            event_type=IntermediateStepType[prefix + "_END"], event_timestamp=event["timestamp_s"],
            span_event_timestamp=event["timestamp_s"] - elapsed / 1000 if elapsed is not None else None,
            name=event["name"], UUID=event["span_id"], metadata=metadata, usage_info=None))


def observe_result(result: dict, output_dir: str | Path, *, origin: str = "main_investigation") -> dict:
    """Fallback summary of returned data; explicitly does not fabricate subevents."""
    observer = InvestigationObserver(result["run_id"], output_dir, origin=origin)
    summary = observer.finish(result.get("run_status", "UNKNOWN"),
                              expected_tool_calls=None, expected_model_calls=result.get("model_calls"))
    summary.update(coverage_status="SUMMARY_ONLY", main_flow_verified=False,
                   reported_run_status=result.get("run_status"))
    (observer.output_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary
