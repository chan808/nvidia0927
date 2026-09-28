"""Create a single-file source portfolio without local secrets or build artifacts."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
TOP_LEVEL = [
    ".env.example",
    ".gitignore",
    ".gitattributes",
    ".streamlit/config.toml",
    "README.md",
    "DESIGN.md",
    "SUBMISSION_DRAFT.md",
    "SKILL_SCAN_REPORT.md",
    "app.py",
    "nat_workflow.yml",
    "pyproject.toml",
    "requirements.txt",
]
DIRECTORIES = ["tracebridge", "scripts", "tests", "skills", "docs", "pages", "examples"]
SOURCE_SUFFIXES = {".py", ".md", ".json", ".yml", ".yaml", ".toml", ".kts", ".kt", ".go", ".mod", ".log"}


def runtime_artifact(path: Path) -> bool:
    if any(part in {"tmp", "temp", "output", "generated", "__pycache__", ".pytest_cache"} for part in path.relative_to(ROOT).parts):
        return True
    if re.search(r"\.(?:db|sqlite3?|tmp|temp)(?:$|[-.])", path.name, re.I):
        return True
    # A configured DB with a source-looking extension must also stay out.
    with path.open("rb") as stream:
        return stream.read(16) == b"SQLite format 3\x00"


def create_package(team_name: str) -> Path:
    if not team_name or not re.fullmatch(r"[\w가-힣 -]{1,40}", team_name):
        raise ValueError("Team name must contain only letters, numbers, Korean, spaces, _ or -")
    destination = ROOT / "submission" / f"NVIDIA 해커톤_{team_name}_TraceBridge.zip"
    destination.parent.mkdir(exist_ok=True)
    sources = [ROOT / name for name in TOP_LEVEL]
    for directory in DIRECTORIES:
        sources.extend(
            path for path in (ROOT / directory).rglob("*")
            if path.is_file() and not path.is_symlink() and path.suffix in SOURCE_SUFFIXES
            and path.resolve().is_relative_to(ROOT.resolve())
            and not runtime_artifact(path)
        )
    with ZipFile(destination, "w", compression=ZIP_DEFLATED) as archive:
        for path in sorted(sources):
            archive.write(path, path.relative_to(ROOT).as_posix())
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("team_name", help="Exact team name used by both applicants")
    args = parser.parse_args()
    package = create_package(args.team_name)
    print(f"Created {package} ({package.stat().st_size:,} bytes)")
