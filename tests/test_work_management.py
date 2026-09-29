"""Existing DB migration and actual Streamlit queue/reopening contracts."""
import json
from pathlib import Path
import sqlite3

from streamlit.testing.v1 import AppTest

from tracebridge.control_plane import SCHEMA
from tracebridge.work_management import initialize_work_management
from test_control_plane import remote
from test_registered_project_repair import project


def test_existing_jobs_migrate_without_resetting_results_or_inventing_history(tmp_path):
    with sqlite3.connect(tmp_path / "old.sqlite3") as db:
        db.executescript(SCHEMA)
        db.execute("""INSERT INTO jobs(id,project_id,incident_id,run_id,kind,body,input_hash,idempotency_key,state,
            expires,created,result,result_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            ("old-job", "project", "incident", "run", "investigate", '{"service":"api"}', "hash", "old-key", "SUCCEEDED", 1, 1, '{"run_status":"WAITING_CONTEXT"}', "result-hash"))
        db.commit()
        initialize_work_management(db)
        assert db.execute("SELECT result,result_hash,priority FROM jobs").fetchone() == ('{"run_status":"WAITING_CONTEXT"}', "result-hash", 2)
        events = db.execute("SELECT kind,to_state,metadata_json FROM job_events").fetchall()
        assert len(events) == 1 and events[0][:2] == ("MIGRATED_SNAPSHOT", "SUCCEEDED")
        assert json.loads(events[0][2])["history_before_migration"] == "NOT_RECORDED"
        initialize_work_management(db)
        assert db.execute("SELECT COUNT(*) FROM job_events").fetchone()[0] == 1


def test_remote_page_submits_owner_priority_and_reopens_saved_job(remote, monkeypatch):
    owner, _, runner, _, _, _, profile = remote
    import tracebridge.local_runner
    monkeypatch.setenv("TRACEBRIDGE_CONTROL_URL", "http://testserver")
    monkeypatch.setenv("TRACEBRIDGE_OPERATOR_TOKEN", "synthetic-owner")
    monkeypatch.setattr(tracebridge.local_runner, "ControlClient", lambda *args, **kwargs: owner)
    page = str(Path(__file__).resolve().parents[1] / "pages/3_Remote_Projects.py")
    app = AppTest.from_file(page, default_timeout=20).run()
    assert not app.exception
    app.text_area(key="remote_report_text").set_value("QUOTA_BOUNDARY requestId=quota-001")
    app.selectbox(key="remote_new:" + profile.project_id + ":priority").select("P0")
    next(button for button in app.button if button.label == "제보 접수").click().run()
    assert not app.exception
    listing = owner.request("GET", f"/v1/projects/{profile.project_id}/jobs")
    saved_id = listing["jobs"][0]["id"]
    assert listing["jobs"][0]["assessment"]["priority"] == "P0"
    runner.run_once()
    reopened = AppTest.from_file(page, default_timeout=20).run()
    reopened.button(key="remote_open_job:" + profile.project_id).click().run()
    assert not reopened.exception
    assert reopened.session_state["remote_job_id"] == saved_id
    assert any("SUCCEEDED" in item.value for item in reopened.markdown)
