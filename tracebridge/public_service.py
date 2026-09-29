"""Project-scoped public receipts and owner-authorized workflow orchestration."""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import time
from uuid import uuid4

from fastapi import Depends, Header, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import delete, select, update

from .report_contract import ReportContext, plain_questions
from .project_sources import redact
from .service_workflow import decide
from .storage import schema as s
from .work_management import WorkAssessment


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class ServicePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    enabled: bool = False
    title: str = Field(default="문제 제보", min_length=1, max_length=80)
    services: list[str] = Field(default_factory=list, max_length=16)
    service_labels: dict[str, str] = Field(default_factory=dict, max_length=16)
    use_nvidia: bool = False
    automation: str = Field(default="INVESTIGATE", pattern=r"^(INVESTIGATE|PREPARE|APPLY_NONPROD)$")
    repair_policy_id: str | None = Field(default=None, max_length=128)
    auto_review_recovered: bool = False
    hourly_limit: int = Field(default=100, ge=1, le=1000)
    pending_limit: int = Field(default=20, ge=1, le=200)
    guidance_labels: dict[str, str] = Field(default_factory=dict, max_length=30)

    @model_validator(mode="after")
    def validate_scope(self):
        if len(set(self.services)) != len(self.services) or any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", v) for v in self.services):
            raise ValueError("Distinct registered services required")
        if self.enabled and not self.services:
            raise ValueError("Select public services")
        if self.automation != "INVESTIGATE" and (not self.repair_policy_id or not self.use_nvidia):
            raise ValueError("Automatic repair requires a registered policy and explicit model permission")
        if any(not re.fullmatch(r"[A-Za-z0-9_.-]{1,120}", key) or not 1 <= len(value) <= 80 for key, value in self.guidance_labels.items()):
            raise ValueError("Bounded public field labels required")
        if set(self.service_labels) - set(self.services) or any(not 1 <= len(value) <= 80 for value in self.service_labels.values()):
            raise ValueError("Public labels must belong to selected services")
        return self


class PolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    expected_revision: int = Field(ge=0)
    policy: ServicePolicy


