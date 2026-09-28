"""Deterministic contract differences and separately checked responsibility evidence.

Only captured names/types are compared. A spelling candidate is never a rename
instruction; response status and missing input alone do not identify a culprit.
"""

from __future__ import annotations

from copy import deepcopy
import re
from typing import Any


TYPE_ALIASES = {
    "str": "string", "int": "integer", "long": "integer", "float": "number",
    "double": "number", "bool": "boolean", "dict": "object", "list": "array",
}
JSON_TYPES = {"string", "integer", "number", "boolean", "object", "array", "null"}
REDACTED = {
    "[VALUE]", "[REDACTED]", "[MASKED]", "[API_KEY]", "[EMAIL]", "[IP]",
    "***", "****", "<REDACTED>", "__REDACTED__",
}
UNSUPPORTED = {
    "oneOf", "anyOf", "allOf", "not", "$ref", "if", "then", "else",
    "patternProperties", "dependentRequired", "dependentSchemas",
}
VALUE_CONSTRAINTS = {
    "enum", "const", "pattern", "format", "minimum", "maximum", "exclusiveMinimum",
    "exclusiveMaximum", "multipleOf", "minLength", "maxLength", "minItems",
    "maxItems", "uniqueItems", "minProperties", "maxProperties",
}


def _type_name(value: Any) -> str | None:
    if type(value) is bool:
        return "boolean"
    if type(value) is int:
        return "integer"
    if type(value) is float:
        return "number"
    if value is None:
        return "null"
    if isinstance(value, str):
        if value.upper() in REDACTED or re.fullmatch(r"\[(?:REDACTED|MASKED)(?:[: _-].*)?\]", value, re.I):
            return None
        return "string"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    return None


def _types(schema: Any) -> list[str]:
    if isinstance(schema, str):
        schema = {"type": schema}
    if not isinstance(schema, dict):
        return []
    raw = schema.get("type")
    if raw is None and ("properties" in schema or "required" in schema):
        raw = "object"
    if raw is None and "items" in schema:
        raw = "array"
    values = raw if isinstance(raw, list) else [raw]
    normalized = [TYPE_ALIASES.get(item, item) for item in values if isinstance(item, str)]
    if schema.get("nullable") is True:
        normalized.append("null")
    return sorted(set(normalized)) if normalized and all(item in JSON_TYPES for item in normalized) else []


def _names(value: Any) -> list[str] | None:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        return None
    return sorted(set(value))


def _accepts(actual: str, expected: list[str]) -> bool:
    return actual in expected or actual == "integer" and "number" in expected


