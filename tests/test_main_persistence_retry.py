from copy import deepcopy
import json

import pytest

from tracebridge.incident_memory import IncidentStore, RunConflict


def incident_result():
    return {
        "project_id": "main-retry", "incident_id": "incident-one", "run_id": "run-one",
        "revision": 1, "executed_at": "2026-09-28T10:00:00+09:00",
        "route": "INVESTIGATE", "run_status": "WAITING_CONTEXT", "correlation": "EXACT_ID",
        "symptom_summary": "Synthetic report", "summary": "Cause not confirmed",
        "next_action": "Read current evidence", "cause_confirmed": False,
        "fix_applied": False, "fix_verified": False, "private_input": "first-private-input",
    }


def test_cli_display_fields_do_not_change_the_immutable_run(tmp_path):
    result = incident_result()
    with IncidentStore(tmp_path / "retry.sqlite3") as store:
        assert store.save_run(result) == "SAVED"
        before = dict(store.connection.execute("SELECT * FROM runs WHERE run_id=?", (result["run_id"],)).fetchone())
        result.update(persistence={"status": "SAVED"}, presentation={"title": "Display only"},
                      manual_export={"card_count": 0})
        assert store.save_run(result) == "ALREADY_SAVED"
        after = dict(store.connection.execute("SELECT * FROM runs WHERE run_id=?", (result["run_id"],)).fetchone())
        assert before == after
        assert store.connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 1


def test_exact_retrieved_record_can_be_replayed_without_rehashing_private_input(tmp_path):
    result = incident_result()
    with IncidentStore(tmp_path / "retry.sqlite3") as store:
        store.save_run(result)
        record = store.get_run(result["project_id"], result["run_id"])
        assert "private_input" not in record
        assert store.save_run(record) == "ALREADY_SAVED"
        changed = deepcopy(record)
        changed["summary"] = "Different cause"
        with pytest.raises(RunConflict):
            store.save_run(changed)


@pytest.mark.parametrize("field,value", [("summary", "Changed fact"), ("private_input", "different-private-input"),
                                        ("fix_verified", True)])
def test_real_or_discarded_input_changes_still_conflict(tmp_path, field, value):
    result = incident_result()
    with IncidentStore(tmp_path / "retry.sqlite3") as store:
        store.save_run(result)
        changed = deepcopy(result)
        changed[field] = value
        with pytest.raises(RunConflict):
            store.save_run(changed)


def test_old_full_input_digest_is_preserved_and_accepted(tmp_path):
    import hashlib

    result = incident_result()
    result["presentation"] = {"title": "Previously attached before save"}
    with IncidentStore(tmp_path / "retry.sqlite3") as store:
        store.save_run(result)
        legacy_digest = hashlib.sha256(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with store.connection:
            store.connection.execute("UPDATE runs SET content_hash=? WHERE run_id=?", (legacy_digest, result["run_id"]))
        assert store.save_run(result) == "ALREADY_SAVED"
        assert store.connection.execute("SELECT content_hash FROM runs WHERE run_id=?", (result["run_id"],)).fetchone()[0] == legacy_digest
