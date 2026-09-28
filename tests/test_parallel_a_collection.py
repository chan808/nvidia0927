"""Session A's offline collection truthfulness checks; no shared DB or I/O."""

from copy import deepcopy
import io
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from tracebridge import project_sources, report_agent
from tracebridge.deadline import DeadlineExceeded
from tracebridge.incident_memory import IncidentStore
from tracebridge.project_sources import LogRead, LogText, collect_scoped_logs
from tracebridge.report_contract import ReportContext
from tracebridge.report_intake import LocalEventCatalog


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT / "tests/fixtures/agolive_repo"
RECORDS = [json.loads(line) for line in (ROOT / "examples/scoped_agolive.log").read_text(encoding="utf-8").splitlines()]


@pytest.fixture
def tmp_path():
    # pytest's Windows private-temp ACLs are inaccessible inside this sandbox.
    # Ordinary session-owned directories keep these tests entirely in A's root.
    path = ROOT / "output/parallel-a" / ("collection-" + uuid4().hex)
    path.mkdir(parents=True)
    return path


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    for name in ("TRACEBRIDGE_EVENTS_FILE", "TRACEBRIDGE_LOG_FILE", "TRACEBRIDGE_LOG_SERVICE",
                 "TRACEBRIDGE_LOG_ENVIRONMENT", "TRACEBRIDGE_LOG_TIMEZONE", "TRACEBRIDGE_DEPLOYED_SHA",
                 "TRACEBRIDGE_PROJECT_PROFILE"):
        monkeypatch.delenv(name, raising=False)


def scope(record):
    trace = record["trace"]
    return ReportContext(trace_id=trace["trace_id"], service=trace["service"], environment=trace["environment"], occurred_at=trace["occurred_at"])


def investigate(tmp_path, text, record, *, source="file", catalog=True, monkeypatch=None):
    options = {}
    if source == "file":
        path = tmp_path / "registered.log"
        path.write_text(text, encoding="utf-8")
        options["log_file"] = path
    elif source == "provided":
        options["provided_logs"] = text
    else:
        raw = text.encode("utf-8")
        monkeypatch.setattr(report_agent, "docker_compose_logs", lambda *a, **kw:
                            (LogRead((raw[i:i + 4096] for i in range(0, len(raw), 4096)), {"coverage": "REQUEST_TIME_RANGE"}), None))
        monkeypatch.setattr(report_agent, "running_service_revision", lambda *a, **kw: {"status": "NOT_OBSERVED", "sha": None})
        options["include_docker_logs"] = True
    selected = LocalEventCatalog({"project_id": "agolive", "events": [record]}) if catalog else None
    return report_agent.investigate_submission(
        "500 가입이 실패해요. 고쳐줘. requestId=" + record["trace"]["trace_id"],
        repo=REPO, catalog=selected, context=scope(record), db_path=tmp_path / "incidents.sqlite3", **options)


@pytest.mark.parametrize("case,padding", [("short", ""), ("481kb", "unrelated line\n" * 30000), ("6000lines", "x\n" * 6000)], ids=["short", "481kb", "6000lines"])
@pytest.mark.parametrize("source", ["file", "provided", "docker-double"])
@pytest.mark.parametrize("catalog", [False, True])
def test_three_lost_counterevidence_reproductions(tmp_path, monkeypatch, case, padding, source, catalog):
    current = deepcopy(RECORDS[0])
    counter = deepcopy(current)
    counter["trace"]["response_status"] = 500
    text = json.dumps(counter) + "\n" + padding + json.dumps(current) + "\n"
    result = investigate(tmp_path, text, current, source=source, catalog=catalog, monkeypatch=monkeypatch)
    aggregate = result["log_scope"]["aggregate"]
    assert aggregate["complete"] is True
    assert aggregate["observation_count"] == 2
    assert current["trace"]["trace_id"] in aggregate["conflicting_trace_ids"]
    assert "response_status" in aggregate["conflicts"][0]["fields"]
    assert {item["response_status"] for item in result["log_scope"]["retained_observations"]} == {500, 422}
    assert result["route"] == "REQUEST_CONTEXT" and result.get("observed_status") is None
    assert not any(result[key] for key in ("cause_confirmed", "fix_applied", "fix_verified"))
    assert result["model_calls"] == 0
    with IncidentStore(tmp_path / "incidents.sqlite3") as store:
        saved = store.get_run("agolive", result["run_id"])
    assert saved["log_scope"] == result["log_scope"]
    # Write concrete results for the handoff without reusing the public DB.
    (tmp_path / "collection-result.json").write_text(json.dumps({
        "case": case, "source": source, "catalog": catalog,
        "bytes": (tmp_path / "registered.log").stat().st_size if source == "file" else len(text.encode()),
        "route": result["route"], "aggregate": aggregate,
        "statuses": [item["response_status"] for item in result["log_scope"]["retained_observations"]],
    }, ensure_ascii=False, indent=2), encoding="utf-8")


