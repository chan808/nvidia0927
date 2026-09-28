"""B-owned structural/attribution variants; all evidence is local and synthetic."""

from copy import deepcopy

import pytest

from tracebridge.contract_analysis import analyze_contract, compare_contract
from tracebridge.evidence import LocalBundleEvidenceSource
from tracebridge.triage import analyze, route_verdict, summarize


def bundle_for(required="accountId", sent="account_id", *, status=422):
    return {
        "trace": {"trace_id": "b-variant", "service": "accounts", "environment": "test", "method": "POST",
                  "path": "/accounts", "version": "r1", "request": {sent: "synthetic", "name": "Synthetic"}, "response_status": status},
        "contract": {"required": [required, "name"], "properties": {required: "string", "name": "string"}},
        "dto": {required: "str", "name": "str"},
        "contract_context": {"provenance": {"source": "registered-openapi.json", "code_version": "r1"},
                             "versions": {"runtime": "r1", "contract": "r1", "dto": "r1", "caller": "r1"}},
        "caller": {"source": "src/caller.py#serialize", "source_kind": "caller_code", "version": "r1",
                   "method": "POST", "path": "/accounts", "field_mapping": {sent: required, "name": "name"},
                   "required_inputs": {required: required, "name": "name"}, "input_fields": [required, "name"],
                   "input_types": {required: "string", "name": "string"}},
        "logs": [],
    }


def verdict_for(bundle):
    return analyze(bundle["trace"]["trace_id"], "HTTP 500, 가입이 안 돼요", source=LocalBundleEvidenceSource(bundle))


@pytest.mark.parametrize("required,sent", [("userId", "user_id"), ("accountId", "account_id"), ("orderRef", "order_ref")])
def test_same_structure_same_verified_caller_verdict(required, sent):
    verdict = verdict_for(bundle_for(required, sent))
    differences = verdict["contract_analysis"]
    assert differences["missing_fields"] == [required]
    assert differences["unexpected_fields"] == [sent]
    assert differences["similarity_candidates"] == [{"observed_field": sent, "required_field": required, "status": "CANDIDATE"}]
    assert differences["automatic_rename"] is False
    assert verdict["diagnosis_type"] == "contract_mismatch"
    assert verdict["responsibility"]["status"] == "CALLER_DEFECT"
    assert route_verdict(verdict, "EXACT_ID")["route"] == "WORK_CANDIDATE"
    assert verdict["repro_eligible"] is False
    assert "같은 검사" in verdict["next_action"]
    assert "이름을 바꾸지" in verdict["next_action"]


@pytest.mark.parametrize("required,sent", [("userId", "user_id"), ("accountId", "account_id")])
def test_dto_and_spelling_alone_do_not_assign_caller(required, sent):
    bundle = bundle_for(required, sent)
    del bundle["caller"]
    verdict = verdict_for(bundle)
    assert verdict["contract_analysis"]["missing_fields"] == [required]
    assert verdict["finding_status"] == "OBSERVED_CONTRACT_DIFFERENCE"
    assert verdict["responsibility"]["status"] == "UNCONFIRMED"
    assert route_verdict(verdict, "EXACT_ID")["route"] == "INVESTIGATE"
    assert verdict["product_status"] == "UNCONFIRMED"


def test_input_name_without_observed_type_is_not_confirmed_as_valid_input():
    bundle = bundle_for()
    del bundle["caller"]["input_types"]
    verdict = verdict_for(bundle)
    assert verdict["responsibility"]["status"] == "UNCONFIRMED"
    assert route_verdict(verdict, "EXACT_ID")["route"] == "INVESTIGATE"


def test_missing_input_requires_input_and_serialization_evidence():
    bundle = bundle_for(sent="accountId")
    del bundle["trace"]["request"]["name"]
    bundle["caller"]["input_fields"] = ["accountId"]
    verdict = verdict_for(bundle)
    assert verdict["diagnosis_type"] == "expected_validation"
    assert verdict["responsibility"]["status"] == "INPUT_OMISSION"
    assert route_verdict(verdict, "EXACT_ID")["route"] == "GUIDANCE"
    assert verdict["product_status"] == "UNCONFIRMED"
    without_caller = deepcopy(bundle)
    del without_caller["caller"]
    assert route_verdict(verdict_for(without_caller), "EXACT_ID")["route"] == "INVESTIGATE"


