"""Read registered OpenAPI/DTO/caller files independently of log snapshots."""

from __future__ import annotations

from copy import deepcopy
import hashlib
from typing import Any

from .evidence import EvidenceError
from .project_profile import ProjectProfile, _local_path, _read_json_file


def _unwrap(document: dict, node: Any) -> Any:
    """Resolve just the object selected by lookup; unrelated responses are untouched."""
    seen: set[str] = set()
    while isinstance(node, dict) and "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen or len(seen) > 20:
            raise EvidenceError("Only noncyclic document-local OpenAPI references are supported")
        if set(node) - {"$ref", "description", "summary"}:
            raise EvidenceError("OpenAPI reference sibling constraints require additional schema support")
        seen.add(ref)
        target: Any = document
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or part not in target:
                raise EvidenceError("OpenAPI reference target is unavailable")
            target = target[part]
        node = target
    return node


def _resolve(document: dict, node: Any, *, seen: tuple[str, ...] = (), depth: int = 0) -> Any:
    """Resolve only bounded document-local references; never access a URL/file ref."""
    if depth > 20:
        raise EvidenceError("OpenAPI reference/nesting limit")
    if isinstance(node, list):
        return [_resolve(document, item, seen=seen, depth=depth + 1) for item in node]
    if not isinstance(node, dict):
        return deepcopy(node)
    if "$ref" in node:
        ref = node["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            raise EvidenceError("Only noncyclic document-local OpenAPI references are supported")
        target: Any = document
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            if not isinstance(target, dict) or part not in target:
                raise EvidenceError("OpenAPI reference target is unavailable")
            target = target[part]
        if set(node) - {"$ref", "description", "summary"}:
            raise EvidenceError("OpenAPI reference sibling constraints require additional schema support")
        return _resolve(document, target, seen=(*seen, ref), depth=depth + 1)
    return {key: _resolve(document, value, seen=seen, depth=depth + 1) for key, value in node.items()
            if key not in {"example", "examples", "default", "description", "summary"}}


def _artifact(profile: ProjectProfile, location, method: str, path: str) -> tuple[dict | None, dict]:
    if location is None:
        return None, {"status": "UNOBSERVED", "source": None, "code_version": None}
    location = _local_path(profile.root, str(location))
    document, digest = _read_json_file(location)
    if not isinstance(document, dict):
        raise EvidenceError("Registered operation evidence must be an object")
    if "operations" in document:
        operations = document["operations"]
        data = operations.get(f"{method} {path}") if isinstance(operations, dict) else None
    else:
        data = document if document.get("method") == method and document.get("path") == path else None
    metadata = {"status": "OBSERVED" if isinstance(data, dict) else "UNOBSERVED", "source": str(location),
                "sha256": digest, "code_version": document.get("code_version")}
    if not isinstance(data, dict):
        return None, metadata
    metadata["code_version"] = data.get("code_version", metadata["code_version"])
    return deepcopy(data), metadata


def load_project_contract(profile: ProjectProfile, method: str, path: str, *, runtime_version: str | None = None) -> dict:
    """Return legacy contract keys plus provenance, versions and unobserved states.

    OpenAPI ``info.version`` is an API artifact version, not an executable SHA.
    Comparable code versions must be separately recorded as ``x-code-version``.
    DTO/caller JSON snapshots use ``code_version`` in that same namespace.
    """
    method = method.upper() if isinstance(method, str) else ""
    result = {
        "status": "UNOBSERVED", "method": method, "path": path,
        "project_id": profile.project_id, "service": profile.service, "environment": profile.environment,
        "openapi": None, "backend_dto": None, "caller": None,
        "provenance": {"kind": "registered_openapi", "source": None, "artifact_version": None, "code_version": None},
        "dto_provenance": {"status": "UNOBSERVED"}, "caller_provenance": {"status": "UNOBSERVED"},
        "versions": {"runtime": runtime_version, "contract": None, "dto": None, "caller": None},
        "limitations": [], "execution_authorized": False,
    }
    if not method or not isinstance(path, str) or not path.startswith("/"):
        result["limitations"].append("Observed method/path are required for contract lookup")
        return result
    if profile.openapi_path is None:
        result["limitations"].append("OpenAPI source is not registered")
    else:
        try:
            location = _local_path(profile.root, str(profile.openapi_path))
            document, digest = _read_json_file(location)
            if not isinstance(document, dict) or not str(document.get("openapi", "")).startswith("3."):
                raise EvidenceError("Only registered OpenAPI 3 JSON documents are supported")
            info = document.get("info", {})
            result["provenance"].update(source=str(location), sha256=digest,
                                        artifact_version=info.get("version") if isinstance(info, dict) else None,
                                        code_version=document.get("x-code-version"))
            result["versions"]["contract"] = document.get("x-code-version")
            paths = document.get("paths", {})
            path_item = paths.get(path) if isinstance(paths, dict) else None
            path_item = _unwrap(document, path_item)
            operation = path_item.get(method.lower()) if isinstance(path_item, dict) else None
            operation = _unwrap(document, operation)
            if not isinstance(operation, dict):
                result["limitations"].append("Operation is absent from the registered OpenAPI")
            else:
                body = _unwrap(document, operation.get("requestBody"))
                content = body.get("content", {}) if isinstance(body, dict) else {}
                media = content.get("application/json") if isinstance(content, dict) else None
                if media is None and isinstance(content, dict):
                    media = next((value for key, value in content.items() if key.endswith("+json")), None)
                schema = _resolve(document, media.get("schema")) if isinstance(media, dict) else None
                if not isinstance(schema, dict):
                    result["limitations"].append("Operation has no supported JSON request schema")
                elif schema.get("type", "object") != "object":
                    result["limitations"].append("Only object JSON request schemas are supported")
                else:
                    schema.setdefault("required", [])
                    schema.setdefault("properties", {})
                    result.update(status="OBSERVED", openapi=schema)
                    result["provenance"]["code_version"] = operation.get("x-code-version", document.get("x-code-version"))
                    result["versions"]["contract"] = result["provenance"]["code_version"]
        except EvidenceError as exc:
            result["limitations"].append(str(exc))
    try:
        dto, metadata = _artifact(profile, profile.dto_path, method, path)
        result["dto_provenance"] = metadata
        if dto:
            result["backend_dto"] = deepcopy(dto.get("schema", dto.get("fields")))
            result["versions"]["dto"] = metadata["code_version"]
            if not isinstance(result["backend_dto"], dict):
                result["backend_dto"] = None
                result["limitations"].append("DTO snapshot has no schema/fields object")
    except EvidenceError as exc:
        result["limitations"].append(str(exc))
    try:
        caller, metadata = _artifact(profile, profile.caller_evidence_path, method, path)
        result["caller_provenance"] = metadata
        if caller:
            # Verify the registered snapshot still names the same local code bytes.
            source_name = caller.get("source")
            if not isinstance(source_name, str):
                raise EvidenceError("Caller snapshot has no code source")
            source = _local_path(profile.root, source_name.split("#", 1)[0])
            try:
                with source.open("rb") as stream:
                    code = stream.read(300_001)
            except OSError as exc:
                raise EvidenceError("Caller code source is unavailable") from exc
            hash_format = caller.get("source_hash_format", "raw")
            if hash_format not in {"raw", "lf"}:
                raise EvidenceError("Unsupported caller source hash format")
            observed_hash = hashlib.sha256(code).hexdigest()
            canonical_code = code.replace(b"\r\n", b"\n") if hash_format == "lf" else code
            if len(code) > 300_000 or hashlib.sha256(canonical_code).hexdigest() != caller.get("source_sha256"):
                raise EvidenceError("Caller code no longer matches the registered evidence hash")
            metadata.update(source_bytes_sha256=observed_hash, source_hash_format=hash_format)
            result["caller"] = {
                "source": str(source) + ("#" + source_name.split("#", 1)[1] if "#" in source_name else ""),
                "source_kind": "caller_code", "source_verified": True, "source_sha256": caller["source_sha256"],
                "source_hash_format": hash_format, "source_bytes_sha256": observed_hash,
                "version": metadata["code_version"], "method": method, "path": path,
                "project_id": profile.project_id, "service": profile.service, "environment": profile.environment,
                "field_mapping": deepcopy(caller.get("field_mapping")), "required_inputs": deepcopy(caller.get("required_inputs")),
            }
            # Input observations belong to the selected incident, never to this static file.
            result["versions"]["caller"] = metadata["code_version"]
    except EvidenceError as exc:
        result["caller_provenance"]["status"] = "UNVERIFIED"
        result["limitations"].append(str(exc))
    return result
