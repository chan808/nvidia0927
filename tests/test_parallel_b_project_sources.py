"""B local registration/contract adapter tests, isolated to --basetemp parallel-b."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import shutil
import socket
import subprocess

import pytest

from tracebridge.evidence import EvidenceError, LocalBundleEvidenceSource, ProjectEvidenceSource
from tracebridge.project_contracts import load_project_contract
from tracebridge.project_profile import load_project_profile, observe_project_version, read_project_logs
from tracebridge.triage import analyze, route_verdict


EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "parallel_b" / "ledger_demo"


def copy_project(tmp_path):
    project = tmp_path / "independent-ledger"
    shutil.copytree(EXAMPLE, project)
    return project


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def update_profile(project, **changes):
    data = read_json(project / "profile.json")
    data.update(changes)
    write_json(project / "profile.json", data)
    return load_project_profile(project / "profile.json")


def read_event(project):
    return json.loads((project / "events.jsonl").read_text(encoding="utf-8").splitlines()[0])


def write_events(project, events):
    (project / "events.jsonl").write_text("".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events), encoding="utf-8")


def test_project_without_agolive_name_reads_independent_contract_and_current_input(tmp_path):
    project = copy_project(tmp_path)
    profile = load_project_profile(project / "profile.json")
    assert "agolive" not in project.name.lower()
    assert profile.project_id == "ledger-demo"
    assert profile.code_roots == (project / "src",)
    collection = read_project_logs(profile)
    assert collection["complete"] is True
    assert "contract" not in collection["events"][0]
    source = ProjectEvidenceSource(profile)
    contract = source.get_contract("ledger-001")
    assert contract["status"] == "OBSERVED"
    assert contract["openapi"]["required"] == ["accountId", "name"]
    assert contract["caller"]["source_verified"] is True
    assert contract["caller"]["input_fields"] == ["accountId", "name"]
    assert contract["provenance"]["artifact_version"] == "2.0"
    assert contract["versions"]["contract"] == "ledger-r1"
    assert len(contract["provenance"]["sha256"]) == 64
    verdict = analyze("ledger-001", "HTTP 500, 계정 만들기가 안돼요", source=source)
    assert verdict["contract_analysis"]["versions"]["status"] == "MATCHED"
    assert verdict["reported_status"] == 500 and verdict["observed_status"] == 422
    assert verdict["symptom_status"] == "REQUEST_REJECTED"
    assert verdict["responsibility"]["status"] == "CALLER_DEFECT"
    assert route_verdict(verdict, "EXACT_ID")["route"] == "WORK_CANDIDATE"
    assert contract["execution_authorized"] is False


def test_static_contract_lookup_does_not_invent_current_input_fields(tmp_path):
    profile = load_project_profile(copy_project(tmp_path) / "profile.json")
    contract = load_project_contract(profile, "POST", "/accounts")
    assert "input_fields" not in contract["caller"]
    assert contract["versions"]["runtime"] is None


def test_source_is_protocol_compatible_with_existing_scoped_event_source(tmp_path):
    project = copy_project(tmp_path)
    profile = load_project_profile(project / "profile.json")
    event_source = LocalBundleEvidenceSource(read_event(project))
    with pytest.raises(EvidenceError, match="contract"):
        event_source.get_contract("ledger-001")
    source = ProjectEvidenceSource(profile, event_source=event_source)
    assert source.get_trace("ledger-001")["response_status"] == 422
    assert source.get_backend_evidence("ledger-001")["logs"]
    assert source.get_contract("ledger-001")["openapi"]["required"] == ["accountId", "name"]
    assert analyze("ledger-001", source=source)["diagnosis_type"] == "contract_mismatch"
    with pytest.raises(EvidenceError, match="scope"):
        source.get_trace("other")


@pytest.mark.parametrize("change", ["unregistered", "missing_file", "operation", "dto", "caller"])
def test_missing_contract_or_responsibility_artifact_abstains_without_losing_status(tmp_path, change):
    project = copy_project(tmp_path)
    if change == "unregistered":
        profile = update_profile(project, openapi_path=None)
    elif change == "missing_file":
        profile = update_profile(project, openapi_path="not-present.json")
    elif change == "operation":
        event = read_event(project)
        event["trace"]["path"] = "/other"
        write_events(project, [event])
        profile = load_project_profile(project / "profile.json")
    else:
        profile = update_profile(project, **{f"{change}_path" if change == "dto" else "caller_evidence_path": None})
    source = ProjectEvidenceSource(profile)
    verdict = analyze("ledger-001", "HTTP 500", source=source)
    assert verdict["observed_status"] == 422
    assert verdict["symptom_status"] == "REQUEST_REJECTED"
    assert verdict["responsibility"]["status"] == "UNCONFIRMED"
    assert route_verdict(verdict, "EXACT_ID")["route"] == "INVESTIGATE"
    if change in {"unregistered", "missing_file", "operation"}:
        with pytest.raises(EvidenceError) as unavailable:
            source.get_contract("ledger-001")
        contract = unavailable.value.details
        assert contract["status"] == "UNOBSERVED"
        assert contract["openapi"] is None
        assert contract["limitations"]


@pytest.mark.parametrize("available", [True, False])
def test_existing_offline_consumer_keeps_working_with_adapter_and_missing_contract(tmp_path, available):
    from tracebridge.agent import run_offline

    project = copy_project(tmp_path)
    profile = load_project_profile(project / "profile.json") if available else update_profile(project, openapi_path=None)
    result = run_offline("ledger-001", "HTTP 500", source=ProjectEvidenceSource(profile))
    assert result["mode"] == "offline_source"
    assert result["verdict"]["observed_status"] == 422
    assert result["verdict"]["contract_analysis"]["status"] == ("COMPARED" if available else "CONTRACT_UNAVAILABLE")
    assert [step["tool"] for step in result["steps"]] == (["get_trace", "get_contract"] if available else ["get_trace"])


@pytest.mark.parametrize("component", ["runtime_snapshot", "code_snapshot", "openapi", "dto", "caller"])
def test_registered_runtime_and_source_version_mismatch_abstains(tmp_path, component):
    project = copy_project(tmp_path)
    if component in {"runtime_snapshot", "code_snapshot"}:
        data = read_json(project / "version.json")
        data["runtime_version" if component == "runtime_snapshot" else "code_version"] = "ledger-r2"
        write_json(project / "version.json", data)
    else:
        data = read_json(project / f"{component}.json")
        data["x-code-version" if component == "openapi" else "code_version"] = "ledger-r2"
        write_json(project / f"{component}.json", data)
    verdict = analyze("ledger-001", source=ProjectEvidenceSource(load_project_profile(project / "profile.json")))
    assert verdict["observed_status"] == 422
    assert verdict["responsibility"]["status"] == "VERSION_MISMATCH"
    assert verdict["diagnosis_type"] == "version_mismatch"
    assert route_verdict(verdict, "EXACT_ID")["route"] == "INVESTIGATE"


def test_unobserved_runtime_is_explicit_and_not_replaced_by_local_code_version(tmp_path):
    project = copy_project(tmp_path)
    event = read_event(project)
    del event["trace"]["version"]
    write_events(project, [event])
    profile = update_profile(project, version_observation={"method": "none"})
    assert observe_project_version(profile)["status"] == "UNOBSERVED"
    verdict = analyze("ledger-001", source=ProjectEvidenceSource(profile))
    assert verdict["contract_analysis"]["versions"]["runtime"] is None
    assert verdict["responsibility"]["status"] == "UNCONFIRMED"


def test_request_values_are_masked_but_observed_types_are_preserved(tmp_path):
    project = copy_project(tmp_path)
    event = read_event(project)
    event["trace"]["request"] = {"account_id": 17, "name": "private@example.invalid", "token": "secret-token"}
    event["logs"] = ["ValueError private@example.invalid token=secret-token"]
    write_events(project, [event])
    collection = read_project_logs(load_project_profile(project / "profile.json"))
    trace = collection["events"][0]["trace"]
    assert trace["request"]["account_id"] == "[VALUE]"
    assert trace["request_types"]["account_id"] == "integer"
    assert "private@example.invalid" not in str(collection)
    assert "secret-token" not in str(collection)


def test_malformed_or_unscoped_line_marks_collection_incomplete(tmp_path):
    project = copy_project(tmp_path)
    valid = read_event(project)
    (project / "events.jsonl").write_text(json.dumps(valid) + "\n{bad json\n", encoding="utf-8")
    profile = load_project_profile(project / "profile.json")
    collection = read_project_logs(profile)
    assert len(collection["events"]) == 1
    assert collection["complete"] is False
    verdict = analyze("ledger-001", source=ProjectEvidenceSource(profile))
    assert verdict["finding_status"] == "INCONCLUSIVE"
    assert verdict["source_status"] == "UNAVAILABLE"
    assert route_verdict(verdict, "EXACT_ID")["route"] == "INVESTIGATE"
    unscoped = deepcopy(valid)
    del unscoped["trace"]["environment"]
    write_events(project, [unscoped])
    assert read_project_logs(profile)["complete"] is False


@pytest.mark.parametrize("limit", ["bytes", "records"])
def test_bounded_reads_preserve_incompleteness_and_acquired_observations(tmp_path, limit):
    project = copy_project(tmp_path)
    event = read_event(project)
    other = deepcopy(event)
    other["trace"]["trace_id"] = "ledger-002"
    write_events(project, [event, other])
    profile = load_project_profile(project / "profile.json")
    if limit == "records":
        collection = read_project_logs(profile, max_records=1)
        assert len(collection["events"]) == 1
    else:
        first_line = (project / "events.jsonl").read_bytes().splitlines(keepends=True)[0]
        collection = read_project_logs(profile, max_bytes=len(first_line))
        assert len(collection["events"]) == 1
    assert collection["complete"] is False
    assert f"{limit[:-1] if limit == 'records' else 'byte'}_limit" in str(collection["limitations"])


def test_conflicting_observations_cannot_be_selected_as_a_single_trace(tmp_path):
    project = copy_project(tmp_path)
    event = read_event(project)
    conflict = deepcopy(event)
    conflict["trace"]["response_status"] = 500
    write_events(project, [event, conflict])
    source = ProjectEvidenceSource(load_project_profile(project / "profile.json"))
    with pytest.raises(EvidenceError, match="Conflicting"):
        source.get_trace("ledger-001")
    verdict = analyze("ledger-001", source=source)
    assert verdict["finding_status"] == "INCONCLUSIVE"
    assert verdict["repro_eligible"] is False


def test_local_json_catalog_and_explicit_outside_scope(tmp_path):
    project = copy_project(tmp_path)
    event = read_event(project)
    outside = deepcopy(event)
    outside["trace"]["service"] = "other-api"
    write_json(project / "events.json", {"project_id": "ledger-demo", "events": [outside, event]})
    profile = update_profile(project, log_sources=[{"id": "json-catalog", "path": "events.json", "format": "json"}])
    collection = read_project_logs(profile)
    assert collection["complete"] is True
    assert len(collection["events"]) == 1
    assert ProjectEvidenceSource(profile).get_trace("ledger-001")["service"] == "ledger-api"


def test_missing_response_status_is_not_invented_by_contract_lookup(tmp_path):
    project = copy_project(tmp_path)
    event = read_event(project)
    del event["trace"]["response_status"]
    write_events(project, [event])
    verdict = analyze("ledger-001", source=ProjectEvidenceSource(load_project_profile(project / "profile.json")))
    assert verdict["observed_status"] is None
    assert verdict["symptom_status"] == "UNOBSERVED"
    assert verdict["responsibility"]["status"] == "UNCONFIRMED"


@pytest.mark.parametrize("field", ["version", "build_revision"])
def test_registered_log_version_method_and_conflicts(tmp_path, field):
    project = copy_project(tmp_path)
    event = read_event(project)
    event["trace"][field] = event["trace"].pop("version")
    write_events(project, [event])
    profile = update_profile(project, version_observation={"method": "log_field", "field": field})
    observed = observe_project_version(profile)
    assert observed["status"] == "OBSERVED" and observed["runtime_version"] == "ledger-r1"
    another = deepcopy(event)
    another["trace"].update(trace_id="ledger-002", **{field: "ledger-r2"})
    write_events(project, [event, another])
    assert observe_project_version(profile)["status"] == "CONFLICT"
    verdict = analyze("ledger-001", source=ProjectEvidenceSource(profile))
    assert verdict["responsibility"]["status"] == "VERSION_MISMATCH"


def test_registered_caller_source_hash_change_invalidates_code_evidence(tmp_path):
    project = copy_project(tmp_path)
    (project / "src/client.py").write_text("raise RuntimeError('must never execute')\n", encoding="utf-8")
    source = ProjectEvidenceSource(load_project_profile(project / "profile.json"))
    contract = source.get_contract("ledger-001")
    assert contract["caller"] is None
    assert contract["caller_provenance"]["status"] == "UNVERIFIED"
    assert "hash" in str(contract["limitations"])
    assert analyze("ledger-001", source=source)["responsibility"]["status"] == "UNCONFIRMED"


def test_explicit_lf_code_hash_survives_checkout_line_endings_without_ignoring_changes(tmp_path):
    project = copy_project(tmp_path)
    code_path = project / "src/client.py"
    normalized = code_path.read_bytes().replace(b"\r\n", b"\n")
    code_path.write_bytes(normalized.replace(b"\n", b"\r\n"))
    profile = load_project_profile(project / "profile.json")
    contract = load_project_contract(profile, "POST", "/accounts")
    assert contract["caller"]["source_verified"] is True
    assert contract["caller"]["source_hash_format"] == "lf"
    assert contract["caller"]["source_bytes_sha256"] != contract["caller"]["source_sha256"]
    code_path.write_bytes(normalized + b"# content changed\n")
    assert load_project_contract(profile, "POST", "/accounts")["caller"] is None


@pytest.mark.parametrize("ref", ["https://example.invalid/schema.json", "../outside.json", "#/components/schemas/AccountInput"])
def test_external_or_recursive_schema_references_are_unobserved_without_fetching(tmp_path, ref):
    project = copy_project(tmp_path)
    document = read_json(project / "openapi.json")
    document["components"]["schemas"]["AccountInput"] = {"$ref": ref}
    write_json(project / "openapi.json", document)
    contract = load_project_contract(load_project_profile(project / "profile.json"), "POST", "/accounts")
    assert contract["status"] == "UNOBSERVED"
    assert contract["openapi"] is None
    assert contract["limitations"]


def test_unrelated_recursive_response_does_not_block_request_contract(tmp_path):
    project = copy_project(tmp_path)
    document = read_json(project / "openapi.json")
    document["components"]["schemas"]["ResponseNode"] = {"type": "object", "properties": {"child": {"$ref": "#/components/schemas/ResponseNode"}}}
    document["paths"]["/accounts"]["post"]["responses"]["201"] = {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/ResponseNode"}}}}
    write_json(project / "openapi.json", document)
    assert load_project_contract(load_project_profile(project / "profile.json"), "POST", "/accounts")["status"] == "OBSERVED"


@pytest.mark.parametrize("extra", [{"command": "anything"}, {"automation_level": "A4"}, {"token": "private"}, {"db_connection": "anything"}])
def test_profile_cannot_register_commands_secrets_or_execution_permissions(tmp_path, extra):
    project = copy_project(tmp_path)
    data = read_json(project / "profile.json")
    data.update(extra)
    write_json(project / "profile.json", data)
    with pytest.raises(EvidenceError, match="unsupported"):
        load_project_profile(project / "profile.json")


@pytest.mark.parametrize("path", ["../outside.json", "https://example.invalid/openapi.json", "\\\\host\\private\\api.json"])
def test_registered_sources_cannot_escape_local_root(tmp_path, path):
    project = copy_project(tmp_path)
    with pytest.raises(EvidenceError):
        update_profile(project, openapi_path=path)


def test_explicit_local_project_root_can_be_registered_from_a_separate_config(tmp_path):
    project = copy_project(tmp_path)
    data = read_json(project / "profile.json")
    data["root"] = str(project)
    separate_config = tmp_path / "registration.json"
    write_json(separate_config, data)
    profile = load_project_profile(separate_config)
    assert profile.root == project.resolve()
    assert ProjectEvidenceSource(profile).get_contract("ledger-001")["status"] == "OBSERVED"


def test_policy_refs_are_identifiers_and_never_execution_authority(tmp_path):
    project = copy_project(tmp_path)
    profile = update_profile(project, policy_refs=["seed-signup-a2-v1"])
    assert profile.policy_refs == ("seed-signup-a2-v1",)
    assert read_project_logs(profile)["execution_authorized"] is False
    assert observe_project_version(profile)["execution_authorized"] is False
    assert load_project_contract(profile, "POST", "/accounts")["execution_authorized"] is False
    verdict = analyze("ledger-001", source=ProjectEvidenceSource(profile))
    assert verdict["repro_eligible"] is False
    assert "execution_authorized" not in route_verdict(verdict, "EXACT_ID")


def test_sources_use_no_network_database_or_project_command(tmp_path, monkeypatch):
    profile = load_project_profile(copy_project(tmp_path) / "profile.json")

    def forbidden(*args, **kwargs):
        raise AssertionError("B sources must not perform external or project execution")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    source = ProjectEvidenceSource(profile)
    assert source.get_contract("ledger-001")["status"] == "OBSERVED"
    assert analyze("ledger-001", source=source)["diagnosis_type"] == "contract_mismatch"
    with pytest.raises(EvidenceError, match="migration state"):
        source.get_migration_state("ledger-001")
