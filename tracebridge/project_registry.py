"""Owner-managed local registrations. Reporter/model input never calls this API."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
import re
import time
from uuid import uuid4

from .project_profile import ProjectProfile, load_project_profile

ROOT = Path(__file__).resolve().parents[1]


def registry_directory() -> Path:
    return Path(os.getenv("TRACEBRIDGE_PROJECT_REGISTRY", str(ROOT / "output/project-profiles"))).resolve()


def profile_data(profile: ProjectProfile) -> dict:
    """Absolute local serialization preserves references when moving a profile."""
    def version(value):
        result = {"method": value.method, "field": value.field}
        if value.path:
            result["path"] = str(value.path)
        return result
    data = {"project_id": profile.project_id, "service": profile.service, "environment": profile.environment,
        "root": str(profile.root), "code_roots": [str(path) for path in profile.code_roots],
        "repositories": [{"id": item.id, "service": item.service, "root": str(item.root), "code_roots": [str(path) for path in item.code_roots],
                          **({"git_root": str(item.git_root)} if item.git_root else {})} for item in profile.repositories],
        "log_sources": [{"id": item.id, "path": str(item.path), "format": item.format,
            **({"timezone": item.timezone} if item.timezone else {}), **({"service": item.service} if item.service else {})} for item in profile.log_sources],
        "policy_refs": list(profile.policy_refs), "version_observation": version(profile.version_observation)}
    for name in ("openapi_path", "dto_path", "caller_evidence_path"):
        if getattr(profile, name):
            data[name] = str(getattr(profile, name))
    if profile.health_url:
        data["health_url"] = profile.health_url
    if profile.display_name:
        data["display_name"] = profile.display_name
    if profile.services:
        data["services"] = [{"id": item.id, "log_source_ids": list(item.log_source_ids), "version_observation": version(item.version_observation),
            **({"health_url": item.health_url} if item.health_url else {}),
            **{name: str(getattr(item, name)) for name in ("openapi_path", "dto_path", "caller_evidence_path") if getattr(item, name)}} for item in profile.services]
    return data


def list_profiles(directory: Path | None = None) -> tuple[list[ProjectProfile], list[str]]:
    directory = directory or registry_directory()
    profiles, errors = [], []
    if not directory.is_dir():
        return profiles, errors
    for path in sorted(directory.glob("*.json"))[:32]:
        if path.name.startswith(".") or path.is_symlink():
            continue
        try:
            profiles.append(load_project_profile(path))
        except (OSError, ValueError):
            errors.append(path.name)
    return profiles, errors


def _replace_registration(temporary: Path, destination: Path, *, before_replace=None) -> None:
    """Keep atomic replacement when Windows readers briefly deny deletion."""
    for attempt in range(8):
        if before_replace is not None:
            before_replace()
        try:
            temporary.replace(destination)
            return
        except PermissionError as exc:
            if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))


def save_profile(data: dict, directory: Path | None = None, *, expected_sha256: str | None = None) -> Path:
    """Validate before replacing a registration; no target files are changed."""
    project_id = data.get("project_id")
    if not isinstance(project_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", project_id):
        raise ValueError("프로젝트 ID는 영문·숫자·밑줄·하이픈 1~64자입니다")
    directory = (directory or registry_directory()).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / (project_id + ".json")
    if destination.is_symlink():
        raise ValueError("프로젝트 설정의 링크 파일을 교체할 수 없습니다")
    def unchanged():
        if expected_sha256 is not None and (not destination.is_file()
                or hashlib.sha256(destination.read_bytes()).hexdigest() != expected_sha256):
            raise ValueError("프로젝트 연결 설정이 변경됐습니다. 다시 불러온 뒤 저장해 주세요")
    unchanged()
    raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    if len(raw) > 64_000:
        raise ValueError("프로젝트 설정은 64 KB 이하여야 합니다")
    temporary = directory / (".registration-" + uuid4().hex + ".json")
    try:
        with temporary.open("xb") as stream:
            stream.write(raw)
        load_project_profile(temporary)
        _replace_registration(temporary, destination, before_replace=unchanged)
    finally:
        temporary.unlink(missing_ok=True)
    return destination
