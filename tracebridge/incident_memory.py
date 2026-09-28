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

from .project_sources import redact, REQUEST_ID
from .report_contract import RUN_STATUSES
from .triage import EXCEPTION_PATTERN


WORKSPACE = Path(__file__).resolve().parents[1]
REUSABLE = {"APPROVED", "EDITED"}
SIGNAL_KEYS = ("error_codes", "paths", "exceptions", "stack_fingerprints")
EDITABLE = {"symptom", "finding", "next_action"}
ROUTES = {"GUIDANCE", "WORK_CANDIDATE", "INVESTIGATE", "REQUEST_CONTEXT"}
SCHEMA = """
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
    output = _selected(search, ("status", "error_type", "strategy", "hit_count", "elapsed_ms", "query_signals", "query_hash"))
    output["cards"] = []
    for card in search.get("cards", [])[:2]:
        saved = _selected(card, ("card_id", "project_id", "incident_id", "source_run_id", "revision", "card_kind", "diagnosis_type", "observed_status", "scope", "signals", "source_run_status", "verification", "version_provenance", "evidence_refs"))
        for key in EDITABLE:
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
    output["rechecks"] = [_selected(item, ("card_id", "source_run_id", "status", "reasons", "current_evidence_refs")) for item in search.get("rechecks", [])[:2]]
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
        "correlated", "scope_status", "scope_checks", "scope", "source_revision",
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
        "case_kind", "requested_action",
    ) if key in result}
    record = _metadata(record)
    record.update(revision=revision, record_format="minimal_sqlite_v1")
    for key in ("summary", "route_reason", "next_action"):
        record[key] = _text(result.get(key, ""))
    for key in ("scope", "candidates", "candidate_trace_ids", "version_provenance", "log_scope", "usage", "timeout_reasons", "hypothesis_updates", "relative_date_basis", "correlation_confirmation"):
        record[key] = _metadata(result.get(key, [] if key in {"candidates", "candidate_trace_ids", "timeout_reasons", "hypothesis_updates"} else {}))
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
    return record


def _new_card(record: dict) -> dict:
    aggregate = record.get("log_scope", {}).get("aggregate", {})
    guidance = record["route"] == "GUIDANCE" and record.get("correlation") == "EXACT_ID" and record["run_status"] == "COMPLETED" and not aggregate.get("conflicts") and aggregate.get("complete", True)
    return {
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
    }


class IncidentStore:
    """Local run/card records and minimal A2 jobs, atomic writes and bounded waits."""

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
            self.connection.executescript(SCHEMA)
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
        digest = _hash(_json({key: value for key, value in result.items() if key != "persistence"}))
        project, incident, run = (record[key] for key in ("project_id", "incident_id", "run_id"))
        when = record.get("executed_at") or datetime.now(timezone.utc).isoformat()
        card = _new_card(record)
        old = self.connection.execute("SELECT content_hash FROM runs WHERE run_id=?", (run,)).fetchone()
        if old:
            if old["content_hash"] != digest:
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
        row = self.connection.execute("SELECT card_json FROM cards WHERE project_id=? AND card_id=?", (_id(project_id, "project_id"), _id(card_id, "card_id"))).fetchone()
        if not row:
            raise ValueError("Card not found in this project")
        card = json.loads(row["card_json"])
        if card["project_id"] != project_id:
            raise ValueError("Card project scope mismatch")
        return card

    def review_card(self, project_id: str, card_id: str, action: str, *, reviewer: str, changes: dict | None = None, note: str = "") -> dict:
        reviewer = _text(_id(reviewer, "reviewer"), 80)
        changes = changes or {}
        if action not in {"approve", "edit", "reject"} or set(changes) - EDITABLE or action != "edit" and changes or action == "edit" and not changes:
            raise ValueError("Review edits may change symptom, finding or next_action only")
        if any(not isinstance(value, str) or not value.strip() or len(value) > 500 for value in changes.values()):
            raise ValueError("Card edits need 1..500 characters")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            card = self.get_card(project_id, card_id)
            status = {"approve": "APPROVED", "edit": "EDITED", "reject": "REJECTED"}[action]
            event = {"action": action, "reviewer": reviewer, "at": datetime.now(timezone.utc).isoformat(), "note": _text(note, 300), "before": {key: card[key] for key in changes}, "changes": {key: _text(value) for key, value in changes.items()}}
            card.update(event["changes"])
            card["review"] = {"status": status, "revision": card["review"]["revision"] + 1, "reviewer": reviewer, "at": event["at"], "note": event["note"], "history": [*card["review"]["history"], event]}
            self.connection.execute("UPDATE cards SET review_status=?,card_json=? WHERE project_id=? AND card_id=?", (status, _json(card), project_id, card_id))
            self.connection.execute("DELETE FROM card_search WHERE card_id=?", (card_id,))
            if status in REUSABLE:
                # Only reviewable summaries and observed fingerprints are indexed.
                text = " ".join([card["symptom"], card["finding"], card["next_action"], *[term for values in card["signals"].values() for term in values]])
                self.connection.execute("INSERT INTO card_search(card_id,search_text) VALUES (?,?)", (card_id, text))
        return card

    def search(self, project_id: str, query: str = "", *, signals: dict | None = None, exclude_incident_id: str | None = None) -> dict:
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
        if expressions:
            # Field names and weights are program constants; every value is bound.
            rows = self.connection.execute(f"SELECT c.card_id, ({' + '.join(expressions)}) AS score FROM cards c WHERE {scope} AND score>0 ORDER BY score DESC, json_extract(c.card_json,'$.card_kind')='GUIDANCE' DESC, c.rowid DESC LIMIT 2", [*args, *scope_args]).fetchall()
            if rows:
                strategy = "EXACT"
        terms = list(dict.fromkeys(re.findall(r"[^\W_]+(?:_[^\W_]+)*", redact(query), re.UNICODE)))[:12]
        terms = [term for term in terms if 2 <= len(term) <= 80]
        if not rows and terms:
            # Quoted lexical tokens never act as FTS operators, columns or syntax.
            match = " OR ".join('"' + term + '"' for term in terms)
            rows = self.connection.execute(f"SELECT c.card_id FROM card_search JOIN cards c ON c.card_id=card_search.card_id WHERE {scope} AND card_search MATCH ? ORDER BY bm25(card_search), c.rowid DESC LIMIT 2", [*scope_args, match]).fetchall()
            strategy = "FTS5"
        cards = [self.get_card(project_id, row["card_id"]) for row in rows]
        for card in cards:
            card["review"].pop("history", None)
            # Bare historical IDs must not collide with a new run's L1/C1/R1.
            for hypothesis in card["hypotheses"]:
                for prefix in ("supporting", "contradicting"):
                    hypothesis.pop(f"{prefix}_evidence_ids", None)
                    hypothesis[f"{prefix}_evidence_refs"] = [f"historical:{ref['run_id']}:{ref['evidence_id']}" for ref in hypothesis[f"{prefix}_evidence_refs"]]
        return {"status": "OK", "strategy": strategy, "hit_count": len(cards), "elapsed_ms": round((time.perf_counter() - started) * 1000, 3), "query_signals": exact, "query_hash": _hash(query), "cards": cards}


def search_memory(project_id: str, query: str, *, signals: dict | None = None, exclude_incident_id: str | None = None, db_path: str | Path | None = None) -> dict:
    started = time.perf_counter()
    try:
        with IncidentStore(db_path) as store:
            result = store.search(project_id, query, signals=signals, exclude_incident_id=exclude_incident_id)
        result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return result
    except (OSError, sqlite3.Error, ValueError) as exc:
        return {"status": "FAILED", "error_type": type(exc).__name__, "hit_count": 0, "cards": [], "elapsed_ms": round((time.perf_counter() - started) * 1000, 3)}


def recheck_memory(search: dict, result: dict) -> dict:
    """Record checks against this run, without changing its rules or hypotheses."""
    search = deepcopy(search)
    observed = current_signals(result)
    aggregate = result.get("log_scope", {}).get("aggregate", {})
    refs = [{"run_id": result["run_id"], "evidence_id": item["id"]} for item in result.get("observations", [])]
    search["current_recheck"] = {
        "run_id": result["run_id"], "request_connection": {key: result.get(key) for key in ("trace_id", "correlation", "correlation_basis")},
        "logs": {"connected": result.get("log_scope", {}).get("connected", False), "aggregate": aggregate, "reads": [step for step in result.get("steps", []) if step["tool"] == "find_logs"], "verified_count": sum(item.get("kind") == "log" and item.get("scope_status") == "VERIFIED" for item in result.get("observations", []))},
        "version_provenance": result.get("version_provenance", {}), "evidence_refs": refs,
    }
    search["rechecks"] = []
    for card in search.get("cards", [])[:2]:
        status, reasons = "NOT_REVALIDATED", []
        if result.get("correlation") != "EXACT_ID" or result["route"] == "REQUEST_CONTEXT" or aggregate.get("conflicts") or not aggregate.get("complete", True):
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
        elif result["run_status"] in {"TIMED_OUT", "PARTIAL_FAILURE", "BUDGET_EXHAUSTED"}:
            reasons.append("현재 조사가 중단되거나 일부 자료를 확인하지 못했습니다")
        elif card["card_kind"] == "GUIDANCE" and result["route"] == "GUIDANCE" and search["current_recheck"]["logs"]["verified_count"]:
            status = "CURRENT_GUIDANCE_OBSERVED"
            reasons.append("새 요청의 현재 로그·계약으로 안내를 독립 판정했습니다. 원인·수정 검증이 아닙니다")
        else:
            reasons.append("과거 가설은 현재 원인으로 검증되지 않았습니다")
        if result.get("version_provenance", {}).get("runtime", {}).get("status") != "OBSERVED":
            reasons.append("현재 실행 버전은 관측되지 않았습니다")
        elif result.get("version_provenance", {}).get("comparison") == "MISMATCH":
            status = "NOT_REVALIDATED"
            reasons.append("현재 로컬 코드와 실행 버전이 다릅니다")
        search["rechecks"].append({"card_id": card["card_id"], "source_run_id": card["source_run_id"], "status": status, "reasons": reasons, "current_evidence_refs": refs[:4]})
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
