"""Frozen incident evaluation with isolated memory and honest missing measurements.

Adapters receive current materials and the common resource contract, never an
expectation. Local and scripted double runs are draft functional evidence only.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace
from typing import Callable
from unittest.mock import patch
from uuid import uuid4

from .nat_observability import InvestigationObserver, observed_usage


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SUITE = ROOT / "examples/evaluation/suite.json"
CONDITIONS = {
    "rules": {"memory_enabled": False, "capability_scope": "diagnosis_only"},
    "single_prompt": {"memory_enabled": False, "capability_scope": "answer_only"},
    "coding_agent": {"memory_enabled": False, "capability_scope": "prepare_change_with_human"},
    "tracebridge_memory_off": {"memory_enabled": False, "capability_scope": "prepare_change"},
    "tracebridge_memory_on": {"memory_enabled": True, "capability_scope": "prepare_change"},
}
CASE_KINDS = {"synthetic", "replay", "real_incident", "controlled_injection"}
FAILURE_STATUSES = {"PARTIAL_FAILURE", "TIMED_OUT", "BUDGET_EXHAUSTED", "FAILED", "REGRESSION_FAILED"}


class AdapterUnavailable(RuntimeError):
    """A missing API, measurement or authorized runner is not a product success."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path: Path) -> dict:
    if path.stat().st_size > 2_000_000:
        raise ValueError("Evaluation JSON exceeds 2 MB")
    return json.loads(path.read_text(encoding="utf-8"))


def _inside(root: Path, relative: str) -> Path:
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Evaluation artifact escaped its root")
    return path


def _write(path: Path, data: dict):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def load_suite(path: str | Path = DEFAULT_SUITE) -> dict:
    """Check the entire frozen input/expectation/resource manifest before running."""
    path = Path(path).resolve()
    suite = _json(path)
    cases = suite.get("cases", [])
    if len(cases) != 12 or len({case["id"] for case in cases}) != 12:
        raise ValueError("A frozen evaluation suite must contain 12 unique cases")
    counts = Counter(case["category"] for case in cases)
    if len(counts) != 6 or set(counts.values()) != {2}:
        raise ValueError("Evaluation suite must contain six categories with two variants")
    if any(not re.fullmatch(r"[a-z0-9_-]{1,64}", case["id"]) for case in cases):
        raise ValueError("Invalid evaluation case ID")
    if any(case.get("source", {}).get("kind") not in CASE_KINDS or not case["source"].get("description") for case in cases):
        raise ValueError("Every case requires explicit provenance")
    hashes = suite.get("sha256", {})
    required = {suite["resource_contract"], suite["initial_memory"]}
    required.update(case[key] for case in cases for key in ("input", "expectation"))
    if not required <= hashes.keys():
        raise ValueError("Frozen manifest is missing required artifact hashes")
    for name, digest in hashes.items():
        if not re.fullmatch(r"[a-f0-9]{64}", digest) or sha256(_inside(path.parent, name)) != digest:
            raise ValueError(f"Frozen artifact changed: {name}")
    resources = _json(_inside(path.parent, suite["resource_contract"]))
    # Expectations cannot enter model/developer inputs through resource paths.
    exposed = resources.get("current_artifacts", [])
    if any("expectation" in name or name == "suite.json" for name in exposed):
        raise ValueError("Resource contract must not expose the evaluation oracle")
    suite["root"], suite["path"] = path.parent, path
    suite["resources"] = resources
    suite["fingerprint"] = sha256(path)
    return suite


def capture_code_snapshot(root: Path = ROOT) -> dict:
    paths = []
    for folder in ("tracebridge", "scripts", "pages"):
        paths.extend(path for path in (root / folder).rglob("*.py") if "__pycache__" not in path.parts)
    paths.extend(root / name for name in ("app.py", "pyproject.toml", "requirements.txt", "nat_workflow.yml"))
    paths.extend(root.glob("requirements-*.lock"))
    hashes = {path.relative_to(root).as_posix(): sha256(path) for path in sorted(paths) if path.is_file()}
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root, capture_output=True,
                              text=True, timeout=5, check=False).stdout.strip() or None
    except (OSError, subprocess.TimeoutExpired):
        head = None
    return {"head": head, "python": sys.version, "files": hashes,
            "fingerprint": hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()}


def clone_memory(source: str | Path, destination: str | Path):
    """SQLite backup captures a consistent WAL snapshot without writing the source."""
    source, destination = Path(source), Path(destination)
    if destination.exists() or source.resolve() == destination.resolve():
        raise ValueError("Each execution must have a new isolated memory DB")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True) as original:
        with sqlite3.connect(destination) as target:
            original.backup(target)