@pytest.mark.parametrize("limit,reason", [("MAX_LOG_SCAN_BYTES", "byte_limit_reached"), ("MAX_LOG_SCAN_LINES", "line_limit_reached")])
@pytest.mark.parametrize("source", ["file", "provided", "docker-double"])
def test_stopped_scan_holds_snapshot_and_preserves_read_prefix(tmp_path, monkeypatch, limit, reason, source):
    current = deepcopy(RECORDS[0])
    counter = deepcopy(current)
    counter["trace"]["response_status"] = 500
    first = json.dumps(counter) + "\n"
    text = first + "x\n" * 100 + json.dumps(current) + "\n"
    monkeypatch.setattr(project_sources, limit, len(first.encode()) + 10 if limit.endswith("BYTES") else 5)
    result = investigate(tmp_path, text, current, source=source, monkeypatch=monkeypatch)
    aggregate = result["log_scope"]["aggregate"]
    assert aggregate["complete"] is False
    assert reason in aggregate["incomplete_reasons"]
    assert result["route"] == "REQUEST_CONTEXT"
    assert result["log_scope"]["retained_observations"][0]["response_status"] == 500
    assert result["observations"] and result["model_calls"] == 0


def test_observation_cap_still_scans_and_retains_late_conflict(tmp_path):
    current = deepcopy(RECORDS[0])
    counter = deepcopy(current)
    counter["trace"]["response_status"] = 500
    result = investigate(tmp_path, (json.dumps(current) + "\n") * 120 + json.dumps(counter), current)
    aggregate = result["log_scope"]["aggregate"]
    assert aggregate["observation_count"] == 121 and not aggregate["complete"]
    assert aggregate["conflicting_trace_ids"] == [current["trace"]["trace_id"]]
    assert {item["response_status"] for item in result["log_scope"]["retained_observations"]} == {500, 422}
    assert any(item.get("response_status") == 500 for item in result["evidence"] if item["kind"] == "log")
    assert result["route"] == "REQUEST_CONTEXT"


@pytest.mark.parametrize("error", [DeadlineExceeded("probe"), OSError("private raw failure")])
def test_stream_failure_keeps_complete_prior_record_and_blocks_guidance(tmp_path, monkeypatch, error):
    current = deepcopy(RECORDS[0])
    closed = []

    def chunks():
        try:
            yield (json.dumps(current) + "\n").encode()
            raise error
        finally:
            closed.append(True)

    monkeypatch.setattr(report_agent, "file_log_stream", lambda *a, **kw: LogRead(chunks()))
    result = investigate(tmp_path, "unused", current)
    assert closed and not result["log_scope"]["aggregate"]["complete"]
    assert result["route"] == "REQUEST_CONTEXT"
    assert result["log_scope"]["retained_observations"][0]["response_status"] == 422
    assert result["run_status"] == ("TIMED_OUT" if isinstance(error, DeadlineExceeded) else "PARTIAL_FAILURE")
    assert "private raw failure" not in str(result)


def test_oversized_record_cannot_turn_the_remaining_snapshot_into_guidance(tmp_path):
    current = deepcopy(RECORDS[0])
    text = "x" * (project_sources.MAX_LOG_RECORD_BYTES + 1) + "\n" + json.dumps(current)
    result = investigate(tmp_path, text, current)
    assert "record_limit_reached" in result["log_scope"]["aggregate"]["incomplete_reasons"]
    assert result["route"] == "REQUEST_CONTEXT"
    assert result["log_scope"]["retained_observations"]


def test_upstream_excerpt_cannot_restore_completeness():
    current = RECORDS[0]
    text = LogText(json.dumps(current), {"complete": False, "reasons": ["upstream_byte_limit_reached"]})
    _, _, events, state = collect_scoped_logs(text, "", source="upstream", scope=scope(current))
    assert events and not state["complete"]
    assert "upstream_byte_limit_reached" in state["reasons"]


@pytest.mark.parametrize("record_index,route", [(0, "GUIDANCE"), (1, "WORK_CANDIDATE")])
def test_complete_sources_keep_supported_fast_routes(tmp_path, record_index, route):
    current = deepcopy(RECORDS[record_index])
    # Explicit doubles add the attribution required by B's strengthened contract;
    # the old sample lacks current caller/input/version proof and must investigate.
    current["trace"].update(version="double-v1", code_version="double-v1")
    caller = {"source": "TEST_DOUBLE/caller.py#build", "source_kind": "caller_code", "source_verified": True,
              "version": "double-v1", "method": "POST", "path": "/api/users",
              "field_mapping": {"userId": "userId", "name": "name"} if record_index == 0 else {"user_id": "userId", "name": "name"},
              "required_inputs": {"userId": "userId", "name": "name"},
              "input_fields": ["userId"] if record_index == 0 else ["userId", "name"],
              "input_types": {"userId": "string", "name": "string"}}
    current["contract_context"] = {"versions": {key: "double-v1" for key in ("runtime", "code", "contract", "dto", "caller")}, "caller": caller}
    result = investigate(tmp_path, json.dumps(current), current)
    assert result["route"] == route
    assert result["log_scope"]["aggregate"]["complete"]
    assert not result["log_scope"]["aggregate"]["conflicts"]


