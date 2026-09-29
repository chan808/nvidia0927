"""Keep default persistence inside each test's temporary workspace."""
import pytest


@pytest.fixture(autouse=True)
def isolated_default_incident_database(monkeypatch, tmp_path):
    # Module/test-specific paths can still override this. Loading the user's
    # .env must never make a routine regression append to their incident log.
    monkeypatch.setenv("TRACEBRIDGE_DB_PATH", str(tmp_path / "default-incidents.sqlite3"))
