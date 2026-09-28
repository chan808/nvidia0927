"""Small intake and run contract shared by rules, investigation and local records."""

from __future__ import annotations

from dataclasses import asdict, dataclass, fields, replace
from datetime import datetime, timedelta, timezone
import re
from uuid import uuid4


TIME_WINDOW = timedelta(minutes=5)
KST = timezone(timedelta(hours=9))
RUN_STATUSES = {"COMPLETED", "WAITING_CONTEXT", "PARTIAL_FAILURE", "TIMED_OUT", "BUDGET_EXHAUSTED"}


def event_time(value: str) -> datetime:
    try:
        when = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError, AttributeError) as exc:
        raise ValueError("발생 시각은 시간대를 포함한 ISO 8601이어야 합니다") from exc
    if when.tzinfo is None:
        raise ValueError("발생 시각의 timezone(시간대)이 필요합니다")
    return when


@dataclass(frozen=True)
class ReportContext:
    environment: str | None = None
    service: str | None = None
    occurred_at: str | None = None
    trace_id: str | None = None
    method: str | None = None
    path: str | None = None
    operation: str | None = None

    def __post_init__(self):
        for item in fields(self):
            value = getattr(self, item.name)
            if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 200):
                raise ValueError(f"잘못된 접수 범위: {item.name}")
        if self.occurred_at:
            event_time(self.occurred_at)
        if self.trace_id and not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", self.trace_id):
            raise ValueError("잘못된 요청 식별 값입니다")

    def to_dict(self) -> dict:
        return asdict(self)

    def with_answer(self, answer: str, *, received_at: str | None = None) -> ReportContext:
        """Parse simple context; received_at is this message's receipt, not the incident's."""
        changes = {}
        for pattern, value in ((r"개발(?:\s*환경)?|\bdev\b", "dev"), (r"스테이징|\bstaging\b", "staging"), (r"운영(?:\s*환경)?|\bprod\b", "prod")):
            if re.search(pattern, answer, re.I):
                changes["environment"] = value
        iso = re.search(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})", answer)
        if iso:
            changes["occurred_at"] = event_time(iso.group()).isoformat()
        else:
            date = re.search(r"(\d{4})[-년./]\s*(\d{1,2})[-월./]\s*(\d{1,2})(?:일)?", answer)
            clock = re.search(r"(?:(오전|오후)\s*)?(\d{1,2})(?:시\s*(?:(\d{1,2})분)?|:(\d{2}))", answer)
            relative = re.search(r"오늘|어제", answer)
            if clock and (date or relative):
                base = event_time(received_at).astimezone(KST) if received_at else datetime.now(KST)
                day = datetime(*map(int, date.groups()), tzinfo=KST) if date else base.replace(hour=0, minute=0, second=0, microsecond=0)
                if not date and relative.group() == "어제":
                    day -= timedelta(days=1)
                hour = int(clock.group(2))
                if clock.group(1):
                    if not 1 <= hour <= 12:
                        raise ValueError("오전·오후 시각을 확인해 주세요")
                    hour = hour % 12 + (12 if clock.group(1) == "오후" else 0)
                changes["occurred_at"] = day.replace(hour=hour, minute=int(clock.group(3) or clock.group(4) or 0)).isoformat()
            elif re.search(r"방금|조금\s*전", answer) and not date:
                base = event_time(received_at).astimezone(KST) if received_at else datetime.now(KST)
                changes["occurred_at"] = base.isoformat()
        return replace(self, **changes)


def plain_questions(*, photo_only: bool = False) -> list[str]:
    return (["어떤 화면에서 무엇을 하려던 중이었나요?"] if not photo_only else ["사진에서 어떤 문제가 보이나요? 하려던 동작을 알려주세요."]) + ["대략 언제였고, 화면에 어떤 오류 문구가 나왔나요?"]


def action_preference(answer: str) -> str | None:
    """Explicit user restrictions survive restart without retaining the user's text."""
    if re.search(r"조사\s*만|확인\s*만|수정(?:하|해)?지\s*마|고치지\s*마|do not (?:edit|fix)|read.only", answer, re.I):
        return "INVESTIGATE_ONLY"
    if re.search(r"고쳐|고치|수정해|수정\s*부탁|해결해|해결\s*부탁", answer):
        return "PREPARE_ALLOWED"
    return None


