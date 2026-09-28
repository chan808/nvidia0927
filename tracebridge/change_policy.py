"""Owner-registered A2 policy for one trusted seed target, never supplied by a report."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import stat
from types import MappingProxyType

from pydantic import BaseModel, ConfigDict, Field


WORKSPACE = Path(__file__).resolve().parents[1]
# Changing a policy needs an owner code/config change, including this digest.
REGISTRATIONS = MappingProxyType({
    "seed-signup-a2-v1": ("examples/change_policy/seed-signup-a2-v1.json", "e769a33c3b14ad798951bdc2c53746b018c47ca0dbbc239964f2421024f88635"),
})
COMMANDS = MappingProxyType({"signup-contract": "contract", "signup-regression": "regression"})
CASE_KIND = "SEEDED_DEVELOPMENT"
LIMITATIONS = [
    "Trusted registered seed target only; not an arbitrary-project code sandbox.",
    "Separate file copy, isolated Python flags, allowlisted environment and bounded subprocess waits.",
    "No OS filesystem/network/resource boundary; no OpenShell connection or enforcement verified.",
    "Only validated dictionary-key literals execute; no new expressions, imports, shell, SQL or URLs.",
    "Local filesystem owner is trusted; link checks do not protect against a concurrent hostile OS user.",
    "HTTP timeouts are phase limits; process startup/cleanup and file I/O are not hard realtime limits.",
    "Candidate validation only; original application, deployment and service recovery are not performed.",
]


class PolicyDenied(ValueError):
    pass


class ChangePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    policy_id: str
    version: int = Field(ge=1)
    authorization: str
    project_id: str
    target_id: str
    case_kind: str
    source_root: str
    source_origin: str
    baseline_version: str
    files: dict[str, str]
    snapshot_sha256: str
    editable_paths: list[str]
    editable_function: str
    editable_key_indices: list[int]
    max_files: int = Field(ge=1, le=1)
    max_edits: int = Field(ge=1, le=2)
    max_changed_lines: int = Field(ge=1, le=6)
    max_changed_bytes: int = Field(ge=1, le=256)
    max_diff_bytes: int = Field(ge=1, le=4096)
    check_ids: list[str]
    check_timeout_seconds: int = Field(ge=1, le=15)
    model_timeout_seconds: int = Field(ge=1, le=45)
    total_seconds: int = Field(ge=1, le=90)
    max_attempts: int = Field(ge=1, le=1)
    environment: dict[str, str]
    execution_mode: str
    external_model_data: str
    failure_signature: str


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def snapshot_hash(files: dict[str, bytes]) -> str:
    return sha256(json_bytes({name: sha256(data) for name, data in sorted(files.items())}))


def safe_path(workspace: Path, relative: str, *, file: bool = False) -> Path:
    """Reject traversal, Windows alternate streams, links/junctions and hardlinked files."""
    if not isinstance(relative, str) or not re.fullmatch(r"[A-Za-z0-9_.\-/]+", relative):
        raise PolicyDenied("Invalid registered path")
    parts = PurePosixPath(relative).parts
    if not parts or relative.startswith("/") or any(part in {".", ".."} for part in relative.split("/")):
        raise PolicyDenied("Path traversal is forbidden")
    root = workspace.absolute()
    current = root
    for part in ("", *parts):
        current = current / part
        if current.exists() or current.is_symlink():
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                raise PolicyDenied("Links and reparse points are forbidden")
            if stat.S_ISREG(info.st_mode) and info.st_nlink > 1:
                raise PolicyDenied("Hardlinked files are forbidden")
    if not current.resolve().is_relative_to(root.resolve()):
        raise PolicyDenied("Path left the workspace")
    if file and not current.is_file():
        raise PolicyDenied("Registered file is unavailable")
    return current


def load_policy(policy_id: str, workspace: Path | None = None) -> tuple[ChangePolicy, str]:
    workspace = workspace or WORKSPACE
    if policy_id not in REGISTRATIONS:
        raise PolicyDenied("No registered A2 policy for this policy ID")
    relative, expected = REGISTRATIONS[policy_id]
    raw = safe_path(workspace, relative, file=True).read_bytes()
    if len(raw) > 32_000 or sha256(raw) != expected:
        raise PolicyDenied("Registered policy digest changed")
    policy = ChangePolicy.model_validate_json(raw)
    if (policy.policy_id != policy_id or policy.authorization != "A2 PREPARE_CHANGE"
            or policy.case_kind != CASE_KIND or policy.execution_mode != "TRUSTED_SEED_RESTRICTED_LITERALS"
            or policy.external_model_data != "REGISTERED_SYNTHETIC_ONLY"
            or policy.check_ids != list(COMMANDS)
            or policy.editable_paths != ["client.py"] or policy.editable_function != "build_signup_request"
            or policy.editable_key_indices != [0, 1]
            or set(policy.files) != {"client.py", "api.py", "checks.py", "cases.json", "events.json", "README.md"}):
        raise PolicyDenied("Unsupported target or execution policy")
    return policy, expected


def read_source(policy: ChangePolicy, workspace: Path) -> dict[str, bytes]:
    files = {}
    for name, digest in policy.files.items():
        path = safe_path(workspace, policy.source_root + "/" + name, file=True)
        if path.stat().st_size > 64_000:
            raise PolicyDenied("Registered source exceeds limit")
        raw = path.read_bytes()
        if sha256(raw) != digest:
            raise PolicyDenied("Registered baseline file hash changed: " + name)
        files[name] = raw
    if snapshot_hash(files) != policy.snapshot_sha256:
        raise PolicyDenied("Baseline snapshot hash changed")
    return files


def authorize_source(source: dict, policy: ChangePolicy) -> None:
    scope, aggregate = source.get("scope", {}), source.get("log_scope", {}).get("aggregate", {})
    if (source.get("project_id") != policy.project_id or source.get("case_kind") != policy.case_kind
            or source.get("route") != "WORK_CANDIDATE" or source.get("run_status") != "COMPLETED"
            or source.get("correlation") != "EXACT_ID" or source.get("finding_status") != "CONFIRMED_MISMATCH"
            or source.get("diagnosis_type") != "contract_mismatch" or source.get("observed_status") not in (400, 422)
            or any(scope.get(key) != value for key, value in policy.environment.items())
            or scope.get("version") != policy.baseline_version or source.get("change")
            or aggregate.get("conflicts") or aggregate.get("conflicting_trace_ids") or aggregate.get("complete") is False
            or not source.get("observations") or not all(item.get("correlated") for item in source["observations"])
            or source.get("development_target", {}).get("snapshot_sha256") != policy.snapshot_sha256):
        raise PolicyDenied("Source incident is not an eligible observed seed work candidate")
