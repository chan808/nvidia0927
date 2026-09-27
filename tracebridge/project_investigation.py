"""Investigate a human report against Agolive's current source and optional logs."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from .project_gpt import ProjectGPT, ProposedReport, agolive_openai_settings, evidence_for_gpt
from .project_sources import (
    Evidence,
    agolive_repo_path,
    code_evidence,
    docker_compose_logs,
    log_evidence,
    report_terms,
    redact,
    repository_revision,
    source_tree_dirty,
    stack_profile,
    validate_agolive_repo,
)


def _checked_hypotheses(proposal: ProposedReport, evidence: list[Evidence]) -> list[dict[str, Any]]:
    by_id = {item.id: item for item in evidence}
    checked: list[dict[str, Any]] = []
    for hypothesis in proposal.hypotheses[:3]:
        supporting = list(dict.fromkeys(id_ for id_ in hypothesis.supporting_evidence_ids if id_ in by_id))
        contradicting = list(dict.fromkeys(id_ for id_ in hypothesis.contradicting_evidence_ids if id_ in by_id))
        support_kinds = {by_id[id_].kind for id_ in supporting}
        correlated_log = any(by_id[id_].kind == "log" and by_id[id_].correlated for id_ in supporting)
        if not supporting or support_kinds <= {"profile"}:
            status = "UNVERIFIED"
        elif correlated_log and "code" in support_kinds:
            status = "SUPPORTED_HYPOTHESIS"
        elif "log" in support_kinds:
            status = "LOG_CANDIDATE"
        else:
            status = "CODE_ONLY"
        checked.append({
            "cause": hypothesis.cause,
            "explanation": hypothesis.explanation,
            "status": status,
            "supporting_evidence_ids": supporting,
            "contradicting_evidence_ids": contradicting,
            "verification_step": hypothesis.verification_step,
            "possible_fix": hypothesis.possible_fix,
            "fix_verified": False,
        })
    return checked


def investigate_agolive_report(
    report: str,
    *,
    repo: Path | None = None,
    provided_logs: str = "",
    include_docker_logs: bool = False,
    since_minutes: int = 30,
    use_gpt: bool = False,
    gpt: ProjectGPT | None = None,
) -> dict[str, Any]:
    if not report or not report.strip():
        raise ValueError("오류 제보를 입력하세요")
    if len(report) > 4000:
        raise ValueError("오류 제보는 4000자 이하여야 합니다")
    project = validate_agolive_repo(repo or agolive_repo_path())
    revision = repository_revision(project)
    dirty = source_tree_dirty(project)
    notes: list[str] = ["로컬 저장소 버전만 확인했습니다. 실제 배포 버전은 별도로 확인해야 합니다."]
    if dirty:
        notes.append("로컬 소스에 미커밋 변경이 있어 HEAD 커밋과 현재 검색한 코드가 다를 수 있습니다")
    profile = stack_profile(project)
    key, model, key_source = agolive_openai_settings(project) if use_gpt else (None, "", "not accessed")
    model_client = gpt
    if use_gpt and model_client is None and key:
        model_client = ProjectGPT(key, model)
    if use_gpt and model_client is None:
        notes.append("Agolive OpenAI API 키를 찾지 못해 코드 검색만 수행했습니다")

    services: list[str] = []
    terms = report_terms(redact(report))
    if use_gpt and model_client:
        try:
            plan = model_client.search_plan(report)
            services = plan.services
            terms = list(dict.fromkeys([*plan.terms, *terms]))[:12]
        except Exception as exc:
            notes.append(f"GPT 검색어 생성 실패: {type(exc).__name__}; 제보의 명시적 단어로 검색했습니다")

    code = code_evidence(project, terms, services)
    evidence: list[Evidence] = [*profile]
    log_notes: list[str] = []
    if provided_logs.strip():
        logs, current_notes = log_evidence(provided_logs, report)
        log_notes.extend(current_notes)
        evidence.extend(logs)
    if include_docker_logs:
        docker_text, error = docker_compose_logs(project, since_minutes=since_minutes)
        if error:
            notes.append(error)
        else:
            logs, current_notes = log_evidence(docker_text, report, source="local-docker")
            log_notes.extend(current_notes)
            first_log_number = sum(1 for item in evidence if item.kind == "log")
            evidence.extend(replace(item, id=f"L{first_log_number + index}") for index, item in enumerate(logs, 1))
    notes.extend(dict.fromkeys(log_notes))
    if not any(item.kind == "log" for item in evidence):
        notes.append("연결된 오류 로그가 없어 실행 시점의 증상과 원인을 확인할 수 없습니다")
    if not code:
        notes.append("제보와 연결되는 코드 줄을 찾지 못했습니다")
    evidence.extend(code)

    hypotheses: list[dict[str, Any]] = []
    missing_information: list[str] = []
    next_steps: list[str] = []
    gpt_used = False
    if use_gpt and model_client and (code or any(item.kind == "log" for item in evidence)):
        try:
            proposal = model_client.hypotheses(report, evidence, revision)
            hypotheses = _checked_hypotheses(proposal, evidence_for_gpt(evidence))
            missing_information = proposal.missing_information[:6]
            next_steps = proposal.next_steps[:6]
            gpt_used = True
        except Exception as exc:
            notes.append(f"GPT 가설 생성 실패: {type(exc).__name__}; 근거 목록만 표시합니다")
    if not any(item.kind == "log" for item in evidence):
        missing_information.append("실패한 요청의 requestId 또는 시각·환경을 포함한 백엔드 로그")
    if not next_steps:
        next_steps = ["제보 발생 시각·환경·requestId와 해당 서비스 오류 로그를 추가하세요."]

    return {
        "project": "Agolive",
        "repository": str(project),
        "repository_revision": revision,
        "source_tree_dirty": dirty,
        "deployed_revision": None,
        "report": redact(report),
        "evidence": [item.to_dict() for item in evidence],
        "hypotheses": hypotheses,
        "missing_information": list(dict.fromkeys(missing_information)),
        "next_steps": next_steps,
        "notes": notes,
        "search_terms": terms,
        "searched_services": services,
        "gpt_used": gpt_used,
        "gpt_model": model if gpt_used else None,
        "gpt_key_source": key_source if gpt_used else None,
        "fix_applied": False,
    }
