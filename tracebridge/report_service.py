"""Small local service bridge between intake, checked A2 work and later replies."""

from copy import deepcopy

from .change_worker import prepare_change
from .incident_memory import IncidentStore, db_location
from . import report_agent
from .report_contract import action_preference


def prepare_submission(result: dict, *, db_path=None, live: bool = False, proposer=None) -> dict:
    """Prepare only a saved registered candidate; the worker rechecks its policy."""
    if result.get("persistence", {}).get("status") not in {"SAVED", "ALREADY_SAVED"}:
        raise ValueError("현재 조사 기록을 저장한 뒤 수정안을 준비해 주세요")
    if result.get("project_id") != "tracebridge-seed-signup" or result.get("route") != "WORK_CANDIDATE":
        raise ValueError("등록된 개발 프로젝트의 작업 후보만 수정안을 준비할 수 있습니다")
    return prepare_change(result["project_id"], result["run_id"], db_path=db_location(db_path), live=live, proposer=proposer)


def auto_prepare_submission(result: dict, *, enabled: bool, db_path=None, live: bool = False) -> dict | None:
    text = "\n".join([result.get("session", {}).get("text", ""), *result.get("session", {}).get("answers", [])])
    preference = result.get("requested_action", result.get("session", {}).get("action_preference"))
    read_only = (preference or action_preference(text)) == "INVESTIGATE_ONLY"
    if (not enabled or not live or read_only or result.get("project_id") != "tracebridge-seed-signup"
            or result.get("route") != "WORK_CANDIDATE" or result.get("run_status") != "COMPLETED"
            or result.get("persistence", {}).get("status") not in {"SAVED", "ALREADY_SAVED"}):
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