def create_initial_memory(path: Path, fixture: dict):
    """Seed fixed, explicitly synthetic reviewed clues through C's public API."""
    from .incident_memory import IncidentStore
    if path.exists():
        raise ValueError("Initial memory must be new")
    with IncidentStore(path) as store:
        for entry in fixture.get("runs", []):
            run = deepcopy(entry["result"])
            store.save_run(run)
            card_id = store.get_card(run["project_id"], run["run_id"])["card_id"]
            store.review_card(run["project_id"], card_id, "approve", reviewer="SYNTHETIC_EVALUATION_CURATOR",
                              note="Controlled fixture review; no real cause, fix or human timing claim")


@dataclass(frozen=True)
class AdapterContext:
    condition: str
    mode: str
    memory_enabled: bool
    db_path: Path
    output_dir: Path
    suite_root: Path
    resource_contract: dict


@dataclass
class AdapterResult:
    result: dict
    execution_scope: str = "diagnosis_only"
    measurements: dict | None = None
    provenance: str = "local"
    artifact_root: Path | None = None


def _nonnegative(value, key: str):
    if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
        raise ValueError(f"Invalid measured value: {key}")
    return value


def usage_metrics(result: dict, measurements: dict) -> dict:
    events = measurements.get("model_events")
    calls = measurements.get("model_calls", result.get("model_calls"))
    if calls is not None and (type(calls) is not int or calls < 0):
        raise ValueError("Model call count must be observed")
    metrics = {"model_calls": calls, "external_model_calls": measurements.get("external_model_calls"),
               "tool_calls": measurements.get("tool_calls"), "service_calls": measurements.get("service_calls"),
               "prompt_tokens": None, "completion_tokens": None,
               "prompt_tokens_observed_sum": None, "completion_tokens_observed_sum": None,
               "usage_missing_calls": calls, "failed_model_calls": None}
    if events is not None:
        if not isinstance(events, list) or calls is None or len(events) != calls:
            raise ValueError("Per-call usage events must match attempted model calls")
        parsed = [observed_usage(event.get("usage")) for event in events]
        for key in ("prompt_tokens", "completion_tokens"):
            known = [event[key] for event in parsed if event[key] is not None]
            metrics[key] = sum(known) if len(known) == calls else None
            metrics[key + "_observed_sum"] = sum(known) if known or calls == 0 else None
        metrics["usage_missing_calls"] = sum(any(event[key] is None for key in ("prompt_tokens", "completion_tokens")) for event in parsed)
        metrics["failed_model_calls"] = sum(event.get("status") in {"failed", "timeout", "rejected", "cancelled"} for event in events)
    elif calls == 0:
        metrics.update(prompt_tokens=0, completion_tokens=0, prompt_tokens_observed_sum=0,
                       completion_tokens_observed_sum=0, usage_missing_calls=0, failed_model_calls=0)
    # Aggregate usage with unknown coverage is not trusted, especially legacy zeros.
    for key in ("external_model_calls", "tool_calls", "service_calls"):
        value = metrics[key]
        if value is not None and (type(value) is not int or value < 0):
            raise ValueError(f"Invalid observed count: {key}")
    return metrics


def verified_action(change: dict | None, *, artifact_root: Path | None = None) -> bool | None:
    """An assertion flag alone cannot turn a proposed change into a verified action."""
    if not change:
        return None
    if change.get("status") in {"NOT_ATTEMPTED", "NOT_REQUESTED"}:
        return False
    checks = {check.get("phase"): check for check in change.get("checks", [])}
    before, after, regression = (checks.get(key) for key in ("before", "after", "regression"))
    if not all((before, after, regression)):
        return False
    if any(not re.fullmatch(r"[a-f0-9]{64}", str(check.get(key, "")))
           for check in (before, after, regression)
           for key in ("input_sha256", "test_sha256", "execution_settings_sha256")):
        return False
    if type(before.get("exit_code")) is not int or before["exit_code"] == 0 or after.get("exit_code") != 0 or regression.get("exit_code") != 0:
        return False
    same = all(before.get(key) and before[key] == after.get(key)
               for key in ("command_id", "input_sha256", "test_sha256", "execution_settings_sha256"))
    if not same or change.get("original_preserved") is not True or change.get("candidate_fix_verified") is not True or artifact_root is None:
        return False
    try:
        for check in (before, after, regression):
            path = _inside(artifact_root, check["artifact_ref"])
            if sha256(path) != check["artifact_sha256"]:
                return False
            record = _json(path)
            if any(record.get(key) != check.get(key) for key in
                   ("phase", "command_id", "input_sha256", "test_sha256", "execution_settings_sha256", "exit_code")):
                return False
        for ref, digest in ((change["diff_ref"], change["diff_sha256"]),
                            (change["original_ref"], change["original_sha256"])):
            path = _inside(artifact_root, ref)
            if sha256(path) != digest:
                return False
        return bool(_inside(artifact_root, change["diff_ref"]).stat().st_size)
    except (OSError, ValueError, KeyError, TypeError):
        return False