def test_caller_drops_valid_input_even_without_similar_field_spelling():
    bundle = bundle_for(sent="accountId")
    del bundle["trace"]["request"]["name"]
    del bundle["caller"]["field_mapping"]["name"]
    verdict = verdict_for(bundle)
    assert verdict["contract_analysis"]["similarity_candidates"] == []
    assert verdict["responsibility"]["status"] == "CALLER_DEFECT"
    assert verdict["responsibility"]["fields"] == ["name"]
    assert route_verdict(verdict, "EXACT_ID")["route"] == "WORK_CANDIDATE"


def test_422_alone_never_establishes_expected_validation_or_product_health():
    bundle = bundle_for(sent="accountId")
    bundle["trace"]["request"] = {}
    del bundle["caller"]
    verdict = verdict_for(bundle)
    assert verdict["observed_status"] == 422
    assert verdict["symptom_status"] == "REQUEST_REJECTED"
    assert verdict["product_status"] == "UNCONFIRMED"
    assert verdict["diagnosis_type"] == "contract_difference_unconfirmed"
    assert route_verdict(verdict, "EXACT_ID")["route"] == "INVESTIGATE"


def test_wrong_reported_status_does_not_erase_actual_signup_failure():
    verdict = verdict_for(bundle_for())
    assert verdict["reported_status"] == 500
    assert verdict["observed_status"] == 422
    assert next(item for item in verdict["claim_items"] if item["facet"] == "http_status")["status"] == "CONTRADICTED"
    assert "가입이 안" in verdict["claim"]
    assert verdict["symptom_status"] == "REQUEST_REJECTED"
    assert verdict["product_status"] == "CALLER_DEFECT_OBSERVED"
    assert "실제 HTTP 422: 불일치" in summarize(verdict)
    assert "호출자" in summarize(verdict)


@pytest.mark.parametrize("status", [201, 200, 500, 502])
def test_success_or_5xx_is_not_explained_by_structural_difference(status):
    verdict = verdict_for(bundle_for(status=status))
    assert verdict["contract_analysis"]["missing_fields"] == ["accountId"]
    assert verdict["responsibility"]["status"] == "UNCONFIRMED"
    assert verdict["finding_status"] != "CONFIRMED_MISMATCH"
    assert verdict["product_status"] == "UNCONFIRMED"
    assert verdict["diagnosis_type"] == "unknown"
    assert route_verdict(verdict, "EXACT_ID")["route"] == "INVESTIGATE"
    assert verdict["symptom_status"] == ("SERVER_ERROR_OBSERVED" if status >= 500 else "HTTP_SUCCESS_OBSERVED")


def test_no_contract_keeps_actual_response_and_abstains():
    bundle = bundle_for()
    del bundle["contract"]
    verdict = verdict_for(bundle)
    assert verdict["contract_analysis"]["status"] == "CONTRACT_UNAVAILABLE"
    assert verdict["contract_analysis"]["missing_fields"] == []
    assert verdict["observed_status"] == 422
    assert verdict["responsibility"]["status"] == "UNCONFIRMED"
    assert route_verdict(verdict, "EXACT_ID")["route"] == "INVESTIGATE"


@pytest.mark.parametrize("component", ["runtime", "contract", "dto", "caller", "code"])
def test_runtime_evidence_version_mismatch_blocks_work(component):
    bundle = bundle_for()
    if component == "runtime":
        bundle["trace"]["version"] = "r2"
    elif component == "caller":
        bundle["caller"]["version"] = "r2"
    elif component == "code":
        bundle["trace"]["code_version"] = "r2"
    else:
        bundle["contract_context"]["versions"][component] = "r2"
    verdict = verdict_for(bundle)
    assert verdict["contract_analysis"]["versions"]["status"] == "MISMATCH"
    assert verdict["diagnosis_type"] == "version_mismatch"
    assert verdict["responsibility"]["status"] == "VERSION_MISMATCH"
    assert verdict["symptom_status"] == "REQUEST_REJECTED"
    assert route_verdict(verdict, "EXACT_ID")["route"] == "INVESTIGATE"


