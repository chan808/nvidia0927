"""Applied source and live API evidence are separate, revisioned facts."""
from copy import deepcopy
from datetime import datetime, timezone
import json
import re
import time
from uuid import uuid4

import httpx

from .change_policy import PolicyDenied, sha256
from .incident_memory import IncidentStore, minimal_record
from .project_lifecycle import get_application, save_recovery
from .project_profile import select_project_service
from .project_repair import load_repair_policy, _repository, _snapshot, _snapshot_hash
from .recovery_contract import recovery_status


def recovery_configuration(profile, application, source):
    selected = select_project_service(profile, source.get("scope", {}).get("service") or source["source_registration"]["service"])
    if (application["project_id"] != profile.project_id or source["project_id"] != profile.project_id
            or source["incident_id"] != application["run"]["incident_id"]
            or source.get("change", {}).get("work_id") != application["work_id"] or source.get("fix_applied") is not True
            or sha256(profile.config_path.read_bytes()) != source["source_registration"]["profile_sha256"]):
        raise PolicyDenied("적용 출처와 현재 프로젝트 연결이 다릅니다")
    policy, digest = load_repair_policy(selected, source["change"]["policy"]["policy_id"])
    if not policy.enabled or not policy.recovery or digest != source["change"]["policy"]["sha256"]:
        raise PolicyDenied("후보 준비 전에 등록한 동일한 회복 검사 정책이 필요합니다")
    scope = source.get("scope", {})
    if not any(check.id == policy.recovery.journey_check_id and check.method == scope.get("method") and check.path == scope.get("path") for check in policy.recovery.checks):
        raise PolicyDenied("신고한 API 동작과 같은 method·path의 기대 결과 검사가 필요합니다")
    return selected, policy, digest


def _observe(client, check, expected_sha, timeout, guard, started_ns):
    item = {"check_id": check.id, "method": check.method, "path": check.path,
        "status": "INCONCLUSIVE", "http_status": None, "expected_status": check.expected_status,
        "outcome_matches": False, "runtime_matches": False, "runtime_snapshot_sha256": None,
        "response_complete": False,
        "elapsed_ms": (time.monotonic_ns() - started_ns) // 1_000_000,
        "observed_at": datetime.now(timezone.utc).isoformat()}
    if guard:
        guard()
    try:
        deadline = time.monotonic() + timeout
        kwargs = {"json": check.json_body} if check.method == "POST" else {}
        with client.stream(check.method, check.url, headers={"Accept-Encoding": "identity"}, **kwargs) as response:
            item["http_status"] = response.status_code
            version = response.headers.get(check.snapshot_header)
            if isinstance(version, str) and re.fullmatch(r"[a-f0-9]{64}", version):
                item["runtime_snapshot_sha256"] = version
                item["runtime_matches"] = version == expected_sha
            if response.headers.get("content-encoding", "identity").lower() != "identity":
                return item
            raw = bytearray()
            for chunk in response.iter_raw():
                raw.extend(chunk)
                if guard:
                    guard()
                if len(raw) > 16_000 or time.monotonic() > deadline:
                    return item
            data = json.loads(raw)
            if not isinstance(data, dict):
                return item
            item["response_complete"] = True
            item["outcome_matches"] = all(key in data and type(data[key]) is type(value) and data[key] == value for key, value in check.expected_json.items())
            # Wrong/absent runtime evidence cannot establish a failure of the applied candidate.
            if item["runtime_matches"]:
                item["status"] = "PASSED" if response.status_code == check.expected_status and item["outcome_matches"] else "FAILED"
    except (httpx.HTTPError, ValueError):
        pass
    return item


