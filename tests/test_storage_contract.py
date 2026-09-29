"""The legacy SQLite adapter retains database-enforced audit references."""
import pytest
from sqlalchemy.exc import IntegrityError
from tracebridge.storage import open_control_store
from tracebridge.storage import schema as s

def test_sqlite_control_adapter_enforces_foreign_keys(tmp_path):
    store = open_control_store(tmp_path / "control.sqlite3")
    try:
        with pytest.raises(IntegrityError):
            with store.transaction() as tx:
                assert tx.connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one() == 1
                tx.insert(s.job_events, job_id="absent", kind="STATE_CHANGED", epoch=0, occurred=0, metadata_json="{}")
    finally:
        store.close()
