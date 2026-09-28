"""Freeze local sources and optionally smoke-test the offline CLI in isolation."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIRS = ("tracebridge", "scripts", "examples", "pages", "tests", "docs", "skills")
ROOT_FILES = ("app.py", "pyproject.toml", "requirements.txt", "nat_workflow.yml",
              ".env.example", ".streamlit/config.toml", ".gitignore", ".gitattributes")
SUFFIXES = {".py", ".json", ".jsonl", ".md", ".toml", ".yml", ".yaml",
            ".kt", ".kts", ".go", ".mod", ".log", ".lock", ".txt",
            ".png", ".jpg", ".jpeg"}
SKIP_DIRS = {"__pycache__", ".pytest_cache", "output", "generated", ".git", ".venv"}
MAX_FILE = 4_000_000
MAX_TOTAL = 40_000_000
OFFLINE_BOOTSTRAP = """
import runpy, sys
def deny_network(event, args):
    if event in {'socket.connect', 'socket.getaddrinfo', 'socket.bind'}:
        raise PermissionError('MAIN_OFFLINE_SMOKE_NETWORK_DISABLED')
sys.addaudithook(deny_network)
sys.argv = ['investigate_report', *sys.argv[1:]]
runpy.run_module('scripts.investigate_report', run_name='__main__')
"""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def source_files(root: Path) -> list[Path]:
    paths = [root / name for name in ROOT_FILES if (root / name).is_file()]
    paths.extend(root.glob("*.md"))
    paths.extend(root.glob("requirements-*.lock"))
    for directory in SOURCE_DIRS:
        base = root / directory
        if not base.exists():
            continue
        for current, dirs, files in os.walk(base, followlinks=False):
            dirs[:] = [name for name in dirs if name not in SKIP_DIRS
                       and not (Path(current) / name).is_symlink()
                       and not (Path(current) / name).is_junction()]
            paths.extend(Path(current) / name for name in files
                         if Path(name).suffix in SUFFIXES and not name.startswith(".env"))
    checked = []
    for path in sorted(set(paths)):
        if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError(f"Source path is not a regular workspace file: {path.name}")
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        checked.append(path)
    return checked


def source_contents(root: Path) -> dict[str, bytes]:
    contents, total = {}, 0
    for path in source_files(root):
        with path.open("rb") as stream:
            data = stream.read(MAX_FILE + 1)
        if data.startswith(b"SQLite format 3\x00"):
            continue
        total += len(data)
        if len(data) > MAX_FILE or total > MAX_TOTAL:
            raise ValueError("Source snapshot exceeds its size budget")
        contents[path.relative_to(root).as_posix()] = data
    return contents


def freeze_sources(root: Path, destination: Path) -> dict:
    """Copy uncommitted files too, then reject an unstable source collection."""
    if destination.exists():
        raise ValueError("Use a new snapshot directory; existing snapshots are preserved")
    first = source_contents(root)
    if not first:
        raise ValueError("No local sources were found")
    destination.mkdir(parents=True)
    for name, data in first.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    second = source_contents(root)
    first_hashes = {name: digest(data) for name, data in first.items()}
    second_hashes = {name: digest(data) for name, data in second.items()}
    changed = sorted(name for name in first_hashes.keys() | second_hashes.keys()
                     if first_hashes.get(name) != second_hashes.get(name))
    return {"status": "SOURCE_CHANGED" if changed else "FROZEN",
            "files": first_hashes, "changed_during_copy": changed,
            "snapshot_hash": digest(json.dumps(first_hashes, sort_keys=True).encode()),
            "bytes": sum(map(len, first.values()))}


def environment_check(root: Path) -> dict:
    packages = {}
    for name in ("streamlit", "pydantic", "openai", "httpx", "python-dotenv", "Pillow", "pytest", "nvidia-nat"):
        try:
            packages[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            packages[name] = None
    configured = bool(os.environ.get("NVIDIA_API_KEY", "").strip())
    env_file = root / ".env"
    if not configured and env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            key, separator, value = line.strip().removeprefix("export ").partition("=")
            if separator and key.strip() == "NVIDIA_API_KEY":
                candidate = value.strip().strip("\"'")
                configured = bool(candidate and not candidate.startswith("your_"))
                break
    return {"python": sys.version.split()[0], "python_312": sys.version_info[:2] == (3, 12),
            "packages": packages, "nvidia_key_present": configured,
            "credentials_validated": False, "network_probe": "NOT_RUN"}


def offline_environment(snapshot: Path) -> dict[str, str]:
    # The fixture CLI receives no provider credentials or project source overrides.
    names = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT",
             "LANG", "LC_ALL"}
    env = {key: value for key, value in os.environ.items() if key.upper() in names}
    env.update(PYTHONIOENCODING="utf-8", PYTHONDONTWRITEBYTECODE="1",
               GIT_CEILING_DIRECTORIES=str(snapshot.parent), GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull)
    return env


def assess_smoke(result: dict, *, memory_enabled: bool) -> dict[str, bool]:
    return {
        "incident_correlated": result.get("correlation") == "EXACT_ID",
        "caller_candidate": result.get("route") == "WORK_CANDIDATE",
        "actual_422": result.get("observed_status") == 422,
        "reported_500_rejected": result.get("claim_status") == "CONTRADICTED",
        "stored": result.get("persistence", {}).get("status") == "SAVED",
        "no_model_calls": result.get("model_calls") == 0,
        "no_original_fix_claim": result.get("fix_verified") is False,
        "no_change_execution": "change" not in result,
        "memory_mode": bool(result.get("memory_search", {}).get("status"))
        and result["memory_search"]["status"] != "DISABLED"
        if memory_enabled else result.get("memory_search", {}).get("status") == "DISABLED",
    }


def saved_run_check(path: Path, result: dict) -> dict[str, bool]:
    checks = {"database_contains_run": False, "saved_memory_mode": False}
    if not path.is_file():
        return checks
    try:
        with sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT project_id, incident_id, record_json FROM runs WHERE run_id=?",
                (result.get("run_id"),),
            ).fetchone()
        if not row:
            return checks
        checks["database_contains_run"] = row[:2] == (result.get("project_id"), result.get("incident_id"))
        record = json.loads(row[2])
        checks["saved_memory_mode"] = record.get("memory_search", {}).get("status") == result.get("memory_search", {}).get("status")
    except (sqlite3.Error, ValueError, OSError):
        pass
    return checks


def run_offline_smoke(snapshot: Path, python: str) -> list[dict]:
    profile = snapshot / "examples/parallel_b/ledger_demo/profile.json"
    if not profile.is_file():
        return [{"status": "BLOCKED", "reason": "B project fixture is not available"}]
    results = []
    for enabled in (False, True):
        name = "memory_on" if enabled else "memory_off"
        target = snapshot / "output/main-smoke" / name
        target.mkdir(parents=True)
        result_path = target / "incident.json"
        argv = [python, "-B", "-c", OFFLINE_BOOTSTRAP, "--profile", str(profile),
                "--report", "계정 생성이 안 돼요. HTTP 500 requestId=ledger-001",
                "--service", "ledger-api", "--environment", "test",
                "--occurred-at", "2026-09-28T10:00:00+09:00", "--max-seconds", "30",
                "--db", str(target / "incidents.sqlite3"), "--output", str(result_path)]
        if not enabled:
            argv.append("--no-memory")
        entry = {"case": name, "data_origin": "SYNTHETIC_REGISTERED_PROFILE",
                 "network": "PYTHON_AUDIT_BLOCKED", "database": str(target / "incidents.sqlite3")}
        try:
            completed = subprocess.run(argv, cwd=snapshot, env=offline_environment(snapshot),
                                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=45)
            (target / "stderr.txt").write_text(completed.stderr, encoding="utf-8")
            entry["exit_code"] = completed.returncode
            if completed.returncode != 0 or not result_path.is_file():
                entry.update(status="FAILED", reason="Offline CLI did not produce a successful result",
                             stderr_file=str(target / "stderr.txt"))
            else:
                result = json.loads(result_path.read_text(encoding="utf-8"))
                checks = assess_smoke(result, memory_enabled=enabled)
                checks.update(saved_run_check(target / "incidents.sqlite3", result))
                entry.update(status="PASSED" if all(checks.values()) else "FAILED",
                             checks=checks, result_file=str(result_path),
                             route=result.get("route"), run_status=result.get("run_status"),
                             incident_id=result.get("incident_id"), run_id=result.get("run_id"))
        except subprocess.TimeoutExpired:
            entry.update(status="FAILED", reason="Offline CLI exceeded 45 seconds")
        except (OSError, ValueError) as exc:
            entry.update(status="FAILED", reason=type(exc).__name__)
        results.append(entry)
    return results


def prepare(root: Path, output: Path, *, smoke: bool = False) -> dict:
    root, output = root.resolve(), output.resolve()
    if not output.is_relative_to(root / "output"):
        raise ValueError("Foundation outputs must stay within this workspace's output directory")
    output.mkdir(parents=True, exist_ok=True)
    run_dir = output / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    run_dir.mkdir()
    report = {"scope": "MAIN_FOUNDATION_NOT_FINAL_INTEGRATION",
              "created_at": datetime.now(timezone.utc).isoformat(), "run_directory": str(run_dir),
              "environment": environment_check(root), "live_validation": "NOT_RUN",
              "operating_project_validation": "NOT_RUN", "original_application": "NOT_RUN"}
    try:
        snapshot = run_dir / "snapshot"
        frozen = freeze_sources(root, snapshot)
        report["snapshot"] = frozen
        report["status"] = frozen["status"]
        if frozen["status"] == "FROZEN" and smoke:
            cases = run_offline_smoke(snapshot, sys.executable)
            report["offline_smoke"] = cases
            report["status"] = "OFFLINE_SMOKE_PASSED" if cases and all(item["status"] == "PASSED" for item in cases) else "OFFLINE_SMOKE_FAILED"
        report["snapshot_directory"] = str(snapshot)
    except (OSError, ValueError) as exc:
        report.update(status="BLOCKED", reason=type(exc).__name__)
    (run_dir / "foundation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    report["report_file"] = str(run_dir / "foundation.json")
    return report


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "output/main-foundation")
    parser.add_argument("--smoke", action="store_true", help="Run the frozen synthetic profile CLI with Python network access blocked")
    args = parser.parse_args()
    try:
        result = prepare(ROOT, args.output, smoke=args.smoke)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    summary = {key: value for key, value in result.items() if key != "snapshot"}
    if "snapshot" in result:
        frozen = result["snapshot"]
        summary["snapshot"] = {key: value for key, value in frozen.items() if key != "files"}
        summary["snapshot"]["file_count"] = len(frozen["files"])
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if result["status"] in {"FROZEN", "OFFLINE_SMOKE_PASSED"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
