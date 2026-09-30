"""Build a reviewable DRAFT archive without DBs, secrets or local output."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL = [
    ".env.example",
    ".gitignore",
    ".gitattributes",
    ".streamlit/config.toml",
    "README.md",
    "app.py",
    "nat_workflow.yml",
    "pyproject.toml",
    "requirements.txt",
    "alembic.ini",
    "deploy/.env.example",
    "deploy/app.env.example",
]
DIRECTORIES = ["tracebridge", "scripts", "tests", "skills", "docs", "pages", "examples", "deploy", ".github"]
SOURCE_SUFFIXES = {".py", ".md", ".json", ".jsonl", ".yml", ".yaml", ".toml", ".kts", ".kt", ".go", ".mod", ".log", ".lock"}
EXCLUDED_PARTS = {"tmp", "temp", "output", "generated", "submission", "__pycache__", ".pytest_cache",
                  ".git", ".venv", ".uv-cache", "node_modules", ".codex", ".agents", ".codex-remote-attachments"}
SECRET_NAMES = re.compile(r"^(?:\.env(?!\.example$).*|credentials?|secrets?|tokens?|passwords?|id_rsa|id_ed25519)(?:\..*)?$", re.I)


def runtime_artifact(path: Path, root: Path = ROOT) -> bool:
    if any(part in EXCLUDED_PARTS or part.startswith("pytest-cache-files-") for part in path.relative_to(root).parts):
        return True
    if SECRET_NAMES.fullmatch(path.name) or path.suffix.lower() in {".pem", ".key", ".pfx", ".p12"}:
        return True
    if path.name.startswith(".") and path.name not in {".env.example", ".gitignore", ".gitattributes"}:
        return True
    if re.search(r"\.(?:db|sqlite3?|tmp|temp)(?:$|[-.])", path.name, re.I):
        return True
    # A configured DB with a source-looking extension must also stay out.
    with path.open("rb") as stream:
        return stream.read(16) == b"SQLite format 3\x00"


def _safe_source(path: Path, root: Path) -> bool:
    if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        return False
    if any(parent.is_symlink() for parent in path.parents if parent != root and parent.is_relative_to(root)):
        return False
    relative = path.relative_to(root)
    permitted_image = path.suffix.lower() == ".png" and (
        relative.parts[:3] == ("examples", "evaluation", "assets")
        or relative.parts[:3] == ("docs", "validation", "assets") and len(relative.parts) > 3
        and relative.parts[3] in {"local-gui", "basic-service", "project-connection"}
    )
    permitted_deploy = relative.parts[0] == "deploy" and (
        path.name in {"Dockerfile", "Caddyfile", "Caddyfile.control-plane"}
        or path.suffix.lower() in {".conf", ".template"})
    return (path.suffix.lower() in SOURCE_SUFFIXES or permitted_image or permitted_deploy) and not runtime_artifact(path, root)


def create_package(team_name: str | None = None, *, output_dir: str | Path | None = None,
                   root: str | Path | None = None) -> Path:
    """Preserve the old team_name call; produce only isolated drafts."""
    if team_name is not None and (not team_name or not re.fullmatch(r"[\w가-힣 -]{1,40}", team_name)):
        raise ValueError("Team name must contain only letters, numbers, Korean, spaces, _ or -")
    root = Path(ROOT if root is None else root).resolve()
    target_dir = Path(output_dir) if output_dir is not None else root / "output/parallel-d/packages"
    if not target_dir.resolve().is_relative_to(root):
        raise ValueError("Draft output must remain in the workspace")
    target_dir.mkdir(parents=True, exist_ok=True)
    tag = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
    destination = target_dir / ("TraceBridge_DRAFT_" + (team_name + "_" if team_name else "") + tag + ".zip")
    sources = {root / name for name in TOP_LEVEL if (root / name).is_file()}
    sources.update(root.glob("requirements-*.lock"))
    for directory in DIRECTORIES:
        sources.update(path for path in (root / directory).rglob("*") if _safe_source(path, root))
    sources = {path for path in sources if not path.is_symlink()
               and path.resolve().is_relative_to(root) and not runtime_artifact(path, root)}
    manifest = {"artifact_status": "DRAFT", "team_name": team_name,
                "created_at": datetime.now(timezone.utc).isoformat(), "fresh_environment_verified": False,
                "external_submission_performed": False, "source_changed_during_packaging": False, "files": {},
                "verification_documents": [name for name in ("docs/current-state.md", "docs/validation/basic-service.md",
                    "docs/validation/basic-service.json", "docs/validation/project-connection.md",
                    "docs/validation/project-connection.json", "docs/validation/simple-project.md",
                    "docs/validation/simple-project.json", "docs/validation/knowledge-review.md",
                    "docs/validation/knowledge-review.json", "docs/validation/ci-portability.md",
                    "docs/validation/ci-portability.json", "docs/README.md") if (root / name).is_file()],
                "limitations": ["Review current scope and dated evidence in docs/current-state.md and docs/README.md",
                    "No runtime DB, secret, prior evaluation result or local output is included",
                    "New environment CLI/UI/restart verification remains a main integration gate"]}
    with ZipFile(destination, "x", compression=ZIP_DEFLATED) as archive:
        for path in sorted(sources):
            name = path.relative_to(root).as_posix()
            data = path.read_bytes()
            manifest["files"][name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            archive.writestr(name, data)
        manifest["source_changed_during_packaging"] = any(
            not (root / name).is_file() or hashlib.sha256((root / name).read_bytes()).hexdigest() != item["sha256"]
            for name, item in manifest["files"].items())
        archive.writestr("DRAFT_PACKAGE_MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        archive.writestr("DRAFT_NOTICE.md", "# DRAFT — 제출 전 검토용\n\n외부 제출을 수행하지 않은 검토용 묶음입니다. "
                         "현재 검증 범위는 docs/current-state.md·docs/README.md와 DRAFT_PACKAGE_MANIFEST.json을 확인하세요. "
                         "평가·매뉴얼 자료는 출처가 붙은 합성 예시이며 실제 사건 해결 성능을 뜻하지 않습니다.\n")
    destination.with_suffix(".manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return destination


def main(argv=None):
    parser = argparse.ArgumentParser(description="Create a DRAFT package; no submission or deployment")
    parser.add_argument("team_name", nargs="?", help="Exact team name only when already known")
    parser.add_argument("--output-dir", default=None)
    args = parser.parse_args(argv)
    package = create_package(args.team_name, output_dir=args.output_dir)
    print(f"DRAFT created: {package} ({package.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