def recovery_run(source, verification, run_id, executed_at):
    run = deepcopy(source)
    status = verification["status"]
    run.update(run_id=run_id, revision=source["revision"] + 1, executed_at=executed_at,
        cause_confirmed=False, fix_applied=True, fix_verified=status == "PASSED", run_status="COMPLETED",
        stop_reason="service_recovery_" + status.lower(), recovery=deepcopy(verification),
        summary={"PASSED": "적용 버전의 신고 API 기대 결과와 등록된 회귀 동작을 관측 기간 동안 확인했습니다.",
                 "FAILED": "적용 버전에서 기대 결과 검사가 실패했습니다. 사건을 다시 열었습니다.",
                 "INCONCLUSIVE": "실행 버전 또는 응답 근거가 부족합니다. 사건은 열린 상태로 유지합니다."}[status])
    run["change"].update(service_recovery={"PASSED": "VERIFIED", "FAILED": "FAILED", "INCONCLUSIVE": "INCONCLUSIVE"}[status],
        verification_scope="REGISTERED_LOCAL_API_WINDOW")
    return run


def validate_recovery(result, application, source, *, spec=None, policy_sha256=None):
    verification, run = result["verification"], result["run"]
    if set(verification) != {"work_id", "source_run_id", "policy_sha256", "applied_snapshot_sha256", "samples", "interval_seconds", "journey_check_id", "regression_check_id",
            "started_at", "finished_at", "duration_ms", "checks", "source_unchanged", "status", "incident_state"}:
        raise ValueError("Unknown recovery proof fields")
    checks = verification["checks"]
    start, end = (datetime.fromisoformat(verification[key]) for key in ("started_at", "finished_at"))
    samples = verification["samples"]
    if (start.tzinfo is None or end.tzinfo is None or end < start
            or type(samples) is not int or not 2 <= samples <= 6 or not 1 <= verification["interval_seconds"] <= 10
            or type(verification["duration_ms"]) is not int or verification["duration_ms"] < (samples - 1) * verification["interval_seconds"] * 1000
            or verification["work_id"] != application["work_id"] or verification["source_run_id"] != source["run_id"]
            or verification["applied_snapshot_sha256"] != application["applied_snapshot_sha256"]
            or source.get("fix_applied") is not True or source.get("change", {}).get("work_id") != application["work_id"]
            or source["incident_id"] != application["run"]["incident_id"]
            or not checks or len(checks) > 24 or len(checks) % samples
            or verification["status"] != (recovery_status(checks) if verification["source_unchanged"] is True else "INCONCLUSIVE")
            or verification["policy_sha256"] != source["change"]["policy"]["sha256"]
            or verification["incident_state"] != {"PASSED": "RESOLVED", "FAILED": "REOPENED", "INCONCLUSIVE": "OPEN"}[verification["status"]]):
        raise ValueError("Recovery evidence differs from the applied incident")
    width = len(checks) // samples
    expected_ids = [item["check_id"] for item in checks[:width]]
    if (len(set(expected_ids)) != width or verification["journey_check_id"] == verification["regression_check_id"]
            or {verification["journey_check_id"], verification["regression_check_id"]} - set(expected_ids)):
        raise ValueError("Unique checks required in each sample")
    for index, item in enumerate(checks):
        if set(item) != {"check_id", "method", "path", "status", "http_status", "expected_status", "outcome_matches", "runtime_matches",
                "runtime_snapshot_sha256", "response_complete", "elapsed_ms", "observed_at", "sample"}:
            raise ValueError("Unknown recovery observation fields")
        matches = item["runtime_snapshot_sha256"] == application["applied_snapshot_sha256"]
        expected_status = "INCONCLUSIVE" if not matches or item["response_complete"] is not True else "PASSED" if item["http_status"] == item["expected_status"] and item["outcome_matches"] is True else "FAILED"
        if (item["sample"] != index // width + 1 or item["check_id"] != expected_ids[index % width]
                or item["runtime_matches"] is not matches or item["status"] != expected_status
                or not 200 <= item["expected_status"] <= 299):
            raise ValueError("Invalid recovery observation")
        observed = datetime.fromisoformat(item["observed_at"])
        if observed.tzinfo is None or not start <= observed <= end or type(item["elapsed_ms"]) is not int or not 0 <= item["elapsed_ms"] <= verification["duration_ms"]:
            raise ValueError("Observation outside the registered window")
        if index >= width:
            if item["elapsed_ms"] - checks[index - width]["elapsed_ms"] < verification["interval_seconds"] * 1000:
                raise ValueError("Recovery samples are too close together")
    scope = source.get("scope", {})
    if not any(item["check_id"] == verification["journey_check_id"] and item["method"] == scope.get("method") and item["path"] == scope.get("path") for item in checks):
        raise ValueError("Recovery does not check the reported API")
    if spec is not None:
        if samples != spec.samples or verification["interval_seconds"] != spec.interval_seconds or width != len(spec.checks) or verification["policy_sha256"] != policy_sha256:
            raise ValueError("Recovery differs from the dispatched policy")
        if (verification["journey_check_id"], verification["regression_check_id"]) != (spec.journey_check_id, spec.regression_check_id):
            raise ValueError("Recovery journey differs from its policy")
        for index, item in enumerate(checks):
            check = spec.checks[index % width]
            if (item["check_id"], item["method"], item["path"], item["expected_status"]) != (check.id, check.method, check.path, check.expected_status):
                raise ValueError("Recovery check differs from the registered policy")
    expected = recovery_run(source, verification, run["run_id"], run["executed_at"])
    if run["executed_at"] != verification["finished_at"]:
        raise ValueError("Recovery execution time differs from its observations")
    if minimal_record(run) != minimal_record(expected):
        raise ValueError("Recovery run differs from its evidence")


