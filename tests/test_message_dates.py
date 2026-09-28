"""Message receipt dates across live replies and SQLite reconnects; all clocks are fake."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

import pytest

from tracebridge import report_agent
from tracebridge.incident_memory import IncidentStore
from tracebridge.report_agent import follow_up_submission, investigate_submission
from tracebridge.report_contract import ReportContext
from tracebridge.report_intake import LocalEventCatalog


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "tests/fixtures/agolive_repo"
EVENTS = ROOT / "examples/report_events.json"
DAY_ONE = "2026-09-28T01:05:00+00:00"
DAY_TWO = "2026-09-29T01:05:00+00:00"
EVENT_TIME = "2026-09-28T10:00:00+09:00"


@pytest.fixture
def clock(monkeypatch):
    class Clock(datetime):
        current = datetime.fromisoformat(DAY_ONE)

        @classmethod
        def set(cls, value):
            cls.current = datetime.fromisoformat(value)

        @classmethod
        def now(cls, tz=None):
            return cls.current.astimezone(tz) if tz else cls.current.replace(tzinfo=None)

    monkeypatch.setattr(report_agent, "datetime", Clock)
    return Clock


@pytest.fixture
def intake(tmp_path, monkeypatch, clock):
    for name in ("TRACEBRIDGE_EVENTS_FILE", "TRACEBRIDGE_LOG_FILE", "TRACEBRIDGE_LOG_SERVICE", "TRACEBRIDGE_LOG_ENVIRONMENT", "TRACEBRIDGE_LOG_TIMEZONE", "TRACEBRIDGE_DEPLOYED_SHA"):
        monkeypatch.delenv(name, raising=False)
    db = tmp_path / "dates.sqlite3"
    log = tmp_path / "current.log"
    catalog = LocalEventCatalog.from_file(EVENTS)
    options = {"repo": REPO, "catalog": catalog, "log_file": log, "db_path": db}

    def start(trace_id="claim-003", *, text=None):
        data = json.loads(EVENTS.read_text(encoding="utf-8"))
        event = next(item for item in data["events"] if item["trace"]["trace_id"] == trace_id)
        log.write_text(json.dumps({**event, "message": " ".join(event["logs"])}), encoding="utf-8")
        context = ReportContext(environment="dev", service="account-api", occurred_at=EVENT_TIME)
        return investigate_submission(text or f"500 requestId={trace_id}", context=context, **options)

    def restore(result):
        with IncidentStore(db) as store:
            return store.resume_result(catalog.project_id, result["incident_id"])

    return start, restore, options


def check_message(result, receipt, basis_date, occurred_at):
    assert result["message_received_at"] == receipt
    assert result["relative_date_basis"] == {"date": basis_date, "timezone": "+09:00"}
    assert result["session"]["context"]["occurred_at"] == occurred_at
    assert result["log_scope"]["requested"]["occurred_at"] == occurred_at
    assert any(step["tool"] == "find_logs" and step["phase"] == "correlation" for step in result["steps"])
    assert result["persistence"]["status"] == "SAVED" and result["model_calls"] == 0
    assert not result["cause_confirmed"] and not result["fix_verified"]


def test_same_day_today_keeps_existing_result(intake, clock):
    start, _, options = intake
    first = start()
    clock.set("2026-09-28T01:30:00+00:00")
    second = follow_up_submission(first, "오늘 오전 10시에 발생한 오류예요.", **options)
    check_message(second, "2026-09-28T01:30:00+00:00", "2026-09-28", EVENT_TIME)
    assert second["route"] == "GUIDANCE" and second["observed_status"] == 422
    assert second["session"]["received_at"] == first["session"]["received_at"] == DAY_ONE
    assert second["history"][0]["message_received_at"] == DAY_ONE
    assert second["memory_search"]["current_recheck"]["logs"]["verified_count"] == 1


@pytest.mark.parametrize("trace_id,old_route", [("claim-003", "GUIDANCE"), ("contract-001", "WORK_CANDIDATE")])
def test_db_resume_next_day_today_holds_old_date_events(intake, clock, trace_id, old_route):
    start, restore, options = intake
    first = start(trace_id)
    assert first["route"] == old_route
    with IncidentStore(options["db_path"]) as store:
        original_record = store.get_run(first["project_id"], first["run_id"])
    clock.set(DAY_TWO)
    restored = restore(first)
    second = follow_up_submission(restored, "오늘 오전 10시에 발생한 오류예요.", **options)
    # Assert the resolved date first: this reproduces the original stale-date defect.
    assert second["session"]["context"]["occurred_at"] == "2026-09-29T10:00:00+09:00"
    check_message(second, DAY_TWO, "2026-09-29", "2026-09-29T10:00:00+09:00")
    assert second["route"] == "REQUEST_CONTEXT" and second["run_status"] == "WAITING_CONTEXT"
    assert second.get("observed_status") is None and second["correlation"] == "NEEDS_CONTEXT"
    assert second["observations"] == []
    assert second["log_scope"]["sources"][0]["counts"]["excluded"] == 1
    assert second["session"]["received_at"] == DAY_ONE
    assert second["session"]["source_binding"] == first["session"]["source_binding"]
    assert second["session"]["answer_count"] == 1 and second["revision"] == 2
    assert second["history"][0]["run_id"] == first["run_id"]
    assert second["history"][0]["relative_date_basis"] == {"date": "2026-09-28", "timezone": "+09:00"}
    with IncidentStore(options["db_path"]) as store:
        assert store.get_run(first["project_id"], first["run_id"]) == original_record
        saved = store.get_run(second["project_id"], second["run_id"])
        assert saved["message_received_at"] == DAY_TWO
        assert saved["relative_date_basis"] == second["relative_date_basis"]
        assert saved["session"]["received_at"] == DAY_ONE and saved["session"]["answers"] == []


def test_db_resume_next_day_yesterday_requeries_first_date(intake, clock):
    start, restore, options = intake
    first = start()
    clock.set(DAY_TWO)
    second = follow_up_submission(restore(first), "어제 오전 10시에 발생한 오류예요.", **options)
    check_message(second, DAY_TWO, "2026-09-29", EVENT_TIME)
    assert second["route"] == "GUIDANCE" and second["observed_status"] == 422
    logs = [item for item in second["observations"] if item["kind"] == "log"]
    assert len(logs) == 1 and logs[0]["run_id"] == second["run_id"] != first["run_id"]
    assert second["session"]["received_at"] == DAY_ONE


def test_live_follow_up_crosses_kst_midnight(intake, clock):
    start, _, options = intake
    clock.set("2026-09-28T14:59:00+00:00")
    first = start()
    before = deepcopy(first)
    clock.set("2026-09-28T15:01:00+00:00")
    second = follow_up_submission(first, "오늘 오전 10시에 발생한 오류예요.", **options)
    check_message(second, "2026-09-28T15:01:00+00:00", "2026-09-29", "2026-09-29T10:00:00+09:00")
    assert second["route"] == "REQUEST_CONTEXT"
    assert second["session"]["received_at"] == "2026-09-28T14:59:00+00:00"
    assert first == before


@pytest.mark.parametrize("answer,expected", [
    ("2026년 9월 28일 오전 10시에 발생했어요.", EVENT_TIME),
    ("오늘 얘기하는 오류는 2026-09-28 오전 10시에 발생했어요.", EVENT_TIME),
    ("2026-09-28T10:00:00+09:00에 발생했어요.", EVENT_TIME),
    ("2026-09-28T01:00:00Z에 발생했어요.", "2026-09-28T01:00:00+00:00"),
])
def test_explicit_date_and_iso_respect_the_input(intake, clock, answer, expected):
    start, restore, options = intake
    first = start()
    clock.set(DAY_TWO)
    second = follow_up_submission(restore(first), answer, **options)
    check_message(second, DAY_TWO, "2026-09-29", expected)
    assert second["route"] == "GUIDANCE" and second["observed_status"] == 422


@pytest.mark.parametrize("receipt,basis,expected,route", [
    ("2026-09-28T14:59:00+00:00", "2026-09-28", EVENT_TIME, "GUIDANCE"),
    ("2026-09-28T15:01:00+00:00", "2026-09-29", "2026-09-29T10:00:00+09:00", "REQUEST_CONTEXT"),
])
def test_initial_today_uses_kst_date_at_utc_boundary(intake, clock, receipt, basis, expected, route):
    start, _, _ = intake
    clock.set(receipt)
    result = start(text="500 requestId=claim-003 오늘 오전 10시에 발생했어요.")
    check_message(result, receipt, basis, expected)
    assert result["route"] == route and result["session"]["received_at"] == receipt


def test_receipts_history_binding_and_answer_limit_survive_restarts(intake, clock):
    start, restore, options = intake
    first = start()
    current = restore(first)
    for index in range(6):
        receipt = (datetime.fromisoformat(DAY_TWO) + timedelta(days=index)).isoformat()
        clock.set(receipt)
        current = follow_up_submission(current, "2026-09-28 오전 10시에 발생했어요.", **options)
        assert current["message_received_at"] == receipt
        assert current["session"]["received_at"] == DAY_ONE
        assert current["session"]["source_binding"] == first["session"]["source_binding"]
        assert current["session"]["answer_count"] == index + 1 and current["revision"] == index + 2
        current = restore(current)
    with pytest.raises(ValueError, match="자료 연결"):
        follow_up_submission(current, "오늘 오전 10시", **{**options, "log_file": options["log_file"].with_name("another.log")})
    with pytest.raises(ValueError, match="후속 답변 한도"):
        follow_up_submission(current, "오늘 오전 10시", **options)
    with IncidentStore(options["db_path"]) as store:
        incident = store.get_incident(first["project_id"], first["incident_id"])
        assert len(incident["runs"]) == 7 and incident["latest_revision"] == 7
        assert store.get_run(first["project_id"], first["run_id"])["session"]["received_at"] == DAY_ONE


def test_receipt_is_captured_before_source_preparation(intake, clock, monkeypatch):
    start, _, _ = intake
    clock.set("2026-09-28T14:59:00+00:00")
    validate = report_agent.validate_agolive_repo

    def cross_midnight(repo):
        result = validate(repo)
        clock.set("2026-09-28T15:01:00+00:00")
        return result

    monkeypatch.setattr(report_agent, "validate_agolive_repo", cross_midnight)
    result = start(text="500 requestId=claim-003 오늘 오전 10시에 발생했어요.")
    check_message(result, "2026-09-28T14:59:00+00:00", "2026-09-28", EVENT_TIME)
    assert result["route"] == "GUIDANCE"
    assert result["session"]["received_at"] == "2026-09-28T14:59:00+00:00"
    assert result["executed_at"] == "2026-09-28T15:01:00+00:00"
