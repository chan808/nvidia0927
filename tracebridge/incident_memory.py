"""Local SQLite records and reviewed investigation clues; never current evidence."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import time
from sqlalchemy.exc import SQLAlchemyError

from .project_sources import redact, REQUEST_ID
from .report_contract import RUN_STATUSES
from .triage import EXCEPTION_PATTERN
from .semantic_memory import VECTOR_SCHEMA, current_review_matches, embedding_client_from_env, fuse_candidates, vector_candidates


WORKSPACE = Path(__file__).resolve().parents[1]
REUSABLE = {"APPROVED", "EDITED"}
SIGNAL_KEYS = ("error_codes", "paths", "exceptions", "stack_fingerprints")
TEXT_EDITABLE = {"symptom", "finding", "next_action"}
EDITABLE = TEXT_EDITABLE | {"applicability", "check_sequence", "disproof_conditions", "invalid_conditions", "limitations"}
CONDITION_KEYS = {"service", "environment", "operation", "method", "path", "runtime_sha", "local_sha"}
ROUTES = {"GUIDANCE", "WORK_CANDIDATE", "INVESTIGATE", "REQUEST_CONTEXT"}
SCHEMA = """
CREATE TABLE IF NOT EXISTS project_applications (work_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, record_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS incidents (
    project_id TEXT NOT NULL, incident_id TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    latest_run_id TEXT NOT NULL, latest_revision INTEGER NOT NULL,
    PRIMARY KEY (project_id, incident_id)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, incident_id TEXT NOT NULL,
    revision INTEGER NOT NULL CHECK (revision > 0), executed_at TEXT NOT NULL,
    route TEXT NOT NULL, run_status TEXT NOT NULL,
    content_hash TEXT NOT NULL, record_json TEXT NOT NULL,
    UNIQUE (project_id, incident_id, revision),
    UNIQUE (project_id, incident_id, run_id),
    FOREIGN KEY (project_id, incident_id) REFERENCES incidents(project_id, incident_id)
);
CREATE TABLE IF NOT EXISTS cards (
    card_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, incident_id TEXT NOT NULL,
    run_id TEXT NOT NULL UNIQUE, review_status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (review_status IN ('PENDING', 'APPROVED', 'EDITED', 'REJECTED')),
    signals_json TEXT NOT NULL, card_json TEXT NOT NULL,
    FOREIGN KEY (project_id, incident_id, run_id) REFERENCES runs(project_id, incident_id, run_id)
);
CREATE INDEX IF NOT EXISTS cards_project_review ON cards(project_id, review_status);
CREATE VIRTUAL TABLE IF NOT EXISTS card_search USING fts5(card_id UNINDEXED, search_text);
CREATE TABLE IF NOT EXISTS change_jobs (
    work_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, incident_id TEXT NOT NULL,
    source_run_id TEXT NOT NULL UNIQUE, result_run_id TEXT NOT NULL UNIQUE,
    content_hash TEXT NOT NULL, record_json TEXT NOT NULL,
    FOREIGN KEY (project_id, incident_id, source_run_id) REFERENCES runs(project_id, incident_id, run_id),
    FOREIGN KEY (project_id, incident_id, result_run_id) REFERENCES runs(project_id, incident_id, run_id)
);
"""


class RunConflict(ValueError):
    """An existing run ID or incident revision has different contents."""


def db_location(path: str | Path | None = None) -> Path:
    value = path if path is not None else os.getenv("TRACEBRIDGE_DB_PATH") or "output/tracebridge/incidents.sqlite3"
    if str(value) == ":memory:":
        raise ValueError("Incident records require a persistent SQLite file")
    location = Path(value).expanduser()
    if location.suffix.lower() not in {".db", ".sqlite", ".sqlite3"}:
        raise ValueError("Incident DB filename must end with .db, .sqlite or .sqlite3")
    return location if location.is_absolute() else WORKSPACE / location


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _id(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 128 or any(ord(c) < 32 for c in value):
        raise ValueError(f"Invalid {name}")
    return value


def _path(value: str) -> str:
    value = redact(value.split("?", 1)[0].split("#", 1)[0])[:200]
    # Request values in common dynamic URL segments are not durable search keys.
    return re.sub(r"(?<=/)(?:\d+|[a-fA-F0-9]{8}-[a-fA-F0-9-]{27,})(?=/|$)", "{id}", value)


def _text(value: str, limit: int = 500) -> str:
    """Bounded summaries only. Raw input/log/code bodies are omitted separately."""
    value = redact(value)
    value = re.sub(r'(https?://[^\s"<>]+|/[^\s"<>]+)\?[^\s"<>]*', lambda m: m[1] + "?[VALUES_OMITTED]", value)
    value = re.sub(r'("[^"\n]{1,80}"\s*:\s*)(?:"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?|true|false|null)', r'\1"[VALUE]"', value)
    value = re.sub(r"\b([A-Za-z_][\w.-]{0,79})\s*[=:]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)", r"\1=[VALUE]", value)
    return value[:limit]


def _metadata(value):
    """Only used on selected provenance, scope and call metadata, never bodies."""
    if isinstance(value, dict):
        output = {}
        for key, item in value.items():
            if re.search(r"(?i)password|passwd|token(?!s$)|secret|api.?key|authorization|cookie|user.?id|client.?ip", key):
                # Token counts are usage metadata; credentials are not.
                if key not in {"prompt_tokens", "completion_tokens"}:
                    continue
            if key in {"request", "body", "raw", "headers", "lines", "usable_text", "text", "answers", "messages", "logs", "content"}:
                continue
            output[key] = _path(item) if key == "path" and isinstance(item, str) else _metadata(item)
        return output
    if isinstance(value, list):
        return [_metadata(item) for item in value]
    return redact(value)[:500] if isinstance(value, str) else value


def _selected(data: dict, keys) -> dict:
    return _metadata({key: data[key] for key in keys if key in data})


def _memory_record(search: dict) -> dict:
    """Store search measurements and clue snapshots, not arbitrary nested input."""
    output = _selected(search, ("status", "error_type", "strategy", "hit_count", "elapsed_ms", "query_signals", "query_hash", "retrieval_scope"))
    if "semantic" in search:
        output["semantic"] = _selected(search["semantic"], ("status", "error_type", "embedding_calls", "scanned", "candidate_count",
            "scan_truncated", "stale_skipped", "dimension_mismatches", "model_key", "query_cache_hits", "query_embedding_budget"))
    output["cards"] = []
    if search.get("status") == "DISABLED":
        output.update(hit_count=0, current_recheck={}, rechecks=[])
        return output
    for card in search.get("cards", [])[:2]:
        saved = _selected(card, ("card_id", "project_id", "incident_id", "source_run_id", "revision", "card_kind", "diagnosis_type", "observed_status", "scope", "signals", "source_run_status", "verification", "version_provenance", "evidence_refs",
            "card_format", "applicability", "observed_features", "check_sequence", "disproof_conditions", "invalid_conditions", "limitations", "source", "last_checked", "verification_results", "field_sources",
            "contract_analysis", "responsibility", "symptom_status", "product_status", "claim_verification"))
        for key in TEXT_EDITABLE:
            saved[key] = _text(card.get(key, ""))
        saved["review"] = _selected(card.get("review", {}), ("status", "revision", "reviewer", "at", "note"))
        saved["hypotheses"] = []
        for hypothesis in card.get("hypotheses", [])[:3]:
            item = _selected(hypothesis, ("status", "cause_confirmed", "fix_verified", "supporting_evidence_refs", "contradicting_evidence_refs"))
            item.update({key: _text(hypothesis.get(key, ""), 300) for key in ("cause", "explanation", "verification_step", "possible_fix")})
            saved["hypotheses"].append(item)
        output["cards"].append(saved)
    current = search.get("current_recheck", {})
    output["current_recheck"] = _selected(current, ("run_id", "request_connection", "version_provenance", "evidence_refs"))
    output["current_recheck"]["logs"] = _selected(current.get("logs", {}), ("connected", "aggregate", "verified_count"))
    output["current_recheck"]["logs"]["reads"] = [_selected(step, ("tool", "phase", "status", "evidence_ids", "elapsed_ms")) for step in current.get("logs", {}).get("reads", [])]
    output["rechecks"] = [_selected(item, ("card_id", "source_run_id", "status", "reasons", "current_evidence_refs", "applicability_status", "current_guidance_observed", "usable_as_current_evidence")) for item in search.get("rechecks", [])[:2]]
    return output


def signals_from_text(text: str) -> dict[str, list[str]]:
    text = redact(text[:300_000])
    frames = re.findall(r"\bat\s+([\w.$]+)\(([^\n()]+)\)", text)[:8]
    stack = "\n".join(f"{function}({re.sub(r':\d+', '', file)})" for function, file in frames)
    return {
        "error_codes": [value for value in dict.fromkeys(re.findall(r"\b[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)+\b", text)) if len(value) <= 80][:8],
        "paths": list(dict.fromkeys(_path(p) for p in re.findall(r"(?<![/\w])/[A-Za-z0-9_./{}~-]+", text) if not p.startswith("//")))[:8],
        "exceptions": [value for value in dict.fromkeys(EXCEPTION_PATTERN.findall(text)) if len(value) <= 200][:8],
        "stack_fingerprints": [_hash(stack)] if stack else [],
    }


def current_signals(result: dict) -> dict[str, list[str]]:
    """Use current scoped observations, excluding report guesses and source constants."""
    signals = {key: [] for key in SIGNAL_KEYS}
    for item in result.get("observations", []):
        if item.get("run_id", result.get("run_id")) != result.get("run_id"):
            continue
        if item.get("kind") != "rule_observation" and not (item.get("kind") == "log" and item.get("scope_status") == "VERIFIED"):
            continue
        found = item.get("signals") or signals_from_text(item.get("content", item.get("fact", "")))
        for key in SIGNAL_KEYS:
            signals[key].extend(found.get(key, []))
    path = result.get("scope", {}).get("path")
    if path:
        signals["paths"].append(_path(path))
    return {key: list(dict.fromkeys(values))[:8] for key, values in signals.items()}


def _evidence(item: dict, run_id: str, *, inherited_clue: bool = False) -> dict:
    if item.get("run_id", run_id) != run_id and not inherited_clue:
        raise ValueError("Evidence belongs to another run")
    output = _metadata({key: item[key] for key in (
        "id", "kind", "source", "source_system", "service", "event_at", "environment", "trace_id",
        "correlated", "scope_status", "scope_checks", "scope", "source_revision", "repository_id", "source_tree_dirty",
        "image_sha256", "confidence", "inference",
    ) if key in item})
    content = item.get("content", item.get("fact", ""))
    output.update(run_id=run_id, content_hash=item.get("content_hash", _hash(content)) if "content" not in item and "fact" not in item else _hash(content), redaction_applied=True)
    if inherited_clue and item.get("run_id", run_id) != run_id:
        output["origin_run_id"] = item.get("origin_run_id", item["run_id"])
    if item.get("kind") == "rule_observation":
        output["content"] = _text(content)
    else:
        output["content_omitted"] = True
    output["signals"] = deepcopy(item.get("signals", signals_from_text(content)))
    return output


def _resume_session(result: dict) -> dict | None:
    session = result.get("session")
    if not session:
        return None
    # Preserve only explicit identity clues. Never turn a context candidate or visual-model ID into an exact ID.
    original = "\n".join([session.get("text", ""), *session.get("answers", []), session.get("ocr_clue_text", "")])
    ids = list(dict.fromkeys(REQUEST_ID.findall(original)))
    clues = [f"requestId={id_}" for id_ in ids]
    if result.get("reported_status") is not None:
        clues.append(str(result["reported_status"]))
    reported = signals_from_text(original)
    clues.extend(reported["error_codes"] + reported["exceptions"] + reported["paths"])
    scope = session.get("context", {})
    if scope.get("operation"):
        clues.append(_text(scope["operation"], 80))
    return {
        "text": " ".join(clues) or "이전 접수: 화면·동작 재확인 필요",
        "answers": [], "answer_count": session.get("answer_count", len(session.get("answers", []))),
        "received_at": session["received_at"], "context": _metadata(scope),
        "source_binding": session["source_binding"], "input_modes": session.get("input_modes", []),
        "action_preference": session.get("action_preference", "UNSPECIFIED") if session.get("action_preference", "UNSPECIFIED") in {"UNSPECIFIED", "INVESTIGATE_ONLY", "PREPARE_ALLOWED"} else "UNSPECIFIED",
        "retention": "structured_clues_only",
    }


def minimal_record(result: dict) -> dict:
    """An allowlisted record, not a dump of the live result or its session history."""
    for name in ("project_id", "incident_id", "run_id"):
        _id(result.get(name), name)
    revision = result.get("revision", 1)
    if type(revision) is not int or revision < 1 or result.get("route") not in ROUTES or result.get("run_status") not in RUN_STATUSES:
        raise ValueError("Invalid run revision, route or status")
    record = {key: result[key] for key in (
        "contract_version", "project_id", "incident_id", "run_id", "trace_id", "correlation", "correlation_basis",
        "route", "work_role", "diagnosis_type", "finding_status", "claim_status", "claim_coverage",
        "observed_status", "reported_status", "run_status", "stop_reason", "executed_at", "elapsed_ms", "message_received_at",
        "model", "model_calls", "session_model_calls", "deployment_observed", "input_modes",
        "case_kind", "requested_action", "symptom_status", "product_status",
    ) if key in result}
    record = _metadata(record)
    record.update(revision=revision, record_format="minimal_sqlite_v1")
    for key in ("summary", "route_reason", "next_action"):
        record[key] = _text(result.get(key, ""))
    for key in ("scope", "candidates", "candidate_trace_ids", "version_provenance", "log_scope", "usage", "timeout_reasons", "hypothesis_updates", "relative_date_basis", "correlation_confirmation", "source_registration"):
        record[key] = _metadata(result.get(key, [] if key in {"candidates", "candidate_trace_ids", "timeout_reasons", "hypothesis_updates"} else {}))
    if "responsibility" in result:
        record["responsibility"] = _selected(result["responsibility"], ("status", "reason", "fields", "evidence_sources"))
    if "contract_analysis" in result:
        record["contract_analysis"] = _selected(result["contract_analysis"], ("status", "complete", "scope_status", "provenance", "versions",
            "required_fields", "request_fields", "missing_fields", "unexpected_fields", "forbidden_fields", "type_mismatches", "unobserved_types", "responsibility", "limitations"))
    record["steps"] = [_selected(step, ("tool", "phase", "status", "evidence_ids", "elapsed_ms")) for step in result.get("steps", [])]
    record["model_trace"] = [_selected(call, ("call", "status", "http_status", "tools", "elapsed_ms", "model", "response_id", "finish_reason", "mode")) for call in result.get("model_trace", [])]
    record["service_calls"] = []
    for call in result.get("service_calls", []):
        saved = _selected(call, ("service", "phase", "model", "status", "image_sha256", "elapsed_ms", "http_status"))
        if call.get("endpoint"):
            saved["endpoint_hash"] = _hash(call["endpoint"])
        record["service_calls"].append(saved)
    record["claim_items"] = [{"facet": item["facet"], "status": item["status"]} for item in result.get("claim_items", [])]
    for key in ("questions", "missing_information", "next_steps", "notes"):
        record[key] = [_text(value) for value in result.get(key, [])]
    for key in ("cause_confirmed", "fix_applied", "fix_verified"):
        record[key] = result.get(key) is True
    run_id = record["run_id"]
    for key in ("observations", "evidence", "report_clues"):
        record[key] = [_evidence(item, run_id, inherited_clue=key == "report_clues") for item in result.get(key, [])]
    known_ids = {item["id"] for item in record["evidence"]}
    record["hypotheses"] = []
    for hypothesis in result.get("hypotheses", [])[:3]:
        checked = {key: _text(hypothesis.get(key, ""), 300) for key in ("cause", "explanation", "verification_step", "possible_fix", "status")}
        checked["cause_confirmed"] = hypothesis.get("cause_confirmed") is True
        checked["fix_verified"] = hypothesis.get("fix_verified") is True
        checked["limitations"] = [_text(value) for value in hypothesis.get("limitations", [])]
        for prefix in ("supporting", "contradicting"):
            ids = [id_ for id_ in hypothesis.get(f"{prefix}_evidence_ids", []) if id_ in known_ids]
            checked[f"{prefix}_evidence_ids"] = ids
            checked[f"{prefix}_evidence_refs"] = [{"run_id": run_id, "evidence_id": id_} for id_ in ids]
        record["hypotheses"].append(checked)
    record["signals"] = current_signals(result)
    scope = record.get("scope", {})
    record["symptom_summary"] = " · ".join(str(value) for value in (
        scope.get("operation") or scope.get("path"),
        f"HTTP {record['observed_status']}" if record.get("observed_status") else None,
        *record["signals"]["error_codes"], *record["signals"]["exceptions"],
    ) if value)[:200] or "사건 식별·관측 추가 확인 필요"
    if session := _resume_session(result):
        record["session"] = session
    record["memory_search"] = _memory_record(result.get("memory_search", {}))
    if "development_target" in result:
        record["development_target"] = _selected(result["development_target"], ("target_id", "snapshot_sha256", "source_origin"))
    if "change" in result:
        record["change"] = _selected(result["change"], ("work_id", "source_run_id", "status", "review_status", "case_kind", "policy", "diff",
            "candidate_fix_verified", "original_applied", "deployment_status", "service_recovery", "artifact_ref", "model_mode", "verification_scope"))
    if "recovery" in result:
        record["recovery"] = _metadata(result["recovery"])
    return record


def _sha(value) -> str | None:
    return value.lower() if isinstance(value, str) and re.fullmatch(r"[a-fA-F0-9]{7,64}", value) else None


def _same_sha(left: str, right: str) -> bool:
    return left.startswith(right) or right.startswith(left)


def _historical_refs(record: dict) -> list[str]:
    return list(dict.fromkeys(f"historical:{record['run_id']}:{item['id']}"
        for item in [*record.get("observations", []), *record.get("evidence", [])]))


def _assessment(record: dict, key: str, default):
    return record.get(key) or record.get("log_scope", {}).get("assessment", {}).get(key) or default


def _applicability(record: dict) -> dict:
    scope, versions = record.get("scope", {}), record.get("version_provenance", {})
    runtime = versions.get("runtime", {})
    return {
        "project_id": record["project_id"],
        **{key: scope.get(key) for key in ("service", "environment", "operation", "method", "path")},
        "runtime_sha": _sha(runtime.get("sha")) if runtime.get("status") == "OBSERVED" else None,
        "local_sha": _sha(versions.get("local_head", {}).get("sha")),
        "contract_versions": _metadata(_assessment(record, "contract_analysis", {}).get("versions", {})),
        "source_run_id": record["run_id"],
    }


def _verification_results(record: dict, job: dict | None = None) -> dict:
    """Describe recorded verification, requiring a persisted job for candidate success."""
    run_id, change = record["run_id"], record.get("change", {})
    known = set(_historical_refs(record))
    supporting = []
    for hypothesis in record.get("hypotheses", []):
        if hypothesis.get("cause_confirmed"):
            for ref in hypothesis.get("supporting_evidence_refs", []):
                qualified = f"historical:{ref['run_id']}:{ref['evidence_id']}"
                if qualified in known:
                    supporting.append(qualified)
    cause_recorded = record.get("cause_confirmed") is True
    candidate = job.get("candidate_fix_verified") is True if job else False
    checks = [_selected(item, ("phase", "command_id", "status", "exit_code", "elapsed_ms", "input",
        "execution_settings_sha256", "stdout_ref", "stdout_sha256", "stderr_ref", "stderr_sha256"))
        for item in (job or {}).get("checks", [])]
    return {
        "cause_confirmation": {
            "status": "RECORDED_CONFIRMED" if cause_recorded and supporting else "REPORTED_UNVERIFIED" if cause_recorded else "NOT_CONFIRMED",
            "recorded": cause_recorded, "source_run_id": run_id, "evidence_refs": list(dict.fromkeys(supporting)),
        },
        "candidate_validation": {
            "status": "VERIFIED" if candidate else "REPORTED_UNVERIFIED" if change.get("candidate_fix_verified") is True else "NOT_VERIFIED",
            "source_run_id": run_id, "source_work_id": (job or {}).get("work_id"),
            "scope": change.get("verification_scope", "NOT_RECORDED"), "checks": checks,
            "artifact_ref": (job or change).get("artifact_ref"), "diff_sha256": (job or change).get("diff", {}).get("sha256"),
        },
        "original_application": {
            "status": "RECORDED_APPLIED" if record.get("fix_applied") is True or change.get("original_applied") is True else "NOT_APPLIED",
            "source_run_id": run_id,
        },
        "service_recovery": {
            "status": "RECORDED_VERIFIED" if change.get("service_recovery") == "VERIFIED" else change.get("service_recovery", "NOT_VERIFIED"),
            "source_run_id": run_id, "legacy_fix_verified": record.get("fix_verified") is True,
            "evidence": _metadata(record.get("recovery", {})),
        },
    }


def _enrich_card(card: dict, record: dict, job: dict | None = None, investigation: dict | None = None) -> dict:
    """Add JSON defaults on read; never rewrite source runs, hashes or review history."""
    run_id, refs = record["run_id"], _historical_refs(record)
    source = {"run_id": run_id, "revision": record["revision"], "at": record.get("executed_at"), "kind": "SOURCE_RUN"}
    aggregate = record.get("log_scope", {}).get("aggregate", {})
    card.setdefault("card_format", "reviewed_manual_v2")
    card.setdefault("applicability", _applicability(record))
    card["applicability"].setdefault("contract_versions", _applicability(record)["contract_versions"])
    card.setdefault("contract_analysis", _metadata(_assessment(record, "contract_analysis", {})))
    card.setdefault("responsibility", _metadata(_assessment(record, "responsibility", {"status": "UNCONFIRMED"})))
    card.setdefault("symptom_status", _assessment(record, "symptom_status", "UNOBSERVED"))
    card.setdefault("product_status", _assessment(record, "product_status", "UNCONFIRMED"))
    card.setdefault("claim_verification", _selected(record, ("reported_status", "observed_status", "claim_status", "claim_items")))
    card.setdefault("observed_features", {
        "observed_status": record.get("observed_status"), "signals": deepcopy(record.get("signals", {})),
        "observations_complete": aggregate.get("complete"), "conflicts": bool(aggregate.get("conflicts")),
        "evidence_refs": refs,
    })
    sequence = []
    descriptions = {
        "find_logs": "현재 요청의 로그를 다시 조회하고 연결·범위 완전성·관측 충돌을 확인한다.",
        "get_version": "현재 실행 버전과 로컬 코드 버전의 관측 출처를 다시 확인한다.",
        "search_code": "현재 코드에서 과거 조회 대상을 다시 확인한다.",
        "get_contract": "현재 계약과 DTO의 출처·버전을 다시 확인한다.",
    }
    for step in record.get("steps", [])[:12]:
        if step.get("tool") in descriptions:
            sequence.append({"step": descriptions[step["tool"]], "origin": "RECORDED_TOOL",
                "source_run_id": run_id, "tool": step["tool"], "past_status": step.get("status"),
                "evidence_refs": [ref for id_ in step.get("evidence_ids", [])
                    if (ref := f"historical:{run_id}:{id_}") in refs]})
    planned = [*record.get("next_steps", []), *[h.get("verification_step", "") for h in record.get("hypotheses", [])], record.get("next_action", "")]
    for text in dict.fromkeys(value for value in planned if value):
        sequence.append({"step": _text(text), "origin": "RECORDED_PLAN", "source_run_id": run_id, "evidence_refs": refs[:4]})
    card.setdefault("check_sequence", sequence[:12])
    disproof = [
        "현재 응답 상태·오류 코드·예외·스택 지문이 과거 관측과 다르다.",
        "현재 프로젝트·서비스·환경·동작·메서드·경로가 적용 조건과 다르거나 미관측이다.",
        "과거 또는 현재 실행 버전이 미관측이거나 서로 다르다.",
        "현재 관측이 충돌하거나 조회 범위를 끝까지 확인하지 못했다.",
    ]
    card.setdefault("disproof_conditions", [{"condition": text, "origin": "MEMORY_RECHECK_POLICY", "source_run_id": run_id, "evidence_refs": refs[:4]} for text in disproof])
    card.setdefault("invalid_conditions", [])
    limits = ["과거 카드는 현재 근거와 실행 권한을 대신하지 않는다.", "검토 승인은 원인·수정·회복 확인이 아니다."]
    if record.get("version_provenance", {}).get("runtime", {}).get("status") != "OBSERVED":
        limits.append("출처 실행의 실제 실행 버전이 미관측이다.")
    limits.extend(_text(item) for item in (job or {}).get("limitations", []))
    card.setdefault("limitations", limits[:12])
    card.setdefault("source", {
        "run_id": run_id, "incident_id": record["incident_id"], "revision": record["revision"],
        "executed_at": record.get("executed_at"), "route": record["route"], "run_status": record["run_status"],
        "case_kind": record.get("case_kind", "NOT_RECORDED"),
        "evidence": [_selected(item, ("id", "kind", "source", "content_hash", "scope_status", "source_revision"))
            for item in record.get("evidence", [])[:24]],
    })
    card.setdefault("last_checked", {
        "source_run_id": run_id, "at": record.get("executed_at"), "run_status": record["run_status"],
        "observations_complete": aggregate.get("complete"), "conflicts": bool(aggregate.get("conflicts")),
        "runtime_status": record.get("version_provenance", {}).get("runtime", {}).get("status", "NOT_OBSERVED"),
    })
    origins = card.setdefault("field_sources", {})
    for key in EDITABLE:
        origins.setdefault(key, deepcopy(source))
    if job and investigation:
        # A prepared-change run carries the original investigation's scope/version.
        # Finishing copy checks does not constitute a new read of service logs.
        parent = investigation["run_id"]
        card["source"]["investigation_run_id"] = parent
        card["source"]["work_id"] = job["work_id"]
        card["source"]["investigation_evidence"] = [_selected(item, ("id", "kind", "source", "content_hash", "scope_status")) for item in investigation.get("evidence", [])[:24]]
        previous = investigation.get("log_scope", {}).get("aggregate", {})
        card["observed_features"] = {"source_run_id": parent, "observed_status": investigation.get("observed_status"),
            "signals": deepcopy(investigation.get("signals", {})), "observations_complete": previous.get("complete"),
            "conflicts": bool(previous.get("conflicts")), "evidence_refs": _historical_refs(investigation)}
        card["version_provenance"] = deepcopy(investigation.get("version_provenance", {}))
        card["applicability"]["contract_versions"] = _applicability(investigation)["contract_versions"]
        card["contract_analysis"] = _metadata(_assessment(investigation, "contract_analysis", {}))
        card["responsibility"] = _metadata(_assessment(investigation, "responsibility", {"status": "UNCONFIRMED"}))
        card["symptom_status"] = _assessment(investigation, "symptom_status", "UNOBSERVED")
        card["product_status"] = _assessment(investigation, "product_status", "UNCONFIRMED")
        card["claim_verification"] = _selected(investigation, ("reported_status", "observed_status", "claim_status", "claim_items"))
        card["last_checked"] = {"source_run_id": parent, "at": investigation.get("executed_at"), "run_status": investigation["run_status"],
            "observations_complete": previous.get("complete"), "conflicts": bool(previous.get("conflicts")),
            "runtime_status": investigation.get("version_provenance", {}).get("runtime", {}).get("status", "NOT_OBSERVED")}
        if origins["applicability"].get("kind") != "REVIEW_EDIT":
            origins["applicability"] = {"run_id": parent, "kind": "SOURCE_RUN", "at": investigation.get("executed_at"), "revision": investigation["revision"]}
        if origins["limitations"].get("kind") != "REVIEW_EDIT":
            card["limitations"] = list(dict.fromkeys([*card["limitations"], *[_text(value) for value in job.get("limitations", [])]]))[:12]
    # These factual projections always come from source records, never editable card text.
    card["verification_results"] = _verification_results(record, job)
    return card


def _new_card(record: dict) -> dict:
    aggregate = record.get("log_scope", {}).get("aggregate", {})
    guidance = record["route"] == "GUIDANCE" and record.get("correlation") == "EXACT_ID" and record["run_status"] == "COMPLETED" and not aggregate.get("conflicts") and aggregate.get("complete", True)
    return _enrich_card({
        "card_id": record["run_id"], "project_id": record["project_id"], "incident_id": record["incident_id"],
        "source_run_id": record["run_id"], "revision": record["revision"],
        "card_kind": "GUIDANCE" if guidance else "UNCONFIRMED",
        "symptom": record["symptom_summary"], "finding": record["summary"], "next_action": record["next_action"],
        "diagnosis_type": record.get("diagnosis_type"), "observed_status": record.get("observed_status"),
        "scope": record.get("scope", {}), "signals": record["signals"],
        "hypotheses": record["hypotheses"], "source_run_status": record["run_status"],
        "verification": {key: record[key] for key in ("cause_confirmed", "fix_applied", "fix_verified")},
        "version_provenance": record.get("version_provenance", {}),
        "evidence_refs": [f"historical:{record['run_id']}:{item['id']}" for item in record["observations"][:4]],
        "review": {"status": "PENDING", "revision": 0, "history": []},
        **({"prepared_change": deepcopy(record["change"])} if "change" in record else {}),
    }, record)


def _condition_edit(conditions: dict, *, nonempty: bool = False) -> dict:
    if not isinstance(conditions, dict) or set(conditions) - CONDITION_KEYS or nonempty and not conditions:
        raise ValueError("Conditions may contain service, environment, operation, method, path, runtime_sha or local_sha only")
    output = {}
    for key, value in conditions.items():
        if value is None and not nonempty:
            output[key] = None
        elif not isinstance(value, str) or not value.strip() or len(value) > 200:
            raise ValueError("Condition values need 1..200 characters")
        elif key.endswith("_sha"):
            if not _sha(value):
                raise ValueError("Version conditions require a 7..64 character hexadecimal SHA")
            output[key] = _sha(value)
        else:
            output[key] = _path(value) if key == "path" else redact(value)
    return output


def _review_changes(card: dict, changes: dict, revision: int) -> dict:
    output = {}
    for key, value in changes.items():
        if key in TEXT_EDITABLE:
            if not isinstance(value, str) or not value.strip() or len(value) > 500:
                raise ValueError("Card edits need 1..500 characters")
            output[key] = _text(value)
        elif key == "applicability":
            output[key] = {**card[key], **_condition_edit(value)}
        elif key == "invalid_conditions":
            if not isinstance(value, list) or len(value) > 12:
                raise ValueError("Invalid conditions need a list of at most 12 entries")
            output[key] = []
            for item in value:
                if not isinstance(item, dict) or set(item) != {"when", "reason"}:
                    raise ValueError("An invalid condition needs when and reason")
                reason = item["reason"]
                if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
                    raise ValueError("Invalid condition reason needs 1..500 characters")
                output[key].append({"when": _condition_edit(item["when"], nonempty=True), "reason": _text(reason),
                    "source_run_id": card["source_run_id"], "review_revision": revision})
        else:
            if not isinstance(value, list) or len(value) > 12 or any(not isinstance(item, str) or not item.strip() or len(item) > 500 for item in value):
                raise ValueError("Steps, disproof conditions and limitations need at most 12 short strings")
            if key == "limitations":
                output[key] = [_text(item) for item in value]
            else:
                label = "step" if key == "check_sequence" else "condition"
                output[key] = [{label: _text(item), "origin": "REVIEW_EDIT", "source_run_id": card["source_run_id"],
                    "review_revision": revision, "evidence_refs": []} for item in value]
    return output


def _md(value) -> str:
    """Render untrusted card wording as text, without links, HTML or code blocks."""
    value = "미관측" if value is None else str(value)
    value = re.sub(r"[\x00-\x20]+", " ", value).strip()
    return re.sub(r"([\\`*_{}\[\]()<>#+.!|~-])", r"\\\1", value)


def _origin(card: dict, field: str) -> str:
    origin = card.get("field_sources", {}).get(field, {})
    text = "출처 실행 " + _md(origin.get("run_id", card["source_run_id"]))
    if origin.get("kind") == "REVIEW_EDIT":
        text += ", 검토 정정 " + _md(origin.get("review_revision")) + " · " + _md(origin.get("reviewer"))
    return text


def _render_manual(project_id: str, cards: list[dict]) -> str:
    lines = [f"# {_md(project_id)} 사건 매뉴얼", "",
        "검토된 과거 기록의 확인 순서와 조치 단서다. 현재 자료로 재검증해야 하며 편집·실행·배포 권한을 부여하지 않는다.",
        "검토 승인, 원인 확인, 후보 사본 검증, 원본 적용, 서비스 회복은 각각 별도 상태다.", ""]
    if not cards:
        lines.append("현재 내보낼 검토 카드가 없다.")
    for card in cards:
        source, review = card["source"], card["review"]
        lines.extend([f"## {_md(card['symptom'])}", "",
            f"- 카드: {_md(card['card_id'])}; 사건: {_md(card['incident_id'])}; 출처 실행: {_md(card['source_run_id'])}; revision: {card['revision']}",
            f"- 출처 시각: {_md(source.get('executed_at'))}; 실행 상태: {_md(source['run_status'])}; 자료 유형: {_md(source['case_kind'])}",
            f"- 검토: {_md(review['status'])}; 검토자: {_md(review.get('reviewer'))}; 시각: {_md(review.get('at'))}; 검토 revision: {review['revision']}",
            f"- 기록된 판정·가설: {_md(card['finding'])} ({_origin(card, 'finding')})", "", "### 적용 조건과 버전", ""])
        investigation_id = source.get("investigation_run_id", card["source_run_id"])
        if source.get("investigation_run_id"):
            lines.append(f"- 관측·버전의 조사 출처 실행: {_md(investigation_id)}; 후보 검증 출처 작업: {_md(source['work_id'])}")
        claim = card["claim_verification"]
        lines.append(f"- 제보 HTTP: {_md(claim.get('reported_status'))}; 실제 HTTP: {_md(claim.get('observed_status'))}; 제보 확인: {_md(claim.get('claim_status'))}; 증상: {_md(card['symptom_status'])}; 제품: {_md(card['product_status'])}; 책임: {_md(card['responsibility'].get('status'))}; 출처 실행: {_md(investigation_id)}")
        for key in ("project_id", "service", "environment", "operation", "method", "path", "runtime_sha", "local_sha"):
            lines.append(f"- {key}: {_md(card['applicability'].get(key))} ({_origin(card, 'applicability')})")
        for name in ("local_head", "configured", "runtime"):
            version = card.get("version_provenance", {}).get(name, {})
            lines.append(f"- 버전 출처 {name}: {_md(version.get('source'))}; SHA: {_md(version.get('sha'))}; 상태: {_md(version.get('status', 'NOT_RECORDED'))}; 출처 실행: {_md(investigation_id)}")
        versions = card.get("applicability", {}).get("contract_versions", {})
        if versions:
            lines.append(f"- 계약·DTO·호출자 버전: {_md(_json(versions))}; 출처 실행: {_md(investigation_id)}")
        if card["contract_analysis"].get("provenance"):
            lines.append(f"- 계약 분석 출처: {_md(_json(card['contract_analysis']['provenance']))}; 출처 실행: {_md(investigation_id)}")
        features = card["observed_features"]
        lines.extend([f"- 관측 특징: HTTP {_md(features.get('observed_status'))}; {_md(_json(features.get('signals', {})))}; 출처 실행: {_md(features.get('source_run_id', card['source_run_id']))}",
            "", "### 확인 순서", ""])
        for index, step in enumerate(card["check_sequence"], 1):
            refs = ", ".join(_md(ref) for ref in step.get("evidence_refs", [])) or "근거 ID 없음"
            lines.append(f"{index}. {_md(step['step'])} ({_origin(card, 'check_sequence')}; {_md(step['origin'])}; {refs})")
        if not card["check_sequence"]:
            lines.append("출처에 확인 순서가 기록되지 않았다.")
        lines.extend(["", "### 반증·보류 및 부적합 조건", ""])
        for item in card["disproof_conditions"]:
            lines.append(f"- {_md(item['condition'])} ({_origin(card, 'disproof_conditions')}; {_md(item['origin'])})")
        for item in card["invalid_conditions"]:
            lines.append(f"- 부적합: {_md(_json(item['when']))} — {_md(item['reason'])} ({_origin(card, 'invalid_conditions')})")
        lines.extend(["", "### 조치와 검증 결과", "",
            f"- 기록된 다음 조치: {_md(card['next_action'])} ({_origin(card, 'next_action')}); 담당자 검토와 현재 근거 확인이 필요하다.",
            f"- 승인·정정: {_md(review['status'])} (검토 revision {review['revision']}; 출처 실행: {_md(card['source_run_id'])})"])
        labels = {"cause_confirmation": "원인 확인", "candidate_validation": "후보 사본 검증", "original_application": "원본 적용", "service_recovery": "회복 확인"}
        for key, label in labels.items():
            item = card["verification_results"][key]
            detail = "; 수정안 검증, 원본 적용·회복은 별도" if key == "candidate_validation" and item["status"] == "VERIFIED" else ""
            lines.append(f"- {label}: {_md(item['status'])} (출처 실행: {_md(item['source_run_id'])}{detail})")
        candidate = card["verification_results"]["candidate_validation"]
        if candidate.get("source_work_id"):
            lines.append(f"- 검증 작업: {_md(candidate['source_work_id'])}; 범위: {_md(candidate['scope'])}; diff SHA: {_md(candidate.get('diff_sha256'))}; 산출물: {_md(candidate.get('artifact_ref'))}; 출처 실행: {_md(candidate['source_run_id'])}")
        for check in candidate["checks"]:
            lines.append(f"- 검사 {_md(check.get('phase'))}/{_md(check.get('command_id'))}: {_md(check.get('status'))}; exit: {_md(check.get('exit_code'))}; 기록: {_md(check.get('stdout_ref'))}; 해시: {_md(check.get('stdout_sha256'))}; 출처 작업: {_md(candidate['source_work_id'])}; 출처 실행: {_md(candidate['source_run_id'])}")
        lines.extend(["", "### 근거 출처·마지막 확인·한계", ""])
        for item in source["evidence"]:
            lines.append(f"- {_md('historical:' + card['source_run_id'] + ':' + item['id'])}: {_md(item.get('kind'))}; 출처: {_md(item.get('source'))}; 해시: {_md(item.get('content_hash'))}")
        for item in source.get("investigation_evidence", []):
            lines.append(f"- {_md('historical:' + investigation_id + ':' + item['id'])}: {_md(item.get('kind'))}; 조사 출처: {_md(item.get('source'))}; 해시: {_md(item.get('content_hash'))}")
        last = card["last_checked"]
        lines.append(f"- 마지막 자료 확인: {_md(last.get('at'))}; 출처 실행: {_md(last['source_run_id'])}; 실행 상태: {_md(last['run_status'])}; 조회 완전성: {_md(last.get('observations_complete'))}; 충돌: {_md(last['conflicts'])}; 실행 버전: {_md(last['runtime_status'])}")
        for limit in card["limitations"]:
            lines.append(f"- 한계: {_md(limit)} ({_origin(card, 'limitations')})")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def validate_project_change(job: dict, run: dict) -> str:
    """The same strict candidate evidence contract for every storage backend."""
    for key in ("work_id", "project_id", "incident_id", "source_run_id", "result_run_id"):
        _id(job[key], key)
    if (job.get("record_format") != "project_change_job_v1" or len(_json(job)) > 100_000
            or job.get("case_kind") != "REGISTERED_PROJECT"
            or job.get("status") not in {"CHANGE_PREPARED", "POLICY_REJECTED", "NOT_REPRODUCED", "MODEL_NOT_REQUESTED", "MODEL_FAILED", "VERIFICATION_FAILED", "TIMED_OUT"}
            or job.get("review_status") != "WAITING_REVIEW" or job.get("original_applied") is not False
            or job.get("deployment_status") != "NOT_ATTEMPTED" or job.get("service_recovery") != "NOT_VERIFIED"
            or any(run.get(key) is not False for key in ("cause_confirmed", "fix_applied", "fix_verified"))
            or (run.get("project_id"), run.get("incident_id"), run.get("run_id")) != (job["project_id"], job["incident_id"], job["result_run_id"])
            or run.get("change", {}).get("work_id") != job["work_id"]
            or run["change"].get("verification_scope") != "REGISTERED_PROJECT_SNAPSHOT_ONLY"):
        raise ValueError("Invalid registered project candidate record")
    checks = job.get("checks", [])
    check_ids = job.get("policy", {}).get("check_ids", [])
    verified = (job["status"] == "CHANGE_PREPARED" and job.get("original_unchanged") is True
        and bool(job.get("baseline", {}).get("snapshot_sha256")) and bool(job.get("candidate", {}).get("snapshot_sha256"))
        and bool(job.get("diff", {}).get("sha256")) and job.get("identical_related_check") is True
        and len(check_ids) == 2 and len(checks) == 3
        and [item["phase"] for item in checks] == ["before", "after", "regression"]
        and [item["status"] for item in checks] == ["FAILED", "PASSED", "PASSED"]
        and [item["exit_code"] for item in checks] == [1, 0, 0]
        and [item["command_id"] for item in checks] == [check_ids[0], check_ids[0], check_ids[1]]
        and all(item.get("candidate_integrity") == "UNCHANGED" for item in checks)
        and checks[0].get("failure_marker_observed") is True
        and all(item.get("success_marker_observed") is True for item in checks[1:])
        and checks[0]["argv"] == checks[1]["argv"] and checks[0]["input"] == checks[1]["input"]
        and checks[0]["execution_settings_sha256"] == checks[1]["execution_settings_sha256"])
    if job.get("candidate_fix_verified") is not verified or run["change"].get("candidate_fix_verified") is not verified or (job["status"] == "CHANGE_PREPARED") != verified:
        raise ValueError("Candidate verification requires recorded reproduction and regression")
    return _hash(_json({"job": job, "run": run}))


class IncidentStore:
    """Local run/card records and minimal A2 jobs, atomic writes and bounded waits."""

    def __new__(cls, path=None):
        if getattr(path, "postgres", False):
            return path.incidents()
        return super().__new__(cls)

    def __init__(self, path: str | Path | None = None):
        self.path = db_location(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(str(self.path), timeout=0.25)
        self.connection.row_factory = sqlite3.Row
        try:
            self.connection.execute("PRAGMA foreign_keys=ON")
            version = self.connection.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2):
                raise ValueError("Unsupported incident database version")
            self.connection.executescript(SCHEMA + VECTOR_SCHEMA)
            self.connection.execute("PRAGMA user_version=2")
        except Exception:
            self.connection.close()
            raise

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.connection.close()

    def save_run(self, result: dict) -> str:
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            return self._insert_run(result)

    def _insert_run(self, result: dict) -> str:
        """Shared insertion inside the caller's transaction, including A2 job writes."""
        record = minimal_record(result)
        # Hash discarded input too: different private input must not silently share one run ID.
        # CLI presentation/export metadata is attached after the run is saved.
        # It carries no incident facts and must not make a save-only retry conflict.
        transport_fields = {"persistence", "presentation", "manual_export"}
        digest = _hash(_json({key: value for key, value in result.items() if key not in transport_fields}))
        legacy_digest = _hash(_json({key: value for key, value in result.items() if key != "persistence"}))
        project, incident, run = (record[key] for key in ("project_id", "incident_id", "run_id"))
        when = record.get("executed_at") or datetime.now(timezone.utc).isoformat()
        card = _new_card({**record, "executed_at": when})
        old = self.connection.execute("SELECT content_hash, record_json FROM runs WHERE run_id=?", (run,)).fetchone()
        if old:
            supplied_record = {key: value for key, value in result.items() if key != "persistence"}
            exact_stored_replay = supplied_record == json.loads(old["record_json"])
            if old["content_hash"] not in {digest, legacy_digest} and not exact_stored_replay:
                raise RunConflict("Run ID already has different contents")
            return "ALREADY_SAVED"
        self.connection.execute("""INSERT INTO incidents VALUES (?,?,?,?,?,?)
            ON CONFLICT(project_id,incident_id) DO UPDATE SET
            updated_at=excluded.updated_at, latest_run_id=excluded.latest_run_id, latest_revision=excluded.latest_revision
            WHERE excluded.latest_revision > incidents.latest_revision""", (project, incident, when, when, run, record["revision"]))
        try:
            self.connection.execute("INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?)", (run, project, incident, record["revision"], when, record["route"], record["run_status"], digest, _json(record)))
        except sqlite3.IntegrityError as exc:
            raise RunConflict("Incident revision already has a different run") from exc
        self.connection.execute("INSERT INTO cards VALUES (?,?,?,?,?,?,?)", (run, project, incident, run, "PENDING", _json(record["signals"]), _json(card)))
        return "SAVED"

    def save_change_result(self, result: dict) -> str:
        job, run = result["job"], result["run"]
        for key in ("work_id", "project_id", "incident_id", "source_run_id", "result_run_id"):
            _id(job[key], key)
        if (job.get("record_format") != "change_job_v1" or len(_json(job)) > 100_000
                or job["status"] not in {"CHANGE_PREPARED", "POLICY_REJECTED", "NOT_REPRODUCED", "MODEL_NOT_REQUESTED", "MODEL_FAILED", "VERIFICATION_FAILED", "TIMED_OUT"}
                or job["review_status"] != "WAITING_REVIEW" or job["original_applied"] is not False
                or job["deployment_status"] != "NOT_ATTEMPTED" or job["service_recovery"] != "NOT_VERIFIED"
                or any(run.get(key) is not False for key in ("cause_confirmed", "fix_applied", "fix_verified"))
                or (run["project_id"], run["incident_id"], run["run_id"]) != (job["project_id"], job["incident_id"], job["result_run_id"])
                or run.get("change", {}).get("work_id") != job["work_id"]
                or run["change"].get("candidate_fix_verified") != job["candidate_fix_verified"]):
            raise ValueError("Invalid A2 change record or completion claim")
        checks = job["checks"]
        verified = (job["status"] == "CHANGE_PREPARED" and job.get("original_unchanged") is True and bool(job["diff"].get("sha256"))
            and len(checks) == 3 and [item["phase"] for item in checks] == ["before", "after", "regression"]
            and [item["status"] for item in checks] == ["FAILED", "PASSED", "PASSED"]
            and [item["exit_code"] for item in checks] == [1, 0, 0] and job.get("identical_related_check") is True
            and [item["command_id"] for item in checks] == ["signup-contract", "signup-contract", "signup-regression"]
            and checks[0]["argv"] == checks[1]["argv"] and checks[0]["input"] == checks[1]["input"]
            and checks[0]["execution_settings_sha256"] == checks[1]["execution_settings_sha256"]
            and checks[0]["result"].get("failure_signature") == "seed-user_id-vs-userId")
        if job["candidate_fix_verified"] is not verified or (job["status"] == "CHANGE_PREPARED") != verified:
            raise ValueError("Candidate verification requires before/after/regression evidence")
        # No source bodies or model proposals in SQLite; only allowlisted job metadata.
        record = _selected(job, ("record_format", "worker_protocol_version", "worker_code_sha256", "work_id", "project_id", "incident_id", "source_run_id", "result_run_id", "source_revision",
            "source_record_sha256", "case_kind", "status", "review_status", "candidate_fix_verified", "original_applied", "deployment_status", "service_recovery",
            "started_at", "finished_at", "elapsed_ms", "policy", "artifact_ref", "baseline", "candidate", "diff", "checks", "attempts", "limitations", "model",
            "execution", "original_unchanged", "identical_related_check", "error_type"))
        digest = _hash(_json({"job": job, "run": run}))
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            old = self.connection.execute("SELECT content_hash FROM change_jobs WHERE work_id=?", (job["work_id"],)).fetchone()
            if old:
                if old["content_hash"] != digest:
                    raise RunConflict("Change job already has different contents")
                return "ALREADY_SAVED"
            source = self.get_run(job["project_id"], job["source_run_id"])
            incident = self.get_incident(job["project_id"], job["incident_id"])
            if (source["incident_id"] != job["incident_id"] or _hash(_json(source)) != job["source_record_sha256"]
                    or incident["latest_run_id"] != job["source_run_id"] or run["revision"] != incident["latest_revision"] + 1):
                raise RunConflict("Source run changed; preserve the obtained result for review")
            self._insert_run(run)
            try:
                self.connection.execute("INSERT INTO change_jobs VALUES (?,?,?,?,?,?,?)", (job["work_id"], job["project_id"], job["incident_id"],
                    job["source_run_id"], job["result_run_id"], digest, _json(record)))
            except sqlite3.IntegrityError as exc:
                raise RunConflict("Source run already has a change job") from exc
        return "SAVED"

    def save_project_change(self, job: dict, run: dict) -> str:
        """A separate generic candidate contract; the seed validator remains strict."""
        digest = validate_project_change(job, run)
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            old = self.connection.execute("SELECT content_hash FROM change_jobs WHERE work_id=?", (job["work_id"],)).fetchone()
            if old:
                if old["content_hash"] != digest:
                    raise RunConflict("Change job already has different contents")
                return "ALREADY_SAVED"
            source = self.get_run(job["project_id"], job["source_run_id"])
            incident = self.get_incident(job["project_id"], job["incident_id"])
            if (source["incident_id"] != job["incident_id"] or _hash(_json(source)) != job["source_record_sha256"]
                    or incident["latest_run_id"] != source["run_id"] or run["revision"] != incident["latest_revision"] + 1):
                raise RunConflict("Source run changed; preserve the obtained result for review")
            self._insert_run(run)
            record = deepcopy(job)
            self.connection.execute("INSERT INTO change_jobs VALUES (?,?,?,?,?,?,?)", (job["work_id"], job["project_id"], job["incident_id"],
                job["source_run_id"], job["result_run_id"], digest, _json(record)))
        return "SAVED"

    def find_change(self, project_id: str, source_run_id: str) -> dict | None:
        row = self.connection.execute("SELECT record_json FROM change_jobs WHERE project_id=? AND source_run_id=?",
            (_id(project_id, "project_id"), _id(source_run_id, "source_run_id"))).fetchone()
        return json.loads(row["record_json"]) if row else None

    def get_change(self, project_id: str, work_id: str) -> dict:
        row = self.connection.execute("SELECT record_json FROM change_jobs WHERE project_id=? AND work_id=?",
            (_id(project_id, "project_id"), _id(work_id, "work_id"))).fetchone()
        if not row:
            raise ValueError("Change job not found in this project")
        return json.loads(row["record_json"])

    def list_changes(self, project_id: str, incident_id: str | None = None) -> list[dict]:
        scope, args = "project_id=?", [_id(project_id, "project_id")]
        if incident_id is not None:
            scope += " AND incident_id=?"
            args.append(_id(incident_id, "incident_id"))
        rows = self.connection.execute(f"SELECT record_json FROM change_jobs WHERE {scope} ORDER BY rowid DESC LIMIT 50", args)
        return [json.loads(row["record_json"]) for row in rows]

    def get_run(self, project_id: str, run_id: str) -> dict:
        row = self.connection.execute("SELECT record_json FROM runs WHERE project_id=? AND run_id=?", (_id(project_id, "project_id"), _id(run_id, "run_id"))).fetchone()
        if not row:
            raise ValueError("Run not found in this project")
        return json.loads(row["record_json"])

    def get_evidence(self, project_id: str, run_id: str, evidence_id: str) -> dict:
        for item in self.get_run(project_id, run_id)["evidence"]:
            if item["id"] == evidence_id:
                return item
        raise ValueError("Evidence not found in this run")

    def list_incidents(self, project_id: str) -> list[dict]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM incidents WHERE project_id=? ORDER BY updated_at DESC LIMIT 50", (_id(project_id, "project_id"),))]

    def get_incident(self, project_id: str, incident_id: str) -> dict:
        row = self.connection.execute("SELECT * FROM incidents WHERE project_id=? AND incident_id=?", (_id(project_id, "project_id"), _id(incident_id, "incident_id"))).fetchone()
        if not row:
            raise ValueError("Incident not found in this project")
        runs = [dict(item) for item in self.connection.execute("SELECT run_id,revision,executed_at,route,run_status FROM runs WHERE project_id=? AND incident_id=? ORDER BY revision", (project_id, incident_id))]
        changes = [{key: item[key] for key in ("work_id", "source_run_id", "result_run_id", "status", "review_status", "candidate_fix_verified")}
                   for item in self.list_changes(project_id, incident_id)]
        return {**dict(row), "runs": runs, "changes": changes}

    def resume_result(self, project_id: str, incident_id: str) -> dict:
        incident = self.get_incident(project_id, incident_id)
        result = self.get_run(project_id, incident["latest_run_id"])
        if not result.get("session"):
            raise ValueError("This run has no resumable intake session")
        result["history"] = [self.get_run(project_id, item["run_id"]) for item in incident["runs"][-7:-1]]
        return result

    def get_card(self, project_id: str, card_id: str) -> dict:
        row = self.connection.execute("""SELECT c.card_json,r.record_json,r.executed_at FROM cards c
            JOIN runs r ON r.run_id=c.run_id WHERE c.project_id=? AND c.card_id=?""",
            (_id(project_id, "project_id"), _id(card_id, "card_id"))).fetchone()
        if not row:
            raise ValueError("Card not found in this project")
        card = json.loads(row["card_json"])
        if card["project_id"] != project_id:
            raise ValueError("Card project scope mismatch")
        record = json.loads(row["record_json"])
        record.setdefault("executed_at", row["executed_at"])
        row_job = self.connection.execute("SELECT record_json FROM change_jobs WHERE project_id=? AND result_run_id=?", (project_id, card["source_run_id"])).fetchone()
        job = json.loads(row_job["record_json"]) if row_job else None
        investigation = self.get_run(project_id, job["source_run_id"]) if job else None
        return _enrich_card(card, record, job, investigation)

    def review_card(self, project_id: str, card_id: str, action: str, *, reviewer: str, changes: dict | None = None, note: str = "") -> dict:
        reviewer = _text(_id(reviewer, "reviewer"), 80)
        changes = {} if changes is None else changes
        if not isinstance(changes, dict) or action not in {"approve", "edit", "reject"} or set(changes) - EDITABLE or action != "edit" and changes or action == "edit" and not changes:
            raise ValueError("Review edits may change wording and applicability only; factual verification is immutable")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            card = self.get_card(project_id, card_id)
            status = {"approve": "APPROVED", "edit": "EDITED", "reject": "REJECTED"}[action]
            revision = card["review"]["revision"] + 1
            checked = _review_changes(card, changes, revision)
            event = {"action": action, "reviewer": reviewer, "at": datetime.now(timezone.utc).isoformat(), "note": _text(note, 300),
                "before_status": card["review"]["status"], "before": {key: deepcopy(card[key]) for key in changes}, "changes": checked}
            card.update(event["changes"])
            for key in changes:
                card["field_sources"][key] = {"kind": "REVIEW_EDIT", "run_id": card["source_run_id"], "review_revision": revision, "reviewer": reviewer, "at": event["at"]}
            card["review"] = {"status": status, "revision": revision, "reviewer": reviewer, "at": event["at"], "note": event["note"], "history": [*card["review"]["history"], event]}
            self.connection.execute("UPDATE cards SET review_status=?,card_json=? WHERE project_id=? AND card_id=?", (status, _json(card), project_id, card_id))
            self.connection.execute("DELETE FROM card_search WHERE card_id=?", (card_id,))
            # An edited/rejected/reapproved card must never keep an old vector.
            self.connection.execute("DELETE FROM card_embeddings WHERE project_id=? AND card_id=?", (project_id, card_id))
            if status in REUSABLE:
                # Only reviewable summaries and observed fingerprints are indexed.
                text = " ".join([card["symptom"], card["finding"], card["next_action"], *[step["step"] for step in card["check_sequence"]], *[term for values in card["signals"].values() for term in values]])
                self.connection.execute("INSERT INTO card_search(card_id,search_text) VALUES (?,?)", (card_id, text))
        return card

    def export_manual(self, project_id: str, *, card_ids: list[str] | None = None) -> dict:
        """Render a consistent snapshot of reviewed cards; no commands or new findings."""
        project_id = _id(project_id, "project_id")
        if card_ids is not None and (not isinstance(card_ids, list) or len(card_ids) > 1000):
            raise ValueError("Manual card selection needs a list of at most 1000 IDs")

        def snapshot():
            ids = list(dict.fromkeys(_id(value, "card_id") for value in card_ids)) if card_ids is not None else [row["card_id"]
                for row in self.connection.execute("SELECT card_id FROM cards WHERE project_id=? AND review_status IN ('APPROVED','EDITED') ORDER BY rowid", (project_id,))]
            selected = [self.get_card(project_id, value) for value in ids]
            selected = [card for card in selected if card["review"]["status"] in REUSABLE]
            return {"status": "OK", "project_id": project_id, "card_count": len(selected),
                "card_ids": [card["card_id"] for card in selected], "markdown": _render_manual(project_id, selected)}

        if self.connection.in_transaction:
            return snapshot()
        with self.connection:
            self.connection.execute("BEGIN")
            return snapshot()

    def search(self, project_id: str, query: str = "", *, signals: dict | None = None,
               exclude_incident_id: str | None = None, scope_filters: dict | None = None, embedding_client=None) -> dict:
        started = time.perf_counter()
        project_id = _id(project_id, "project_id")
        if not isinstance(query, str) or len(query) > 12000:
            raise ValueError("Search query must be at most 12000 characters")
        supplied = signals or signals_from_text(query)
        if not isinstance(supplied, dict) or set(supplied) - set(SIGNAL_KEYS):
            raise ValueError("Unknown exact search field")
        if any(not isinstance(values, list) or any(not isinstance(value, str) or not 1 <= len(value) <= 200 for value in values) for values in supplied.values()):
            raise ValueError("Invalid exact search value")
        exact = {key: list(dict.fromkeys(supplied.get(key, [])))[:8] for key in SIGNAL_KEYS}
        exact["paths"] = [_path(value) for value in exact["paths"]]
        scope = "c.project_id=? AND c.review_status IN ('APPROVED','EDITED')"
        scope_args = [project_id]
        scope_filters = {} if scope_filters is None else scope_filters
        if (not isinstance(scope_filters, dict) or set(scope_filters) - {"service", "environment"}
                or any(not isinstance(value, str) or not 1 <= len(value) <= 80 for value in scope_filters.values())):
            raise ValueError("Known service/environment retrieval filters required")
        for key, value in scope_filters.items():
            scope += f" AND json_extract(c.card_json,'$.scope.{key}')=?"
            scope_args.append(value)
        if exclude_incident_id is not None:
            scope += " AND c.incident_id<>?"
            scope_args.append(_id(exclude_incident_id, "incident_id"))
        expressions, args = [], []
        for key, weight in (("error_codes", 4), ("paths", 1), ("exceptions", 4), ("stack_fingerprints", 6)):
            if exact[key]:
                marks = ",".join("?" for _ in exact[key])
                expressions.append(f"{weight} * EXISTS(SELECT 1 FROM json_each(c.signals_json, '$.{key}') WHERE value IN ({marks}))")
                args.extend(exact[key])
        rows, strategy = [], "NONE"
        candidate_limit = 20 if embedding_client is not None else 2
        if expressions:
            # Field names and weights are program constants; every value is bound.
            rows = self.connection.execute(f"SELECT c.card_id, json_extract(c.card_json,'$.review.revision') AS review_revision, ({' + '.join(expressions)}) AS score FROM cards c WHERE {scope} AND score>0 ORDER BY score DESC, json_extract(c.card_json,'$.card_kind')='GUIDANCE' DESC, c.rowid DESC LIMIT ?", [*args, *scope_args, candidate_limit]).fetchall()
            if rows:
                strategy = "EXACT"
        terms = list(dict.fromkeys(re.findall(r"[^\W_]+(?:_[^\W_]+)*", redact(query), re.UNICODE)))[:12]
        terms = [term for term in terms if 2 <= len(term) <= 80]
        if not rows and terms:
            # Quoted lexical tokens never act as FTS operators, columns or syntax.
            # Prefix matching tolerates Korean particles (가입 -> 가입이/가입을).
            match = " OR ".join('"' + term + '"' + ('*' if re.fullmatch(r"[가-힣]+", term) else '') for term in terms)
            rows = self.connection.execute(f"SELECT c.card_id, json_extract(c.card_json,'$.review.revision') AS review_revision FROM card_search JOIN cards c ON c.card_id=card_search.card_id WHERE {scope} AND card_search MATCH ? ORDER BY bm25(card_search), c.rowid DESC LIMIT ?", [*scope_args, match, candidate_limit]).fetchall()
            strategy = "FTS5"
        card_ids = [row["card_id"] for row in rows][:2]
        revisions = {row["card_id"]: row["review_revision"] for row in rows}
        semantic = None
        if embedding_client is not None:
            if strategy == "EXACT" and len(card_ids) == 2:
                semantic = {"status": "SKIPPED_EXACT_MATCH", "embedding_calls": 0}
            else:
                try:
                    semantic = vector_candidates(self, project_id, query, embedding_client,
                        scope_filters=scope_filters, exclude_incident_id=exclude_incident_id)
                    vector_ids = [item["card_id"] for item in semantic["candidates"]]
                    for item in semantic["candidates"]:
                        revisions.setdefault(item["card_id"], item["review_revision"])
                    if vector_ids:
                        card_ids = fuse_candidates([row["card_id"] for row in rows], vector_ids, exact=strategy == "EXACT")
                        strategy = "HYBRID" if rows else "VECTOR"
                except Exception as exc:
                    # An unavailable optional retriever cannot erase lexical results.
                    semantic = {"status": "FAILED", "error_type": type(exc).__name__}
        cards = [self.get_card(project_id, card_id) for card_id in card_ids]
        cards = [card for card in cards if current_review_matches(card, revisions[card["card_id"]], scope_filters, exclude_incident_id)]
        for card in cards:
            card["review"].pop("history", None)
            # Bare historical IDs must not collide with a new run's L1/C1/R1.
            for hypothesis in card["hypotheses"]:
                for prefix in ("supporting", "contradicting"):
                    hypothesis.pop(f"{prefix}_evidence_ids", None)
                    hypothesis[f"{prefix}_evidence_refs"] = [f"historical:{ref['run_id']}:{ref['evidence_id']}" for ref in hypothesis[f"{prefix}_evidence_refs"]]
        output = {"status": "OK", "strategy": strategy, "hit_count": len(cards), "elapsed_ms": round((time.perf_counter() - started) * 1000, 3), "query_signals": exact, "query_hash": _hash(query), "cards": cards,
                  "retrieval_scope": dict(scope_filters)}
        if semantic is not None:
            output["semantic"] = {key: value for key, value in semantic.items() if key != "candidates"}
        return output


def _empty_search(status: str, query: str, started: float) -> dict:
    return {"status": status, "strategy": "NONE", "hit_count": 0, "cards": [],
        "query_signals": {key: [] for key in SIGNAL_KEYS}, "query_hash": _hash(query) if isinstance(query, str) else None,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}


def search_memory(project_id: str, query: str, *, signals: dict | None = None, exclude_incident_id: str | None = None,
                  db_path: str | Path | None = None, enabled: bool = True, scope_filters: dict | None = None,
                  embedding_client=None, semantic_enabled: bool = False) -> dict:
    started = time.perf_counter()
    try:
        if type(enabled) is not bool:
            raise ValueError("Memory enabled option must be a bool")
        if not enabled:
            return _empty_search("DISABLED", query, started)
        configuration_error = None
        if embedding_client is None and semantic_enabled:
            try:
                embedding_client = embedding_client_from_env()
            except (KeyError, ValueError) as exc:
                configuration_error = type(exc).__name__
        with IncidentStore(db_path) as store:
            result = store.search(project_id, query, signals=signals, exclude_incident_id=exclude_incident_id,
                scope_filters=scope_filters, embedding_client=embedding_client)
        if configuration_error:
            result["semantic"] = {"status": "FAILED", "error_type": configuration_error}
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return result
    except (OSError, sqlite3.Error, SQLAlchemyError, ValueError) as exc:
        return {**_empty_search("FAILED", query, started), "error_type": type(exc).__name__}


def export_manual(project_id: str, *, db_path: str | Path | None = None, card_ids: list[str] | None = None) -> dict:
    """Return Markdown and reviewed card identities; the caller controls file output."""
    path = db_location(db_path)
    if not path.is_file():
        raise FileNotFoundError("Manual export requires an existing incident DB")
    with IncidentStore(path) as store:
        return store.export_manual(project_id, card_ids=card_ids)


def _applicability_recheck(card: dict, result: dict) -> tuple[str, list[str]]:
    expected = card.get("applicability") or _applicability({"project_id": card["project_id"], "run_id": card["source_run_id"],
        "scope": card.get("scope", {}), "version_provenance": card.get("version_provenance", {})})
    actual = _applicability(result)
    mismatch, missing = [], []
    if expected.get("project_id") != actual["project_id"]:
        mismatch.append("현재 프로젝트가 과거 카드의 적용 프로젝트와 다릅니다")
    for key in ("service", "environment", "operation", "method", "path"):
        before, now = expected.get(key), actual.get(key)
        if key == "path":
            before, now = _path(before) if before else None, _path(now) if now else None
        if before and now and before != now:
            mismatch.append(f"현재 {key} 조건이 과거 카드의 적용 조건과 다릅니다")
        elif not before or not now:
            missing.append(f"과거 또는 현재 {key} 적용 조건을 확인하지 못했습니다")
    if not expected.get("path") and not expected.get("operation") or not actual.get("path") and not actual.get("operation"):
        missing.append("과거 또는 현재 동작·경로 적용 조건을 확인하지 못했습니다")
    past_versions, current_versions = card.get("version_provenance", {}), result.get("version_provenance", {})
    past_runtime, current_runtime = past_versions.get("runtime", {}), current_versions.get("runtime", {})
    before_sha = _sha(past_runtime.get("sha")) if past_runtime.get("status") == "OBSERVED" else None
    now_sha = _sha(current_runtime.get("sha")) if current_runtime.get("status") == "OBSERVED" else None
    if not before_sha or not now_sha:
        missing.append("과거 또는 현재 실제 실행 버전이 관측되지 않았습니다")
    elif not _same_sha(before_sha, now_sha):
        mismatch.append("현재 실행 버전이 과거 카드 출처의 실행 버전과 다릅니다")
    if past_versions.get("comparison") == "MISMATCH" or current_versions.get("comparison") == "MISMATCH":
        mismatch.append("출처 또는 현재 실행의 로컬 코드와 실제 실행 버전이 다릅니다")
    for key in ("runtime_sha", "local_sha"):
        before, now = expected.get(key), actual.get(key)
        if before and now and not _same_sha(before, now):
            mismatch.append(f"현재 {key}가 검토된 버전 조건과 다릅니다")
        elif before and not now:
            missing.append(f"현재 {key} 조건을 관측하지 못했습니다")
    before_contract = expected.get("contract_versions", card.get("contract_analysis", {}).get("versions", {}))
    now_contract = actual["contract_versions"]
    if before_contract or now_contract:
        if before_contract.get("status") == "MISMATCH" or now_contract.get("status") == "MISMATCH":
            mismatch.append("출처 또는 현재 계약·DTO·호출자 버전 관측이 충돌합니다")
        for component in ("runtime", "code", "contract", "dto", "caller"):
            before, now = before_contract.get(component), now_contract.get(component)
            if not before or not now:
                missing.append(f"과거 또는 현재 {component} 계약 근거 버전을 확인하지 못했습니다")
            elif not (_same_sha(_sha(before), _sha(now)) if _sha(before) and _sha(now) else before == now):
                mismatch.append(f"현재 {component} 계약 근거 버전이 과거 출처와 다릅니다")
    if any(item.get("origin") == "REVIEW_EDIT" for item in card.get("disproof_conditions", [])):
        missing.append("검토 정정된 반증 문구는 현재 자료로 별도 확인해야 합니다")
    for item in card.get("invalid_conditions", []):
        states = []
        for key, value in item["when"].items():
            now = actual.get(key)
            states.append(None if now is None else _same_sha(value, now) if key.endswith("_sha") else value == now)
        if False in states:
            continue
        if None in states:
            missing.append("검토된 부적합 조건의 현재 값을 확인하지 못했습니다")
        else:
            mismatch.append("검토된 부적합 조건에 해당합니다: " + item["reason"])
    if card.get("observed_features", {}).get("observations_complete") is not True:
        missing.append("과거 출처의 관측 범위 완전성을 확인하지 못했습니다")
    if card.get("observed_features", {}).get("conflicts"):
        mismatch.append("과거 출처에 관측 충돌이 보존돼 있습니다")
    return ("MISMATCH" if mismatch else "NOT_OBSERVED" if missing else "MATCH", [*mismatch, *missing])


def recheck_memory(search: dict, result: dict) -> dict:
    """Record checks against this run, without changing its rules or hypotheses."""
    search = deepcopy(search)
    if search.get("status") == "DISABLED":
        search.update(strategy="NONE", hit_count=0, cards=[], current_recheck={}, rechecks=[])
        return search
    observed = current_signals(result)
    aggregate = result.get("log_scope", {}).get("aggregate", {})
    refs = [{"run_id": result["run_id"], "evidence_id": item["id"]} for item in result.get("observations", []) if item.get("run_id", result["run_id"]) == result["run_id"]]
    search["current_recheck"] = {
        "run_id": result["run_id"], "request_connection": {key: result.get(key) for key in ("trace_id", "correlation", "correlation_basis")},
        "logs": {"connected": result.get("log_scope", {}).get("connected", False), "aggregate": aggregate, "reads": [step for step in result.get("steps", []) if step["tool"] == "find_logs"], "verified_count": sum(item.get("kind") == "log" and item.get("scope_status") == "VERIFIED" and item.get("run_id", result["run_id"]) == result["run_id"] for item in result.get("observations", []))},
        "version_provenance": result.get("version_provenance", {}), "evidence_refs": refs,
    }
    search["rechecks"] = []
    for card in search.get("cards", [])[:2]:
        status, reasons = "NOT_REVALIDATED", []
        guidance_observed = False
        if card.get("review", {}).get("status") not in REUSABLE:
            status = "REJECTED"
            reasons.append("카드가 미검토 또는 폐기 상태입니다")
        elif result.get("correlation") != "EXACT_ID" or result["route"] == "REQUEST_CONTEXT" or aggregate.get("conflicts") or aggregate.get("complete") is not True:
            reasons.append("현재 요청 연결·관측 범위가 부족하거나 충돌합니다")
        elif result.get("observed_status") is not None and card.get("observed_status") is not None and result["observed_status"] != card["observed_status"]:
            status = "REJECTED"
            reasons.append("현재 응답 상태가 과거 사례와 다릅니다")
        elif any(observed[key] and card["signals"][key] and not set(observed[key]) & set(card["signals"][key]) for key in ("error_codes", "exceptions", "stack_fingerprints")):
            status = "REJECTED"
            reasons.append("현재 오류 코드·예외·스택 지문이 과거 사례와 다릅니다")
        elif result.get("diagnosis_type") not in (None, "unknown") and card.get("diagnosis_type") not in (None, "unknown") and result["diagnosis_type"] != card["diagnosis_type"]:
            status = "REJECTED"
            reasons.append("현재 규칙 관측의 진단 유형이 과거 사례와 다릅니다")
        elif result["run_status"] != "COMPLETED":
            reasons.append("현재 조사가 중단되거나 일부 자료를 확인하지 못했습니다")
        elif card["card_kind"] == "GUIDANCE" and result["route"] == "GUIDANCE" and search["current_recheck"]["logs"]["verified_count"]:
            status = "CURRENT_GUIDANCE_OBSERVED"
            guidance_observed = True
            reasons.append("새 요청의 현재 로그·계약으로 안내를 독립 판정했습니다. 원인·수정 검증이 아닙니다")
        else:
            reasons.append("과거 가설은 현재 원인으로 검증되지 않았습니다")
        applicability, condition_reasons = _applicability_recheck(card, result)
        if applicability != "MATCH" and status != "REJECTED":
            status = "NOT_REVALIDATED"
        reasons.extend(condition_reasons)
        search["rechecks"].append({"card_id": card["card_id"], "source_run_id": card["source_run_id"], "status": status,
            "reasons": reasons, "applicability_status": applicability, "current_guidance_observed": guidance_observed,
            "usable_as_current_evidence": False, "current_evidence_refs": refs[:4]})
    return search


def persist_result(result: dict, db_path: str | Path | None = None) -> dict:
    """Persistence failure is separate from the already completed investigation."""
    try:
        with IncidentStore(db_path) as store:
            status = store.save_run(result)
        result["persistence"] = {"status": status, "run_id": result["run_id"]}
    except (OSError, sqlite3.Error, ValueError, TypeError) as exc:
        result["persistence"] = {"status": "FAILED", "run_id": result["run_id"], "error_type": type(exc).__name__, "retry": "save_this_result_without_reinvestigation"}
    return result