def verify_project_recovery(profile, work_id, *, db_path=None, source=None, result_run_id=None, execution_guard=None):
    with IncidentStore(db_path) as store:
        application = get_application(store, profile.project_id, work_id)
        incident = store.get_incident(profile.project_id, application["run"]["incident_id"])
        latest = store.get_run(profile.project_id, incident["latest_run_id"])
    if source is None:
        source = latest
    if latest["run_id"] != source["run_id"]:
        raise PolicyDenied("새 사건 기록이 있습니다. 최신 기록에서 검사를 요청해 주세요")
    selected, policy, digest = recovery_configuration(profile, application, source)
    root = _repository(selected, policy)
    if _snapshot_hash(_snapshot(root, deadline=time.monotonic() + 30)) != application["applied_snapshot_sha256"]:
        raise PolicyDenied("적용 이후 원본이 바뀌었습니다. 현재 코드를 회복 근거로 연결할 수 없습니다")
    spec = policy.recovery
    verification = {"work_id": work_id, "source_run_id": source["run_id"], "policy_sha256": digest,
        "applied_snapshot_sha256": application["applied_snapshot_sha256"], "samples": spec.samples,
        "journey_check_id": spec.journey_check_id, "regression_check_id": spec.regression_check_id,
        "interval_seconds": spec.interval_seconds, "started_at": datetime.now(timezone.utc).isoformat(), "checks": []}
    started_ns = time.monotonic_ns()
    with httpx.Client(timeout=spec.timeout_seconds, follow_redirects=False, trust_env=False) as client:
        for sample in range(1, spec.samples + 1):
            if sample > 1:
                deadline = time.monotonic() + spec.interval_seconds
                while time.monotonic() < deadline:
                    if execution_guard:
                        execution_guard()
                    time.sleep(min(0.1, max(0, deadline - time.monotonic())))
            for check in spec.checks:
                item = _observe(client, check, application["applied_snapshot_sha256"], spec.timeout_seconds, execution_guard, started_ns)
                verification["checks"].append({**item, "sample": sample})
    if execution_guard:
        execution_guard()
    verification["finished_at"] = datetime.now(timezone.utc).isoformat()
    verification["duration_ms"] = (time.monotonic_ns() - started_ns) // 1_000_000
    verification["source_unchanged"] = _snapshot_hash(_snapshot(root, deadline=time.monotonic() + 30)) == application["applied_snapshot_sha256"]
    verification["status"] = recovery_status(verification["checks"]) if verification["source_unchanged"] else "INCONCLUSIVE"
    verification["incident_state"] = {"PASSED": "RESOLVED", "FAILED": "REOPENED", "INCONCLUSIVE": "OPEN"}[verification["status"]]
    run = recovery_run(source, verification, result_run_id or uuid4().hex, verification["finished_at"])
    result = {"run": run, "verification": verification}
    validate_recovery(result, application, source, spec=spec, policy_sha256=digest)
    with IncidentStore(db_path) as store:
        result["persistence"] = {"status": save_recovery(store, result)}
    return result
