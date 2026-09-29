"""Connect the local daily HTTP observation files to an owner registration."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
from uuid import uuid4

from .project_profile import _health_url, load_project_profile
from .project_registry import save_profile

LOGIN_PATH = "/api/v1/auth/dev-login"
CONTROLLER = "backend/platform/identity/src/main/kotlin/dev/daily/identity/web/DevAuthController.kt"


def configure_daily(root: Path, *, registry: Path, artifacts: Path, observation: Path,
                    backend_url: str = "http://127.0.0.1:8081", frontend_url: str = "http://127.0.0.1:3100",
                    project_id: str = "daily-local") -> dict:
    root, registry, artifacts, observation = (item.resolve() for item in (root, registry, artifacts, observation))
    backend_health = _health_url(backend_url.rstrip("/") + "/actuator/health/readiness")
    frontend_health = _health_url(frontend_url.rstrip("/") + "/api/v1/auth/capabilities")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", project_id):
        raise ValueError("A valid project ID is required")
    controller = root / CONTROLLER
    caller = root / "frontend/src/lib/api.ts"
    controller_bytes, caller_bytes = controller.read_bytes(), caller.read_bytes()
    controller_text = controller_bytes.decode("utf-8-sig")
    caller_text = caller_bytes.decode("utf-8-sig")
    if (not re.search(r"data class DevLoginRequest\(\s*@field:Email val email: String,\s*@field:NotBlank val name: String,\s*\)", controller_text)
            or '@PostMapping("/dev-login")' not in controller_text
            or '@RequestMapping("/api/v1/auth")' not in controller_text
            or '@Profile("local & !google")' not in controller_text
            or not re.search(r'export const devLogin\s*=.*?api<.*?' + re.escape('("/api/v1/auth/dev-login",') + r'.*?JSON.stringify\(\{ email, name \}\)', caller_text, re.S)):
        raise ValueError("The observed development-login contract changed; review its source before registering snapshots")
    controller_hash = hashlib.sha256(controller_bytes).hexdigest()
    version = None
    version_notes = []
    if (observation / "runtime.json").is_file():
        with (observation / "runtime.json").open("rb") as stream:
            raw = stream.read(64_001)
        if len(raw) > 64_000:
            raise ValueError("Runtime observation exceeds its limit")
        runtime = json.loads(raw)
        build = runtime.get("build", {}) if isinstance(runtime, dict) else {}
        candidate = runtime.get("runtime_version") if isinstance(runtime, dict) else None
        if (isinstance(runtime, dict) and runtime.get("project_id") == project_id and runtime.get("service") == "backend"
                and runtime.get("environment") == "dev" and isinstance(build, dict)
                and build.get("source") == "spring_boot_build_info" and build.get("dev_login_source_sha256") == controller_hash
                and isinstance(candidate, str) and re.fullmatch(r"[a-fA-F0-9]{40}", candidate)):
            version = candidate
    if version is None:
        version_notes.append("DTO/contract runtime correspondence is unobserved until the embedded source hash matches")
    schema = {"type": "object", "required": ["email", "name"], "properties": {
        "email": {"type": "string", "format": "email"}, "name": {"type": "string", "minLength": 1}}}
    operation = "POST " + LOGIN_PATH
    documents = {
        "openapi.json": {"openapi": "3.0.3", "info": {"title": "daily development-login source snapshot", "version": "1"},
            "x-code-version": version, "x-source-sha256": controller_hash,
            "paths": {LOGIN_PATH: {"post": {"requestBody": {"required": True, "content": {"application/json": {"schema": schema}}}}}}},
        "dto.json": {"code_version": version, "source_sha256": controller_hash,
            "operations": {operation: {"schema": schema}}},
        "caller.json": {"code_version": None, "operations": {operation: {
            "source": "src/lib/api.ts#devLogin", "repository": "frontend",
            "source_sha256": hashlib.sha256(caller_bytes).hexdigest(),
            "field_mapping": {"email": "email", "name": "name"}, "required_inputs": {"email": "email", "name": "name"}}}},
    }
    existing = registry / (project_id + ".json")
    if existing.exists():
        profile = load_project_profile(existing)
        if profile.root != root or profile.environment != "dev":
            raise ValueError("Existing registration belongs to another root or environment")
        data = json.loads(existing.read_bytes())
    else:
        data = {"project_id": project_id, "service": "backend", "environment": "dev", "root": str(root), "code_roots": [],
            "repositories": [
                {"id": "frontend", "service": "frontend", "root": str(root / "frontend"), "code_roots": ["src"], "git_root": str(root)},
                {"id": "backend", "service": "backend", "root": str(root / "backend"), "code_roots": ["app/src/main", "domain", "platform"], "git_root": str(root)}],
            "log_sources": [], "policy_refs": [], "services": [{"id": "backend", "log_source_ids": []}, {"id": "frontend", "log_source_ids": []}]}
    data = deepcopy(data)
    for id_, directory in (("daily-observations", observation), ("daily-contracts", artifacts)):
        repositories = data.setdefault("repositories", [])
        matching = next((item for item in repositories if item["id"] == id_), None)
        value = {"id": id_, "service": "backend", "root": str(directory), "code_roots": []}
        if matching:
            if Path(matching["root"]).resolve() != directory:
                raise ValueError("Existing observation repository has another location")
        else:
            repositories.append(value)
    sources = data.setdefault("log_sources", [])
    id_ = "daily-http-requests"
    previous = next((item for item in sources if item["id"] == id_), None)
    source = {"id": id_, "repository": "daily-observations", "path": "requests.jsonl", "format": "jsonl", "service": "backend"}
    if previous and previous != source:
        raise ValueError("Existing observation source differs; review its registration")
    if previous is None:
        sources.append(source)
    backend = next(item for item in data["services"] if item["id"] == "backend")
    backend["log_source_ids"] = list(dict.fromkeys([*backend.get("log_source_ids", []), id_]))
    backend.update(health_url=backend_health, version_observation={"method": "json_file", "path": {"repository": "daily-observations", "path": "runtime.json"}},
                   **{name: {"repository": "daily-contracts", "path": filename} for name, filename in
                      (("openapi_path", "openapi.json"), ("dto_path", "dto.json"), ("caller_evidence_path", "caller.json"))})
    frontend = next((item for item in data["services"] if item["id"] == "frontend"), None)
    if frontend:
        frontend["health_url"] = frontend_health
    if any((artifacts / filename).is_symlink() for filename in documents):
        raise ValueError("Evidence artifact must not be a symlink")
    for directory in (artifacts, observation):
        directory.mkdir(parents=True, exist_ok=True)
    for filename, document in documents.items():
        path = artifacts / filename
        temporary = artifacts / (".evidence-" + uuid4().hex + ".json")
        temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
    if existing.exists():
        backup = artifacts / "profile-before.json"
        if not backup.exists():
            backup.write_bytes(existing.read_bytes())
    path = save_profile(data, registry)
    return {"profile": str(path), "project_id": project_id, "observation_directory": str(observation),
            "contract_runtime_version": version, "limitations": [*version_notes,
                "Only the development-login operation has a source-derived contract snapshot",
                "The active bounded request file is registered; rotated older files require explicit additional registration",
                "Caller code is hash-checked, but its browser runtime version and captured inputs remain unobserved",
                "Original apply permissions are preserved"]}