def compare_contract(
    request: dict | None,
    contract: dict | None,
    *,
    request_fields: list[str] | None = None,
    observed_types: dict[str, str] | None = None,
    redacted_fields: list[str] | None = None,
) -> dict:
    """Compare an object request to a legacy or OpenAPI object schema, without values.

    Metadata types can preserve a type captured before masking. Without that
    observation, a masked value has no inferred type. JSON Schema constraints
    outside names/types are reported as limitations, never treated as checked.
    """
    result: dict[str, Any] = {
        "status": "CONTRACT_UNAVAILABLE", "complete": False,
        "required_fields": [], "request_fields": [], "missing_fields": [],
        "unexpected_fields": [], "forbidden_fields": [], "type_mismatches": [],
        "unobserved_types": [], "similarity_candidates": [], "limitations": [],
        "automatic_rename": False,
    }
    if not isinstance(contract, dict):
        result["limitations"].append("API contract was not observed")
        return result
    if not isinstance(contract.get("properties", {}), dict) or _names(contract.get("required", [])) is None:
        result.update(status="UNSUPPORTED_SCHEMA")
        result["limitations"].append("Invalid object properties/required declaration")
        return result
    if _types(contract) not in ([], ["object"]):
        result.update(status="UNSUPPORTED_SCHEMA")
        result["limitations"].append("Only object request bodies are supported")
        return result
    captured = _names(request_fields) if request_fields is not None else None
    if isinstance(request, dict) and all(isinstance(key, str) for key in request):
        actual_fields = sorted(request)
        if captured is not None and captured != actual_fields:
            result["limitations"].append("Captured request names conflict with the request object")
        captured = actual_fields
    elif captured is None:
        result.update(status="REQUEST_UNOBSERVED")
        result["limitations"].append("Request field names were not observed")
        return result
    result.update(status="COMPARED", required_fields=_names(contract.get("required", [])), request_fields=captured)
    properties = contract.get("properties", {})
    result["missing_fields"] = sorted(set(result["required_fields"]) - set(captured))
    result["unexpected_fields"] = sorted(set(captured) - set(properties))
    if contract.get("additionalProperties") is False:
        result["forbidden_fields"] = list(result["unexpected_fields"])
    metadata = observed_types if isinstance(observed_types, dict) else {}
    masked = set(redacted_fields or [])

    def walk(value: Any, schema: Any, field: str, *, present: bool = True, depth: int = 0) -> None:
        schema = {"type": schema} if isinstance(schema, str) else schema
        if not isinstance(schema, dict):
            result["unobserved_types"].append(field)
            return
        if depth > 16:
            result["limitations"].append(f"Nested schema limit at {field}")
            return
        gaps = sorted((UNSUPPORTED | VALUE_CONSTRAINTS).intersection(schema))
        if gaps:
            result["limitations"].append(f"Unvalidated schema constraints at {field}: {', '.join(gaps)}")
        if UNSUPPORTED.intersection(schema):
            result["unobserved_types"].append(field)
            return
        expected = _types(schema)
        if not expected:
            result["unobserved_types"].append(field)
            return
        observed = _type_name(value) if present else None
        recorded = metadata.get(field)
        recorded = TYPE_ALIASES.get(recorded, recorded) if isinstance(recorded, str) else None
        if recorded not in JSON_TYPES:
            recorded = None
        if recorded and observed and recorded != observed:
            result["limitations"].append(f"Captured type conflicts with the value type at {field}")
            result["unobserved_types"].append(field)
            return
        actual = None if field in masked else recorded or observed
        if actual is None:
            result["unobserved_types"].append(field)
            return
        if not _accepts(actual, expected):
            result["type_mismatches"].append({"field": field, "expected": expected, "observed": actual})
            return
        if (actual == "object" and (schema.get("properties") or schema.get("required")) and not isinstance(value, dict)
                or actual == "array" and "items" in schema and not isinstance(value, list)):
            result["unobserved_types"].append(field + ".*")
            result["limitations"].append(f"Nested request fields/items were not observed at {field}")
            return
        if actual == "object" and isinstance(value, dict) and present:
            nested = schema.get("properties", {})
            required = _names(schema.get("required", []))
            if not isinstance(nested, dict) or required is None:
                result["limitations"].append(f"Invalid nested object schema at {field}")
                return
            result["missing_fields"].extend(f"{field}.{key}" for key in required if key not in value)
            extra = sorted(set(value) - set(nested))
            result["unexpected_fields"].extend(f"{field}.{key}" for key in extra)
            if schema.get("additionalProperties") is False:
                result["forbidden_fields"].extend(f"{field}.{key}" for key in extra)
            for key in sorted(set(value).intersection(nested)):
                walk(value[key], nested[key], f"{field}.{key}", depth=depth + 1)
        elif actual == "array" and isinstance(value, list) and "items" in schema and present:
            if len(value) > 256:
                result["limitations"].append(f"Array item inspection limit at {field}")
            for index, item in enumerate(value[:256]):
                walk(item, schema["items"], f"{field}[{index}]", depth=depth + 1)

    root_gaps = sorted((UNSUPPORTED | VALUE_CONSTRAINTS).intersection(contract))
    if root_gaps:
        result["limitations"].append(f"Unvalidated request constraints: {', '.join(root_gaps)}")
    for name in sorted(set(captured).intersection(properties)):
        present = isinstance(request, dict) and name in request
        walk(request.get(name) if present else None, properties[name], name, present=present)
    for missing in result["missing_fields"]:
        for extra in result["unexpected_fields"]:
            compact = lambda name: re.sub(r"[_\-\s]", "", name).casefold()
            if compact(missing) == compact(extra):
                result["similarity_candidates"].append({
                    "observed_field": extra, "required_field": missing, "status": "CANDIDATE",
                })
    for key in ("missing_fields", "unexpected_fields", "forbidden_fields", "unobserved_types", "limitations"):
        result[key] = sorted(set(result[key]))
    result["complete"] = not result["limitations"] and not result["unobserved_types"]
    return result