def assess_result(result: dict, expectation: dict, *, artifact_root: Path | None = None) -> dict:
    checks = {}
    for field, allowed in expectation.get("allowed", {}).items():
        checks[field] = result.get(field) in allowed
    for field, value in expectation.get("equals", {}).items():
        checks[field] = result.get(field) == value
    if expectation.get("requires_questions"):
        checks["questions"] = bool(result.get("questions"))
    if expectation.get("forbid_cause_confirmation"):
        checks["no_false_cause"] = result.get("cause_confirmed") is False
    if expectation.get("forbid_resolution_confirmation"):
        checks["no_false_resolution"] = (result.get("fix_applied") is False and result.get("fix_verified") is False
                                           and result.get("service_recovery") != "VERIFIED")
    if expectation.get("forbid_modification"):
        change = result.get("change") or {}
        checks["no_unnecessary_modification"] = not change.get("diff_sha256") and not change.get("candidate_fix_verified")
    if expectation.get("adaptive_lookup"):
        searches = [step.get("arguments", {}).get("terms") for step in result.get("evaluation_tool_trace", result.get("steps", []))
                    if step.get("tool") == "search_code" and step.get("status") == "success"]
        checks["changed_lookup"] = len(searches) >= 2 and searches[0] != searches[1]
    if expectation.get("failure_preserved"):
        checks["failure_preserved"] = result.get("run_status") in FAILURE_STATUSES
    resolution_claim = (result.get("fix_applied") is True or result.get("fix_verified") is True
                        or result.get("service_recovery") == "VERIFIED")
    wrong = {"cause": result.get("cause_confirmed") is True if "cause_confirmed" in result else None,
             "resolution": True if resolution_claim else False
                            if result.get("fix_applied") is False and result.get("fix_verified") is False else None}
    return {"status": "PASS" if checks and all(checks.values()) else "FAIL", "checks": checks,
            "failed_checks": [key for key, passed in checks.items() if not passed],
            "wrong_cause_confirmation": wrong["cause"], "wrong_resolution_confirmation": wrong["resolution"],
            "unnecessary_modification": not checks.get("no_unnecessary_modification", True),
            "incident_match": checks.get("trace_id", checks.get("correlation")),
            "routing_correct": checks.get("route"),
            "correct_hold": checks.get("route") if expectation.get("hold") else None,
            "verified_action": verified_action(result.get("change"), artifact_root=artifact_root)}


class ImportedAdapter:
    """Import actual baseline/coding-agent measurements without making model calls."""

    def __init__(self, path: str | Path, suite_fingerprint: str):
        self.artifact_root = Path(path).resolve().parent
        self.source_sha256 = sha256(Path(path))
        data = _json(Path(path))
        if data.get("suite_sha256") != suite_fingerprint:
            raise ValueError("Imported observations are for a different frozen suite")
        rows = data.get("observations", [])
        self.rows = {}
        for row in rows:
            key = (row["condition"], row["case_id"])
            if key in self.rows:
                raise ValueError("Duplicate imported condition/case observation")
            self.rows[key] = row

    def __call__(self, material: dict, ctx: AdapterContext) -> AdapterResult:
        row = self.rows.get((ctx.condition, material["case_id"]))
        if row is None:
            raise AdapterUnavailable("No measured baseline/coding-agent observation")
        resource_hash = hashlib.sha256(json.dumps(ctx.resource_contract, sort_keys=True).encode()).hexdigest()
        if row.get("resource_contract_sha256") != resource_hash or not row.get("source_ref"):
            raise ValueError("Imported result needs the same material/command/budget contract and source reference")
        if not row.get("source_sha256") or sha256(_inside(self.artifact_root, row["source_ref"])) != row["source_sha256"]:
            raise ValueError("Imported baseline observation source file/hash must be verifiable")
        if row.get("memory_enabled") is not ctx.memory_enabled:
            raise ValueError("Imported memory condition differs")
        origin = row.get("execution_origin")
        if origin not in {"RECORDED_LIVE", "RECORDED_REPLAY", "RECORDED_DOUBLE", "RECORDED_HUMAN"}:
            raise ValueError("Imported runs must distinguish live, replay, double or human origin")
        measurements = deepcopy(row.get("measurements", {}))
        measurements.update(observation_source_ref=row["source_ref"], observation_source_sha256=row["source_sha256"],
                            import_file_sha256=self.source_sha256)
        if measurements.get("human_seconds") is not None or measurements.get("manual_actions") is not None:
            human_ref, human_hash = measurements.get("human_measurement_ref"), measurements.get("human_measurement_sha256")
            if not human_ref or not human_hash or sha256(_inside(self.artifact_root, human_ref)) != human_hash:
                raise ValueError("Human measurement needs an existing recorded source with a matching digest")
        return AdapterResult(deepcopy(row["result"]), row["execution_scope"], measurements, "IMPORTED_" + origin, self.artifact_root)