def empty_result(project_id: str, *, incident_id: str | None = None) -> dict:
    return {
        "contract_version": 1, "project_id": project_id,
        "incident_id": incident_id or uuid4().hex, "run_id": uuid4().hex,
        "revision": 1,
        "message_received_at": None, "relative_date_basis": {},
        "correlation": "NEEDS_CONTEXT", "correlation_basis": None,
        "trace_id": None, "candidate_trace_ids": [], "candidates": [],
        "route": "REQUEST_CONTEXT", "route_reason": "제보와 실제 요청의 연결을 더 확인해야 합니다",
        "work_role": None, "diagnosis_type": None, "finding_status": "INCONCLUSIVE",
        "claim_status": "UNVERIFIABLE", "claim_items": [], "claim_coverage": "explicit_facts_only",
        "symptom_status": "UNOBSERVED", "product_status": "UNCONFIRMED",
        "responsibility": {"status": "UNCONFIRMED", "evidence_sources": []}, "contract_analysis": {},
        "summary": "현재 관측만으로 사건과 원인을 확인하지 못했습니다.", "next_action": "화면·동작·대략적인 시각을 보완해 주세요.",
        "observations": [], "report_clues": [], "evidence": [], "hypotheses": [],
        "questions": plain_questions(), "missing_information": [], "next_steps": [],
        "version_provenance": {
            "local_head": {"sha": None, "source": "not_connected", "source_tree_dirty": None},
            "configured": {"sha": None, "source": "manual_configuration", "deployment_observed": False},
            "runtime": {"sha": None, "source": None, "status": "NOT_OBSERVED"}, "comparison": "UNKNOWN",
        }, "deployment_observed": False, "log_scope": {}, "run_status": "WAITING_CONTEXT", "stop_reason": "context_needed",
        "cause_confirmed": False, "fix_applied": False, "fix_verified": False,
        "steps": [], "service_calls": [], "model_calls": 0,
        "usage": {"prompt_tokens": 0, "completion_tokens": 0}, "model_trace": [], "notes": [],
    }


def presentation_status(result: dict, change_job: dict | None = None) -> dict:
    """Independent report, symptom, product and execution states for UI/CLI."""
    collection = result.get("log_scope", {})
    aggregate = collection.get("aggregate", {})
    assessment = collection.get("assessment", {})
    conflicts = bool(aggregate.get("conflicts") or aggregate.get("conflicting_trace_ids"))
    status_item = next((item for item in result.get("claim_items", []) if item.get("facet") == "http_status"), {})
    observed = result.get("observed_status")
    response_states = sorted({item["response_status"] for item in collection.get("retained_observations", [])
                              if type(item.get("response_status")) is int})
    if type(observed) is int:
        response_states = sorted(set([*response_states, observed]))
    symptom = result.get("symptom_status") or assessment.get("symptom_status")
    if not symptom or symptom == "UNOBSERVED":
        symptom = "REQUEST_REJECTED" if any(value >= 400 for value in response_states) else "HTTP_SUCCESS_OBSERVED" if response_states else "UNOBSERVED"
    product = result.get("product_status") or assessment.get("product_status") or "UNCONFIRMED"
    responsibility = result.get("responsibility") or assessment.get("responsibility") or {"status": "UNCONFIRMED"}
    if conflicts or aggregate.get("complete") is False:
        product, responsibility = "UNCONFIRMED", {"status": "UNCONFIRMED"}
    change = change_job or result.get("change", {})
    return {
        "reported_http_status": result.get("reported_status"),
        "report_status_verification": status_item.get("status", "UNVERIFIABLE"),
        "observed_http_statuses": response_states,
        "actual_symptom_status": symptom,
        "product_status": product, "responsibility_status": responsibility.get("status", "UNCONFIRMED"),
        "collection_status": "INCOMPLETE" if aggregate.get("complete") is False else "COMPLETE" if aggregate.get("complete") is True else "UNOBSERVED",
        "observations_conflict": conflicts,
        "investigation_status": result.get("run_status", "WAITING_CONTEXT"),
        "investigation_final_return": collection.get("investigation", {}).get("final_return_status", "NOT_RECORDED"),
        "candidate_validation": "VERIFIED" if change.get("candidate_fix_verified") is True else "NOT_VERIFIED",
        "original_application": "APPLIED" if change.get("original_applied") is True or result.get("fix_applied") is True else "NOT_APPLIED",
        "service_recovery": change.get("service_recovery", "NOT_VERIFIED"),
    }
