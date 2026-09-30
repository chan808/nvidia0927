"""Outbound-only paired runner, local durable results and server NVIDIA gateway."""
from __future__ import annotations

import base64
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
import threading
import time
from urllib.parse import urlparse

import httpx
from openai.types.chat import ChatCompletion

from .incident_memory import IncidentStore
from .project_health import inspect_project, connection_checks
from .project_registry import list_profiles
from .project_profile import load_project_profile, select_project_service
from .project_repair import NvidiaProjectProposer, prepare_project_change
from .change_policy import sha256
from .report_agent import investigate_submission
from .report_contract import ReportContext


class GatewayError(RuntimeError):
    def __init__(self, message, status_code=None):
        super().__init__(message)
        self.status_code = status_code


class ControlClient:
    def __init__(self, url: str, token: str, *, client=None, allow_private_http=False):
        parsed = urlparse(url)
        if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.scheme not in {"http", "https"}:
            raise ValueError("HTTPS control-plane URL required")
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1", "testserver"} and not allow_private_http:
            raise ValueError("Only loopback development connections may use HTTP")
        self.url = url.rstrip("/")
        self.token = token
        self.client = client or httpx.Client(timeout=100, follow_redirects=False)

    def request(self, method, path, payload=None, *, idempotency_key=None):
        headers = {"Authorization": "Bearer " + self.token} if self.token else {}
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        timeout = 100 if path.endswith("/model") else 45 if path.endswith("/ocr") else 35 if path.endswith("/index") else 20 if path.endswith("/memory") else 10
        options = {} if hasattr(self.client, "app") else {"timeout": timeout}
        response = self.client.request(method, self.url + path, json=payload, headers=headers, **options)
        if not response.is_success:
            try:
                detail = response.json().get("detail", "Control plane request failed")
            except ValueError:
                detail = "Control plane request failed"
            raise GatewayError(str(detail), response.status_code)
        return response.json()


class GatewayModelClient:
    def __init__(self, api: ControlClient, job: dict, *, model: str, guard=None):
        self.api, self.job, self.model, self.guard = api, job, model, guard
        self.calls = 0
        self.provider_metadata = {}
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        if self.guard:
            self.guard()
        self.calls += 1
        if [item["function"]["name"] for item in kwargs.get("tools", [])] == ["describe_screen"]:
            from copy import deepcopy
            kwargs = deepcopy(kwargs)
            for message in kwargs["messages"]:
                if isinstance(message.get("content"), list):
                    for part in message["content"]:
                        if part.get("type") == "image_url":
                            part["image_url"]["url"] = "data:image/jpeg;base64," + self.job["input"]["image_b64"]
        data = self.api.request("POST", f"/v1/runner/jobs/{self.job['job_id']}/model",
            {"epoch": self.job["epoch"], "step_id": "model-" + str(self.calls), "payload": kwargs})
        self.provider_metadata = data.pop("_tracebridge_provider", {})
        return ChatCompletion.model_validate(data)


class GatewayOCR:
    """Only the server holds the OCR credential; the leased job owns the photo."""
    def __init__(self, api, job, guard):
        self.api, self.job, self.guard = api, job, guard

    def extract(self, raw, *, deadline=None):
        from .deadline import check_deadline, remaining_timeout
        from .report_photo import decode_report_image
        check_deadline(deadline)
        self.guard()
        if raw != decode_report_image(self.job["input"].get("image_b64")):
            raise ValueError("Photo differs from the registered job")
        data = self.api.request("POST", f"/v1/runner/jobs/{self.job['job_id']}/ocr",
            {"epoch": self.job["epoch"], "step_id": "photo-ocr", "timeout_seconds": remaining_timeout(deadline, 35)})
        self.guard()
        check_deadline(deadline)
        return data


def _protect(raw: bytes) -> str:
    if os.name != "nt":
        return "owner-file:" + base64.b64encode(raw).decode()
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    data = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source, output = Blob(len(raw), data), Blob()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), "TraceBridge paired runner", None, None, None, 0x1, ctypes.byref(output)):
        raise OSError("Windows credential protection failed")
    try:
        encrypted = ctypes.string_at(output.data, output.size)
    finally:
        ctypes.windll.kernel32.LocalFree(output.data)
    return "dpapi:" + base64.b64encode(encrypted).decode()