class ScriptedModelDouble:
    """Explicit scripted transport double, never a stand-in for a live baseline."""

    def __init__(self, actions: list[dict], observer: InvestigationObserver):
        self.actions, self.observer = deepcopy(actions), observer
        self.events: list[dict] = []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        started = time.perf_counter()
        action = self.actions.pop(0) if self.actions else {"kind": "finish"}
        names = [schema["function"]["name"] for schema in kwargs.get("tools", [])]
        if names == ["finish_investigation"] and action.get("kind") == "tool":
            action = {"kind": "finish"}
        event = {"usage": None, "status": "success"}
        try:
            if action["kind"] in {"timeout", "serving_failure"}:
                event["status"] = "timeout" if action["kind"] == "timeout" else "failed"
                raise TimeoutError("TEST_DOUBLE serving timeout")
            if names == ["describe_screen"]:
                name, arguments = "describe_screen", {"visible_symptom": "가입 오류 화면", "has_app_screen": True}
            elif action["kind"] == "tool":
                name, arguments = action["name"], action.get("arguments", {})
            else:
                name, arguments = "finish_investigation", {
                    "intent": "investigate", "symptom_summary": "현재 자료 확인 후 추가 조사",
                    "cause": "", "explanation": "", "supporting_evidence_ids": [], "contradicting_evidence_ids": [],
                    "verification_step": "", "possible_fix": "", "missing_information": ["실제 실행 자료"],
                    "next_steps": ["현재 자료를 재확인하세요"], "previous_hypothesis_outcome": "not_rechecked",
                    "counter_evidence_ids": [],
                }
            tool_call = SimpleNamespace(id=uuid4().hex, function=SimpleNamespace(name=name, arguments=json.dumps(arguments, ensure_ascii=False)))
            return SimpleNamespace(id="TEST_DOUBLE-" + uuid4().hex, usage=None,
                                   choices=[SimpleNamespace(finish_reason="tool_calls", message=SimpleNamespace(content="", tool_calls=[tool_call]))])
        finally:
            self.events.append(event)
            self.observer.record_model("TEST_DOUBLE", status=event["status"], usage=None,
                                       elapsed_ms=(time.perf_counter() - started) * 1000,
                                       error_type="TimeoutError" if event["status"] != "success" else None)


class ReplayedOCR:
    def __init__(self, text: str):
        self.text, self.calls = text, 0

    def extract(self, image, *, deadline=None):
        started = time.perf_counter()
        self.calls += 1
        return {"service": "TEST_DOUBLE OCR transcript", "model": "TEST_DOUBLE", "status": "success",
                "usable_text": self.text, "lines": [{"text": self.text, "confidence": 1.0}],
                "image_sha256": hashlib.sha256(image).hexdigest(),
                "elapsed_ms": (time.perf_counter() - started) * 1000}


def _catalog(material: dict, observer: InvestigationObserver):
    from .report_intake import ObservedEventCatalog

    class MeasuredSource:
        def __init__(self, source, event):
            self.source, self.event = source, event

        def __getattr__(self, name):
            if name not in {"get_trace", "get_contract", "get_backend_evidence", "get_migration_state"}:
                return getattr(self.source, name)
            def call(trace_id):
                with observer.tool(name, phase="rules"):
                    data = getattr(self.source, name)(trace_id)
                    if name == "get_contract":
                        data.update(deepcopy(self.event.get("contract_context", {})))
                    return data
            return call

    catalog = ObservedEventCatalog(material["project_id"], material.get("events", []), complete=material.get("complete", True))
    catalog.locator = None
    for trace_id, (source, when) in list(catalog.events.items()):
        event = next(event for event in material["events"] if event["trace"]["trace_id"] == trace_id)
        catalog.events[trace_id] = (MeasuredSource(source, event), when)
    return catalog