def test_unobserved_runtime_is_not_filled_from_api_version_or_caller_assumption():
    bundle = bundle_for()
    del bundle["trace"]["version"]
    del bundle["contract_context"]["versions"]["runtime"]
    verdict = verdict_for(bundle)
    assert verdict["contract_analysis"]["versions"]["status"] == "UNOBSERVED"
    assert verdict["responsibility"]["status"] == "UNCONFIRMED"
    assert route_verdict(verdict, "EXACT_ID")["route"] == "INVESTIGATE"


@pytest.mark.parametrize("defect", ["dto", "caller_path", "caller_mapping", "caller_hash", "input_observation", "contract_path"])
def test_contradicting_or_missing_responsibility_evidence_abstains(defect):
    bundle = bundle_for()
    if defect == "dto":
        bundle["dto"]["accountId"] = "int"
    elif defect == "caller_path":
        bundle["caller"]["path"] = "/other"
    elif defect == "caller_mapping":
        bundle["caller"]["field_mapping"] = {"accountId": "accountId", "name": "name"}
    elif defect == "caller_hash":
        bundle["caller"]["source_verified"] = False
    elif defect == "input_observation":
        del bundle["caller"]["input_fields"]
    else:
        data = LocalBundleEvidenceSource(bundle).get_contract("b-variant")
        data["path"] = "/other"
        analysis = analyze_contract(bundle["trace"], data)
        assert analysis["status"] == "SCOPE_MISMATCH"
        assert analysis["missing_fields"] == []
        assert analysis["responsibility"]["status"] == "UNCONFIRMED"
        return
    verdict = verdict_for(bundle)
    assert verdict["responsibility"]["status"] == "UNCONFIRMED"
    assert route_verdict(verdict, "EXACT_ID")["route"] == "INVESTIGATE"


@pytest.mark.parametrize("value,expected,actual", [(True, "integer", "boolean"), ("17", "integer", "string"), (17, "string", "integer"), (None, "string", "null")])
def test_real_json_types_are_checked_without_coercion(value, expected, actual):
    analysis = compare_contract({"value": value}, {"required": ["value"], "properties": {"value": expected}})
    assert analysis["type_mismatches"] == [{"field": "value", "expected": [expected], "observed": actual}]


@pytest.mark.parametrize("marker", ["[VALUE]", "[REDACTED]", "[MASKED]", "[EMAIL]", "***"])
def test_redacted_values_do_not_prove_string_types(marker):
    analysis = compare_contract({"count": marker}, {"required": ["count"], "properties": {"count": "integer"}})
    assert analysis["type_mismatches"] == []
    assert analysis["unobserved_types"] == ["count"]
    assert analysis["complete"] is False


def test_types_captured_before_redaction_are_distinct_from_guessed_types():
    contract = {"required": ["count"], "properties": {"count": "integer"}}
    captured = compare_contract({"count": "[VALUE]"}, contract, observed_types={"count": "string"})
    assert captured["type_mismatches"][0]["observed"] == "string"
    conflicting = compare_contract({"count": 4}, contract, observed_types={"count": "string"})
    assert conflicting["type_mismatches"] == []
    assert conflicting["complete"] is False
    explicitly_unknown = compare_contract({"count": 4}, contract, observed_types={"count": "integer"}, redacted_fields=["count"])
    assert explicitly_unknown["unobserved_types"] == ["count"]


def test_type_defect_uses_captured_input_types_and_caller_evidence():
    bundle = bundle_for(sent="accountId")
    bundle["trace"]["request"]["accountId"] = 17
    verdict = verdict_for(bundle)
    assert verdict["contract_analysis"]["type_mismatches"] == [{"field": "accountId", "expected": ["string"], "observed": "integer"}]
    assert verdict["responsibility"]["status"] == "CALLER_DEFECT"
    del bundle["caller"]["input_types"]
    assert verdict_for(bundle)["responsibility"]["status"] == "UNCONFIRMED"


def test_field_names_only_do_not_invent_values_or_types():
    contract = {"required": ["accountId", "name"], "properties": {"accountId": "integer", "name": "string"}}
    analysis = compare_contract(None, contract, request_fields=["account_id", "name"])
    assert analysis["missing_fields"] == ["accountId"]
    assert analysis["unobserved_types"] == ["name"]
    assert analysis["type_mismatches"] == []
    assert compare_contract(None, contract)["status"] == "REQUEST_UNOBSERVED"