def _unprotect(value: str) -> str:
    kind, encoded = value.split(":", 1)
    raw = base64.b64decode(encoded, validate=True)
    if kind == "owner-file" and os.name != "nt":
        return raw.decode()
    if kind != "dpapi" or os.name != "nt":
        raise ValueError("Runner credential belongs to a different OS account")
    import ctypes
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]
    data = (ctypes.c_ubyte * len(raw)).from_buffer_copy(raw)
    source, output = Blob(len(raw), data), Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 0x1, ctypes.byref(output)):
        raise OSError("Windows runner credential could not be decrypted")
    try:
        return ctypes.string_at(output.data, output.size).decode()
    finally:
        ctypes.windll.kernel32.LocalFree(output.data)


def pair_runner(url: str, code: str, config_path: Path, *, client=None) -> dict:
    if config_path.exists():
        raise ValueError("Existing runner configuration must be preserved; choose a new config path")
    api = ControlClient(url, "", client=client)
    credentials = api.request("POST", "/v1/pairings/consume", {"code": code})
    config = {"url": url, "runner_id": credentials["runner_id"], "project_ids": credentials["project_ids"],
        "credential": _protect(credentials["token"].encode()), "paired_at": datetime.now(timezone.utc).isoformat()}
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("x", encoding="utf-8") as stream:
        json.dump(config, stream)
    if os.name != "nt":
        os.chmod(config_path, 0o600)
    return {"runner_id": config["runner_id"], "project_ids": config["project_ids"], "config_path": str(config_path)}


