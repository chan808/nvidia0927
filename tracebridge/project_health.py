"""Bounded registration diagnostics; readiness is not proof of a resolved incident."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from .project_profile import ProjectProfile, select_project_service, read_project_logs, observe_project_version
from .project_sources import SOURCE_SUFFIXES, repository_revision, source_tree_dirty


def inspect_project(profile: ProjectProfile) -> dict:
    repositories = [{"id": item.id, "service": item.service, "root": item.root, "roots": item.code_roots, "git_root": item.git_root}
                    for item in profile.repositories]
    if profile.code_roots:
        repositories.insert(0, {"id": "primary", "service": profile.service, "root": profile.root, "roots": profile.code_roots})
    rows = []
    for item in repositories:
        roots = item.pop("roots")
        metadata_root = item.pop("git_root", None) or item["root"]
        problems = ["code_root_unavailable" for root in roots if not root.is_dir()]
        rows.append({**item, "root": str(item["root"]), "code_roots": [str(root) for root in roots],
            "status": "UNAVAILABLE" if problems else "READY", "problems": problems,
            "local_head": repository_revision(metadata_root),
            "dirty": source_tree_dirty(metadata_root, code_roots=roots) if roots else None})
    services = []
    for service in [item.id for item in profile.services] or [profile.service]:
        selected = select_project_service(profile, service)
        logs = read_project_logs(selected, max_bytes=300_000, max_records=200)
        version = observe_project_version(selected)
        services.append({"id": service, "environment": profile.environment,
            "log_sources": [{"id": item.id, "path": str(item.path), "exists": item.path.is_file(), "format": item.format} for item in selected.log_sources],
            "log_status": "READY" if logs["complete"] and logs["events"] else "DEGRADED",
            "observed_requests": len(logs["events"]), "limitations": logs["limitations"],
            "contract_status": "REGISTERED" if selected.openapi_path and selected.openapi_path.is_file() else "UNAVAILABLE",
            "runtime_version_status": version["status"], "runtime_version": version.get("runtime_version")})
    ready = bool(rows) and all(row["status"] == "READY" for row in rows)
    return {"project_id": profile.project_id, "environment": profile.environment,
        "status": "READY" if ready and all(row["log_status"] == "READY" for row in services) else "DEGRADED" if ready else "UNAVAILABLE",
        "repositories": rows, "services": services, "source_extensions": sorted(SOURCE_SUFFIXES),
        "repair_policy_ids": list(profile.policy_refs),
        "limitations": ["등록 경로와 현재 자료의 접근 검사입니다. 사건 해결·실행 버전 대응의 증거가 아닙니다."]}
