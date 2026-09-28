from pathlib import Path
import subprocess
import json
import sqlite3
import sys

import pytest

from scripts.prepare_verification import (
    OFFLINE_BOOTSTRAP, assess_smoke, environment_check, freeze_sources,
    offline_environment, prepare, saved_run_check,
)


def test_snapshot_includes_uncommitted_sources_but_excludes_secrets_and_databases(tmp_path):
    root = tmp_path / "project"
    (root / "tracebridge").mkdir(parents=True)
    (root / "scripts").mkdir()
    (root / "examples/assets").mkdir(parents=True)
    (root / ".env").write_text("NVIDIA_API_KEY=private-token")
    (root / ".env.example").write_text("NVIDIA_API_KEY=your_key")
    (root / "tracebridge/new_module.py").write_text("VALUE = 7\n")
    (root / "scripts/renamed.json").write_bytes(b"SQLite format 3\x00" + b"internal-record")
    (root / "examples/assets/screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    result = freeze_sources(root, tmp_path / "snapshot")
    assert result["status"] == "FROZEN"
    assert "tracebridge/new_module.py" in result["files"]
    assert ".env.example" in result["files"]
    assert ".env" not in result["files"]
    assert "scripts/renamed.json" not in result["files"]
    assert "examples/assets/screenshot.png" in result["files"]


def test_source_edit_during_copy_marks_snapshot_unstable(tmp_path, monkeypatch):
    import scripts.prepare_verification as module

    root = tmp_path / "project"
    root.mkdir()
    (root / "app.py").write_text("VALUE = 1")
    original = module.source_contents
    calls = 0

    def edit_after_first_read(path):
        nonlocal calls
        values = original(path)
        calls += 1
        if calls == 1:
            (root / "app.py").write_text("VALUE = 2")
        return values

    monkeypatch.setattr(module, "source_contents", edit_after_first_read)
    result = freeze_sources(root, tmp_path / "snapshot")
    assert result["status"] == "SOURCE_CHANGED"
    assert result["changed_during_copy"] == ["app.py"]


def test_environment_reports_presence_without_recording_credential(tmp_path, monkeypatch):
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    (tmp_path / ".env").write_text("NVIDIA_API_KEY=private-token\n")
    report = environment_check(tmp_path)
    assert report["nvidia_key_present"] is True
    assert "private-token" not in str(report)
    assert report["credentials_validated"] is False


def test_child_environment_removes_provider_keys_and_source_overrides(tmp_path, monkeypatch):
    monkeypatch.setenv("NVIDIA_API_KEY", "private-token")
    monkeypatch.setenv("TRACEBRIDGE_DB_PATH", "shared.sqlite3")
    monkeypatch.setenv("OPENAI_API_KEY", "another-token")
    env = offline_environment(tmp_path)
    assert "NVIDIA_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "TRACEBRIDGE_DB_PATH" not in env


def test_network_guard_stops_outbound_python_connect_before_cli_import():
    code = OFFLINE_BOOTSTRAP.split("sys.argv =", 1)[0] + "\nimport socket\nsocket.socket().connect(('127.0.0.1', 9))\n"
    result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True, text=True)
    assert result.returncode != 0
    assert "MAIN_OFFLINE_SMOKE_NETWORK_DISABLED" in result.stderr


def test_smoke_does_not_accept_missing_observations_or_storage():
    result = {"correlation": "EXACT_ID", "route": "WORK_CANDIDATE",
              "claim_status": "CONTRADICTED", "model_calls": 0, "fix_verified": False,
              "memory_search": {"status": "DISABLED"}}
    checks = assess_smoke(result, memory_enabled=False)
    assert checks["actual_422"] is False
    assert checks["stored"] is False
    assert not all(checks.values())


def test_smoke_accepts_canonical_persistence_field_and_complete_current_result():
    result = {"correlation": "EXACT_ID", "route": "WORK_CANDIDATE",
              "observed_status": 422, "claim_status": "CONTRADICTED",
              "model_calls": 0, "fix_verified": False,
              "persistence": {"status": "SAVED"}, "memory_search": {"status": "DISABLED"}}
    assert all(assess_smoke(result, memory_enabled=False).values())


def test_saved_run_must_match_incident_scope_and_saved_memory_mode(tmp_path):
    path = tmp_path / "incidents.sqlite3"
    result = {"run_id": "run-1", "project_id": "project-1", "incident_id": "incident-1",
              "memory_search": {"status": "DISABLED"}}
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE runs(run_id TEXT, project_id TEXT, incident_id TEXT, record_json TEXT)")
        connection.execute("INSERT INTO runs VALUES(?,?,?,?)",
                           ("run-1", "project-1", "incident-1", json.dumps(result)))
    assert all(saved_run_check(path, result).values())
    assert not saved_run_check(path, {**result, "incident_id": "another"})["database_contains_run"]
    assert not saved_run_check(path, {**result, "memory_search": {"status": "MATCH"}})["saved_memory_mode"]


def test_saved_run_check_does_not_create_a_missing_database(tmp_path):
    path = tmp_path / "missing.sqlite3"
    assert not any(saved_run_check(path, {}).values())
    assert not path.exists()


def test_output_cannot_be_written_outside_workspace_output(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    with pytest.raises(ValueError, match="workspace"):
        prepare(root, tmp_path / "outside")