def local_adapter(material: dict, ctx: AdapterContext) -> AdapterResult:
    """Consume public product APIs; no suite-specific diagnosis or repair branches."""
    from .report_contract import ReportContext
    from .report_intake import triage_report

    observer = InvestigationObserver(uuid4().hex, ctx.output_dir / "events", origin="evaluation_double")
    catalog = _catalog(material, observer)
    context = ReportContext(**material.get("context", {}))
    text = material["report"]
    transcript = material.get("photo", {}).get("ocr_transcript", "")
    model = None
    ocr = ReplayedOCR(transcript)
    if ctx.condition == "rules":
        # All conditions receive the exact same recorded photo transcript.
        report = text + ("\n사진 문자 단서(재생): " + transcript if transcript else "")
        result = triage_report(report, catalog, **context.to_dict())
        result.update(model_calls=0, cause_confirmed=False, fix_applied=False, fix_verified=False)
    else:
        from . import report_agent
        signature = inspect.signature(report_agent.investigate_submission)
        if "memory_enabled" not in signature.parameters:
            observer.finish("BLOCKED", stop_reason="memory_enabled_api_missing")
            raise AdapterUnavailable("A main investigation memory_enabled API is not connected yet")
        main_observer = InvestigationObserver(uuid4().hex, ctx.output_dir / "main-events", origin="evaluation_double")
        kwargs = {"repo": _inside(ctx.suite_root, ctx.resource_contract["code_root"]), "catalog": catalog,
                  "context": context, "db_path": ctx.db_path, "memory_enabled": ctx.memory_enabled,
                  "observer": main_observer,
                  "max_seconds": ctx.resource_contract["budget"]["investigation_seconds"], "use_nvidia": ctx.mode == "doubles"}
        photo = material.get("photo")
        if photo:
            kwargs["image"] = _inside(ctx.suite_root, photo["path"]).read_bytes()
        if ctx.mode == "doubles":
            model = ScriptedModelDouble(material.get("model_double", [{"kind": "finish"}]), observer)
            kwargs.update(client=model, ocr=ocr)
        elif transcript:
            text += "\n사진 문자 단서(재생, 서버 관측 아님): " + transcript
        if material.get("logs_path"):
            kwargs["log_file"] = _inside(ctx.suite_root, material["logs_path"])
        actual_tools = []
        original_call = report_agent.ProjectTools.call

        def measured_call(tools, name, arguments):
            with observer.tool(name, phase="current_tools"):
                output = original_call(tools, name, arguments)
                actual_tools.append({"tool": name, "arguments": json.loads(arguments),
                                     "status": "failed" if output.get("error") else "success"})
                return output

        with patch.dict(os.environ, clear=False), \
             patch.object(report_agent, "nvidia_settings", return_value=("TEST_DOUBLE", "TEST_DOUBLE")), \
             patch.object(report_agent.ProjectTools, "call", measured_call):
            for name in ("TRACEBRIDGE_EVENTS_FILE", "TRACEBRIDGE_LOG_FILE", "TRACEBRIDGE_PROJECT_PROFILE",
                         "TRACEBRIDGE_DEPLOYED_SHA", "TRACEBRIDGE_LOG_SERVICE", "TRACEBRIDGE_LOG_ENVIRONMENT",
                         "TRACEBRIDGE_LOG_TIMEZONE"):
                os.environ.pop(name, None)
            result = report_agent.investigate_submission(text, **kwargs)
        result["evaluation_tool_trace"] = actual_tools
    result.setdefault("change", {"status": "NOT_ATTEMPTED", "reason": "diagnosis_only_evaluation_adapter"})
    models = model.events if model else []
    tool_events = [event for event in observer.events if event["kind"] == "tool"]
    summary = observer.finish(result.get("run_status", "UNKNOWN"), expected_model_calls=len(models),
                              expected_tool_calls=len(tool_events))
    measurements = {"model_calls": len(models), "model_events": models, "external_model_calls": 0,
                    "tool_calls": len(tool_events), "service_calls": ocr.calls,
                    "tool_call_scope": "EvidenceSource reads and executed ProjectTools calls; no claims of savings",
                    "human_seconds": None, "manual_actions": None,
                    "measurement_limitations": ["Synthetic local/double timing; no live model or human time",
                        "EvidenceSource reads counted; main ProjectTools coverage requires A observer integration"]}
    result["evaluation_telemetry"] = summary
    return AdapterResult(result, "diagnosis_only", measurements, "TEST_DOUBLE" if ctx.mode == "doubles" else "LOCAL_OFFLINE")


