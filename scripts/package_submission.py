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
    "README.md",
    "DESIGN.md",
    "SUBMISSION_DRAFT.md",
    "app.py",
    "nat_workflow.yml",
    "pyproject.toml",
    "requirements.txt",
]
DIRECTORIES = ["tracebridge", "scripts", "tests", "skills"]


def create_package(team_name: str) -> Path:
    if not team_name or not re.fullmatch(r"[\w가-힣 -]{1,40}", team_name):
        raise ValueError("Team name must contain only letters, numbers, Korean, spaces, _ or -")
    destination = ROOT / "submission" / f"NVIDIA 해커톤_{team_name}_TraceBridge.zip"
    destination.parent.mkdir(exist_ok=True)
    sources = [ROOT / name for name in TOP_LEVEL]
    for directory in DIRECTORIES:
        sources.extend(path for path in (ROOT / directory).rglob("*") if path.is_file() and path.suffix in {".py", ".md"})
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
