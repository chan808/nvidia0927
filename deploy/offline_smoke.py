"""Container release check using only bundled seed data and a fresh SQLite DB."""

from pathlib import Path
import json
import sqlite3
import sys
from uuid import uuid4


def main():
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("This deployment supports Python 3.12")
    from tracebridge.change_policy import WORKSPACE, load_policy, read_source
    from tracebridge.change_worker import seed_incident
    from tracebridge.incident_memory import IncidentStore
    import tracebridge.report_agent

    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE VIRTUAL TABLE smoke_fts USING fts5(body)")
    connection.close()
    policy, _ = load_policy("seed-signup-a2-v1")
    read_source(policy, WORKSPACE)
    directory = WORKSPACE / "output" / "deployment-smoke" / uuid4().hex
    directory.mkdir(parents=True, exist_ok=False)
    database = directory / "incidents.sqlite3"
    result = seed_incident(db_path=database)
    if result["route"] != "WORK_CANDIDATE":
        raise RuntimeError("Bundled seed intake did not produce the expected candidate route")
    with IncidentStore(database) as store:
        stored = store.get_run(result["project_id"], result["run_id"])
    if stored["run_id"] != result["run_id"]:
        raise RuntimeError("SQLite reopen did not restore the seed run")
    print(json.dumps({"status": "OFFLINE_SEED_SMOKE_PASSED", "route": result["route"],
                      "sqlite_reopen": True, "registered_seed_hashes": "PASSED",
                      "live_model_requested": False, "real_incident_verified": False}))


if __name__ == "__main__":
    main()