def aggregate_results(rows: list[dict]) -> dict:
    """Keep condition + executed scope + provenance separate for all time/cost sums."""
    groups = {}
    for row in rows:
        key = "|".join((row["condition"], row.get("execution_scope", "not_run"), row.get("provenance", "not_run")))
        group = groups.setdefault(key, {"cases": 0, "executions": Counter(), "assessments": Counter(),
                                       "result_observed_rows": 0,
                                       "wrong_cause_confirmations": 0, "wrong_resolution_confirmations": 0,
                                       "unnecessary_modifications": 0, "verified_actions": 0,
                                       "metrics": {}})
        group["cases"] += 1
        group["result_observed_rows"] += row.get("result") is not None
        group["executions"][row["execution_status"]] += 1
        assessment = row.get("assessment", {})
        group["assessments"][assessment.get("status", "NOT_RUN")] += 1
        for source, target in (("wrong_cause_confirmation", "wrong_cause_confirmations"),
                               ("wrong_resolution_confirmation", "wrong_resolution_confirmations"),
                               ("unnecessary_modification", "unnecessary_modifications"),
                               ("verified_action", "verified_actions")):
            group[target] += assessment.get(source) is True
        values = {**row.get("metrics", {}), **{key: assessment.get(key) for key in
                  ("incident_match", "routing_correct", "correct_hold")}}
        for metric, value in values.items():
            if metric.endswith("limitations") or isinstance(value, (dict, list, str)):
                continue
            cell = group["metrics"].setdefault(metric, {"observed_sum": None, "observed_rows": 0, "missing_rows": 0})
            if value is None:
                cell["missing_rows"] += 1
            else:
                cell["observed_rows"] += 1
                cell["observed_sum"] = (cell["observed_sum"] or 0) + value
    for group in groups.values():
        group["executions"], group["assessments"] = dict(group["executions"]), dict(group["assessments"])
    return {"artifact_status": "DRAFT", "groups": groups, "performance_claim": "NOT_MEASURED",
            "memory_effect": "NOT_ESTABLISHED", "do_not_compare_across_scope_or_provenance": True}


def compare_memory(rows: list[dict]) -> list[dict]:
    """Paired functional outcomes, with no efficiency/accuracy superiority claim."""
    pairs = {}
    for row in rows:
        if row["condition"].startswith("tracebridge_memory_"):
            pairs.setdefault(row["case_id"], {})[row["condition"]] = row
    comparisons = []
    for case_id, pair in pairs.items():
        off, on = pair.get("tracebridge_memory_off"), pair.get("tracebridge_memory_on")
        comparable = bool(off and on and off["execution_status"] != "BLOCKED" and on["execution_status"] != "BLOCKED"
            and off["execution_scope"] == on["execution_scope"] and off["provenance"] == on["provenance"])
        status = "NOT_COMPARABLE"
        if comparable:
            before, after = off["assessment"].get("status") == "PASS", on["assessment"].get("status") == "PASS"
            status = "TIE" if before == after else "BETTER_ON_FIXED_CHECKS" if after else "WORSE_ON_FIXED_CHECKS"
        comparisons.append({"case_id": case_id, "functional_comparison": status,
                            "efficiency_effect": "NOT_ESTABLISHED", "synthetic_or_double_limit_applies": True})
    return comparisons


