"""Bounded, read-only project and log collection for Agolive investigations."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
import heapq
import json
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from typing import Iterable

from .evidence import EvidenceError, LocalBundleEvidenceSource
from .deadline import DeadlineExceeded, check_deadline, remaining_timeout
from .report_contract import ReportContext, TIME_WINDOW, event_time


SOURCE_DIRS = (
    "backend/src/main",
    "realtime",
    "agolive-agent",
    "frontend/src",
)
SOURCE_SUFFIXES = {".kt", ".kts", ".java", ".go", ".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte", ".rs", ".cs", ".sql"}
SKIP_PARTS = {"node_modules", ".git", ".next", "dist", "build", "__pycache__", ".venv", "tests", "__tests__", "test"}
MAX_FILE_BYTES = 300_000
MAX_SOURCE_FILES = 1500
MAX_CODE_HITS = 12
MAX_LOG_BYTES = 300_000
MAX_LOG_LINES = 20
# Collection has its own streaming budget. Display/model excerpts never decide
# whether the requested range was inspected. Each record and retained event is
# bounded even when unrelated padding is much larger than a display excerpt.
LOG_CHUNK_BYTES = 64_000
MAX_LOG_SCAN_BYTES = 4_000_000
MAX_LOG_SCAN_LINES = 50_000
MAX_LOG_RECORD_BYTES = 64_000
MAX_LOG_OBSERVATIONS = 100

SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|token|api[_-]?key|secret|authorization)\s*[:=]\s*([^\s,;]+)"
)
JSON_SECRET = re.compile(r'(?i)("(?:password|passwd|token|api[_-]?key|secret|authorization)"\s*:\s*)"(?:\\.|[^"\\])*"')
JSON_IDENTITY = re.compile(r'(?i)("(?:userId|clientIp)"\s*:\s*)(?:"(?:\\.|[^"\\])*"|\d+)')
IDENTITY_ASSIGNMENT = re.compile(r"(?i)\b(userId|clientIp)\s*[:=]\s*([^\s,;]+)")
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+")
API_KEY = re.compile(r"\b(?:sk|nvapi)-[A-Za-z0-9_-]{12,}\b")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
SPRING_CLIENT_IP = re.compile(r"(\[[A-Za-z0-9-]{8}\]\s*)\[(?:\d{1,3}\.){3}\d{1,3}\]")
REQUEST_ID = re.compile(r"(?i)\b(?:request[_ -]?id|trace[_ -]?id)\s*[:=#]\s*([A-Za-z0-9._-]{1,64})(?![A-Za-z0-9._-])")
LOG_REQUEST_ID = re.compile(r"(?i)\b(?:request[_ -]?id|trace[_ -]?id)[\"']?\s*[:=#]\s*[\"']?([A-Za-z0-9._-]{1,64})(?![A-Za-z0-9._-])")
SPRING_MDC_PREFIX = re.compile(
    r"^\s*(?:[\w.-]+\s*\|\s*)?\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?\s+(?:TRACE|DEBUG|INFO|WARN|ERROR|FATAL)"
    r"\s+\[([A-Za-z0-9._?-]{1,64})\]\s+\["
)
REPORT_WORDS = re.compile(r"[A-Za-z_][A-Za-z0-9_./-]{2,}|[가-힣]{2,}")
ERROR_WORDS = ("error", "exception", "failed", "failure", "warn", "panic", "오류", "실패")


@dataclass(frozen=True)
class Evidence:
    id: str
    kind: str
    source: str
    content: str
    service: str | None = None
    correlated: bool = False
    event_at: str | None = None
    environment: str | None = None
    trace_id: str | None = None
    scope_status: str = "NOT_CHECKED"
    scope_checks: dict[str, str] = field(default_factory=dict)
    source_revision: str | None = None
    response_status: int | None = None
    repository_id: str | None = None
    source_tree_dirty: bool | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class LogRead:
    """One bounded byte stream plus sticky upstream collection facts."""

    chunks: Iterable[bytes]
    state: dict = field(default_factory=dict)


class LogText(str):
    """Compatibility excerpt carrying facts about any upstream truncation."""

    def __new__(cls, value: str, collection_state: dict):
        instance = super().__new__(cls, value)
        instance.collection_state = collection_state
        return instance


def file_log_stream(path: Path, *, deadline: float | None = None, registered_format: str | None = None) -> LogRead:
    check_deadline(deadline)
    if registered_format not in {None, "jsonl", "text"}:
        raise ValueError("등록 스트리밍 로그 형식은 JSONL 또는 text여야 합니다")
    if path.is_symlink() or registered_format is None and path.suffix.lower() not in {".log", ".txt", ".jsonl"} or not path.is_file():
        raise ValueError("등록된 로그는 일반 .log/.txt/.jsonl 파일이어야 합니다")
    initial = path.stat()
    state = {"source_bytes": initial.st_size, "source_mtime_ns": initial.st_mtime_ns, "coverage": "REGISTERED_FILE",
             "structured_jsonl": registered_format == "jsonl"}

    def chunks():
        with path.open("rb") as stream:
            while True:
                check_deadline(deadline)
                raw = stream.read(LOG_CHUNK_BYTES)
                if not raw:
                    break
                yield raw
        final = path.stat()
        if (initial.st_size, initial.st_mtime_ns) != (final.st_size, final.st_mtime_ns):
            state["source_changed"] = True

    return LogRead(chunks(), state)


def _log_lines(value: str | LogRead, *, deadline: float | None = None):
    """Yield complete records without materializing the source or splitlines()."""
    if isinstance(value, LogRead):
        read = value
    else:
        inherited = dict(getattr(value, "collection_state", {}))

        def chunks():
            for offset in range(0, len(value), LOG_CHUNK_BYTES // 4):
                part = value[offset:offset + LOG_CHUNK_BYTES // 4]
                try:
                    yield part.encode("utf-8")
                except UnicodeEncodeError:
                    state["reasons"].append("invalid_unicode_input")
                    yield part.encode("utf-8", errors="replace")

        read = LogRead(chunks(), inherited)
    state = read.state
    inherited_incomplete = state.get("complete") is False
    state.setdefault("reasons", [])
    if inherited_incomplete and "upstream_incomplete" not in state["reasons"]:
        state["reasons"].append("upstream_incomplete")
    state.update(scan_complete=False, scanned_bytes=0, scanned_lines=0)

    def hold(reason):
        state[reason] = True
        if reason not in state["reasons"]:
            state["reasons"].append(reason)

    def decode_record(raw):
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            hold("invalid_utf8_record")
            decoded = raw.decode("utf-8", errors="replace")
        if state["scanned_lines"] == 1:
            decoded = decoded.removeprefix("\ufeff")
        return decoded.rstrip("\r")

    def lines():
        pending = bytearray()
        discarding = False
        iterator = iter(read.chunks)
        try:
            for raw in iterator:
                check_deadline(deadline)
                remaining = MAX_LOG_SCAN_BYTES - state["scanned_bytes"]
                cut = len(raw) > remaining
                raw = raw[:remaining]
                state["scanned_bytes"] += len(raw)
                if cut:
                    hold("byte_limit_reached")
                for part_index, part in enumerate(raw.split(b"\n")):
                    if part_index:
                        if state["scanned_lines"] >= MAX_LOG_SCAN_LINES:
                            hold("line_limit_reached")
                            return
                        state["scanned_lines"] += 1
                        if not discarding:
                            yield state["scanned_lines"], decode_record(pending)
                        pending.clear()
                        discarding = False
                    if not discarding:
                        if len(pending) + len(part) > MAX_LOG_RECORD_BYTES:
                            hold("record_limit_reached")
                            pending.clear()
                            discarding = True
                        else:
                            pending.extend(part)
                if cut:
                    return  # Never parse the cut final record as a complete observation.
            if pending or discarding:
                if state["scanned_lines"] >= MAX_LOG_SCAN_LINES:
                    hold("line_limit_reached")
                    return
                state["scanned_lines"] += 1
                if not discarding:
                    yield state["scanned_lines"], decode_record(pending)
            state["scan_complete"] = True
        except DeadlineExceeded:
            hold("deadline_exhausted")
        except (OSError, ValueError) as exc:
            hold("read_failed")
            state["error_type"] = type(exc).__name__
        finally:
            close = getattr(iterator, "close", None)
            if close:
                close()
            for reason in ("source_changed", "deadline_exhausted", "process_failed", "read_failed"):
                if state.get(reason):
                    hold(reason)
            state["complete"] = bool(state["scan_complete"] and not state["reasons"])

    return lines(), state


def redact(text: str, *, mask_identity: bool = True) -> str:
    """Remove common secret and identity shapes before display or model input."""
    text = SPRING_CLIENT_IP.sub(r"\1[IP]", text)
    text = BEARER.sub("Bearer [REDACTED]", text)
    text = API_KEY.sub("[API_KEY]", text)
    text = JSON_SECRET.sub(r'\1"[REDACTED]"', text)
    text = SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    if mask_identity:
        text = JSON_IDENTITY.sub(r'\1"[REDACTED]"', text)
        text = IDENTITY_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    text = EMAIL.sub("[EMAIL]", text)
    return IPV4.sub("[IP]", text)


def report_terms(report: str) -> list[str]:
    stop = {"error", "failed", "failure", "오류", "오류가", "실패", "발생", "합니다", "안됩니다", "납니다", "뜹니다", "문제", "please"}
    return [
        word for word in dict.fromkeys(REPORT_WORDS.findall(report))
        if word.casefold() not in stop and len(word) <= 80
    ][:12]


def request_id_from_report(report: str) -> str | None:
    ids = list(dict.fromkeys(match.group(1) for match in REQUEST_ID.finditer(report)))
    return ids[0] if len(ids) == 1 else None


def _log_request_ids(line: str) -> set[str]:
    # Agolive's logback prefix takes precedence over IDs mentioned in the message.
    stripped, _ = _log_payload(line)
    prefix = SPRING_MDC_PREFIX.match(stripped)
    if prefix:
        return {prefix.group(1)}
    if stripped.lstrip().startswith("{"):
        try:
            record = json.loads(stripped)
        except (ValueError, TypeError):
            return set()
        if not isinstance(record, dict):
            return set()
        metadata = record.get("trace") if isinstance(record.get("trace"), dict) else record
        return {value for key in ("requestId", "request_id", "traceId", "trace_id") if isinstance(value := metadata.get(key), str) and re.fullmatch(r"[A-Za-z0-9._-]{1,64}", value)}
    # Message/quoted payload IDs are not metadata fields.
    metadata_text = re.split(r"\s+-\s+|\b(?:message|msg|note)\s*=", stripped, maxsplit=1, flags=re.I)[0]
    return {match.group(1) for match in LOG_REQUEST_ID.finditer(metadata_text)}


def agolive_repo_path() -> Path:
    import os

    configured = os.getenv("TRACEBRIDGE_AGOLIVE_REPO")
    return Path(configured).expanduser().resolve() if configured else (
        Path(__file__).resolve().parents[2] / "projects" / "agolive"
    ).resolve()


def validate_agolive_repo(repo: Path) -> Path:
    repo = repo.expanduser().resolve(strict=True)
    required = ("backend/build.gradle.kts", "realtime/go.mod", "agolive-agent/pyproject.toml", "frontend/package.json")
    if not all((repo / name).is_file() for name in required):
        raise ValueError("Agolive 저장소의 네 서비스 manifest를 찾지 못했습니다")
    return repo


def repository_revision(repo: Path, *, deadline: float | None = None) -> str:
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=repo, capture_output=True, text=True, timeout=remaining_timeout(deadline, 5), check=False,
        )
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != repo.resolve():
            return "unknown"
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=repo, capture_output=True, text=True, timeout=remaining_timeout(deadline, 5), check=False,
        )
    except DeadlineExceeded:
        raise
    except subprocess.TimeoutExpired:
        if deadline is not None:
            raise DeadlineExceeded("git_revision") from None
        return "unknown"
    except OSError:
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def source_tree_dirty(repo: Path, *, deadline: float | None = None, code_roots: tuple[Path, ...] | None = None) -> bool | None:
    if repository_revision(repo, deadline=deadline) == "unknown":
        return None
    try:
        directories = [str(path.relative_to(repo)) for path in code_roots] if code_roots is not None else SOURCE_DIRS
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", *directories],
            cwd=repo, capture_output=True, text=True, timeout=remaining_timeout(deadline, 8), check=False,
        )
    except DeadlineExceeded:
        raise
    except subprocess.TimeoutExpired:
        if deadline is not None:
            raise DeadlineExceeded("git_status") from None
        return None
    except OSError:
        return None
    return bool(result.stdout.strip()) if result.returncode == 0 else None


def stack_profile(repo: Path, *, deadline: float | None = None) -> list[Evidence]:
    manifests = [
        ("backend/build.gradle.kts", "backend", "Kotlin / Spring Boot"),
        ("realtime/go.mod", "realtime", "Go realtime server"),
        ("agolive-agent/pyproject.toml", "agent", "Python / FastAPI agent"),
        ("frontend/package.json", "frontend", "Next.js / React frontend"),
        ("docker-compose.yml", "infra", "Docker Compose services"),
    ]
    evidence = []
    for index, (path, service, description) in enumerate(manifests, 1):
        check_deadline(deadline)
        if (repo / path).is_file():
            evidence.append(Evidence(f"P{index}", "profile", path, description, service))
    return evidence


def _source_files(repo: Path, *, deadline: float | None = None, code_roots: tuple[Path, ...] | None = None) -> Iterable[Path]:
    count = 0
    resolved_repo = repo.resolve()
    for directory in code_roots if code_roots is not None else SOURCE_DIRS:
        check_deadline(deadline)
        root = repo / directory
        if not root.resolve().is_relative_to(resolved_repo):
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
            check_deadline(deadline)
            if count >= MAX_SOURCE_FILES:
                return
            if not path.is_file() or path.is_symlink() or path.suffix not in SOURCE_SUFFIXES:
                continue
            if not path.resolve().is_relative_to(resolved_repo):
                continue
            relative = path.relative_to(repo)
            if any(part.lower() in SKIP_PARTS for part in relative.parts):
                continue
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    continue
            except OSError:
                continue
            count += 1
            yield path


def code_evidence(repo: Path, terms: list[str], services: list[str] | None = None, *, deadline: float | None = None,
                  code_roots: tuple[Path, ...] | None = None, repositories: tuple = ()) -> list[Evidence]:
    check_deadline(deadline)
    cleaned = [
        (term.strip().casefold(), term.strip().isupper() and "_" in term)
        for term in terms if 2 <= len(term.strip()) <= 80
    ][:12]
    if not cleaned:
        return []
    preferred = set(services or [])
    def locations():
        seen = set()
        for repository, root, directories in [(None, repo, code_roots), *[(item, item.root, item.code_roots) for item in repositories]]:
            for path in _source_files(root, deadline=deadline, code_roots=directories):
                canonical = path.resolve()
                if canonical in seen:
                    continue
                if len(seen) >= MAX_SOURCE_FILES:
                    return
                seen.add(canonical)
                yield repository, root, path

    hits = []
    for repository, root, path in locations():
        relative = path.relative_to(root).as_posix()
        service = repository.service if repository is not None else "backend" if relative.startswith("backend/") else (
            "realtime" if relative.startswith("realtime/") else (
                "agent" if relative.startswith("agolive-agent/") else "frontend"
            )
        )
        try:
            check_deadline(deadline)
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except DeadlineExceeded:
            raise
        except OSError:
            continue
        filename = path.name.casefold()
        for line_number, line in enumerate(lines, 1):
            check_deadline(deadline)
            if len(line) > 1000:
                continue
            lowered = line.casefold()
            matched = [(term, identifier) for term, identifier in cleaned if term in lowered]
            if not matched:
                continue
            score = sum(12 if identifier else (3 if len(term) >= 6 else 1) for term, identifier in matched)
            score += sum(2 for term, _ in cleaned if term in filename)
            score += 2 if service in preferred else 0
            score += 2 if any(word in lowered for word in ("throw", "error", "warn", "exception", "slog.")) else 0
            source = f"repository:{repository.id}/{relative}" if repository else relative
            hits.append((score, source, line_number, line, service, path, repository))
    hits.sort(key=lambda hit: (-hit[0], hit[1], hit[2]))
    selected: list[Evidence] = []
    per_file: dict[str, int] = {}
    strong_found = bool(hits and hits[0][0] >= 10)
    weak_count = 0
    versions = {}
    for score, relative, line_number, _, service, path, repository in hits:
        if strong_found and score < 10 and weak_count >= 4:
            continue
        if per_file.get(relative, 0) >= 2:
            continue
        per_file[relative] = per_file.get(relative, 0) + 1
        try:
            check_deadline(deadline)
            if repository and not path.resolve().is_relative_to(repository.root.resolve()):
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except DeadlineExceeded:
            raise
        except OSError:
            continue
        start = max(1, line_number - 2)
        end = min(len(lines), line_number + 2)
        snippet = "\n".join(f"{number}: {lines[number - 1].strip()}" for number in range(start, end + 1))
        if repository and repository.id not in versions:
            metadata_root = repository.git_root or repository.root
            versions[repository.id] = (repository_revision(metadata_root, deadline=deadline),
                                      source_tree_dirty(metadata_root, deadline=deadline, code_roots=repository.code_roots))
        selected.append(Evidence(
            f"C{len(selected) + 1}", "code", f"{relative}:{line_number}",
            redact(snippet, mask_identity=False)[:800], service,
            repository_id=repository.id if repository else None,
            source_revision=versions[repository.id][0] if repository else None,
            source_tree_dirty=versions[repository.id][1] if repository else None,
        ))
        if strong_found and score < 10:
            weak_count += 1
        if len(selected) >= MAX_CODE_HITS:
            break
    return selected


SERVICE_ALIASES = {"api": "backend", "agolive-api": "backend", "agolive-realtime": "realtime", "agolive-agent": "agent"}
LOG_TIMESTAMP = re.compile(r"^\s*(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?)")


def _log_payload(line: str) -> tuple[str, str | None]:
    docker_prefix = bool(re.match(r"^\s*[\w.-]+\s*\|\s*", line))
    stripped = re.sub(r"^\s*[\w.-]+\s*\|\s*", "", line)
    # Docker's --timestamps can precede JSON or Spring's own timestamp/MDC prefix.
    outer = re.match(r"^\s*(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2}))\s+", stripped)
    if outer:
        rest = stripped[outer.end():]
        if docker_prefix or rest.startswith("{") or LOG_TIMESTAMP.match(rest):
            return rest, outer.group(1)
    return stripped, None


def canonical_service(value: str | None) -> str | None:
    return SERVICE_ALIASES.get(value, value)


def _registered_timezone(value: str | None):
    if value in {"UTC", "Z"}:
        return timezone.utc
    if value and re.fullmatch(r"[+-]\d{2}:\d{2}", value):
        hours, minutes = map(int, value[1:].split(":"))
        if hours <= 14 and minutes <= 59:
            return timezone((1 if value[0] == "+" else -1) * timedelta(hours=hours, minutes=minutes))
    return None


def _log_record(line: str, *, source_metadata: dict) -> tuple[dict, dict]:
    """Recognize metadata only. Report/model claims never supply missing log fields."""
    prefix = re.match(r"^\s*([\w.-]+)\s*\|\s*", line)
    stripped, docker_time = _log_payload(line)
    data = {}
    json_object = False
    if stripped.lstrip().startswith("{"):
        try:
            value = json.loads(stripped)
            data = value if isinstance(value, dict) else {}
            json_object = isinstance(value, dict)
        except ValueError:
            pass
    metadata = data.get("trace") if isinstance(data.get("trace"), dict) else data
    trace = {}
    for target, keys in {
        "occurred_at": ("occurred_at", "timestamp", "@timestamp", "event_at", "time", "ts"),
        "service": ("service", "service_name"), "environment": ("environment", "env"),
        "method": ("method",), "path": ("path",), "operation": ("operation",),
        "version": ("version", "git_sha", "service_version"), "code_version": ("code_version",),
        "project_id": ("project_id",),
    }.items():
        value = next((metadata.get(key) for key in keys if isinstance(metadata.get(key), str) and metadata[key].strip()), None)
        if value:
            trace[target] = value[:200]
    status = next((metadata.get(key) for key in ("response_status", "http_status", "status_code", "status") if type(metadata.get(key)) is int and 100 <= metadata[key] <= 599), None)
    if status is not None:
        trace["response_status"] = status
    if not data:
        before_message = re.split(r"\s+-\s+|\b(?:message|msg|note)\s*=", stripped, maxsplit=1, flags=re.I)[0]
        match = LOG_TIMESTAMP.match(stripped)
        if match:
            trace["occurred_at"] = match.group(1).replace(",", ".")
        for key in ("service", "environment", "method", "path", "operation", "version"):
            match = re.search(rf"\b{key}=([^\s,;]+)", before_message)
            if match:
                trace[key] = match.group(1)[:200]
        match = re.search(r"\b(?:response_status|http_status|status_code|status)=(\d{3})\b", before_message)
        if match and 100 <= int(match.group(1)) <= 599:
            trace["response_status"] = int(match.group(1))
    ids = _log_request_ids(line)
    if len(ids) == 1 and next(iter(ids)) != "?":
        trace["trace_id"] = next(iter(ids))
    provenance = {"json_object": json_object}
    for key in ("service", "environment"):
        provenance[key] = "LOG_FIELD" if trace.get(key) else "NOT_OBSERVED"
        registered = source_metadata.get(key)
        if not trace.get(key) and registered:
            trace[key], provenance[key] = registered, "REGISTERED_SOURCE"
        elif not trace.get(key) and key == "service" and prefix:
            name = prefix.group(1)
            name = re.sub(r"[-_]\d+$", "", name)
            if name in SERVICE_ALIASES or name in {"backend", "realtime", "agent", "frontend"}:
                trace[key], provenance[key] = name, "DOCKER_PREFIX"
    trace["service"] = trace.get("service") if source_metadata.get("exact_service_ids") else canonical_service(trace.get("service"))
    if isinstance(data.get("project_id"), str) and "project_id" not in trace:
        trace["project_id"] = data["project_id"][:200]
    if docker_time and not trace.get("occurred_at"):
        trace["occurred_at"] = docker_time
    if trace.get("occurred_at"):
        try:
            when = datetime.fromisoformat(trace["occurred_at"].replace("Z", "+00:00"))
            if when.tzinfo is None:
                tz = _registered_timezone(source_metadata.get("timezone"))
                if docker_time:
                    when = event_time(docker_time)
                    provenance["timezone"] = "DOCKER_TIMESTAMP"
                elif tz is None:
                    raise ValueError("No log timezone")
                else:
                    when = when.replace(tzinfo=tz)
                    provenance["timezone"] = "REGISTERED_SOURCE"
            else:
                provenance["timezone"] = "LOG_FIELD"
            trace["occurred_at"] = when.isoformat()
        except ValueError:
            trace.pop("occurred_at", None)
    request = metadata.get("request")
    if isinstance(request, dict) and len(request) <= 100 and all(isinstance(key, str) and len(key) <= 80 for key in request):
        trace["request"] = {key: "[VALUE]" for key in request}
        # Primitive types are observed before values are removed; mask markers
        # remain unknown. Registered metadata can also preserve nested types.
        type_names = {str: "string", int: "integer", float: "number", bool: "boolean", list: "array", dict: "object", type(None): "null"}
        trace["request_types"] = {key: type_names[type(value)] for key, value in request.items()
                                  if type(value) in type_names and value not in ("[VALUE]", "[REDACTED]", "[MASKED]")}
    for key in ("request_types", "input_types"):
        values = metadata.get(key)
        if isinstance(values, dict) and len(values) <= 100:
            types = {name: kind for name, kind in values.items() if isinstance(name, str) and len(name) <= 200
                     and isinstance(kind, str) and kind in {"string", "integer", "number", "boolean", "object", "array", "null"}}
            trace[key] = {**trace.get(key, {}), **types}
    for key in ("request_fields", "input_fields", "redacted_fields"):
        values = metadata.get(key)
        if isinstance(values, list) and len(values) <= 100 and all(isinstance(name, str) and len(name) <= 200 for name in values):
            trace[key] = list(values)
    caller = metadata.get("caller", data.get("caller"))
    if isinstance(caller, dict):
        trace["caller"] = {key: deepcopy(caller[key]) for key in (
            "source", "source_kind", "source_verified", "source_sha256", "version", "method", "path",
            "project_id", "service", "environment", "field_mapping", "required_inputs", "input_fields", "input_types",
        ) if key in caller}
    event = {"trace": trace}
    # Optional captured snapshots use the existing bundle schema validation.
    if type(trace.get("response_status")) is int and all(trace.get(k) for k in ("trace_id", "service", "environment")):
        snapshot = {"trace": trace, **{k: data[k] for k in ("contract", "dto", "migration", "contract_context", "caller") if k in data}}
        try:
            LocalBundleEvidenceSource(snapshot)
            event.update({k: deepcopy(snapshot[k]) for k in ("contract", "dto", "migration", "contract_context", "caller") if k in snapshot})
        except EvidenceError:
            provenance["snapshot"] = "INVALID_NOT_USED"
    return event, provenance


def collect_scoped_logs(
    log_text: str | LogRead, report: str, *, source: str, scope: ReportContext,
    source_metadata: dict | None = None, search_terms: list[str] | None = None,
    deadline: float | None = None,
) -> tuple[list[Evidence], list[str], list[dict], dict]:
    """Inspect the requested range first; retain bounded observations and excerpts."""
    # Imported lazily because report_intake also consumes the source primitives.
    from .report_intake import observation_conflicts

    source_metadata = source_metadata or {}
    notes = []
    lines, collection = _log_lines(log_text, deadline=deadline)
    request_id = scope.trace_id or request_id_from_report(report)
    terms = [term.casefold() for term in [*report_terms(redact(report)), *(search_terms or [])] if 3 <= len(term) <= 80][:20]
    target_time = event_time(scope.occurred_at) if scope.occurred_at else None
    candidates, events = [], []
    counters = {"matched": 0, "unverified": 0, "excluded": 0}
    comparison_events, conflicts, witnesses = {}, {}, []
    parent = None
    for index, line in lines:
        try:
            check_deadline(deadline)
        except DeadlineExceeded:
            collection["deadline_exhausted"] = True
            collection["reasons"].append("deadline_exhausted")
            notes.append("로그 범위 검사 중 시간 한도에 도달해 확보한 관측만 보존했습니다")
            break
        event, provenance = _log_record(line, source_metadata=source_metadata)
        trace = event["trace"]
        if collection.get("structured_jsonl") and line.strip():
            if not provenance["json_object"]:
                collection["reasons"].append("invalid_structured_record")
            elif not trace.get("trace_id") or not trace.get("occurred_at"):
                collection["reasons"].append("structured_scope_unobserved")
        payload, docker_time = _log_payload(line)
        stack_line = payload.lstrip().startswith(("at ", "Caused by:")) or bool(re.match(r"^\s+\.\.\. \d+ more\s*$", payload))
        same_service = not parent or not trace.get("service") or trace.get("service") == parent["trace"].get("service")
        continuation = bool(parent and not _log_request_ids(line) and stack_line and not LOG_TIMESTAMP.match(payload) and same_service and (not trace.get("occurred_at") or docker_time))
        if continuation:
            trace = deepcopy(parent["trace"])
            event, provenance = {"trace": trace}, parent["provenance"]
        else:
            parent = {"trace": trace, "provenance": provenance} if trace.get("occurred_at") else None
        if request_id and trace.get("trace_id") != request_id:
            counters["excluded"] += 1
            continue
        lowered = line.casefold()
        score = sum(term in lowered for term in terms)
        error = any(word in lowered for word in ERROR_WORDS) or type(trace.get("response_status")) is int and trace["response_status"] >= 400
        if not (request_id or error or score):
            continue
        checks = {}
        checks["project"] = "MISMATCH" if trace.get("project_id") and source_metadata.get("project_id") and trace["project_id"] != source_metadata["project_id"] else "REGISTERED_SOURCE"
        normalize = (lambda value: value) if source_metadata.get("exact_service_ids") else canonical_service
        for key, expected in (("service", normalize(scope.service)), ("environment", scope.environment)):
            actual = trace.get(key)
            registered = source_metadata.get(key)
            registered = normalize(registered) if key == "service" else registered
            checks[key] = "NOT_OBSERVED" if not actual else ("MISMATCH" if expected and actual != expected or registered and actual != registered else provenance.get(key, "LOG_FIELD"))
        checks["time"] = "NOT_OBSERVED" if not trace.get("occurred_at") else ("REPORT_TIME_MISSING" if not target_time else ("MATCH" if abs(event_time(trace["occurred_at"]) - target_time) <= TIME_WINDOW else "MISMATCH"))
        checks["timezone"] = provenance.get("timezone", "NOT_OBSERVED")
        checks["response_status"] = "LOG_FIELD" if trace.get("response_status") is not None else "NOT_OBSERVED"
        checks["request_id"] = "LOG_FIELD" if trace.get("trace_id") else "NOT_OBSERVED"
        if "MISMATCH" in checks.values():
            counters["excluded"] += 1
            continue
        verified = checks["time"] == "MATCH" and all(checks[k] not in {"NOT_OBSERVED", "MISMATCH"} for k in ("service", "environment"))
        counters["matched" if verified else "unverified"] += 1
        correlated = bool(verified and request_id and trace.get("trace_id") == request_id)
        if payload.lstrip().startswith("{"):
            # Show metadata and redacted diagnostic text, never captured request values.
            stripped = payload
            try:
                raw = json.loads(stripped)
            except ValueError:
                raw = {}
            message = " ".join(str(raw[k]) for k in ("level", "message", "exception", "error") if isinstance(raw.get(k), str))
            content = json.dumps({**{k: v for k, v in trace.items() if k != "request"}, "message": redact(message), "request_fields": list(trace.get("request", {}))}, ensure_ascii=False)
        else:
            content = redact(line)
        content = redact(content)
        evidence = Evidence(
            f"L{index}", "log", f"{source}:{index}", content[:800], trace.get("service"), correlated,
            trace.get("occurred_at"), trace.get("environment"), trace.get("trace_id"),
            "VERIFIED" if verified else "UNVERIFIED", checks, response_status=trace.get("response_status"),
        )
        ranked = (10 * correlated + 3 * error + score, -index, evidence)
        if len(candidates) < MAX_LOG_LINES:
            heapq.heappush(candidates, ranked)
        elif ranked[:2] > candidates[0][:2]:
            heapq.heapreplace(candidates, ranked)
        if verified:
            if not trace.get("trace_id"):
                event["trace"]["trace_id"] = f"log-{source}-{index}"[:64]
            event["logs"] = [content[:800]]
            trace_id = trace["trace_id"]
            before = comparison_events.get(trace_id)
            changed = observation_conflicts(before, event) if before else set()
            if changed:
                previous_fields = conflicts.setdefault(trace_id, set())
                if changed - previous_fields:
                    # Counter-evidence survives both observation and excerpt caps.
                    if len(witnesses) < MAX_LOG_OBSERVATIONS:
                        witnesses.append(evidence)
                    if len(events) >= MAX_LOG_OBSERVATIONS and len(events) < 2 * MAX_LOG_OBSERVATIONS:
                        events.append(event)
                previous_fields.update(changed)
            if len(events) < MAX_LOG_OBSERVATIONS:
                events.append(event)
            if before is None and len(comparison_events) < MAX_LOG_OBSERVATIONS:
                comparison_events[trace_id] = event
        if provenance.get("snapshot"):
            notes.append("로그의 계약/버전 자료 형식을 확인하지 못해 판정에 사용하지 않았습니다")
    lines.close()
    event_limit_reached = counters["matched"] > MAX_LOG_OBSERVATIONS
    if event_limit_reached:
        collection["reasons"].append("event_limit_reached")
    if counters["unverified"]:
        collection["reasons"].append("matching_scope_unverified")
    collection["complete"] = bool(collection["scan_complete"] and not collection["reasons"])
    if not collection["complete"]:
        notes.append("등록 로그의 요청 범위를 끝까지 확인하지 못했거나 관측 보존 한도에 도달했습니다. 확정 판정을 보류합니다")
    selected_by_line = {item.source: item for item in witnesses[:MAX_LOG_LINES]}
    for _, _, item in sorted(candidates, key=lambda x: (-x[0], -x[1])):
        if len(selected_by_line) >= MAX_LOG_LINES:
            break
        selected_by_line.setdefault(item.source, item)
    selected = sorted(selected_by_line.values(), key=lambda item: int(item.source.rsplit(":", 1)[1]))
    if counters["unverified"]:
        notes.append("로그의 발생 시각·서비스·환경 중 확인되지 않은 항목이 있습니다. 미확인 로그는 제보 단서 후보로만 유지합니다")
    if counters["excluded"]:
        notes.append("범위 또는 요청 식별 값이 다른 로그를 이번 사건의 근거에서 제외했습니다")
    if not request_id:
        notes.append("requestId가 없어 시간·서비스·환경이 맞아도 동일 사건은 미확정입니다")
    return selected, list(dict.fromkeys(notes)), events, {
        "source": source, "requested": scope.to_dict(), "registration": source_metadata,
        **collection, "reasons": list(dict.fromkeys(collection["reasons"])), "counts": counters,
        "observation_count": counters["matched"], "retained_observation_count": len(events),
        "event_limit_reached": event_limit_reached,
        "deadline_exhausted": bool(collection.get("deadline_exhausted")),
        "conflicts": [{"trace_id": id_, "fields": sorted(fields), "sources": [source]} for id_, fields in sorted(conflicts.items())],
        "display_limit": MAX_LOG_LINES, "display_truncated": counters["matched"] + counters["unverified"] > len(selected),
    }


def log_evidence(log_text: str, report: str, *, source: str = "provided-log", search_terms: list[str] | None = None) -> tuple[list[Evidence], list[str]]:
    if not log_text.strip():
        return [], []
    notes: list[str] = []
    if getattr(log_text, "collection_state", {}).get("complete") is False:
        notes.append("로그 소스 조회가 불완전합니다. 발췌는 전체 사건을 확인한 근거가 아닙니다")
    encoded = log_text.encode("utf-8", errors="replace")
    if len(encoded) > MAX_LOG_BYTES:
        log_text = encoded[:MAX_LOG_BYTES].decode("utf-8", errors="ignore")
        notes.append("로그 입력을 300 KB로 제한했습니다")
    lines = log_text.splitlines()[:5000]
    request_id = request_id_from_report(report)
    terms = [term.casefold() for term in [*report_terms(redact(report)), *(search_terms or [])] if 3 <= len(term) <= 80][:20]
    candidates: list[tuple[int, int, str, bool]] = []
    for index, line in enumerate(lines, 1):
        lowered = line.casefold()
        correlated = bool(request_id and _log_request_ids(line) == {request_id})
        if request_id and not correlated:
            continue
        error = any(word in lowered for word in ERROR_WORDS)
        term_count = sum(1 for term in terms if term in lowered)
        if error or correlated or term_count:
            score = (10 if correlated else 0) + (3 if error else 0) + term_count
            candidates.append((score, index, line, correlated))
            if error:
                for next_index in range(index + 1, min(index + 5, len(lines) + 1)):
                    continuation = lines[next_index - 1]
                    if _log_request_ids(continuation):
                        break
                    if not (continuation.startswith((" ", "\t")) or continuation.lstrip().startswith(("at ", "Caused by:"))):
                        break
                    candidates.append((score - 1, next_index, continuation, correlated))
    if request_id and not candidates:
        notes.append("제보한 requestId와 일치하는 로그를 찾지 못했습니다")
    candidates.sort(key=lambda item: (-item[0], item[1]))
    unique: list[tuple[int, int, str, bool]] = []
    seen_lines: set[int] = set()
    for candidate in candidates:
        if candidate[1] in seen_lines:
            continue
        unique.append(candidate)
        seen_lines.add(candidate[1])
        if len(unique) >= MAX_LOG_LINES:
            break
    selected = sorted(unique, key=lambda item: item[1])
    evidence = [
        Evidence(f"L{number}", "log", f"{source}:{index}", redact(line)[:500], correlated=correlated)
        for number, (_, index, line, correlated) in enumerate(selected, 1)
    ]
    if evidence and not request_id:
        notes.append("requestId가 없어 로그와 제보의 연결은 문구 기반 후보입니다. 발생 시각 필터는 아직 적용하지 않았습니다")
    return evidence, notes


def docker_compose_logs(repo: Path, *, since_minutes: int = 30, deadline: float | None = None,
                        scope: ReportContext | None = None, streaming: bool = False) -> tuple[str | LogRead, str | None]:
    """Stream an explicit request time range with bounded pipes and no --tail."""
    if not 1 <= since_minutes <= 120:
        raise ValueError("로그 조회 범위는 1~120분이어야 합니다")
    check_deadline(deadline)
    state = {"available": True, "reasons": [], "coverage": "REQUEST_TIME_RANGE"}
    if scope and scope.occurred_at:
        when = event_time(scope.occurred_at).astimezone(timezone.utc)
        since, until = (when - TIME_WINDOW).isoformat(), (when + TIME_WINDOW).isoformat()
        # Future time has not been observed yet, even if docker reaches EOF.
        if when + TIME_WINDOW > datetime.now(timezone.utc):
            state["reasons"].append("request_window_not_closed")
    else:
        since, until = f"{since_minutes}m", datetime.now(timezone.utc).isoformat()
        state.update(coverage="RECENT_ONLY", complete=False)
        state["reasons"].append("request_time_not_observed")
    state["queried_since"], state["queried_until"] = since, until
    service = {"backend": "api", "realtime": "realtime"}.get(canonical_service(scope.service) if scope else None)
    services = [service] if service else ["api", "realtime"]
    argv = ["docker", "compose", "-f", str(repo / "docker-compose.yml"), "logs", "--no-color", "--timestamps",
            "--since", since, "--until", until, *services]

    def chunks():
        process, reader = None, None
        stop = threading.Event()
        channel = queue.Queue(maxsize=2)
        end = object()
        local_deadline = time.monotonic() + remaining_timeout(deadline, 15)

        def deliver(value):
            while not stop.is_set():
                try:
                    channel.put(value, timeout=0.05)
                    return
                except queue.Full:
                    pass

        try:
            process = subprocess.Popen(argv, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False)

            def pump():
                try:
                    read = getattr(process.stdout, "read1", None) or process.stdout.read
                    while not stop.is_set():
                        raw = read(LOG_CHUNK_BYTES)
                        if not raw:
                            break
                        deliver(raw)
                except Exception as exc:
                    deliver(OSError(type(exc).__name__))
                finally:
                    deliver(end)

            reader = threading.Thread(target=pump, daemon=True)
            reader.start()
            while True:
                check_deadline(min(local_deadline, deadline) if deadline is not None else local_deadline)
                try:
                    raw = channel.get(timeout=0.05)
                except queue.Empty:
                    continue
                if raw is end:
                    break
                if isinstance(raw, Exception):
                    raise raw
                yield raw
            process.wait(timeout=remaining_timeout(local_deadline, 1))
            if process.returncode:
                state.update(process_failed=True, available=False)
        except (DeadlineExceeded, subprocess.TimeoutExpired):
            state.update(deadline_exhausted=True, complete=False)
        except OSError as exc:
            state.update(read_failed=True, available=False, error_type=type(exc).__name__)
        finally:
            stop.set()
            if process is not None:
                try:
                    if process.poll() is None:
                        process.kill()
                    process.wait(timeout=0.2)
                except (subprocess.TimeoutExpired, OSError):
                    state["process_cleanup_incomplete"] = True
                    state["reasons"].append("process_cleanup_incomplete")
            if reader is not None:
                reader.join(timeout=0.2)
            # Closing a BufferedReader while its read thread is blocked can wait
            # on its lock indefinitely. An unfinished cleanup stays incomplete.
            if reader is not None and reader.is_alive():
                state["reasons"].append("process_cleanup_incomplete")
            elif process is not None and process.stdout is not None:
                try:
                    process.stdout.close()
                except (OSError, ValueError):
                    state["reasons"].append("process_cleanup_incomplete")

    read = LogRead(chunks(), state)
    if streaming:
        return read, None
    # Older investigation entry points consume a string excerpt. Preserve its
    # upstream state, rather than silently returning a prefix as complete logs.
    lines, collection = _log_lines(read, deadline=deadline)
    excerpt, size = [], 0
    for _, line in lines:
        raw = (line + "\n").encode("utf-8")
        if size + len(raw) > MAX_LOG_BYTES:
            collection["reasons"].append("display_byte_limit_reached")
            break
        excerpt.append(line)
        size += len(raw)
    lines.close()
    collection["complete"] = bool(collection.get("scan_complete") and not collection["reasons"])
    error = "로컬 Docker 로그의 조회가 실패했거나 시간 한도에 도달했습니다" if collection.get("available") is False or collection.get("deadline_exhausted") else None
    return LogText("\n".join(excerpt), collection), error


def running_service_revision(repo: Path, *, service: str | None, environment: str | None, deadline: float | None = None) -> dict:
    """Observe only revision labels of selected running containers, never their env/secrets."""
    missing = {"sha": None, "source": None, "status": "NOT_OBSERVED", "service": service, "environment": environment}
    selected = {"backend": "api", "realtime": "realtime"}.get(canonical_service(service))
    if not selected:
        return {**missing, "reason": "등록된 서비스가 하나로 정해지지 않았습니다"}
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(repo / "docker-compose.yml"), "ps", "--status", "running", "-q", selected],
            cwd=repo, capture_output=True, text=True, timeout=remaining_timeout(deadline, 8), check=False,
        )
        ids = [value for value in result.stdout.splitlines() if re.fullmatch(r"[a-f0-9]{12,64}", value)]
        if result.returncode or len(ids) != 1:
            return {**missing, "reason": "실행 중인 등록 서비스 컨테이너를 한 개로 확인하지 못했습니다"}
        observed = subprocess.run(
            ["docker", "inspect", "--format", '{{json .State.Running}} {{json (index .Config.Labels "org.opencontainers.image.revision")}}', ids[0]],
            cwd=repo, capture_output=True, text=True, timeout=remaining_timeout(deadline, 8), check=False,
        )
        parts = observed.stdout.strip().split(" ", 1)
        if observed.returncode or len(parts) != 2 or parts[0] != "true":
            return missing
        sha = json.loads(parts[1])
        if not isinstance(sha, str) or not re.fullmatch(r"[a-fA-F0-9]{7,64}", sha):
            return {**missing, "reason": "실행 컨테이너의 revision label을 확인하지 못했습니다"}
    except DeadlineExceeded:
        raise
    except subprocess.TimeoutExpired:
        if deadline is not None:
            raise DeadlineExceeded("docker_version") from None
        return {**missing, "reason": "실행 서비스의 버전 조회에 실패했습니다"}
    except (OSError, ValueError):
        return {**missing, "reason": "실행 서비스의 버전 조회에 실패했습니다"}
    return {
        "sha": sha, "source": f"running-docker:{selected}/{ids[0][:12]}:org.opencontainers.image.revision",
        "status": "OBSERVED", "service": canonical_service(service), "environment": environment,
        "environment_source": "registered_scope", "observed_at": datetime.now(timezone.utc).isoformat(),
        "limitation": "실행 컨테이너의 선언된 revision label입니다. 실행 코드 동일성과 원인·수정 검증을 대신하지 않습니다",
    }
