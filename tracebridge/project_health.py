"""Bounded registration diagnostics; readiness is not proof of a resolved incident."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from datetime import datetime, timezone

import httpx
import time

from .project_profile import ProjectProfile, select_project_service, read_project_logs, observe_project_version
from .project_sources import SOURCE_SUFFIXES, repository_revision, source_tree_dirty


def observe_service_health(url: str | None) -> dict:
    result = {"status": "NOT_REGISTERED", "checked_at": datetime.now(timezone.utc).isoformat(), "http_status": None,
        "reason": "NO_HEALTH_URL"}
    if url is None:
        return result
    from .project_profile import _health_url
    _health_url(url)
    try:
        deadline = time.monotonic() + 2
        with httpx.Client(timeout=2, follow_redirects=False, trust_env=False) as client:
            with client.stream("GET", url) as response:
                result["http_status"] = response.status_code
                code = response.status_code
                result.update(status="REACHABLE" if 200 <= code < 300 else "DOWN" if code >= 500 else "UNKNOWN",
                    reason="HTTP_SUCCESS" if 200 <= code < 300 else "HTTP_SERVER_ERROR" if code >= 500
                    else "HEALTH_AUTH_REQUIRED" if code in {401, 403} else "HEALTH_ENDPOINT_NOT_FOUND" if code == 404
                    else "HEALTH_REDIRECT_NOT_FOLLOWED" if 300 <= code < 400 else "HEALTH_STATUS_UNEXPECTED")
                if 200 <= code < 300 and "json" in response.headers.get("content-type", ""):
                    raw = bytearray()
                    for chunk in response.iter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 16_000 or time.monotonic() > deadline:
                            result["status"] = "UNKNOWN"
                            result["reason"] = "HEALTH_RESPONSE_LIMIT"
                            return result
                    import json
                    data = json.loads(raw)
                    if isinstance(data, dict) and isinstance(data.get("status"), str) and data["status"] in {"UP", "DOWN", "OUT_OF_SERVICE", "UNKNOWN"}:
                        result["status"] = data["status"]
                        result["reason"] = "REPORTED_HEALTH_STATUS"
    except httpx.HTTPError:
        result.update(status="UNREACHABLE", reason="HEALTH_CONNECTION_FAILED")
    except ValueError:
        result.update(status="UNKNOWN", reason="HEALTH_RESPONSE_INVALID")
    return result


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
        logs = read_project_logs(selected, max_bytes=4_000_000, max_records=50_000)
        version = observe_project_version(selected)
        health = observe_service_health(selected.health_url)
        services.append({"id": service, "environment": profile.environment,
            "log_sources": [{"id": item.id, "path": str(item.path), "exists": item.path.is_file(), "format": item.format} for item in selected.log_sources],
            "log_status": "READY" if logs["complete"] and logs["events"] else "DEGRADED",
            "observed_requests": len(logs["events"]), "limitations": logs["limitations"],
            "contract_status": "REGISTERED" if selected.openapi_path and selected.openapi_path.is_file() else "UNAVAILABLE",
            "dto_registered": bool(selected.dto_path and selected.dto_path.is_file()),
            "caller_registered": bool(selected.caller_evidence_path and selected.caller_evidence_path.is_file()),
            "service_status": health["status"], "health": health,
            "runtime_version_status": version["status"], "runtime_version": version.get("runtime_version")})
    ready = bool(rows) and all(row["status"] == "READY" for row in rows)
    return {"project_id": profile.project_id, "environment": profile.environment,
        "status": "READY" if ready and all(row["log_status"] == "READY" and row["service_status"] in {"NOT_REGISTERED", "UP", "REACHABLE"} for row in services) else "DEGRADED" if ready else "UNAVAILABLE",
        "repositories": rows, "services": services, "source_extensions": sorted(SOURCE_SUFFIXES),
        "repair_policy_ids": list(profile.policy_refs),
        "limitations": ["등록 경로와 현재 자료의 접근 검사입니다. 사건 해결·실행 버전 대응의 증거가 아닙니다."]}


def connection_checks(health):
    """Publish bounded observations, without local paths or response contents."""
    result = {}
    for service in health["services"]:
        repositories = [row for row in health["repositories"] if row["service"] == service["id"] and row["code_roots"]]
        result[service["id"]] = {
            "code_status": "READY" if repositories and all(row["status"] == "READY" for row in repositories) else "UNAVAILABLE",
            "log_status": service["log_status"], "observed_requests": service["observed_requests"],
            "contract_status": service["contract_status"], "dto_registered": service["dto_registered"],
            "caller_registered": service["caller_registered"], "runtime_version_status": service["runtime_version_status"],
            "http_status": service["health"]["http_status"], "health_reason": service["health"]["reason"],
        }
    return result


def assess_connection(project, service, *, now=None):
    """Explain observation limits. Never infer incident recovery from connectivity."""
    now = now or datetime.now(timezone.utc)
    checked = project.get("health_checked_at")
    try:
        age = (now - datetime.fromisoformat(checked)).total_seconds()
        fresh = -30 <= age <= 120
    except (TypeError, ValueError):
        fresh = False
    online = project.get("online", True)
    observed = project.get("service_health", {}).get(service, "NOT_REGISTERED")
    facts = project.get("connection_checks", {}).get(service, {})
    findings, steps = [], []
    if not online:
        findings.append("PC 실행기가 오프라인입니다. 새 작업은 연결될 때까지 대기합니다.")
        steps.append("등록 PC에서 실행기 연결과 인증 상태를 확인해 주세요.")
    if not fresh:
        findings.append("현재 서비스 상태를 판단할 최신 확인 결과가 없습니다.")
        steps.append("연결 상태를 새로 점검해 주세요.")
    live = observed if online and fresh else "UNKNOWN"
    reason = facts.get("health_reason")
    if online and fresh:
        if live in {"UP", "REACHABLE"}:
            findings.append("등록된 서비스 주소에서 정상 HTTP 응답을 확인했습니다. 개별 기능의 성공 여부는 제보 조사에서 확인합니다.")
        elif live in {"DOWN", "UNREACHABLE", "OUT_OF_SERVICE"}:
            findings.append("등록된 서비스가 정상 상태로 응답하지 않았습니다.")
            steps.append("서비스 기동 상태·포트·서버 로그를 확인해 주세요.")
        elif live == "NOT_REGISTERED":
            findings.append("서비스 상태 확인 주소가 등록되지 않아 실제 가동 여부를 확인할 수 없습니다.")
            steps.append("서비스의 로컬 URL 또는 health endpoint를 등록해 주세요.")
        else:
            explanations = {"HEALTH_AUTH_REQUIRED": "HTTP 응답은 있지만 인증이 필요해 정상 상태를 확인하지 못했습니다.",
                "HEALTH_ENDPOINT_NOT_FOUND": "서버가 응답했지만 등록한 상태 확인 경로가 없습니다.",
                "HEALTH_REDIRECT_NOT_FOLLOWED": "서비스가 다른 주소로 이동시켰습니다. 이동 전 응답만 확인했습니다.",
                "HEALTH_RESPONSE_INVALID": "HTTP 응답의 상태 자료를 해석하지 못했습니다."}
            findings.append(explanations.get(reason, "HTTP 응답만으로 서비스 상태를 확인하지 못했습니다."))
            steps.append("등록한 상태 확인 주소와 응답 형식을 확인해 주세요.")
    if not facts or not online or not fresh:
        findings.append("최신 코드·로그·계약·버전 연결 자료를 확인하지 못했습니다.")
    else:
        if facts["code_status"] != "READY":
            findings.append("이 서비스의 코드 경로를 읽을 수 없습니다.")
            steps.append("이 서비스의 저장소·코드 경로와 접근 권한을 확인해 주세요.")
        if facts["log_status"] != "READY":
            findings.append("같은 요청을 연결할 로그가 없거나 수집 자료가 불완전합니다.")
            steps.append("요청 ID·발생 시각·서비스·환경을 담은 로그 파일을 연결해 주세요.")
        if facts["contract_status"] != "REGISTERED":
            findings.append("OpenAPI 계약 자료가 연결되지 않았습니다.")
            steps.append("API 계약과 필요한 DTO·호출자 자료를 연결해 주세요.")
        if facts["runtime_version_status"] != "OBSERVED":
            findings.append("실제로 실행한 코드 버전을 확인하지 못했습니다.")
            steps.append("실행 버전 JSON 또는 로그의 버전 자료를 등록해 주세요.")
    ready = online and fresh and facts.get("code_status") == "READY" and facts.get("log_status") == "READY" and facts.get("contract_status") == "REGISTERED" and facts.get("runtime_version_status") == "OBSERVED"
    return {"availability": live, "fresh": fresh, "readiness": "READY" if ready else "PARTIAL" if online and fresh and facts.get("code_status") == "READY" else "UNKNOWN",
        "findings": findings, "next_steps": list(dict.fromkeys(steps)), "facts": facts if online and fresh else {}, "checked_at": checked}
