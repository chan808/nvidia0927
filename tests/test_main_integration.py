"""Integration evidence uses explicit synthetic provenance and isolated copies."""
from copy import deepcopy
import json
from pathlib import Path
import shutil

from tracebridge.report_intake import LocalEventCatalog, triage_report
from tracebridge.evidence import verify_synthetic_context
from tracebridge.change_worker import seed_incident, prepare_change
from tracebridge.seed_project import capture_seed_action

ROOT = Path(__file__).resolve().parents[1]


def test_demo_provenance_hash_failure_blocks_fast_guidance():
    data = json.loads((ROOT / "examples/report_events.json").read_text(encoding="utf-8"))
    assert triage_report("requestId=claim-003", LocalEventCatalog(data))["route"] == "GUIDANCE"
    bad = deepcopy(data)
    bad["events"][1]["contract_context"]["caller"]["source_sha256"] = "0" * 64
    assert triage_report("requestId=claim-003", LocalEventCatalog(bad))["route"] == "INVESTIGATE"


def test_known_id_without_explicit_context_does_not_gain_fixture_truth():
    data = json.loads((ROOT / "examples/report_events.json").read_text(encoding="utf-8"))
    for event in data["events"]:
        event.pop("contract_context", None)
    assert triage_report("requestId=claim-003", LocalEventCatalog(data))["route"] == "INVESTIGATE"


def test_demo_caller_cannot_escape_repository():
    context = {"provenance": {"kind": "synthetic_fixture"}, "caller": {"source": "../private.py", "source_verified": True}}
    assert verify_synthetic_context(context)["caller"]["source_verified"] is False


def test_capture_does_not_hide_pinned_snapshot_from_worker(tmp_path):
    for relative in ("examples/seed_signup", "examples/change_policy"):
        shutil.copytree(ROOT / relative, tmp_path / relative)
    capture_seed_action(tmp_path)
    db = tmp_path / "snapshot.sqlite3"
    source = seed_incident(workspace=tmp_path, db_path=db)
    result = prepare_change(source["project_id"], source["run_id"], workspace=tmp_path, db_path=db)
    assert result["job"]["status"] == "MODEL_NOT_REQUESTED"
    assert result["job"]["checks"][0]["exit_code"] == 1
    assert result["job"]["original_unchanged"]