def test_docker_stream_uses_event_window_and_preserves_nonzero_exit(monkeypatch):
    current = RECORDS[0]
    invocations = []

    class Process:
        stdout = io.BytesIO((json.dumps(current) + "\n").encode())
        returncode = 1

        def poll(self):
            return self.returncode

        def wait(self, timeout):
            return self.returncode

    def popen(argv, **kwargs):
        invocations.append((argv, kwargs))
        return Process()

    monkeypatch.setattr(project_sources.subprocess, "Popen", popen)
    read, error = project_sources.docker_compose_logs(REPO, scope=scope(current), streaming=True)
    assert error is None
    _, _, events, state = collect_scoped_logs(read, "", source="docker", scope=scope(current))
    assert events and not state["complete"] and state["process_failed"]
    argv, settings = invocations[0]
    assert "--tail" not in argv and "--until" in argv
    assert not argv[argv.index("--since") + 1].endswith("m")
    assert settings["shell"] is False and state["coverage"] == "REQUEST_TIME_RANGE"


def test_empty_closed_stream_is_complete_without_inventing_observations():
    _, _, events, state = collect_scoped_logs("", "", source="empty", scope=scope(RECORDS[0]))
    assert state["complete"] and state["scan_complete"] and events == []


def test_same_id_with_unverified_scope_holds_current_snapshot(tmp_path):
    current = deepcopy(RECORDS[0])
    uncertain = deepcopy(current)
    uncertain["trace"].pop("occurred_at")
    uncertain["trace"]["response_status"] = 500
    result = investigate(tmp_path, json.dumps(uncertain), current)
    assert result["route"] == "REQUEST_CONTEXT"
    assert "matching_scope_unverified" in result["log_scope"]["aggregate"]["incomplete_reasons"]
    assert result["evidence"]


def test_decode_failure_keeps_records_but_never_complete():
    current = RECORDS[0]
    data = b"\xff\n" + json.dumps(current).encode()
    _, _, events, state = collect_scoped_logs(LogRead([data]), "", source="invalid-encoding", scope=scope(current))
    assert events and not state["complete"] and "invalid_utf8_record" in state["reasons"]


def test_utf8_bom_record_is_inspected_without_losing_first_status():
    current = RECORDS[0]
    _, _, events, state = collect_scoped_logs(LogRead([b"\xef\xbb\xbf" + json.dumps(current).encode()]), "", source="bom", scope=scope(current))
    assert state["complete"] and events[0]["trace"]["response_status"] == 422


@pytest.mark.parametrize("failure", ["pipe", "timeout"])
def test_docker_failure_preserves_already_delivered_records(monkeypatch, failure):
    current = RECORDS[0]

    class Pipe:
        first = True

        def read1(self, maximum):
            if self.first:
                self.first = False
                return (json.dumps(current) + "\n").encode()
            if failure == "pipe":
                raise OSError("pipe failure")
            return b""

        def close(self):
            pass

    class Process:
        stdout = Pipe()
        returncode = None

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -9

        def wait(self, timeout):
            if failure == "timeout" and self.returncode is None:
                raise project_sources.subprocess.TimeoutExpired("docker", timeout)
            return self.returncode

    monkeypatch.setattr(project_sources.subprocess, "Popen", lambda *a, **k: Process())
    read, _ = project_sources.docker_compose_logs(REPO, scope=scope(current), streaming=True)
    _, _, events, state = collect_scoped_logs(read, "", source="docker-failed", scope=scope(current))
    assert events and not state["complete"]
    assert state["deadline_exhausted" if failure == "timeout" else "read_failed"]


def test_empty_upstream_failure_cannot_disappear_from_final_completeness(tmp_path):
    result = investigate(tmp_path, LogText("", {"complete": False, "reasons": ["upstream_timeout"]}), RECORDS[0], source="provided")
    assert result["log_scope"]["connected"] and not result["log_scope"]["aggregate"]["complete"]
    assert "upstream_timeout" in result["log_scope"]["aggregate"]["incomplete_reasons"]
    assert result["route"] == "REQUEST_CONTEXT"


def test_followup_cannot_drop_its_registered_provided_source(tmp_path):
    record = RECORDS[0]
    first = investigate(tmp_path, json.dumps(record), record, source="provided")
    catalog = LocalEventCatalog({"project_id": "agolive", "events": [record]})
    with pytest.raises(ValueError, match="자료 연결"):
        report_agent.follow_up_submission(first, "추가 답변", repo=REPO, catalog=catalog,
                                          context=scope(record), db_path=tmp_path / "incidents.sqlite3")