def response_quality_comparison(rows: list[dict]) -> dict:
    """Compare paired, labeled answers without treating synthetic timing as live quality."""
    pairs = {}
    for row in rows:
        if row.get("condition") in {"tracebridge_memory_off", "tracebridge_memory_on"}:
            pairs.setdefault(row["case_id"], {})[row["condition"]] = row
    totals = {name: {"assessed": 0, "passed": 0, "wrong_guidance": 0, "unsupported_cause": 0,
                     "unsupported_resolution": 0, "hold_cases": 0, "correct_holds": 0,
                     "retrieval_hit_cases": 0, "model_calls_observed": 0, "model_calls_sum": 0,
                     "elapsed_observed": 0, "elapsed_sum_ms": 0.0}
              for name in ("tracebridge_memory_off", "tracebridge_memory_on")}
    cases = []
    for case_id, pair in sorted(pairs.items()):
        off, on = pair.get("tracebridge_memory_off"), pair.get("tracebridge_memory_on")
        if not off or not on or any(item.get("result") is None or item.get("execution_status") == "BLOCKED" for item in (off, on)):
            cases.append({"case_id": case_id, "status": "NOT_COMPARABLE"})
            continue
        if off.get("execution_scope") != on.get("execution_scope") or off.get("provenance") != on.get("provenance") or off.get("expected") != on.get("expected"):
            cases.append({"case_id": case_id, "status": "NOT_COMPARABLE"})
            continue
        checks = {}
        for name, row in (("tracebridge_memory_off", off), ("tracebridge_memory_on", on)):
            result, expected, tally = row["result"], row["expected"], totals[name]
            allowed = expected.get("allowed", {}).get("route")
            wrong_guidance = result.get("route") == "GUIDANCE" and allowed is not None and "GUIDANCE" not in allowed
            unsupported_cause = expected.get("forbid_cause_confirmation") is True and result.get("cause_confirmed") is True
            unsupported_resolution = expected.get("forbid_resolution_confirmation") is True and (
                result.get("fix_applied") is True or result.get("fix_verified") is True or result.get("service_recovery") == "VERIFIED")
            hold = expected.get("hold") is True
            correct_hold = hold and result.get("route") in (allowed or []) and (not expected.get("requires_questions") or bool(result.get("questions")))
            hit = (result.get("memory_search") or {}).get("hit_count", 0) > 0
            tally["assessed"] += 1
            tally["passed"] += row.get("assessment", {}).get("status") == "PASS"
            tally["wrong_guidance"] += wrong_guidance
            tally["unsupported_cause"] += unsupported_cause
            tally["unsupported_resolution"] += unsupported_resolution
            tally["hold_cases"] += hold
            tally["correct_holds"] += correct_hold
            tally["retrieval_hit_cases"] += hit
            metrics = row.get("metrics", {})
            calls, elapsed = metrics.get("model_calls"), metrics.get("total_elapsed_ms")
            if type(calls) is int and calls >= 0:
                tally["model_calls_observed"] += 1
                tally["model_calls_sum"] += calls
            if type(elapsed) in {int, float} and math.isfinite(elapsed) and elapsed >= 0:
                tally["elapsed_observed"] += 1
                tally["elapsed_sum_ms"] += elapsed
            checks[name] = {"passed": row.get("assessment", {}).get("status") == "PASS",
                "wrong_guidance": wrong_guidance, "unsupported_cause": unsupported_cause,
                "unsupported_resolution": unsupported_resolution, "correct_hold": correct_hold if hold else None,
                "retrieval_hit": hit}
        cases.append({"case_id": case_id, "category": off.get("category"), "status": "PAIRED",
            "off": checks["tracebridge_memory_off"], "on": checks["tracebridge_memory_on"]})
    paired = [item for item in cases if item["status"] == "PAIRED"]
    for tally in totals.values():
        tally["mean_model_calls"] = round(tally["model_calls_sum"] / tally["model_calls_observed"], 3) if tally["model_calls_observed"] else None
        tally["mean_elapsed_ms"] = round(tally["elapsed_sum_ms"] / tally["elapsed_observed"], 3) if tally["elapsed_observed"] else None
    return {"evidence_scope": "PAIRED_SYNTHETIC_OR_IMPORTED_OBSERVATIONS", "memory_effect": "NOT_ESTABLISHED",
        "paired_cases": len(paired), "not_comparable_cases": len(cases) - len(paired),
        "better_on_fixed_checks": sum(item["on"]["passed"] and not item["off"]["passed"] for item in paired),
        "worse_on_fixed_checks": sum(item["off"]["passed"] and not item["on"]["passed"] for item in paired),
        "totals": totals, "cases": cases,
        "limitations": ["Fixed labels and offline/double timing do not establish real-user accuracy, latency or cost improvement.",
                        "A retrieval hit is a historical clue, not a verified current cause."]}