def _known_version(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() and _type_name(value) is not None else None


def analyze_contract(trace: dict, contract_data: dict | None = None) -> dict:
    """Keep request differences, source versions and attribution independently visible."""
    data = contract_data if isinstance(contract_data, dict) else {}
    caller = data.get("caller", trace.get("caller"))
    caller = caller if isinstance(caller, dict) else {}
    scope = {key: data.get(key) for key in ("method", "path", "service", "environment", "project_id")}
    conflicts = [key for key, value in scope.items() if value is not None and trace.get(key) is not None
                 and (str(value).upper() != str(trace[key]).upper() if key == "method" else value != trace[key])]
    scoped = all(scope.get(key) and trace.get(key) for key in ("method", "path"))
    schema = None if conflicts else data.get("openapi")
    result = compare_contract(
        trace.get("request"), schema, request_fields=trace.get("request_fields"),
        observed_types=trace.get("request_types"), redacted_fields=trace.get("redacted_fields"),
    )
    if conflicts:
        result.update(status="SCOPE_MISMATCH", complete=False)
        result["limitations"].append(f"Contract scope conflicts: {', '.join(conflicts)}")
    result["provenance"] = deepcopy(data.get("provenance", {})) if isinstance(data.get("provenance", {}), dict) else {}
    result["scope_status"] = "MISMATCH" if conflicts else "MATCHED" if scoped else "UNOBSERVED"
    version_data = data.get("versions") if isinstance(data.get("versions"), dict) else {}
    provenance = result["provenance"] if isinstance(result["provenance"], dict) else {}
    versions = {
        "runtime": _known_version(trace.get("version") or version_data.get("runtime")),
        "code": _known_version(trace.get("code_version") or version_data.get("code")),
        "contract": _known_version(version_data.get("contract") or provenance.get("code_version")),
        "dto": _known_version(version_data.get("dto")),
        "caller": _known_version(caller.get("version") or version_data.get("caller")),
    }
    known = {value for value in versions.values() if value is not None}
    duplicate_versions = {
        "runtime": [trace.get("version"), version_data.get("runtime")],
        "code": [trace.get("code_version"), version_data.get("code")],
        "contract": [version_data.get("contract"), provenance.get("code_version")],
        "caller": [caller.get("version"), version_data.get("caller")],
    }
    version_conflicts = [key for key, values in duplicate_versions.items()
                         if len({_known_version(value) for value in values if _known_version(value) is not None}) > 1]
    version_status = "MISMATCH" if len(known) > 1 or version_conflicts or trace.get("version_conflict") is True else "MATCHED" if versions["runtime"] and len([v for v in versions.values() if v]) > 1 else "UNOBSERVED"
    result["versions"] = {"status": version_status, **versions,
                          "unobserved_components": [key for key, value in versions.items() if value is None],
                          "conflicts": version_conflicts}
    responsibility: dict[str, Any] = {
        "status": "UNCONFIRMED", "evidence_sources": [],
        "reason": "Request differences do not identify the input or serialization owner",
    }
    result["responsibility"] = responsibility
    if version_status == "MISMATCH":
        responsibility.update(status="VERSION_MISMATCH", reason="Runtime and evidence code versions disagree")
        return result
    if (result["status"] != "COMPARED" or not result["complete"] or not scoped
            or trace.get("response_status") not in (400, 422)):
        return result
    # All evidence used for attribution must refer to the observed executable.
    runtime = versions["runtime"]
    if not runtime or any(versions[key] != runtime for key in ("contract", "dto", "caller")):
        responsibility["reason"] = "Contract, DTO and caller versions are not all observed at the runtime version"
        return result
    if (not isinstance(caller.get("source"), str) or not caller["source"] or caller.get("source_kind") != "caller_code"
            or caller.get("source_verified") is False):
        return result
    if any(caller.get(key) != trace.get(key) for key in ("method", "path")):
        responsibility["reason"] = "Caller code is not scoped to this operation"
        return result
    if any(caller.get(key) is not None and caller[key] != trace.get(key)
           for key in ("project_id", "service", "environment")):
        responsibility["reason"] = "Caller code scope does not match the incident"
        return result
    inputs = _names(caller.get("input_fields"))
    mapping, required_inputs = caller.get("field_mapping"), caller.get("required_inputs")
    if (inputs is None or not isinstance(mapping, dict) or not isinstance(required_inputs, dict)
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in [*mapping.items(), *required_inputs.items()])):
        responsibility["reason"] = "Caller mapping or captured input names are unavailable"
        return result
    generated = sorted(field for field, input_field in mapping.items() if input_field in inputs)
    if generated != result["request_fields"]:
        responsibility["reason"] = "Caller generation evidence disagrees with the captured request"
        return result
    dto = data.get("backend_dto")
    dto_properties = dto.get("properties", dto) if isinstance(dto, dict) else {}
    dto_required = _names(dto.get("required")) if isinstance(dto, dict) and "properties" in dto else None
    schema_properties = schema.get("properties", {})
    checked_fields = set(result["required_fields"]) | {item["field"] for item in result["type_mismatches"]}
    if any(field not in dto_properties or not _types(schema_properties.get(field))
           or _types(dto_properties[field]) != _types(schema_properties[field])
           or dto_required is not None and field in result["required_fields"] and field not in dto_required
           for field in checked_fields):
        responsibility["reason"] = "Registered contract and backend DTO do not agree on the compared fields"
        return result
    if any("." in field or "[" in field for field in result["missing_fields"]):
        responsibility["reason"] = "Nested field responsibility needs a more detailed caller observation"
        return result
    missing = result["missing_fields"]
    if any(field not in required_inputs for field in missing):
        responsibility["reason"] = "No observed caller input correspondence for the missing fields"
        return result
    input_types = caller.get("input_types") if isinstance(caller.get("input_types"), dict) else {}
    defects = []
    for field in missing:
        input_type = input_types.get(required_inputs[field])
        input_type = TYPE_ALIASES.get(input_type, input_type) if isinstance(input_type, str) else None
        if (required_inputs[field] in inputs and mapping.get(field) != required_inputs[field]
                and input_type in JSON_TYPES and _accepts(input_type, _types(schema_properties[field]))):
            defects.append(field)
    for mismatch in result["type_mismatches"]:
        field = mismatch["field"]
        input_field = mapping.get(field)
        input_type = input_types.get(input_field)
        input_type = TYPE_ALIASES.get(input_type, input_type) if isinstance(input_type, str) else None
        if input_type in JSON_TYPES and _accepts(input_type, mismatch["expected"]):
            defects.append(field)
    if defects:
        responsibility.update(status="CALLER_DEFECT", fields=sorted(set(defects)),
                              evidence_sources=[caller["source"], "backend_dto", "registered_contract", "captured_input"],
                              reason="Valid required input is present but this caller generates a contract-invalid request")
    elif (missing and not result["unexpected_fields"] and not result["type_mismatches"]
          and all(required_inputs[field] not in inputs and mapping.get(field) == required_inputs[field] for field in missing)):
        responsibility.update(status="INPUT_OMISSION", fields=missing,
                              evidence_sources=[caller["source"], "captured_input", "registered_contract"],
                              reason="The required input was absent and the caller preserves the observed input names")
    return result
