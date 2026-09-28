"""Small read-only local project registration; configuration grants no authority."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .contract_analysis import _type_name
from .evidence import EvidenceError


@dataclass(frozen=True)
class LocalLogSource:
    id: str
    path: Path
    format: str = "jsonl"
    root: Path | None = None
    timezone: str | None = None
    service: str | None = None


@dataclass(frozen=True)
class RegisteredRepository:
    id: str
    service: str
    root: Path
    code_roots: tuple[Path, ...]
    git_root: Path | None = None


@dataclass(frozen=True)
class VersionObservation:
    method: str = "none"
    path: Path | None = None
    field: str = "version"


@dataclass(frozen=True)
class RegisteredService:
    id: str
    log_source_ids: tuple[str, ...] = ()
    openapi_path: Path | None = None
    dto_path: Path | None = None
    caller_evidence_path: Path | None = None
    version_observation: VersionObservation = VersionObservation()


@dataclass(frozen=True)
class ProjectProfile:
    project_id: str
    service: str
    environment: str
    root: Path
    code_roots: tuple[Path, ...] = ()
    log_sources: tuple[LocalLogSource, ...] = ()
    openapi_path: Path | None = None
    dto_path: Path | None = None
    caller_evidence_path: Path | None = None
    version_observation: VersionObservation = VersionObservation()
    policy_refs: tuple[str, ...] = ()
    config_path: Path | None = None
    repositories: tuple[RegisteredRepository, ...] = ()
    services: tuple[RegisteredService, ...] = ()


def select_project_service(profile: ProjectProfile, service: str | None = None) -> ProjectProfile:
    target = service or profile.service
    if not profile.services:
        if target != profile.service:
            raise EvidenceError("Requested service is not registered for this project")
        return profile
    entry = next((item for item in profile.services if item.id == target), None)
    if entry is None:
        raise EvidenceError("Requested service is not registered for this project")
    return replace(profile, service=target,
        log_sources=tuple(item for item in profile.log_sources if item.id in entry.log_source_ids),
        openapi_path=entry.openapi_path, dto_path=entry.dto_path,
        caller_evidence_path=entry.caller_evidence_path, version_observation=entry.version_observation)


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise EvidenceError(f"{name} must be a nonempty string of at most 256 characters")
    return value.strip()


def _local_path(root: Path, value: Any) -> Path:
    value = _text(value, "Local path")
    if "://" in value or value.startswith(("\\\\", "//")) or "\x00" in value:
        raise EvidenceError("Only local registered paths are supported")
    path = Path(value)
    if ":" in value and not path.is_absolute():
        raise EvidenceError("Device paths and file streams are unsupported")
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise EvidenceError("Registered source path must remain within the project root")
    return resolved


def _root_path(base: Path, value: Any) -> Path:
    text = _text(value, "Repository root")
    if "://" in text or text.startswith(("\\\\", "//")) or "\x00" in text:
        raise EvidenceError("Repository root must be a local directory")
    path = Path(text)
    if ":" in text and not path.is_absolute():
        raise EvidenceError("Device paths and file streams are unsupported")
    path = (base / path).resolve()
    if not path.is_dir():
        raise EvidenceError("Registered repository directory does not exist on the executing computer")
    return path


def registered_path(profile: ProjectProfile, value: Any) -> Path:
    """Resolve an explicit artifact inside one declared root, never their parent."""
    if isinstance(value, dict):
        if set(value) != {"repository", "path"}:
            raise EvidenceError("Artifact reference accepts repository/path only")
        repository = next((item for item in profile.repositories if item.id == value["repository"]), None)
        if repository is None:
            raise EvidenceError("Unknown registered repository")
        return _local_path(repository.root, value["path"])
    text = _text(value, "Registered path")
    path = Path(text)
    if not path.is_absolute():
        return _local_path(profile.root, text)
    for root in (profile.root, *(item.root for item in profile.repositories)):
        if path.resolve().is_relative_to(root.resolve()):
            return _local_path(root, text)
    raise EvidenceError("Path is outside every registered repository")


def _read_json_file(path: Path, *, max_bytes: int = 1_000_000) -> tuple[Any, str]:
    try:
        with path.open("rb") as stream:
            raw = stream.read(max_bytes + 1)
    except OSError as exc:
        raise EvidenceError(f"Registered local source unavailable: {path.name}") from exc
    if len(raw) > max_bytes:
        raise EvidenceError("Registered local source exceeds its byte limit")
    try:
        return json.loads(raw.decode("utf-8-sig")), hashlib.sha256(raw).hexdigest()
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EvidenceError("Registered local source must be UTF-8 JSON") from exc


def load_project_profile(path: str | Path) -> ProjectProfile:
    """Load one JSON profile; URLs, commands and execution permissions are rejected."""
    config_path = Path(path).resolve()
    data, _ = _read_json_file(config_path, max_bytes=64_000)
    allowed = {
        "project_id", "service", "environment", "root", "code_roots", "log_sources",
        "openapi_path", "dto_path", "caller_evidence_path", "version_observation", "policy_refs", "repositories", "services",
    }
    if not isinstance(data, dict) or set(data) - allowed:
        raise EvidenceError("Project profile contains unsupported settings (including execution or secrets)")
    root_value = data.get("root", ".")
    root_text = _text(root_value, "Project root")
    if "://" in root_text or root_text.startswith(("\\\\", "//")):
        raise EvidenceError("Project root must be a local directory")
    root_path = Path(root_text)
    if ":" in root_text and not root_path.is_absolute():
        raise EvidenceError("Device paths and file streams are unsupported")
    root = _root_path(config_path.parent, root_text)
    repositories = data.get("repositories", [])
    if not isinstance(repositories, list) or len(repositories) > 16:
        raise EvidenceError("repositories must contain at most 16 registered roots")
    registered = []
    for item in repositories:
        if not isinstance(item, dict) or set(item) - {"id", "service", "root", "code_roots", "git_root"}:
            raise EvidenceError("Repository accepts id/service/root/code_roots only")
        id_ = _text(item.get("id"), "Repository id")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", id_):
            raise EvidenceError("Repository id accepts letters, numbers, underscores and hyphens")
        location = _root_path(config_path.parent, item.get("root"))
        directories = item.get("code_roots", ["."])
        if not isinstance(directories, list) or len(directories) > 16:
            raise EvidenceError("Repository code_roots must contain at most 16 paths")
        git_root = _root_path(config_path.parent, item["git_root"]) if item.get("git_root") else None
        if git_root and not location.is_relative_to(git_root):
            raise EvidenceError("Git metadata root must contain the registered source root")
        registered.append(RegisteredRepository(id_, _text(item.get("service"), "Repository service"), location,
                                              tuple(_local_path(location, entry) for entry in directories), git_root))
    if len({item.id for item in registered}) != len(registered):
        raise EvidenceError("Repository ids must be unique")
    if any(item.git_root and item.git_root not in {root, *(entry.root for entry in registered)} for item in registered):
        raise EvidenceError("Git metadata root must be one of the explicitly registered roots")
    resolver = ProjectProfile("registration", "registration", "registration", root, repositories=tuple(registered))
    code_roots = data.get("code_roots", [])
    log_sources = data.get("log_sources", [])
    policies = data.get("policy_refs", [])
    if not isinstance(code_roots, list) or len(code_roots) > 16:
        raise EvidenceError("code_roots must be a bounded list of local paths")
    if not isinstance(log_sources, list) or len(log_sources) > 8:
        raise EvidenceError("log_sources must be a bounded list")
    sources: list[LocalLogSource] = []
    for entry in log_sources:
        if not isinstance(entry, dict) or set(entry) - {"id", "path", "format", "repository", "timezone", "service"}:
            raise EvidenceError("Log source accepts only id/path/format/repository/timezone")
        format_ = entry.get("format", "jsonl")
        if format_ not in {"json", "jsonl", "text"}:
            raise EvidenceError("Only local JSON/JSONL/text logs are supported")
        ref = {"repository": entry["repository"], "path": entry.get("path")} if "repository" in entry else entry.get("path")
        location = registered_path(resolver, ref)
        source_root = next((item.root for item in registered if "repository" in entry and item.id == entry["repository"]), None)
        if source_root is None:
            source_root = next((candidate for candidate in (root, *(item.root for item in registered)) if location.is_relative_to(candidate)), root)
        timezone_ = entry.get("timezone")
        if timezone_ is not None:
            from .project_sources import _registered_timezone
            if not isinstance(timezone_, str) or _registered_timezone(timezone_) is None:
                raise EvidenceError("Log timezone must be UTC or a valid +/-HH:MM offset")
        service_ = _text(entry["service"], "Log source service") if entry.get("service") is not None else None
        sources.append(LocalLogSource(_text(entry.get("id"), "Log source id"), location, format_, source_root, timezone_, service_))
    if len({source.id for source in sources}) != len(sources):
        raise EvidenceError("Log source ids must be unique")
    if not isinstance(policies, list) or len(policies) > 16 or any(not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,128}", item) for item in policies):
        raise EvidenceError("policy_refs contains identifiers only, never permission settings")
    version = data.get("version_observation", {"method": "none"})
    if not isinstance(version, dict) or set(version) - {"method", "path", "field"}:
        raise EvidenceError("Version observation accepts only method/path/field")
    method = version.get("method", "none")
    if method not in {"none", "json_file", "log_field"}:
        raise EvidenceError("Version observation supports none/json_file/log_field; no commands")
    if method != "json_file" and "path" in version:
        raise EvidenceError("Only json_file version observation accepts a path")
    if str(version.get("field", "version")).casefold() in {"token", "password", "secret", "authorization", "api_key"}:
        raise EvidenceError("Version observation cannot use a credential field")
    optional = {key: registered_path(resolver, data[key]) if data.get(key) is not None else None
                for key in ("openapi_path", "dto_path", "caller_evidence_path")}
    service_entries = data.get("services", [])
    if not isinstance(service_entries, list) or len(service_entries) > 16:
        raise EvidenceError("services must contain at most 16 registered services")
    services = []
    for entry in service_entries:
        if not isinstance(entry, dict) or set(entry) - {"id", "log_source_ids", "openapi_path", "dto_path", "caller_evidence_path", "version_observation"}:
            raise EvidenceError("Service accepts id/log_source_ids/contract paths/version observation only")
        id_ = _text(entry.get("id"), "Service id")
        ids = entry.get("log_source_ids", [item.id for item in sources if item.service == id_])
        if not isinstance(ids, list) or any(not isinstance(value, str) or value not in {item.id for item in sources} for value in ids) or len(set(ids)) != len(ids):
            raise EvidenceError("Service log_source_ids must reference registered logs")
        if any(item.service and item.service != id_ for item in sources if item.id in ids):
            raise EvidenceError("Service log source belongs to another service")
        paths = {key: registered_path(resolver, entry[key]) if entry.get(key) is not None else None
                 for key in ("openapi_path", "dto_path", "caller_evidence_path")}
        observed = entry.get("version_observation", {"method": "none"})
        if not isinstance(observed, dict) or set(observed) - {"method", "path", "field"} or observed.get("method", "none") not in {"none", "json_file", "log_field"}:
            raise EvidenceError("Unsupported service version observation")
        observed_method = observed.get("method", "none")
        observed_field = _text(observed.get("field", "version"), "Version field")
        if observed_field.casefold() in {"token", "password", "secret", "authorization", "api_key"} or observed_method != "json_file" and "path" in observed:
            raise EvidenceError("Unsupported service version field/path")
        observed_path = registered_path(resolver, observed.get("path")) if observed_method == "json_file" else None
        services.append(RegisteredService(id_, tuple(ids), version_observation=VersionObservation(observed_method, observed_path, observed_field), **paths))
    if len({item.id for item in services}) != len(services) or services and data.get("service") not in {item.id for item in services}:
        raise EvidenceError("Service ids must be unique and include the primary service")
    return ProjectProfile(
        project_id=_text(data.get("project_id"), "project_id"), service=_text(data.get("service"), "service"),
        environment=_text(data.get("environment"), "environment"), root=root,
        code_roots=tuple(_local_path(root, entry) for entry in code_roots), log_sources=tuple(sources),
        version_observation=VersionObservation(method, registered_path(resolver, version.get("path")) if method == "json_file" else None,
                                               _text(version.get("field", "version"), "Version field")),
        policy_refs=tuple(policies), config_path=config_path, repositories=tuple(registered), services=tuple(services), **optional,
    )


def _mask_request(request: dict) -> tuple[dict, dict[str, str]]:
    types: dict[str, str] = {}

    def mask(value: Any, name: str, depth: int = 0) -> Any:
        observed = _type_name(value)
        if observed:
            types[name] = observed
        if depth > 16:
            return "[VALUE]"
        if isinstance(value, dict):
            return {key: mask(item, f"{name}.{key}" if name else key, depth + 1) for key, item in value.items()}
        if isinstance(value, list):
            return [mask(item, f"{name}[{index}]", depth + 1) for index, item in enumerate(value)]
        return "[VALUE]"

    return {key: mask(value, key) for key, value in request.items()}, types


def _scope_record(record: Any, profile: ProjectProfile, source: LocalLogSource) -> tuple[dict | None, str | None]:
    if not isinstance(record, dict):
        return None, "invalid_record"
    trace = deepcopy(record.get("trace", record))
    if not isinstance(trace, dict):
        return None, "invalid_trace"
    if any(trace.get(key, record.get(key, getattr(profile, key))) != getattr(profile, key) for key in ("project_id", "service", "environment")):
        return None, None  # An explicitly different scope is outside this source query.
    if not trace.get("service") or not trace.get("environment"):
        return None, "unobserved_scope"
    trace_id = trace.get("trace_id", trace.get("request_id", trace.get("requestId")))
    if not isinstance(trace_id, str) or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", trace_id):
        return None, "unobserved_trace_id"
    status = trace.get("response_status")
    if status is not None and (type(status) is not int or not 100 <= status <= 599):
        return None, "invalid_response_status"
    allowed = {
        "occurred_at", "service", "environment", "method", "path", "operation", "version",
        "code_version", "request", "request_fields", "request_types", "redacted_fields", "response_status",
        "input_fields", "input_types",
    }
    custom_version = trace.get(profile.version_observation.field)
    trace = {key: value for key, value in trace.items() if key in allowed}
    if profile.version_observation.method == "log_field" and isinstance(custom_version, str):
        if trace.get("version") and trace["version"] != custom_version:
            trace["version_conflict"] = True
        else:
            trace["version"] = custom_version
    trace.update(trace_id=trace_id, project_id=profile.project_id)
    if isinstance(trace.get("request"), dict):
        original_types = trace.get("request_types", {})
        trace["request"], types = _mask_request(trace["request"])
        trace["request_types"] = {**(original_types if isinstance(original_types, dict) else {}), **types}
    event = {"trace": trace, "observation_source": source.id}
    # No instructions, credentials or policy fields are promoted from a log.
    for key in ("contract", "dto", "migration", "caller"):
        if isinstance(record.get(key), dict):
            event[key] = deepcopy(record[key])
    logs = record.get("logs", [])
    if not isinstance(logs, list) or any(not isinstance(line, str) for line in logs):
        return None, "invalid_backend_logs"
    # Reuse the existing redactor at the boundary, never its project-name rules.
    from .project_sources import redact

    event["logs"] = [redact(line) for line in logs]
    return event, None


def read_project_logs(profile: ProjectProfile, *, max_bytes: int = 1_000_000, max_records: int = 1000) -> dict:
    """Collect registered JSON/JSONL records with explicit bounded-read completeness."""
    if type(max_bytes) is not int or max_bytes < 1 or type(max_records) is not int or max_records < 1:
        raise EvidenceError("Log byte/record limits must be positive integers")
    result: dict[str, Any] = {"project_id": profile.project_id, "events": [], "complete": bool(profile.log_sources),
                              "sources": [], "limitations": [], "execution_authorized": False}
    remaining, record_count = max_bytes, 0
    for source in profile.log_sources:
        report: dict[str, Any] = {"id": source.id, "path": str(source.path), "complete": True, "records": 0, "limitations": []}
        result["sources"].append(report)
        try:
            # Resolve again: a symlink or a replaced path must not escape registration.
            path = _local_path(source.root or profile.root, str(source.path))
            with path.open("rb") as stream:
                raw = stream.read(remaining + 1)
            if len(raw) > remaining:
                report["limitations"].append("byte_limit")
                raw = raw[:remaining]
            remaining -= len(raw)
            report.update(bytes_read=len(raw), sha256=hashlib.sha256(raw).hexdigest())
            if source.format == "json":
                document = json.loads(raw.decode("utf-8-sig"))
                if isinstance(document, dict) and "project_id" in document and document["project_id"] != profile.project_id:
                    raise EvidenceError("JSON log catalog project scope disagrees with registration")
                records = document.get("events", [document]) if isinstance(document, dict) else document
                if not isinstance(records, list):
                    raise EvidenceError("JSON log must be an event, event array or events catalog")
            elif source.format == "jsonl":
                records = []
                for line in raw.decode("utf-8-sig").splitlines():
                    if line.strip():
                        try:
                            records.append(json.loads(line))
                        except json.JSONDecodeError:
                            report["limitations"].append("invalid_jsonl_record")
            else:
                from .project_sources import _log_record
                records = []
                for line in raw.decode("utf-8-sig").splitlines():
                    if line.strip():
                        event, _ = _log_record(line, source_metadata={"service": source.service or profile.service,
                            "environment": profile.environment, "timezone": source.timezone, "exact_service_ids": True})
                        event["logs"] = [line]
                        records.append(event)
            for record in records:
                report["records"] += 1
                record_count += 1
                if record_count > max_records:
                    report["limitations"].append("record_limit")
                    break
                event, error = _scope_record(record, profile, source)
                if error:
                    report["limitations"].append(error)
                elif event:
                    result["events"].append(event)
        except (OSError, ValueError, UnicodeDecodeError) as exc:
            report["limitations"].append("unavailable_source" if isinstance(exc, OSError) else "invalid_source")
        report["limitations"] = sorted(set(report["limitations"]))
        report["complete"] = not report["limitations"]
        result["complete"] = result["complete"] and report["complete"]
        result["limitations"].extend(f"{source.id}:{item}" for item in report["limitations"])
    if not profile.log_sources:
        result["limitations"].append("No registered log source")
    return result


def observe_project_version(profile: ProjectProfile) -> dict:
    """Observe a registered version snapshot or structured field; never run commands."""
    observation = profile.version_observation
    result = {"status": "UNOBSERVED", "runtime_version": None, "code_version": None,
              "source": None, "limitations": [], "execution_authorized": False}
    if observation.method == "none":
        result["limitations"].append("Runtime version observation is not registered")
        return result
    if observation.method == "json_file":
        try:
            path = registered_path(profile, str(observation.path))
            data, digest = _read_json_file(path, max_bytes=64_000)
            if not isinstance(data, dict) or any(data.get(key, getattr(profile, key)) != getattr(profile, key)
                                                 for key in ("project_id", "service", "environment")):
                raise EvidenceError("Version snapshot scope does not match the project")
            result.update(source=str(path), sha256=digest)
            for target in ("runtime_version", "code_version"):
                value = data.get(target)
                if isinstance(value, str) and value.strip() and _type_name(value) is not None:
                    result[target] = value
        except EvidenceError as exc:
            result["limitations"].append(str(exc))
    else:
        logs = read_project_logs(profile)
        versions = {event["trace"].get("version") for event in logs["events"]
                    if isinstance(event["trace"].get("version"), str) and _type_name(event["trace"]["version"]) is not None}
        result["source"] = "registered_log_field:" + observation.field
        if len(versions) > 1:
            result.update(status="CONFLICT")
            result["limitations"].append("Multiple runtime versions in the registered scope")
            return result
        if logs["complete"] and versions:
            result["runtime_version"] = next(iter(versions))
        else:
            result["limitations"].extend(logs["limitations"] or ["No runtime version in the registered logs"])
    if result["runtime_version"]:
        result["status"] = "OBSERVED"
    return result
