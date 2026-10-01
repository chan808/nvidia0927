"""D-owned tests for the runner contract, truthfulness, isolation and packaging."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import tomllib
from zipfile import ZipFile
from uuid import uuid4

import pytest

from scripts.package_submission import create_package
from tracebridge.evaluation import (
    AdapterResult, CONDITIONS, ImportedAdapter, aggregate_results, assess_result,
    clone_memory, create_initial_memory, evaluate_suite, load_suite, usage_metrics, verified_action,
    AdapterContext, local_adapter,
)
from tracebridge.nat_observability import InvestigationObserver, NATEventSink, observe_result


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def tmp_path():
    # Windows sandbox cannot traverse pytest's mode-0700 temp directories.
    # Use normal-mode, unique D-only paths without touching shared temp/DB state.
    path = ROOT / "output/parallel-d/tests" / uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def test_frozen_suite_has_six_types_two_variants_and_explicit_sources():
    suite = load_suite()
    assert len(suite["cases"]) == 12
    assert len({case["category"] for case in suite["cases"]}) == 6
    assert all(case["source"]["kind"] == "synthetic" for case in suite["cases"])
    for case in suite["cases"]:
        expected = json.loads((suite["root"] / case["expectation"]).read_text(encoding="utf-8"))
        assert expected["forbid_cause_confirmation"] and expected["forbid_resolution_confirmation"]


def test_oracle_keys_do_not_appear_recursively_in_any_current_material():
    def keys(value):
        if isinstance(value, dict):
            return set(value) | set().union(*(keys(item) for item in value.values()))
        if isinstance(value, list):
            return set().union(*(keys(item) for item in value))
        return set()
    suite = load_suite()
    for case in suite["cases"]:
        material = json.loads((suite["root"] / case["input"]).read_text(encoding="utf-8"))
        assert not keys(material) & {"expected", "expectation", "rationale", "oracle"}


def test_changed_frozen_input_is_rejected_before_running(tmp_path):
    import shutil
    source = load_suite()["root"]
    target = tmp_path / "suite"
    shutil.copytree(source, target)
    first = target / "inputs/contract-1.json"
    first.write_text(first.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(ValueError, match="Frozen artifact changed"):
        load_suite(target / "suite.json")


def test_unknown_partial_usage_and_failed_calls_remain_observable():
    result = usage_metrics({"model_calls": 2, "usage": {"prompt_tokens": 0, "completion_tokens": 0}}, {
        "model_events": [{"status": "success", "usage": {"prompt_tokens": 9, "completion_tokens": 2}},
                         {"status": "timeout", "usage": None}], "external_model_calls": 0})
    assert result["prompt_tokens"] is None and result["completion_tokens"] is None
    assert result["prompt_tokens_observed_sum"] == 9
    assert result["usage_missing_calls"] == 1 and result["failed_model_calls"] == 1
    legacy = usage_metrics({"model_calls": 1, "usage": {"prompt_tokens": 0}}, {})
    assert legacy["prompt_tokens"] is None and legacy["usage_missing_calls"] == 1


def test_usage_counts_must_match_actual_attempts():
    with pytest.raises(ValueError, match="match attempted"):
        usage_metrics({"model_calls": 2}, {"model_events": []})


def test_no_model_calls_are_distinct_from_unreturned_usage():
    no_calls = usage_metrics({"model_calls": 0}, {"model_events": []})
    assert no_calls["prompt_tokens"] == 0 and no_calls["usage_missing_calls"] == 0


def test_sqlite_backup_reads_wal_and_isolates_next_case(tmp_path):
    source, first, second = (tmp_path / name for name in ("initial.db", "first.db", "second.db"))
    with sqlite3.connect(source) as connection:
        connection.execute("pragma journal_mode=wal")
        connection.execute("create table observations(value text)")
        connection.execute("insert into observations values ('fixed-initial')")
        connection.commit()
        clone_memory(source, first)
        with sqlite3.connect(first) as copied:
            copied.execute("insert into observations values ('current-evaluation')")
        clone_memory(source, second)
        with sqlite3.connect(second) as copied:
            assert copied.execute("select value from observations").fetchall() == [("fixed-initial",)]
    with pytest.raises(ValueError, match="new isolated"):
        clone_memory(source, first)


def test_initial_reviewed_memory_has_unverified_cause_and_no_fix(tmp_path):
    from tracebridge.incident_memory import export_manual, search_memory
    suite = load_suite()
    db = tmp_path / "initial.db"
    create_initial_memory(db, json.loads((suite["root"] / "initial_memory.json").read_text(encoding="utf-8")))
    found = search_memory("tracebridge-evaluation", "RoomFullException /rooms/enter", db_path=db)
    assert found["cards"]
    assert all(card["review"]["status"] == "APPROVED"
               and card["verification_results"]["candidate_validation"]["status"] == "NOT_VERIFIED"
               and card["verification_results"]["service_recovery"]["status"] == "NOT_VERIFIED"
               for card in found["cards"])
    disabled = search_memory("tracebridge-evaluation", "RoomFullException", db_path=db, enabled=False)
    assert disabled["status"] == "DISABLED" and not disabled["cards"]
    manual = export_manual("tracebridge-evaluation", db_path=db)
    assert manual["card_count"] == 1 and "fixture-history-run-rooms" in manual["markdown"].replace("\\-", "-")


def test_runner_never_exposes_expectation_and_clones_memory_per_condition(tmp_path):
    seen = []

    def measured(material, ctx):
        assert "expected" not in material and "expectation" not in material
        assert all("expectations/" not in name for name in ctx.resource_contract["current_artifacts"])
        with sqlite3.connect(ctx.db_path) as connection:
            assert connection.execute("select count(*) from runs").fetchone()[0] == 1
            connection.execute("delete from runs")
        seen.append(ctx.db_path)
        return AdapterResult({"route": "REQUEST_CONTEXT", "questions": ["동작 확인"], "model_calls": 0,
                              "cause_confirmed": False, "fix_applied": False}, measurements={"model_calls": 0, "model_events": [], "external_model_calls": 0})

    summary = evaluate_suite(output_root=tmp_path, conditions=["rules", "tracebridge_memory_off"],
        adapters={"rules": measured, "tracebridge_memory_off": measured})
    assert len(seen) == 24 and len(set(seen)) == 24
    assert summary["artifact_status"] == "DRAFT" and summary["performance_claim"] == "NOT_MEASURED"


def test_missing_baselines_are_blocked_without_fake_human_time(tmp_path):
    summary = evaluate_suite(output_root=tmp_path, conditions=["single_prompt", "coding_agent"])
    rows = json.loads((Path(summary["output_dir"]) / "results.json").read_text(encoding="utf-8"))["rows"]
    assert len(rows) == 24 and all(row["execution_status"] == "BLOCKED" for row in rows)
    assert all(row["result"] is None and "metrics" not in row for row in rows)


def test_runner_preserves_adapter_exception_as_failure(tmp_path):
    def failing(material, ctx):
        raise TimeoutError("controlled adapter failure")
    summary = evaluate_suite(output_root=tmp_path, conditions=["rules"], adapters={"rules": failing})
    rows = json.loads((Path(summary["output_dir"]) / "results.json").read_text(encoding="utf-8"))["rows"]
    assert all(row["execution_status"] == "FAILED" and row["error_type"] == "TimeoutError" for row in rows)


def test_human_measurement_requires_real_record_reference(tmp_path):
    def invalid(material, ctx):
        return AdapterResult({"model_calls": 0}, measurements={"human_seconds": 5})
    summary = evaluate_suite(output_root=tmp_path, conditions=["rules"], adapters={"rules": invalid})
    rows = json.loads((Path(summary["output_dir"]) / "results.json").read_text(encoding="utf-8"))["rows"]
    assert all(row["execution_status"] == "FAILED" and row["error_type"] == "ValueError" for row in rows)


def test_scoring_keeps_false_cause_resolution_and_unnecessary_change(tmp_path):
    actual = {"route": "WORK_CANDIDATE", "cause_confirmed": True, "fix_verified": True,
              "change": {"diff_sha256": "unjustified"}}
    expected = {"allowed": {"route": ["INVESTIGATE"]}, "hold": True,
                "forbid_cause_confirmation": True, "forbid_resolution_confirmation": True,
                "forbid_modification": True}
    scored = assess_result(actual, expected)
    assert scored["status"] == "FAIL" and scored["wrong_cause_confirmation"]
    assert scored["wrong_resolution_confirmation"] and scored["unnecessary_modification"]


def test_verified_action_requires_same_checks_and_regression_evidence(tmp_path):
    check = {"command_id": "repro", "input_sha256": hashlib.sha256(b"controlled input fixture").hexdigest(),
             "test_sha256": hashlib.sha256(b"controlled fixed check fixture").hexdigest(),
             "execution_settings_sha256": hashlib.sha256(b"controlled settings fixture").hexdigest()}
    diff, original = tmp_path / "candidate.diff", tmp_path / "original.py"
    diff.write_text("-old\n+new\n", encoding="utf-8")
    original.write_text("# unchanged original\n", encoding="utf-8")
    change = {"candidate_fix_verified": True, "original_preserved": True,
              "diff_ref": diff.name, "diff_sha256": hashlib.sha256(diff.read_bytes()).hexdigest(),
              "original_ref": original.name, "original_sha256": hashlib.sha256(original.read_bytes()).hexdigest(),
              "checks": [{**check, "phase": "before", "exit_code": 1}, {**check, "phase": "after", "exit_code": 0},
                         {**check, "phase": "regression", "exit_code": 0}]}
    for record in change["checks"]:
        path = tmp_path / (record["phase"] + ".json")
        path.write_text(json.dumps(record), encoding="utf-8")
        record.update(artifact_ref=path.name, artifact_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert verified_action(change, artifact_root=tmp_path)
    assert not verified_action(change)
    for index, field, bad in ((1, "test_sha256", "changed-test"), (2, "exit_code", 1), (0, "exit_code", 0)):
        invalid = deepcopy(change)
        invalid["checks"][index][field] = bad
        assert not verified_action(invalid, artifact_root=tmp_path)
    assert not verified_action({"candidate_fix_verified": True})
    original.write_text("# changed original\n", encoding="utf-8")
    assert not verified_action(change, artifact_root=tmp_path)


def test_scope_and_missing_time_are_separate_in_aggregation():
    base = {"case_id": "x", "condition": "single_prompt", "execution_status": "COMPLETED",
            "assessment": {"status": "PASS"}, "provenance": "imported"}
    rows = [{**base, "execution_scope": "answer_only", "metrics": {"human_seconds": None, "total_elapsed_ms": 30}},
            {**base, "execution_scope": "prepare_change", "metrics": {"human_seconds": None, "total_elapsed_ms": None}}]
    summary = aggregate_results(rows)
    assert len(summary["groups"]) == 2
    assert all(group["metrics"]["human_seconds"]["observed_sum"] is None for group in summary["groups"].values())


def test_import_requires_same_frozen_resources_and_explicit_memory_flag(tmp_path):
    suite = load_suite()
    file = tmp_path / "observations.json"
    file.write_text(json.dumps({"suite_sha256": "different", "observations": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="different frozen"):
        ImportedAdapter(file, suite["fingerprint"])


def test_imported_observation_uses_real_fixture_source_and_keeps_missing_usage(tmp_path):
    suite = load_suite()
    result = {"route": "WORK_CANDIDATE", "model_calls": 2, "cause_confirmed": False,
              "fix_applied": False, "fix_verified": False, "questions": []}
    source = tmp_path / "recorded-source.json"
    source.write_text(json.dumps({"source_kind": "TEST_DOUBLE", "result": result}), encoding="utf-8")
    measurement = {"model_calls": 2, "model_events": [{"status": "success", "usage": {"prompt_tokens": 7}},
        {"status": "timeout", "usage": None}], "human_seconds": None, "manual_actions": None}
    record = {"condition": "coding_agent", "case_id": "contract-1", "memory_enabled": False,
              "execution_origin": "RECORDED_DOUBLE",
              "execution_scope": "prepare_change_with_human", "resource_contract_sha256": hashlib.sha256(
                  json.dumps(suite["resources"], sort_keys=True).encode()).hexdigest(),
              "source_ref": source.name, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "result": result, "measurements": measurement}
    file = tmp_path / "import.json"
    file.write_text(json.dumps({"suite_sha256": suite["fingerprint"], "observations": [record]}), encoding="utf-8")
    adapter = ImportedAdapter(file, suite["fingerprint"])
    ctx = AdapterContext("coding_agent", "local", False, tmp_path / "memory.db", tmp_path,
                         suite["root"], suite["resources"])
    execution = adapter({"case_id": "contract-1"}, ctx)
    metrics = usage_metrics(execution.result, execution.measurements)
    assert execution.result == result and execution.provenance == "IMPORTED_RECORDED_DOUBLE"
    assert metrics["prompt_tokens"] is None and metrics["prompt_tokens_observed_sum"] == 7
    assert execution.measurements["human_seconds"] is None
    source.write_text("changed record", encoding="utf-8")
    with pytest.raises(ValueError, match="verifiable"):
        adapter({"case_id": "contract-1"}, ctx)


@pytest.mark.parametrize("enabled", [False, True])
def test_public_main_adapter_memory_option_persistence_and_observer_agree(tmp_path, enabled):
    suite = load_suite()
    initial, copied = tmp_path / "initial.db", tmp_path / "memory.db"
    create_initial_memory(initial, json.loads((suite["root"] / "initial_memory.json").read_text(encoding="utf-8")))
    clone_memory(initial, copied)
    condition = "tracebridge_memory_on" if enabled else "tracebridge_memory_off"
    ctx = AdapterContext(condition, "local", enabled, copied, tmp_path, suite["root"], suite["resources"])
    material = json.loads((suite["root"] / "inputs/validation-1.json").read_text(encoding="utf-8"))
    execution = local_adapter(material, ctx)
    result = execution.result
    assert result["route"] == "GUIDANCE" and execution.measurements["external_model_calls"] == 0
    assert result["memory_search"]["status"] == ("OK" if enabled else "DISABLED")
    assert result["persistence"]["status"] == "SAVED"
    assert result["observability"]["run_id"] == result["run_id"]
    assert result["observability"]["coverage_status"] == "MATCHED_REPORTED_COUNTS"
    assert result["observability"]["observed_calls"]["model"] == 0
    with sqlite3.connect(copied) as db:
        assert db.execute("select count(*) from runs").fetchone()[0] == 2


def test_main_double_timeout_keeps_failure_usage_and_actual_model_count(tmp_path):
    suite = load_suite()
    initial, copied = tmp_path / "initial.db", tmp_path / "memory.db"
    create_initial_memory(initial, json.loads((suite["root"] / "initial_memory.json").read_text(encoding="utf-8")))
    clone_memory(initial, copied)
    ctx = AdapterContext("tracebridge_memory_off", "doubles", False, copied, tmp_path, suite["root"], suite["resources"])
    material = json.loads((suite["root"] / "inputs/failure-2.json").read_text(encoding="utf-8"))
    execution = local_adapter(material, ctx)
    metrics = usage_metrics(execution.result, execution.measurements)
    assert execution.result["run_status"] in {"PARTIAL_FAILURE", "TIMED_OUT"}
    assert metrics["failed_model_calls"] == metrics["model_calls"] == 1
    assert metrics["prompt_tokens"] is None and metrics["usage_missing_calls"] == 1
    assert execution.result["observability"]["observed_calls"]["model"] == 1
    assert execution.result["observability"]["coverage_status"] == "MATCHED_REPORTED_COUNTS"


def test_observer_counts_failures_missing_tokens_and_partial_coverage(tmp_path):
    observer = InvestigationObserver("measured-run", tmp_path)
    observer.record_tool("find_logs", status="failed", elapsed_ms=3, error_type="OSError")
    observer.record_model("fixture-model", status="success", elapsed_ms=4, usage={"prompt_tokens": 8, "completion_tokens": 2})
    observer.record_model("fixture-model", status="timeout", elapsed_ms=2, usage=None, error_type="TimeoutError")
    summary = observer.finish("PARTIAL_FAILURE", expected_tool_calls=2, expected_model_calls=2)
    assert summary["coverage_status"] == "PARTIAL"
    assert summary["failed_model_calls"] == 1 and summary["failed_tool_calls"] == 1
    assert summary["usage"]["prompt_tokens"] is None
    assert summary["usage"]["prompt_tokens_observed_sum"] == 8
    assert not summary["main_flow_verified"] and summary["nat"]["status"] == "NOT_CONFIGURED"
    with pytest.raises(ValueError, match="already finished"):
        observer.record_tool("find_logs", status="success", elapsed_ms=1)


def test_nat_sink_uses_actual_public_payload_without_default_zero_usage(tmp_path):
    payloads = []
    manager = type("Manager", (), {"push_intermediate_step": lambda self, payload: payloads.append(payload)})()
    observer = InvestigationObserver("bridge-double", tmp_path, nat_sink=NATEventSink(manager))
    observer.record_model("TEST_DOUBLE", status="failed", elapsed_ms=3, usage=None)
    summary = observer.finish("FAILED", expected_tool_calls=0, expected_model_calls=1)
    assert [payload.event_type.value for payload in payloads] == ["LLM_START", "LLM_END"]
    assert payloads[0].UUID == payloads[1].UUID and all(payload.usage_info is None for payload in payloads)
    assert summary["nat"]["status"] == "EVENTS_DELIVERED" and not summary["main_flow_verified"]


def test_nat_sink_failure_is_distinct_from_investigation_status(tmp_path):
    def reject(event):
        raise RuntimeError("controlled telemetry rejection")
    observer = InvestigationObserver("rejected-telemetry", tmp_path, nat_sink=reject)
    observer.record_tool("get_version", status="success", elapsed_ms=2)
    summary = observer.finish("COMPLETED", expected_tool_calls=1, expected_model_calls=0)
    assert summary["run_status"] == "COMPLETED" and summary["nat"]["status"] == "FAILED"


def test_result_only_fallback_never_claims_substep_tracing(tmp_path):
    summary = observe_result({"run_id": "summary-run", "run_status": "COMPLETED", "model_calls": 2}, tmp_path)
    assert summary["coverage_status"] == "SUMMARY_ONLY" and summary["observed_calls"]["model"] == 0
    assert summary["usage"]["prompt_tokens"] is None and summary["usage"]["prompt_tokens_missing_calls"] == 2
    assert not summary["main_flow_verified"]


def test_packaging_excludes_disguised_db_credentials_and_runtime_output(tmp_path):
    for folder in ("tracebridge", "docs", "examples/evaluation/assets"):
        (tmp_path / folder).mkdir(parents=True)
    (tmp_path / "tracebridge/ok.py").write_text("# fixture", encoding="utf-8")
    with sqlite3.connect(tmp_path / "docs/disguised.json") as db:
        db.execute("create table secret(value text)")
    (tmp_path / "docs/credentials.json").write_text('{"key":"PRIVATE"}', encoding="utf-8")
    (tmp_path / "docs/.secrets.json").write_text('{"key":"PRIVATE"}', encoding="utf-8")
    (tmp_path / "docs/archive.db-wal.json").write_text("PRIVATE", encoding="utf-8")
    (tmp_path / ".env").write_text("PRIVATE", encoding="utf-8")
    (tmp_path / ".env.example").write_text("KEY=placeholder", encoding="utf-8")
    (tmp_path / "requirements-fixture.lock").write_text("package==1.0\n", encoding="utf-8")
    (tmp_path / "examples/evaluation/assets/synthetic.png").write_bytes(b"synthetic-fixture")
    package = create_package(root=tmp_path)
    with ZipFile(package) as archive:
        names = archive.namelist()
        assert "tracebridge/ok.py" in names and ".env.example" in names and "requirements-fixture.lock" in names
        assert "examples/evaluation/assets/synthetic.png" in names
        assert not {".env", "docs/disguised.json", "docs/credentials.json", "docs/.secrets.json", "docs/archive.db-wal.json"} & set(names)
        manifest = json.loads(archive.read("DRAFT_PACKAGE_MANIFEST.json"))
        assert manifest["artifact_status"] == "DRAFT" and manifest["team_name"] is None
        assert not manifest["fresh_environment_verified"]
        for name, record in manifest["files"].items():
            assert hashlib.sha256(archive.read(name)).hexdigest() == record["sha256"]
    assert "DRAFT" in package.name


def test_locked_direct_dependencies_match_installed_environment():
    import importlib.metadata
    import platform
    import sys
    from packaging.requirements import Requirement
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    for value in project["project"]["dependencies"]:
        requirement = Requirement(value)
        assert str(requirement.specifier).startswith("==")
        assert importlib.metadata.version(requirement.name) in requirement.specifier
    locks = list(ROOT.glob("requirements-*.lock"))
    assert len(locks) >= 3
    # These lockfiles describe the validated Windows 3.12.7 environment.
    # Linux CI installs the pinned project requirements but may resolve optional
    # transitive packages to different versions.
    validated_platform = sys.platform == "win32" and platform.machine().lower() in {"amd64", "x86_64"} and sys.version_info[:3] == (3, 12, 7)
    for file in locks:
        for line in file.read_text(encoding="utf-8").splitlines():
            if line and not line.startswith("#"):
                requirement = Requirement(line)
                assert str(requirement.specifier).startswith("==")
                if validated_platform and "windows-py312" in file.name:
                    assert importlib.metadata.version(requirement.name) in requirement.specifier
