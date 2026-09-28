"""Small local service bridge between intake, checked A2 work and later replies."""

from copy import deepcopy

from .change_worker import prepare_change
from .incident_memory import IncidentStore, db_location
from . import report_agent
from .report_contract import action_preference
from .change_policy import SUPPORTED_WORK_ROLE


def preparation_blockers(result: dict) -> list[str]:
    """UI/CLI entry gate; the registered worker repeats this on stored facts."""
    aggregate = result.get("log_scope", {}).get("aggregate", {})
    preference = result.get("requested_action") or result.get("session", {}).get("action_preference")
    if not preference:
        session = result.get("session", {})
        preference = action_preference("\n".join([session.get("text", ""), *session.get("answers", [])]))
    return [reason for rejected, reason in (
        (result.get("persistence", {}).get("status") not in {"SAVED", "ALREADY_SAVED"}, "조사 기록 저장 필요"),
        (result.get("project_id") != "tracebridge-seed-signup", "등록된 수정 대상 없음"),
        (result.get("route") != "WORK_CANDIDATE" or result.get("run_status") != "COMPLETED", "확정된 작업 후보 아님"),
        (result.get("correlation") != "EXACT_ID", "같은 사건 연결 미확정"),
        (aggregate.get("complete") is not True, "관측 수집 불완전"),
        (bool(aggregate.get("conflicts") or aggregate.get("conflicting_trace_ids")), "현재 관측 충돌"),
        (preference == "INVESTIGATE_ONLY", "사용자가 조사만 요청함"),
        (result.get("work_role") != SUPPORTED_WORK_ROLE, "지원하는 수정 작업자 없음"),
        (bool(result.get("change")), "이미 확보한 작업 결과"),
    ) if rejected]


def prepare_submission(result: dict, *, db_path=None, live: bool = False, proposer=None, project_profile=None, policy_id=None) -> dict:
    """Prepare only a saved registered candidate; the worker rechecks its policy."""
    if project_profile is not None:
        from .project_repair import prepare_project_change
        return prepare_project_change(result, project_profile, db_path=db_path, live=live, proposer=proposer, policy_id=policy_id)
    blockers = preparation_blockers(result)
    if blockers:
        raise ValueError("수정안을 준비할 수 없습니다: " + ", ".join(blockers))
    with IncidentStore(db_path) as store:
        changes = store.list_changes(result["project_id"], result["incident_id"])
        if changes:
            job = changes[0]
            return {"job": job, "run": store.get_run(result["project_id"], job["result_run_id"]),
                    "persistence": {"status": "ALREADY_SAVED", "work_id": job["work_id"]}}
    return prepare_change(result["project_id"], result["run_id"], db_path=db_location(db_path), live=live, proposer=proposer)


def auto_prepare_submission(result: dict, *, enabled: bool, db_path=None, live: bool = False) -> dict | None:
    text = "\n".join([result.get("session", {}).get("text", ""), *result.get("session", {}).get("answers", [])])
    preference = result.get("requested_action", result.get("session", {}).get("action_preference"))
    read_only = (preference or action_preference(text)) == "INVESTIGATE_ONLY"
    if not enabled or not live or read_only or preparation_blockers(result):
        return None
    with IncidentStore(db_path) as store:
        if store.list_changes(result["project_id"], result["incident_id"]):
            return None
    return prepare_submission(result, db_path=db_path, live=True)


def follow_up_service(previous_result: dict, answer: str, **kwargs) -> dict:
    """A work-result run may sit between the displayed intake and the next answer."""
    previous = deepcopy(previous_result)
    if previous.get("persistence", {}).get("status") in {"SAVED", "ALREADY_SAVED"}:
        with IncidentStore(kwargs.get("db_path")) as store:
            incident = store.get_incident(previous["project_id"], previous["incident_id"])
            latest = store.get_run(previous["project_id"], incident["latest_run_id"])
        if latest["run_id"] != previous["run_id"]:
            if latest.get("change", {}).get("source_run_id") != previous["run_id"]:
                raise ValueError("이 사건에 더 최근 답변이 있습니다. 최신 기록을 불러와 주세요")
            previous["revision"] = latest["revision"]
    return report_agent.follow_up_submission(previous, answer, **kwargs)