def test_nested_types_nullability_and_allowed_extra_fields():
    contract = {"required": ["profile"], "properties": {"profile": {"type": "object", "required": ["enabled"], "properties": {
        "enabled": {"type": "boolean"}, "scores": {"type": "array", "items": {"type": "number"}}, "note": {"type": "string", "nullable": True}}}}}
    request = {"profile": {"enabled": "true", "scores": [1, True], "note": None}, "extension": 1}
    analysis = compare_contract(request, contract)
    assert [item["field"] for item in analysis["type_mismatches"]] == ["profile.enabled", "profile.scores[1]"]
    assert analysis["unexpected_fields"] == ["extension"]
    assert analysis["forbidden_fields"] == []
    del request["profile"]["enabled"]
    assert compare_contract(request, contract)["missing_fields"] == ["profile.enabled"]


def test_unknown_schema_constraints_do_not_turn_into_checked_validation():
    analysis = compare_contract({"value": "x"}, {"properties": {"value": {"type": "string", "minLength": 2}}})
    assert analysis["complete"] is False
    assert "minLength" in str(analysis["limitations"])
    assert analysis["type_mismatches"] == []


def test_container_type_metadata_does_not_invent_unobserved_nested_fields():
    contract = {"properties": {"profile": {"type": "object", "required": ["count"], "properties": {"count": "integer"}}}}
    analysis = compare_contract({"profile": "[VALUE]"}, contract, observed_types={"profile": "object"})
    assert analysis["missing_fields"] == []
    assert analysis["type_mismatches"] == []
    assert analysis["unobserved_types"] == ["profile.*"]
    assert analysis["complete"] is False


@pytest.mark.parametrize("column", ["phone", "billing_code", "consent_at"])
def test_generic_db_column_cause_requires_actual_related_migration_state(column):
    bundle = bundle_for(sent="accountId", status=500)
    bundle["logs"] = [f"sqlite3.OperationalError: table accounts has no column named {column}"]
    without_state = verdict_for(bundle)
    assert without_state["diagnosis_type"] == "backend_exception_unconfirmed"
    assert without_state["hypotheses"][0]["status"] == "UNVERIFIED"
    assert route_verdict(without_state, "EXACT_ID")["route"] == "INVESTIGATE"
    bundle["migration"] = {"applied": "V8", "expected": "V9", "related_file": f"V9__add_{column}.sql"}
    with_state = verdict_for(bundle)
    assert with_state["diagnosis_type"] == "migration_missing"
    assert route_verdict(with_state, "EXACT_ID")["route"] == "WORK_CANDIDATE"
    bundle["migration"]["observation_status"] = "UNOBSERVED"
    assert verdict_for(bundle)["diagnosis_type"] == "backend_exception_unconfirmed"


def test_unrelated_migration_difference_does_not_confirm_db_cause():
    bundle = bundle_for(sent="accountId", status=500)
    bundle["logs"] = ["OperationalError: no column named billing_code"]
    bundle["migration"] = {"applied": "V1", "expected": "V2", "related_file": "V2__add_email.sql"}
    verdict = verdict_for(bundle)
    assert verdict["diagnosis_type"] == "backend_exception_unconfirmed"
    assert verdict["hypotheses"][0]["status"] == "UNVERIFIED"


@pytest.mark.parametrize("trace_id,diagnosis,route", [("contract-001", "contract_mismatch", "WORK_CANDIDATE"), ("claim-003", "expected_validation", "GUIDANCE"), ("migration-002", "migration_missing", "WORK_CANDIDATE")])
def test_fixed_regression_examples_retain_strong_expected_results(trace_id, diagnosis, route):
    verdict = analyze(trace_id, "HTTP 500")
    assert verdict["diagnosis_type"] == diagnosis
    assert route_verdict(verdict, "EXACT_ID")["route"] == route
    assert verdict["observed_status"] == (500 if trace_id == "migration-002" else 422)
    assert verdict["repro_eligible"] == (trace_id != "claim-003")


def test_legacy_routing_arguments_accept_missing_new_fields_but_do_not_infer_responsibility():
    legacy = {"diagnosis_type": "expected_validation", "next_action": "Check inputs"}
    assert route_verdict(legacy, "EXACT_ID")["route"] == "INVESTIGATE"
    assert route_verdict(legacy, "NONE")["route"] == "REQUEST_CONTEXT"
    assert route_verdict(legacy, "CONTEXT_CANDIDATE")["route"] == "INVESTIGATE"
