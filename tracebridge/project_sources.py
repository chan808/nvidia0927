"""Bounded, read-only project and log collection for Agolive investigations."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import re
import subprocess
from typing import Iterable


SOURCE_DIRS = (
    "backend/src/main",
    "realtime",
    "agolive-agent",
    "frontend/src",
)
SOURCE_SUFFIXES = {".kt", ".java", ".go", ".py", ".ts", ".tsx"}
SKIP_PARTS = {"node_modules", ".git", ".next", "dist", "build", "__pycache__", ".venv", "tests", "__tests__", "test"}
MAX_FILE_BYTES = 300_000
MAX_SOURCE_FILES = 1500
MAX_CODE_HITS = 12
MAX_LOG_BYTES = 300_000
MAX_LOG_LINES = 20

SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(password|passwd|token|api[_-]?key|secret|authorization)\s*[:=]\s*([^\s,;]+)"
)
IDENTITY_ASSIGNMENT = re.compile(r"(?i)\b(userId|clientIp)\s*[:=]\s*([^\s,;]+)")
BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+")
API_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
SPRING_CLIENT_IP = re.compile(r"(\[[A-Za-z0-9-]{8}\]\s*)\[(?:\d{1,3}\.){3}\d{1,3}\]")
REQUEST_ID = re.compile(r"(?i)\b(?:request[_ -]?id|trace[_ -]?id)\s*[:=#]\s*([A-Za-z0-9-]{6,64})")
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

    def to_dict(self) -> dict:
        return asdict(self)


def redact(text: str, *, mask_identity: bool = True) -> str:
    """Remove common secret and identity shapes before display or model input."""
    text = SPRING_CLIENT_IP.sub(r"\1[IP]", text)
    text = BEARER.sub("Bearer [REDACTED]", text)
    text = API_KEY.sub("[API_KEY]", text)
    text = SECRET_ASSIGNMENT.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)
    if mask_identity:
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
    match = REQUEST_ID.search(report)
    return match.group(1) if match else None


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


def repository_revision(repo: Path) -> str:
    try:
        top = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=repo, capture_output=True, text=True, timeout=5, check=False,
        )
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != repo.resolve():
            return "unknown"
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=repo, capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "unknown"
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def source_tree_dirty(repo: Path) -> bool | None:
    if repository_revision(repo) == "unknown":
        return None
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--", *SOURCE_DIRS],
            cwd=repo, capture_output=True, text=True, timeout=8, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return bool(result.stdout.strip()) if result.returncode == 0 else None


def stack_profile(repo: Path) -> list[Evidence]:
    manifests = [
        ("backend/build.gradle.kts", "backend", "Kotlin / Spring Boot"),
        ("realtime/go.mod", "realtime", "Go realtime server"),
        ("agolive-agent/pyproject.toml", "agent", "Python / FastAPI agent"),
        ("frontend/package.json", "frontend", "Next.js / React frontend"),
        ("docker-compose.yml", "infra", "Docker Compose services"),
    ]
    return [
        Evidence(f"P{index}", "profile", path, description, service)
        for index, (path, service, description) in enumerate(manifests, 1)
        if (repo / path).is_file()
    ]


def _source_files(repo: Path) -> Iterable[Path]:
    count = 0
    resolved_repo = repo.resolve()
    for directory in SOURCE_DIRS:
        root = repo / directory
        if not root.is_dir():
            continue
        for path in root.rglob("*"):
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


def code_evidence(repo: Path, terms: list[str], services: list[str] | None = None) -> list[Evidence]:
    cleaned = [
        (term.strip().casefold(), term.strip().isupper() and "_" in term)
        for term in terms if 2 <= len(term.strip()) <= 80
    ][:12]
    if not cleaned:
        return []
    preferred = set(services or [])
    hits: list[tuple[int, str, int, str, str]] = []
    for path in _source_files(repo):
        relative = path.relative_to(repo).as_posix()
        service = "backend" if relative.startswith("backend/") else (
            "realtime" if relative.startswith("realtime/") else (
                "agent" if relative.startswith("agolive-agent/") else "frontend"
            )
        )
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        filename = path.name.casefold()
        for line_number, line in enumerate(lines, 1):
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
            hits.append((score, relative, line_number, line, service))
    hits.sort(key=lambda hit: (-hit[0], hit[1], hit[2]))
    selected: list[Evidence] = []
    per_file: dict[str, int] = {}
    strong_found = bool(hits and hits[0][0] >= 10)
    weak_count = 0
    for score, relative, line_number, _, service in hits:
        if strong_found and score < 10 and weak_count >= 4:
            continue
        if per_file.get(relative, 0) >= 2:
            continue
        per_file[relative] = per_file.get(relative, 0) + 1
        try:
            lines = (repo / relative).read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        start = max(1, line_number - 2)
        end = min(len(lines), line_number + 2)
        snippet = "\n".join(f"{number}: {lines[number - 1].strip()}" for number in range(start, end + 1))
        selected.append(Evidence(
            f"C{len(selected) + 1}", "code", f"{relative}:{line_number}",
            redact(snippet, mask_identity=False)[:800], service,
        ))
        if strong_found and score < 10:
            weak_count += 1
        if len(selected) >= MAX_CODE_HITS:
            break
    return selected


def log_evidence(log_text: str, report: str, *, source: str = "provided-log") -> tuple[list[Evidence], list[str]]:
    if not log_text.strip():
        return [], []
    notes: list[str] = []
    encoded = log_text.encode("utf-8", errors="replace")
    if len(encoded) > MAX_LOG_BYTES:
        log_text = encoded[:MAX_LOG_BYTES].decode("utf-8", errors="ignore")
        notes.append("로그 입력을 300 KB로 제한했습니다")
    lines = log_text.splitlines()[:5000]
    request_id = request_id_from_report(report)
    terms = [term.casefold() for term in report_terms(redact(report)) if len(term) >= 3]
    candidates: list[tuple[int, int, str, bool]] = []
    for index, line in enumerate(lines, 1):
        lowered = line.casefold()
        correlated = bool(request_id and request_id.casefold() in lowered)
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


def docker_compose_logs(repo: Path, *, since_minutes: int = 30) -> tuple[str, str | None]:
    """Read bounded local container logs only after an explicit UI/CLI request."""
    if not 1 <= since_minutes <= 120:
        raise ValueError("로그 조회 범위는 1~120분이어야 합니다")
    try:
        result = subprocess.run(
            ["docker", "compose", "-f", str(repo / "docker-compose.yml"), "logs", "--no-color",
             "--since", f"{since_minutes}m", "--tail", "300", "api", "realtime"],
            cwd=repo, capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "", f"로컬 Docker 로그를 읽지 못했습니다: {type(exc).__name__}"
    if result.returncode != 0:
        return "", "로컬 Docker가 실행 중이지 않거나 Agolive 컨테이너 로그에 접근할 수 없습니다"
    return result.stdout[:MAX_LOG_BYTES], None