class PublicReport(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    text: str = Field(min_length=1, max_length=4000)
    service: str = Field(min_length=1, max_length=80)
    context: dict[str, str] = Field(default_factory=dict, max_length=5)
    allow_external_analysis: bool = False

    @model_validator(mode="after")
    def check_context(self):
        if not self.text.strip() or set(self.context) - {"occurred_at", "trace_id", "method", "path", "operation"}:
            raise ValueError("Text and supported report clues required")
        ReportContext(**self.context)
        return self


MESSAGES = {
    "QUEUED": "제보를 접수했습니다. 연결된 실행기가 현재 동작을 확인합니다.",
    "RUNNING": "제보와 관련된 현재 동작을 확인하고 있습니다.",
    "NEEDS_CONTEXT": "제보한 동작을 더 확인할 정보가 필요합니다.",
    "GUIDANCE": "현재 동작에서 입력 누락을 확인했습니다. 필수 입력을 채운 뒤 다시 시도해 주세요.",
    "WAITING_REVIEW": "수정 후보의 검사를 통과했습니다. 담당자가 적용을 검토합니다.",
    "APPLIED": "수정을 적용했습니다. 실제 동작의 회복 여부를 확인합니다.",
    "RESOLVED": "등록된 문제 동작과 회귀 검사에서 회복을 확인했습니다.",
    "REOPENED": "수정 후에도 문제가 확인돼 사건을 다시 열었습니다.",
    "UNRESOLVED": "자동 처리로 해결을 확인하지 못했습니다. 시도 결과를 기록하고 담당자 확인을 기다립니다.",
    "NEEDS_REVIEW": "담당자가 현재 근거와 작업 조건을 확인해야 합니다.",
    "INTERRUPTED": "처리가 중단됐습니다. 담당자가 실행 상태를 확인합니다.",
}


class PublicService:
    def __init__(self, storage, secret, enqueue, report_type, enqueue_recovery=None, recovery_type=None, *, model_is_double=False):
        self.storage, self.secret = storage, secret.encode()
        self.enqueue, self.report_type = enqueue, report_type
        self.enqueue_recovery, self.recovery_type = enqueue_recovery, recovery_type
        self.model_is_double = model_is_double

    def policy(self, tx, project_id, *, public=False):
        row = tx.one(s.service_policies, project_id=project_id, lock=True)
        if not row:
            if public:
                raise HTTPException(404, "Public project not found")
            return ServicePolicy(), 0
        policy = ServicePolicy.model_validate_json(row["policy_json"])
        if public and not policy.enabled:
            raise HTTPException(404, "Public project not found")
        return policy, row["revision"]

    def token(self, project_id, report_id, submission_key):
        # The random client submission key is a capability. Retry survives an
        # operator credential rotation without retaining plaintext capabilities.
        return hmac.new(submission_key.encode(), ("receipt:" + project_id + ":" + report_id).encode(), hashlib.sha256).hexdigest()

    def charge(self, tx, project_id, address, policy, *, submit=True):
        now = time.time()
        address_hash = hmac.new(self.secret, address.encode(), hashlib.sha256).hexdigest()
        # The binding lock serializes buckets across processes and PostgreSQL instances.
        tx.connection.execute(delete(s.public_limits).where(s.public_limits.c.expires < now))
        limits = [(f"ip-{'write' if submit else 'read'}:{project_id}:{address_hash}:{int(now // 60)}", 10 if submit else 60, 120)]
        if submit:
            limits.append((f"project:{project_id}:{int(now // 3600)}", policy.hourly_limit, 7200))
        for key, maximum, ttl in limits:
            row = tx.one(s.public_limits, key=key, lock=True)
            if row and row["count"] >= maximum:
                raise HTTPException(429, "Public request budget exceeded", headers={"Retry-After": "60"})
            if row:
                tx.update(s.public_limits, {"count": row["count"] + 1}, key=key)
            else:
                tx.insert(s.public_limits, key=key, count=1, expires=now + ttl)

    def receipt(self, tx, project_id, report_id, token, *, lock=True):
        row = tx.one(s.public_reports, id=report_id, project_id=project_id, lock=lock)
        if not row or row["expires"] <= time.time() or not token or not hmac.compare_digest(row["access_digest"], digest(token)):
            raise HTTPException(404, "Receipt not found or expired")
        return self.sync_receipt(tx, row)

    def sync_receipt(self, tx, row):
        job = tx.one(s.jobs, id=row["latest_job_id"], project_id=row["project_id"])
        latest = tx.latest_job(row["project_id"], job["incident_id"])
        if latest["id"] != row["latest_job_id"]:
            assigned = json.loads(latest["body"])
            original = json.loads(job["body"])
            if (assigned.get("service"), assigned.get("environment"), assigned.get("profile_sha256")) == (
                    row["service"], original.get("environment"), original.get("profile_sha256")):
                tx.update(s.public_reports, {"latest_job_id": latest["id"]}, id=row["id"])
                return {**row, "latest_job_id": latest["id"]}
        return row

    def configure(self, project_id, body):
        with self.storage.transaction() as tx:
            binding = tx.one(s.bindings, project_id=project_id, lock=True)
            if not binding:
                raise HTTPException(404, "Project not found")
            manifest = json.loads(binding["manifest"])
            policy = body.policy
            if set(policy.services) - set(manifest["service_ids"]):
                raise HTTPException(422, "Public services must belong to the project")
            if policy.automation != "INVESTIGATE" and policy.repair_policy_id not in manifest["repair_policy_ids"]:
                raise HTTPException(422, "Registered repair policy required")
            if policy.automation != "INVESTIGATE" and not self.model_is_double and manifest.get("repair_execution_modes", {}).get(policy.repair_policy_id) != "DOCKER":
                raise HTTPException(422, "Public model-generated code must run in the registered Docker sandbox")
            if policy.automation == "APPLY_NONPROD" and (
                manifest["environment"] not in {"dev", "test", "staging"}
                or policy.repair_policy_id not in manifest.get("auto_apply_policy_ids", [])
                or policy.repair_policy_id not in manifest.get("recovery_policy_ids", [])):
                raise HTTPException(422, "PC must explicitly register non-production automatic application and recovery")
            _, revision = self.policy(tx, project_id)
            if revision != body.expected_revision:
                raise HTTPException(409, "Service policy changed; reload before updating")
            values = {"policy_json": policy.model_dump_json(), "revision": revision + 1, "updated": time.time()}
            if revision:
                tx.update(s.service_policies, values, project_id=project_id)
            else:
                tx.insert(s.service_policies, project_id=project_id, **values)
        return {"revision": revision + 1, "policy": policy.model_dump()}

    def public_status(self, tx, row):
        row = self.sync_receipt(tx, row)
        job = tx.one(s.jobs, id=row["latest_job_id"], project_id=row["project_id"])
        status = job["state"]
        event = tx.latest_event(job["id"], "AUTO_DECISION")
        decision = json.loads(event["metadata_json"]) if event else {}
        if status in {"FAILED", "EXPIRED"}:
            status = "UNRESOLVED"
        elif status in {"RECOVERY_REQUIRED", "CANCELLED", "CANCEL_REQUESTED"}:
            status = "INTERRUPTED"
        elif status == "SUCCEEDED":
            status = decision.get("action", "NEEDS_REVIEW")
        result = json.loads(job["result"]) if job["result"] else {}
        run = result.get("run", result)
        if status == "NEEDS_REVIEW" and not decision and job["state"] == "SUCCEEDED":
            status = decide(run, result.get("job"))["action"]
            if status == "PREPARE_CHANGE":
                status = "NEEDS_REVIEW"
        with self.storage.incidents(tx) as store:
            try:
                incident = store.get_incident(row["project_id"], job["incident_id"])
                latest = store.get_run(row["project_id"], incident["latest_run_id"])
            except ValueError:
                latest = {}
        recovery = latest.get("recovery", {})
        current_run = latest.get("run_id") == job["run_id"]
        current_application = (job["kind"] == "prepare_change" and latest.get("fix_applied")
            and latest.get("change", {}).get("work_id") == result.get("job", {}).get("work_id"))
        if job["state"] == "SUCCEEDED" and current_run and recovery.get("incident_state") in {"RESOLVED", "REOPENED"}:
            status = recovery["incident_state"]
        elif job["state"] == "SUCCEEDED" and latest.get("fix_applied") and (current_run or current_application):
            status = "APPLIED" if status != "UNRESOLVED" else status
        policy, _ = self.policy(tx, row["project_id"])
        binding = tx.one(s.bindings, project_id=row["project_id"])
        manifest = json.loads(binding["manifest"])
        assigned = json.loads(job["body"])
        can_answer = (job["state"] == "SUCCEEDED" and row["answers"] < 10 and policy.enabled
            and row["service"] in policy.services and row["service"] in manifest["service_ids"]
            and assigned["profile_sha256"] == manifest["profile_sha256"] and assigned["environment"] == manifest["environment"])
        message = MESSAGES.get(status, MESSAGES["NEEDS_REVIEW"])
        if status == "GUIDANCE":
            fields = run.get("responsibility", {}).get("fields", [])
            labels = [policy.guidance_labels[field] for field in fields if field in policy.guidance_labels]
            if labels:
                message = "다음 입력을 채운 뒤 다시 시도해 주세요: " + ", ".join(labels)
        return {"report_id": row["id"], "status": status, "message": message,
            "questions": plain_questions() if status == "NEEDS_CONTEXT" else [],
            "expires_at": datetime.fromtimestamp(row["expires"], timezone.utc).isoformat(),
            "answers_remaining": max(0, 10 - row["answers"]), "can_answer": can_answer}

    def annotate(self, tx, job_id, revision, mode):
        job = tx.one(s.jobs, id=job_id)
        payload = json.loads(job["body"])
        payload.update(intake_source="PUBLIC", service_policy_revision=revision, automation_mode=mode,
            model_is_double=self.model_is_double)
        tx.update(s.jobs, {"body": json.dumps(payload, ensure_ascii=False)}, id=job_id)

    def submit(self, project_id, body, key, address, *, report_id=None, token=None):
        if not key or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", key):
            raise HTTPException(422, "Random Idempotency-Key of 32-128 characters required")
        with self.storage.transaction() as tx:
            binding = tx.one(s.bindings, project_id=project_id, lock=True)
            policy, revision = self.policy(tx, project_id, public=True)
            if not binding or body.service not in policy.services:
                raise HTTPException(404, "Public service not found")
            manifest = json.loads(binding["manifest"])
            if body.service not in manifest["service_ids"]:
                raise HTTPException(409, "Public registration changed")
            submitted = digest([project_id, key, report_id])
            hashed = digest(body.model_dump())
            if not report_id:
                old = tx.one(s.public_reports, project_id=project_id, submission_digest=submitted)
                if old:
                    if old["input_hash"] != hashed:
                        raise HTTPException(409, "Submission key has different contents")
                    if old["expires"] <= time.time():
                        raise HTTPException(410, "Submission receipt expired")
                    return {**self.public_status(tx, old), "receipt_token": self.token(project_id, old["id"], key)}
            prior = self.receipt(tx, project_id, report_id, token) if report_id else None
            if prior and body.service != prior["service"]:
                raise HTTPException(422, "Receipt service cannot change")
            public_key = "public:" + submitted
            replay = tx.one(s.jobs, project_id=project_id, idempotency_key=public_key)
            if prior and replay:
                if json.loads(replay["body"]).get("public_answer_hash") != hashed:
                    raise HTTPException(409, "Answer key has different contents")
                return self.public_status(tx, prior)
            if prior and prior["answers"] >= 10:
                raise HTTPException(429, "Receipt answer budget exhausted")
            self.charge(tx, project_id, address, policy)
            dedupe = digest([body.model_dump(), manifest["profile_sha256"], revision])
            duplicate = None
            if not prior:
                duplicate = tx.connection.execute(select(s.public_reports).where(
                    s.public_reports.c.project_id == project_id, s.public_reports.c.dedupe_hash == dedupe,
                    s.public_reports.c.created > time.time() - 300).order_by(s.public_reports.c.created.desc()).limit(1)).mappings().first()
                if duplicate:
                    existing = tx.one(s.jobs, id=duplicate["latest_job_id"])
                    if duplicate["answers"] or existing["state"] not in {"QUEUED", "RUNNING", "SUCCEEDED"}:
                        duplicate = None
            if duplicate:
                job_id = duplicate["latest_job_id"]
            else:
                counts = tx.counts(project_id)
                pending = sum(counts.get(state, 0) for state in ("QUEUED", "RUNNING", "CANCEL_REQUESTED", "RECOVERY_REQUIRED"))
                if pending >= policy.pending_limit:
                    raise HTTPException(429, "Project queue budget exceeded", headers={"Retry-After": "60"})
                request_body = self.report_type(text=body.text, service=body.service,
                    context={name: redact(value) for name, value in body.context.items()},
                    use_nvidia=policy.use_nvidia and body.allow_external_analysis, memory_enabled=True,
                    previous_job_id=prior["latest_job_id"] if prior else None)
                queued = self.enqueue(project_id, "follow_up" if prior else "investigate", request_body, public_key, transaction=tx)
                job_id = queued["job_id"]
                self.annotate(tx, job_id, revision, policy.automation)
                if prior:
                    assigned = json.loads(tx.one(s.jobs, id=job_id)["body"])
                    assigned["public_answer_hash"] = hashed
                    tx.update(s.jobs, {"body": json.dumps(assigned, ensure_ascii=False)}, id=job_id)
                tx.insert(s.job_events, job_id=job_id, kind="PUBLIC_INTAKE", epoch=0, occurred=time.time(),
                    metadata_json=json.dumps({"actor": "anonymous-reporter", "follow_up": bool(prior), "policy_revision": revision}))
            if prior:
                tx.update(s.public_reports, {"latest_job_id": job_id, "answers": prior["answers"] + 1}, id=report_id)
            else:
                report_id = uuid4().hex
                tx.insert(s.public_reports, id=report_id, project_id=project_id, access_digest=digest(self.token(project_id, report_id, key)),
                    submission_digest=submitted, input_hash=hashed, dedupe_hash=dedupe, service=body.service,
                    root_job_id=job_id, latest_job_id=job_id, created=time.time(), expires=time.time() + 7 * 86400, answers=0)
            row = tx.one(s.public_reports, id=report_id)
            return {**self.public_status(tx, row), **({"receipt_token": self.token(project_id, report_id, key)} if not prior else {})}

    def current_authorization(self, tx, job, *, apply=False):
        assigned = json.loads(job["body"])
        if assigned.get("intake_source") != "PUBLIC":
            return None
        binding = tx.one(s.bindings, project_id=job["project_id"], runner_id=job["runner_id"], lock=True)
        policy, revision = self.policy(tx, job["project_id"])
        if not binding:
            return None
        manifest = json.loads(binding["manifest"])
        if (not policy.enabled or revision != assigned.get("service_policy_revision")
                or assigned["profile_sha256"] != manifest["profile_sha256"] or assigned["environment"] != manifest["environment"]
                or assigned["service"] not in policy.services or assigned["service"] not in manifest["service_ids"]):
            return None
        if apply and (policy.automation != "APPLY_NONPROD" or policy.repair_policy_id not in manifest.get("auto_apply_policy_ids", [])):
            return None
        if policy.automation != "INVESTIGATE" and not self.model_is_double and manifest.get("repair_execution_modes", {}).get(policy.repair_policy_id) != "DOCKER":
            return None
        return policy, revision, manifest

    def external_analysis_allowed(self, tx, job):
        assigned = json.loads(job["body"])
        if not assigned.get("use_nvidia"):
            return False
        if assigned.get("intake_source") != "PUBLIC":
            return True
        authorization = self.current_authorization(tx, job)
        return bool(authorization and authorization[0].use_nvidia)

    def on_complete(self, tx, job, result):
        assigned = json.loads(job["body"])
        if assigned.get("intake_source") != "PUBLIC":
            return
        if tx.latest_event(job["id"], "AUTO_DECISION"):
            return
        run = result.get("run", result)
        decision = decide(run, result.get("job"))
        authorization = self.current_authorization(tx, job)
        if job["kind"] == "verify_recovery":
            verification = result["verification"]
            decision["action"] = verification["incident_state"] if verification["incident_state"] in {"RESOLVED", "REOPENED"} else "UNRESOLVED"
            if authorization and authorization[0].auto_review_recovered and verification["status"] == "PASSED" and verification["incident_state"] == "RESOLVED":
                with self.storage.incidents(tx) as store:
                    card = store.get_card(job["project_id"], run["run_id"])
                    if card["review"]["status"] not in {"APPROVED", "EDITED", "REJECTED"}:
                        store.review_card(job["project_id"], run["run_id"], "approve", reviewer="registered-recovery-verifier",
                            note="Owner opted in; registered symptom/regression/version/window checks passed.")
        elif job["kind"] != "prepare_change" and decision["action"] == "PREPARE_CHANGE":
            if (authorization and authorization[0].automation in {"PREPARE", "APPLY_NONPROD"} and assigned["use_nvidia"]
                    and (assigned.get("previous_result") or {}).get("requested_action") != "INVESTIGATE_ONLY"
                    and run.get("requested_action", run.get("session", {}).get("action_preference")) != "INVESTIGATE_ONLY"):
                policy, revision, manifest = authorization
                if manifest["repair_enabled"] and policy.repair_policy_id in manifest["repair_policy_ids"]:
                    assessment = WorkAssessment.model_validate(decision["assessment"])
                    if job["assessment_revision"] > 1:
                        assessment = WorkAssessment.model_validate_json(job["assessment_json"])
                    child = self.enqueue(job["project_id"], "prepare_change", self.report_type(text="현재 사건의 등록된 재현·회귀 검사와 수정 후보를 준비합니다.",
                        service=assigned["service"], context={}, use_nvidia=True, memory_enabled=True,
                        previous_job_id=job["id"], policy_id=policy.repair_policy_id, assessment=assessment),
                        "auto:" + job["id"], transaction=tx)
                    self.annotate(tx, child["job_id"], revision, policy.automation)
                    tx.connection.execute(update(s.public_reports).where(s.public_reports.c.project_id == job["project_id"],
                        s.public_reports.c.latest_job_id == job["id"]).values(latest_job_id=child["job_id"]))
                    decision["child_job_id"] = child["job_id"]
                else:
                    decision["action"] = "NEEDS_REVIEW"
            else:
                decision["action"] = "NEEDS_REVIEW"
        if job["assessment_revision"] == 1:
            tx.update(s.jobs, {"priority": int(decision["assessment"]["priority"][1]),
                "assessment_json": json.dumps(decision["assessment"], ensure_ascii=False), "assessment_revision": 2}, id=job["id"])
        tx.insert(s.job_events, job_id=job["id"], kind="AUTO_DECISION", epoch=job["epoch"], occurred=time.time(),
            metadata_json=json.dumps(decision, ensure_ascii=False))

    def on_application(self, tx, candidate, application):
        authorization = self.current_authorization(tx, candidate)
        if not authorization:
            return
        policy, revision, manifest = authorization
        assigned = json.loads(candidate["body"])
        policy_id = assigned["policy_id"]
        if policy_id not in manifest.get("recovery_policy_ids", []):
            return
        latest = tx.latest_job(candidate["project_id"], candidate["incident_id"])
        # Duplicate receipt delivery may arrive after its recovery job was dispatched.
        if latest["id"] != candidate["id"]:
            return
        child = self.enqueue_recovery(candidate["project_id"], self.recovery_type(candidate_job_id=candidate["id"],
            expected_source_run_id=application["run"]["run_id"], policy_sha256=application["policy"]["sha256"]),
            "auto-recovery:" + candidate["id"], transaction=tx)
        self.annotate(tx, child["job_id"], revision, policy.automation)
        tx.connection.execute(update(s.public_reports).where(s.public_reports.c.project_id == candidate["project_id"],
            s.public_reports.c.latest_job_id == candidate["id"]).values(latest_job_id=child["job_id"]))


def install_routes(app, service, operator, runner, active):
    @app.get("/v1/projects/{project_id}/service-policy", dependencies=[Depends(operator)])
    def get_policy(project_id: str):
        with service.storage.transaction() as tx:
            if not tx.one(s.bindings, project_id=project_id):
                raise HTTPException(404, "Project not found")
            policy, revision = service.policy(tx, project_id)
        return {"revision": revision, "policy": policy.model_dump()}

    @app.put("/v1/projects/{project_id}/service-policy", dependencies=[Depends(operator)])
    def put_policy(project_id: str, body: PolicyUpdate):
        return service.configure(project_id, body)

    @app.get("/v1/public/projects/{project_id}")
    def describe(project_id: str):
        with service.storage.transaction() as tx:
            policy, _ = service.policy(tx, project_id, public=True)
        return {"title": policy.title, "services": [{"id": value, "label": policy.service_labels.get(value, value)} for value in policy.services],
            "external_analysis_available": policy.use_nvidia}

    @app.post("/v1/public/projects/{project_id}/reports", status_code=202)
    def submit(project_id: str, body: PublicReport, request: Request, idempotency_key: str | None = Header(default=None)):
        return service.submit(project_id, body, idempotency_key, request.client.host if request.client else "unknown")

    def bearer(value):
        return value[7:] if value and value.startswith("Bearer ") else None

    @app.get("/v1/public/projects/{project_id}/reports/{report_id}")
    def status(project_id: str, report_id: str, request: Request, authorization: str | None = Header(default=None)):
        with service.storage.transaction() as tx:
            tx.one(s.bindings, project_id=project_id, lock=True)
            row = service.receipt(tx, project_id, report_id, bearer(authorization))
            policy, _ = service.policy(tx, project_id)
            service.charge(tx, project_id, request.client.host if request.client else "unknown", policy, submit=False)
            result = service.public_status(tx, row)
        return result

    @app.post("/v1/public/projects/{project_id}/reports/{report_id}/answers", status_code=202)
    def answer(project_id: str, report_id: str, body: PublicReport, request: Request,
               authorization: str | None = Header(default=None), idempotency_key: str | None = Header(default=None)):
        return service.submit(project_id, body, idempotency_key, request.client.host if request.client else "unknown",
            report_id=report_id, token=bearer(authorization))

    @app.get("/v1/runner/jobs/{job_id}/automatic-application")
    def automatic_application(job_id: str, epoch: int = Query(ge=1), identity=Depends(runner)):
        with service.storage.transaction() as tx:
            job = tx.one(s.jobs, id=job_id, runner_id=identity["id"])
            if not job:
                raise HTTPException(404, "Job not found")
            active(tx, job_id, identity, epoch)
            authorized = service.current_authorization(tx, job, apply=True)
            return {"allowed": bool(authorized and job["kind"] == "prepare_change")}

    @app.get("/v1/runner/jobs/{job_id}/public-execution")
    def public_execution(job_id: str, epoch: int = Query(ge=1), identity=Depends(runner)):
        with service.storage.transaction() as tx:
            job = tx.one(s.jobs, id=job_id, runner_id=identity["id"])
            if not job:
                raise HTTPException(404, "Job not found")
            active(tx, job_id, identity, epoch)
            authorized = service.current_authorization(tx, job)
            return {"allowed": bool(authorized and job["kind"] == "prepare_change"
                and authorized[0].automation in {"PREPARE", "APPLY_NONPROD"}
                and json.loads(job["body"]).get("use_nvidia"))}