class LocalRunner:
    def __init__(self, api: ControlClient, state_directory: Path, *, project_ids: list[str], registry=None, model=None):
        from .report_agent import DEFAULT_MODEL
        self.api, self.state_directory, self.project_ids = api, state_directory.resolve(), project_ids
        self.registry, self.model = registry, model or DEFAULT_MODEL
        self.state_directory.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_directory / "local-incidents.sqlite3"
        self.profiles = {}
        self.stop = threading.Event()
        self.active = None
        self.lease_failure = None
        self.last_manifest = 0

    @classmethod
    def from_config(cls, config_path: Path, *, client=None):
        config = json.loads(config_path.read_bytes())
        api = ControlClient(config["url"], _unprotect(config["credential"]), client=client)
        return cls(api, config_path.parent / "runner-state", project_ids=config["project_ids"])

    def publish_profiles(self):
        profiles, errors = list_profiles(self.registry)
        errors = [name for name in errors if Path(name).stem in self.project_ids]
        if errors:
            raise ValueError("Invalid paired project registrations: " + ", ".join(errors))
        self.profiles = {profile.project_id: profile for profile in profiles if profile.project_id in self.project_ids}
        for profile in self.profiles.values():
            health = inspect_project(profile)
            from .project_repair import load_repair_policy
            enabled_ids = []
            recovery_policies = {}
            auto_apply_ids = []
            execution_modes = {}
            policy_services = {}
            for id_ in profile.policy_refs:
                try:
                    policy, digest = load_repair_policy(profile, id_)
                    if policy.enabled:
                        enabled_ids.append(id_)
                        policy_services[id_] = profile.service if policy.repository_id == "primary" else next(item.service for item in profile.repositories if item.id == policy.repository_id)
                        execution_modes[id_] = policy.execution_mode
                        if policy.recovery:
                            recovery_policies[id_] = {"policy_sha256": digest, "spec": policy.recovery.model_dump()}
                            if policy.auto_apply_nonprod and policy.allow_apply:
                                auto_apply_ids.append(id_)
                except (ValueError, OSError):
                    continue
            manifest = {"project_id": profile.project_id, "environment": profile.environment,
                "service_ids": [item.id for item in profile.services] or [profile.service], "repository_ids": [item.id for item in profile.repositories],
                "profile_sha256": sha256(profile.config_path.read_bytes()), "repair_enabled": bool(enabled_ids), "repair_policy_ids": enabled_ids, "status": health["status"],
                "recovery_policy_ids": list(recovery_policies), "recovery_policies": recovery_policies,
                "auto_apply_policy_ids": auto_apply_ids,
                "repair_execution_modes": execution_modes,
                "service_health": {item["id"]: item["service_status"] for item in health["services"]},
                "health_checked_at": datetime.now(timezone.utc).isoformat(), "connection_checks": connection_checks(health)}
            manifest.update(default_service=profile.service, display_name=profile.display_name, repair_policy_services=policy_services)
            try:
                self.api.request("PUT", "/v1/runner/manifest", manifest)
            except GatewayError as exc:
                # An older server still receives the original registration/health.
                new_fields = {"connection_checks", "default_service", "display_name", "repair_policy_services"}
                if exc.status_code != 422 or "extra_forbidden" not in str(exc) or not any(name in str(exc) for name in new_fields):
                    raise
                for name in new_fields:
                    manifest.pop(name, None)
                self.api.request("PUT", "/v1/runner/manifest", manifest)
        self.last_manifest = time.monotonic()

    def _guard(self):
        if self.lease_failure:
            raise GatewayError("Runner lease lost; no new model or check allowed")

    def _heartbeat_loop(self, stop_event):
        while not stop_event.is_set() and not self.stop.is_set():
            active = self.active
            try:
                self.api.request("POST", "/v1/runner/heartbeat", {})
                active = self.active
                if active:
                    self.api.request("POST", f"/v1/runner/jobs/{active['job_id']}/renew", {"epoch": active["epoch"]})
            except Exception as exc:
                # An in-flight renewal from an older run must not invalidate a
                # newly claimed job after its result was committed or replayed.
                active = active or self.active
                if active is not None and self.active is active:
                    self.lease_failure = type(exc).__name__
            stop_event.wait(15)

    def run_once(self):
        stop_event = threading.Event()
        heartbeat = threading.Thread(target=self._heartbeat_loop, args=(stop_event,), daemon=True)
        heartbeat.start()
        try:
            return self._run_once()
        finally:
            stop_event.set()
            heartbeat.join(timeout=5)
            self.active = None

    def _run_once(self):
        self.flush_outbox()
        if not self.profiles or time.monotonic() - self.last_manifest > 30:
            self.publish_profiles()
        self.flush_applications()
        capabilities = self.api.request("POST", "/v1/runner/heartbeat", {})
        self.model = capabilities.get("model", self.model)
        job = self.api.request("POST", "/v1/runner/jobs/claim", {})["job"]
        if job is None:
            return None
        self.active, self.lease_failure = job, None
        result_path = self.state_directory / (job["job_id"] + ".result.json")
        reservation = self.state_directory / (job["job_id"] + ".running.json")
        if result_path.is_file():
            result = json.loads(result_path.read_bytes())
        else:
            if reservation.exists():
                self.api.request("POST", f"/v1/runner/jobs/{job['job_id']}/failure", {"epoch": job["epoch"], "error_type": "RECOVERY_REQUIRED"})
                self.active = None
                raise GatewayError("Interrupted job requires owner review; no automatic re-execution")
            with reservation.open("x", encoding="utf-8") as stream:
                json.dump({"job_id": job["job_id"], "epoch": job["epoch"]}, stream)
            try:
                payload = job["input"]
                profile = self.profiles.get(payload["project_id"])
                if profile is None or sha256(profile.config_path.read_bytes()) != payload["profile_sha256"]:
                    raise ValueError("Project registration changed after dispatch")
                profile = select_project_service(load_project_profile(profile.config_path), payload["service"])
                self._guard()
                gateway = GatewayModelClient(self.api, job, model=self.model, guard=self._guard)
                previous = payload.get("previous_result")
                if job["kind"] == "prepare_change":
                    if not previous:
                        raise ValueError("Saved investigation required")
                    from .project_repair import load_repair_policy, PolicyDenied
                    if payload.get("intake_source") == "PUBLIC":
                        authorization = self.api.request("GET", f"/v1/runner/jobs/{job['job_id']}/public-execution?epoch={job['epoch']}")
                        if not authorization["allowed"]:
                            raise PolicyDenied("Public execution permission changed")
                    execution_policy, _ = load_repair_policy(profile, payload.get("policy_id"))
                    if payload.get("intake_source") == "PUBLIC" and execution_policy.execution_mode != "DOCKER" and payload.get("model_is_double") is not True:
                        raise PolicyDenied("Public model-generated candidates require OS isolation")
                    with IncidentStore(self.db_path) as store:
                        source = store.get_run(profile.project_id, previous["run_id"])
                    result = prepare_project_change(source, profile, policy_id=payload.get("policy_id"), db_path=self.db_path,
                        proposer=NvidiaProjectProposer(client=gateway) if payload["use_nvidia"] else None,
                        result_run_id=payload["run_id"], execution_guard=self._guard)
                    if result["job"].get("diff", {}).get("ref"):
                        from .project_repair_ui import candidate_diff
                        result["diff_preview"] = candidate_diff(result["job"])
                elif job["kind"] == "verify_recovery":
                    from .project_recovery import recovery_configuration, verify_project_recovery
                    _, policy, digest = recovery_configuration(profile, payload["application"], previous)
                    if digest != payload["policy_sha256"] or policy.recovery.model_dump() != payload["recovery_spec"]:
                        raise ValueError("Recovery assignment differs from the owner-registered policy")
                    result = verify_project_recovery(profile, payload["application"]["work_id"], db_path=self.db_path,
                        source=previous, result_run_id=payload["run_id"], execution_guard=self._guard)
                    result["candidate_job_id"] = payload["candidate_job_id"]
                else:
                    def memory_lookup(project_id, query, **kwargs):
                        self._guard()
                        return self.api.request("POST", f"/v1/runner/jobs/{job['job_id']}/memory",
                            {"epoch": job["epoch"], "step_id": "memory", "payload": {"query": query, "signals": kwargs.get("signals")}})
                    context = ReportContext(**{**payload["context"], "service": payload["service"], "environment": payload["environment"]})
                    result = investigate_submission(payload["text"], project_profile=profile, context=context,
                        use_nvidia=payload["use_nvidia"], client=gateway, db_path=self.db_path,
                        memory_enabled=payload["memory_enabled"], memory_lookup=memory_lookup,
                        max_seconds=payload.get("investigation_max_seconds", 90),
                        previous_result=previous, run_id=payload["run_id"], incident_id=payload["incident_id"], message_received_at=payload["received_at"],
                        image=base64.b64decode(payload["image_b64"]) if payload.get("image_b64") else None,
                        ocr=GatewayOCR(self.api, job, self._guard) if payload.get("image_b64") and payload["use_nvidia"] else None)
                result_path.write_bytes(json.dumps(result, ensure_ascii=False).encode())
            except Exception as exc:
                try:
                    self.api.request("POST", f"/v1/runner/jobs/{job['job_id']}/failure", {"epoch": job["epoch"], "error_type": type(exc).__name__})
                finally:
                    try:
                        self.api.request("POST", f"/v1/runner/jobs/{job['job_id']}/stopped", {"epoch": job["epoch"]})
                    except Exception:
                        pass
                    self.active = None
                raise
        self._guard()
        if job["kind"] == "prepare_change" and job["input"].get("automation_mode") == "APPLY_NONPROD":
            result = self.automatic_application(job, result)
            result_path.write_bytes(json.dumps(result, ensure_ascii=False).encode())
        self._guard()
        self.api.request("POST", f"/v1/runner/jobs/{job['job_id']}/result", {"epoch": job["epoch"], "result": result})
        (self.state_directory / (job["job_id"] + ".ack.json")).write_text('{"saved":true}')
        self.active = None
        self.flush_applications()
        return {"job_id": job["job_id"], "status": "RESULT_SAVED"}

    def automatic_application(self, job, result):
        """Use the owner's two permissions and existing CAS/durable receipt path."""
        if "automatic_application" in result:
            return result  # A recorded failure/denial is not retried without a new job.
        from .project_repair import load_repair_policy, apply_project_change, PolicyDenied
        payload, candidate = job["input"], result["job"]
        outcome = {"status": "WAITING_REVIEW", "risk_basis": "OWNER_ALLOWLIST_AND_BOUNDED_DIFF"}
        if candidate.get("candidate_fix_verified") is not True:
            result["automatic_application"] = {**outcome, "reason": "CANDIDATE_NOT_VERIFIED"}
            return result
        try:
            self._guard()
            authorization = self.api.request("GET", f"/v1/runner/jobs/{job['job_id']}/automatic-application?epoch={job['epoch']}")
            profile = select_project_service(self.profiles[payload["project_id"]], payload["service"])
            policy, policy_hash = load_repair_policy(profile, payload["policy_id"])
            if (not authorization["allowed"] or not policy.auto_apply_nonprod or not policy.allow_apply
                    or policy.recovery is None or policy.environment not in {"dev", "test", "staging"}
                    or policy_hash != candidate["policy"]["sha256"] or payload["assessment"]["risk"] == "HIGH"):
                raise PolicyDenied("Automatic application permission changed")
            paths = candidate["diff"].get("paths", [])
            diff = Path(candidate["diff"]["ref"]).read_text(encoding="utf-8")
            changes = [line[1:] for line in diff.splitlines() if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
            sensitive = r"auth|permission|credential|secret|password|payment|billing|migration|schema|session|token|delete|drop\s"
            if len(paths) != 1 or not 1 <= len(changes) <= 40 or re.search(sensitive, "\n".join([*paths, *changes]), re.I):
                raise PolicyDenied("Candidate requires owner risk review")
            self._guard()
            receipt = apply_project_change(payload["project_id"], candidate["work_id"], profile,
                expected_diff_sha256=candidate["diff"]["sha256"], db_path=self.db_path)
            outcome = {**outcome, "status": receipt["status"], "risk": "LOW", "work_id": candidate["work_id"]}
        except (ValueError, OSError, GatewayError) as exc:
            outcome["reason"] = type(exc).__name__
        result["automatic_application"] = outcome
        return result

    def flush_outbox(self):
        for result_path in list(self.state_directory.glob("*.result.json"))[:32]:
            job_id = result_path.name.removesuffix(".result.json")
            ack = self.state_directory / (job_id + ".ack.json")
            reservation = self.state_directory / (job_id + ".running.json")
            if ack.exists() or not reservation.exists():
                continue
            original = json.loads(reservation.read_bytes())
            try:
                self.api.request("POST", f"/v1/runner/jobs/{job_id}/result", {"epoch": original["epoch"], "result": json.loads(result_path.read_bytes())})
                ack.write_text('{"saved":true}')
                self.active = None
            except GatewayError as exc:
                if exc.status_code != 409:
                    raise

    def flush_applications(self):
        """Report durable PC receipts, including receipts saved before this runner upgrade."""
        from .project_lifecycle import list_applications
        candidates = {}
        for path in self.state_directory.glob("*.result.json"):
            result = json.loads(path.read_bytes())
            change = result.get("job", {})
            if change.get("record_format") == "project_change_job_v1":
                candidates[(change["project_id"], change["work_id"])] = path.name.removesuffix(".result.json")
        with IncidentStore(self.db_path) as store:
            applications = [item for project in self.project_ids for item in list_applications(store, project)]
        for application in applications:
            job_id = candidates.get((application["project_id"], application["work_id"]))
            if not job_id:
                continue
            ack = self.state_directory / (application["work_id"] + ".application-ack.json")
            digest = sha256(json.dumps(application, sort_keys=True, ensure_ascii=False).encode())
            if ack.exists() and json.loads(ack.read_bytes()).get("sha256") == digest:
                continue
            try:
                self.api.request("POST", f"/v1/runner/projects/{application['project_id']}/applications",
                    {"candidate_job_id": job_id, "application": application})
                ack.write_text(json.dumps({"sha256": digest}), encoding="utf-8")
            except GatewayError as exc:
                # Preserve a conflicted receipt for owner review; never apply source again.
                if exc.status_code not in (403, 409, 422):
                    raise

    def run(self):
        delay = 2
        try:
            while not self.stop.is_set():
                try:
                    self.run_once()
                    delay = 2
                except (GatewayError, httpx.HTTPError, OSError, ValueError):
                    delay = min(30, delay * 2)
                self.stop.wait(delay)
        finally:
            self.stop.set()