def evaluate_suite(*, suite_path: str | Path = DEFAULT_SUITE, output_root: str | Path = ROOT / "output/parallel-d/evaluations",
                   conditions: list[str] | None = None, mode: str = "local",
                   adapters: dict[str, Callable] | None = None, initial_db: str | Path | None = None) -> dict:
    if mode not in {"local", "doubles"}:
        raise ValueError("Only local/doubles mode is supported; this API makes no external model calls")
    conditions = list(CONDITIONS) if conditions is None else conditions
    if not conditions or len(set(conditions)) != len(conditions) or set(conditions) - CONDITIONS.keys():
        raise ValueError("Select distinct known comparison conditions")
    suite = load_suite(suite_path)
    execution_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:10]
    destination = Path(output_root) / execution_id
    destination.mkdir(parents=True, exist_ok=False)
    before = capture_code_snapshot()
    resources = deepcopy(suite["resources"])
    resource_hash = hashlib.sha256(json.dumps(resources, sort_keys=True).encode()).hexdigest()
    initial = destination / "initial-memory.db"
    initial_error = None
    try:
        if initial_db is not None:
            clone_memory(initial_db, initial)
        else:
            create_initial_memory(initial, _json(_inside(suite["root"], suite["initial_memory"])))
    except Exception as exc:
        initial_error = type(exc).__name__
    _write(destination / "manifest.json", {"artifact_status": "DRAFT", "mode": mode, "execution_id": execution_id,
        "suite_sha256": suite["fingerprint"], "frozen_artifact_hashes": suite["sha256"], "code_before": before,
        "resource_contract_sha256": resource_hash, "conditions": {name: CONDITIONS[name] for name in conditions},
        "initial_memory_sha256": sha256(initial) if initial.exists() else None, "initial_memory_error": initial_error,
        "external_calls_performed_by_runner": 0, "human_time_source": "NOT_OBSERVED"})
    rows = []
    adapters = adapters or {}
    for case in suite["cases"]:
        material = _json(_inside(suite["root"], case["input"]))
        expectation = _json(_inside(suite["root"], case["expectation"]))
        if material.get("case_id") != case["id"]:
            raise ValueError("Case input ID mismatch")
        for condition in conditions:
            run_dir = destination / case["id"] / condition
            run_dir.mkdir(parents=True)
            row = {"artifact_status": "DRAFT", "case_id": case["id"], "category": case["category"],
                   "variant": case["variant"], "source": case["source"], "condition": condition,
                   "memory_enabled": CONDITIONS[condition]["memory_enabled"], "expected": expectation,
                   "resource_contract_sha256": resource_hash, "execution_status": "BLOCKED",
                   "execution_scope": "not_run", "provenance": "not_run", "result": None,
                   "assessment": {"status": "NOT_RUN"}}
            started = time.perf_counter()
            try:
                adapter = adapters.get(condition)
                if adapter is None:
                    if condition in {"single_prompt", "coding_agent"}:
                        raise AdapterUnavailable("Measured baseline observations are required; no answers or human time are generated")
                    adapter = local_adapter
                if initial_error:
                    raise AdapterUnavailable("Initial reviewed memory initialization failed: " + initial_error)
                run_db = run_dir / "memory.db"
                clone_memory(initial, run_db)
                ctx = AdapterContext(condition, mode, row["memory_enabled"], run_db, run_dir,
                                     suite["root"], deepcopy(resources))
                execution = adapter(deepcopy(material), ctx)
                if not isinstance(execution, AdapterResult) or not isinstance(execution.result, dict):
                    raise ValueError("Adapter must return an AdapterResult with current checked result")
                measurements = execution.measurements or {}
                metrics = usage_metrics(execution.result, measurements)
                scope = execution.execution_scope
                if scope not in {"answer_only", "diagnosis_only", "prepare_change", "prepare_change_with_human"}:
                    raise ValueError("Unknown executed scope")
                metrics.update(question_count=len(execution.result["questions"]) if "questions" in execution.result else None,
                               human_seconds=_nonnegative(measurements.get("human_seconds"), "human_seconds"),
                               manual_actions=_nonnegative(measurements.get("manual_actions"), "manual_actions"),
                               total_elapsed_ms=_nonnegative(measurements.get("total_elapsed_ms"), "total_elapsed_ms"))
                if metrics["human_seconds"] is not None or metrics["manual_actions"] is not None:
                    if not measurements.get("human_measurement_ref"):
                        raise ValueError("Human time/manual actions require a recorded measurement reference")
                elapsed = round((time.perf_counter() - started) * 1000, 3)
                if execution.provenance in {"LOCAL_OFFLINE", "TEST_DOUBLE"}:
                    metrics["total_elapsed_ms"] = elapsed
                row.update(result=execution.result, execution_scope=scope, provenance=execution.provenance,
                           metrics=metrics, measurements=measurements,
                           execution_status="FAILED" if execution.result.get("run_status") in FAILURE_STATUSES else "COMPLETED",
                           assessment=assess_result(execution.result, expectation,
                                                    artifact_root=execution.artifact_root or run_dir), runner_elapsed_ms=elapsed)
            except AdapterUnavailable as exc:
                row.update(execution_status="BLOCKED", blocked_reason=str(exc), runner_elapsed_ms=round((time.perf_counter() - started) * 1000, 3))
            except Exception as exc:
                row.update(execution_status="FAILED", error_type=type(exc).__name__,
                           assessment={"status": "FAIL", "failed_checks": ["adapter_execution"]},
                           measurement_artifact_refs=[path.relative_to(run_dir).as_posix() for path in run_dir.rglob("events.jsonl")],
                           runner_elapsed_ms=round((time.perf_counter() - started) * 1000, 3))
            rows.append(row)
            _write(run_dir / "result.json", row)
    after = capture_code_snapshot()
    summary = aggregate_results(rows)
    summary.update(execution_id=execution_id, output_dir=str(destination), suite_sha256=suite["fingerprint"],
                   code_changed_during_run=before["fingerprint"] != after["fingerprint"], code_after=after,
                   cases=12, result_rows=len(rows), external_calls_performed_by_runner=0,
                   memory_comparison=compare_memory(rows), response_quality=response_quality_comparison(rows))
    _write(destination / "results.json", {"artifact_status": "DRAFT", "rows": rows})
    _write(destination / "summary.json", summary)
    return summary
