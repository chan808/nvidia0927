"""Private-owner preview: reports on server and work on paired local runners."""
import os
from pathlib import Path
from uuid import uuid4

import streamlit as st
import httpx
from tracebridge.local_runner import ControlClient, GatewayError
from tracebridge.work_management_ui import assessment_inputs, render_work_details, render_work_queue
import hashlib
import json


st.set_page_config(page_title="연결된 프로젝트", page_icon="🔗", layout="wide")
st.title("연결된 프로젝트 제보")
st.caption("배포 웹에서 접수하고 등록 PC가 실제 코드·로그 조사와 허용된 후보 검사를 실행합니다.")
url = os.getenv("TRACEBRIDGE_CONTROL_URL")
token = os.getenv("TRACEBRIDGE_OPERATOR_TOKEN")
if not token and os.getenv("TRACEBRIDGE_OPERATOR_TOKEN_FILE"):
    try:
        token = Path(os.environ["TRACEBRIDGE_OPERATOR_TOKEN_FILE"]).read_text().strip()
    except OSError:
        st.error("서버의 연결 인증 파일을 읽을 수 없습니다. 파일 경로와 접근 권한을 확인해 주세요.")
        st.stop()
if not url or not token:
    st.info("서버의 프로젝트 연결 API를 설정하면 PC 실행기를 페어링할 수 있습니다. 로컬 사용은 제보 에이전트 페이지에서 시작하세요.")
    st.stop()
api = ControlClient(url, token, allow_private_http=True)
def submit(path, payload):
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    key = "remote-pending:" + path
    pending = st.session_state.get(key)
    if not pending or pending["digest"] != digest:
        pending = {"digest": digest, "key": uuid4().hex}
        st.session_state[key] = pending
    return api.request("POST", path, payload, idempotency_key=pending["key"])
with st.sidebar.expander("PC 연결 관리"):
    ids = st.text_input("연결할 프로젝트 ID (쉼표 구분)", key="remote_pair_projects")
    if st.button("5분 유효 페어링 코드 생성"):
        try:
            pairing = api.request("POST", "/v1/pairings", {"project_ids": [value.strip() for value in ids.split(",") if value.strip()]})
            st.code(pairing["code"])
            st.caption("PC의 scripts.local_runner pair에 입력하세요. NVIDIA 키는 PC로 전달되지 않습니다.")
        except (GatewayError, ValueError, httpx.HTTPError) as exc:
            st.error(str(exc))
try:
    projects = api.request("GET", "/v1/projects")
except (GatewayError, OSError, httpx.HTTPError) as exc:
    st.error(str(exc))
    st.stop()
if not projects:
    st.info("PC를 페어링하고 실제 경로를 등록한 다음 로컬 실행기를 시작해 주세요.")
    st.stop()
selected = st.selectbox("프로젝트", [item["project_id"] for item in projects], key="remote_project")
project = next(item for item in projects if item["project_id"] == selected)
render_work_queue(api, selected)
service = st.selectbox("조사 대상 서비스", project["service_ids"], key="remote_service")
policy_id = st.selectbox("수정·검사 정책", project["repair_policy_ids"], key="remote_policy") if project.get("repair_policy_ids") else None
st.write("PC:", "온라인" if project["online"] else "오프라인 · 제보는 대기합니다", "· 자료 연결:", project["status"])
service_health = project.get("service_health", {}).get(service, "NOT_REGISTERED") if project["online"] else "UNKNOWN"
st.write("마지막 서비스 응답:", service_health, "· 확인 시각:", project.get("health_checked_at") or "미확인")
st.caption("서비스 응답은 PC가 마지막으로 확인한 상태입니다. 코드 연결·후보 검사·제보 해결 상태와 구분합니다.")
report = st.text_area("증상·기대한 동작·대략적인 시각", max_chars=4000, key="remote_report_text")
use_nvidia = st.checkbox("제보와 선별된 코드·로그를 서버의 NVIDIA 분석에 전송", key="remote_nvidia")
with st.expander("이번 제보의 작업 평가"):
    assessment = assessment_inputs(key="remote_new:" + selected)
with st.expander("유사 사건 검색 설정"):
    st.write("검토된 사건을 현재 서비스·환경 안에서 검색합니다. 과거 결론은 현재 근거로 재확인합니다.")
    if project.get("semantic_search_enabled"):
        st.caption("선택적 벡터 검색이 설정돼 있습니다. 이번 제보의 외부 분석을 선택하면 임베딩 검색도 사용합니다.")
        allow_index = st.checkbox("검토된 사건 요약을 설정된 임베딩 서비스에 전송", key="remote_allow_index:" + selected)
        if st.button("승인된 사건의 검색 인덱스 갱신", disabled=not allow_index, key="remote_index:" + selected):
            try:
                indexed = api.request("POST", f"/v1/memory/{selected}/index")
                if indexed.get("index_job_id"):
                    st.session_state["remote_index_job:" + selected] = indexed["index_job_id"]
                    st.session_state["remote_index_state:" + selected] = indexed["state"]
                    st.session_state["remote_index_retry:" + selected] = indexed["state"] in {"FAILED", "RECOVERY_REQUIRED"}
                    st.success("검색 인덱스 갱신을 접수했습니다.")
                else:
                    st.json(indexed)
            except (GatewayError, httpx.HTTPError, ValueError) as exc:
                st.error(str(exc))
        index_job_id = st.session_state.get("remote_index_job:" + selected)
        if index_job_id:
            if st.button("검색 인덱스 진행 상태 확인", key="remote_index_status:" + selected):
                try:
                    state = api.request("GET", f"/v1/memory/{selected}/index-jobs/{index_job_id}")
                    st.session_state["remote_index_state:" + selected] = state["state"]
                    st.session_state["remote_index_retry:" + selected] = state["state"] in {"FAILED", "RECOVERY_REQUIRED"}
                    st.write("검색 인덱스 상태:", state["state"])
                    if state.get("result"):
                        st.json(state["result"])
                    if state["state"] in {"FAILED", "RECOVERY_REQUIRED"}:
                        st.info("마지막 요청의 결과를 확인한 뒤 재시도할 수 있습니다.")
                except (GatewayError, httpx.HTTPError, ValueError) as exc:
                    st.error(str(exc))
            if st.session_state.get("remote_index_retry:" + selected) and st.button("인덱스 갱신 재시도", key="remote_retry_index:" + selected):
                try:
                    api.request("POST", f"/v1/memory/{selected}/index-jobs/{index_job_id}/retry", {})
                    st.session_state["remote_index_retry:" + selected] = False
                    st.session_state["remote_index_state:" + selected] = "QUEUED"
                    st.success("검색 인덱스 갱신을 다시 접수했습니다.")
                except (GatewayError, httpx.HTTPError, ValueError) as exc:
                    st.error(str(exc))
            if st.session_state.get("remote_index_state:" + selected) in {"QUEUED", "RUNNING", "RECOVERY_REQUIRED", "FAILED"}:
                if st.button("인덱스 갱신 중단", key="remote_cancel_index:" + selected):
                    try:
                        state = api.request("POST", f"/v1/memory/{selected}/index-jobs/{index_job_id}/cancel", {})
                        st.session_state["remote_index_state:" + selected] = state["state"]
                        st.session_state["remote_index_retry:" + selected] = False
                        st.info("인덱스 갱신을 중단했습니다." if state["state"] == "CANCELLED" else "인덱스 갱신이 이미 완료됐습니다.")
                    except (GatewayError, httpx.HTTPError, ValueError) as exc:
                        st.error(str(exc))
    else:
        st.caption("현재는 오류 지문·키워드 검색을 사용합니다. 임베딩 서비스는 서버 설정에서 선택적으로 연결합니다.")
if st.button("제보 접수", type="primary"):
    try:
        response = submit(f"/v1/projects/{selected}/reports", {"text": report, "service": service, "use_nvidia": use_nvidia, "assessment": assessment})
        st.session_state.remote_job_id = response["job_id"]
        st.rerun()
    except (GatewayError, ValueError, httpx.HTTPError) as exc:
        st.error(str(exc))
job_id = st.session_state.get("remote_job_id")
if job_id:
    if st.button("진행 상태 새로 확인", key="refresh_remote_job"):
        st.rerun()
    try:
        status = api.request("GET", f"/v1/jobs/{job_id}")
        st.write("작업 상태:", status["state"])
        payload = status.get("result")
        if status["project_id"] != selected:
            st.info("다른 프로젝트의 사건입니다. 해당 프로젝트를 선택해 주세요.")
            st.stop()
        render_work_details(api, status)
        if payload:
            result = payload.get("run", payload)
            st.write(result.get("summary", payload.get("error_type", "")))
            for question in result.get("questions", []):
                st.write(question)
            answer = st.text_input("같은 사건에 추가 답변", key="remote_answer")
            if st.button("답변 반영", disabled=status["state"] != "SUCCEEDED"):
                next_job = submit(f"/v1/projects/{selected}/reports", {"text": answer, "service": service,
                    "use_nvidia": use_nvidia, "previous_job_id": job_id})
                st.session_state.remote_job_id = next_job["job_id"]
                st.rerun()
            if project["repair_enabled"] and "job" not in payload and status["state"] == "SUCCEEDED":
                if st.button("등록 검사로 수정 후보 준비", disabled=not use_nvidia):
                    next_job = submit(f"/v1/projects/{selected}/changes", {"text": "검토할 수정 후보를 준비해 주세요.", "service": service,
                        "use_nvidia": use_nvidia, "previous_job_id": job_id, "policy_id": policy_id})
                    st.session_state.remote_job_id = next_job["job_id"]
                    st.rerun()
            if "job" in payload:
                job = payload["job"]
                st.write("후보 검증:", "검사 통과 · diff 검토 대기" if job["candidate_fix_verified"] else job["status"])
                st.table([{key: item.get(key) for key in ("phase", "status", "exit_code", "elapsed_ms")} for item in job["checks"]])
                if payload.get("diff_preview"):
                    st.code(payload["diff_preview"], language="diff")
                st.caption("원본 적용은 PC에서 검토한 diff 해시로 별도 실행하고, 저장된 적용 결과를 서버에 동기화합니다.")
                if status.get("original_applied"):
                    st.info(f"원본: APPLIED · 서비스 회복: {status['service_recovery']} · 사건: {status['incident_state']}")
                    application = status["application"]
                    st.caption("적용 snapshot: " + application["applied_snapshot_sha256"])
                    if status.get("recovery"):
                        st.table(status["recovery"]["checks"])
                    can_check = job["policy"]["policy_id"] in project.get("recovery_policy_ids", []) and application["work_id"] == job["work_id"]
                    if st.button("적용 후 등록된 API 동작 확인", disabled=not can_check or status["state"] != "SUCCEEDED", key="verify_remote_recovery"):
                        response = submit(f"/v1/projects/{selected}/recovery-checks", {"candidate_job_id": job_id,
                            "expected_source_run_id": status["latest_run_id"], "policy_sha256": job["policy"]["sha256"]})
                        st.session_state.remote_job_id = response["job_id"]
                        st.session_state.pop(f"remote-pending:/v1/projects/{selected}/recovery-checks", None)
                        st.rerun()
                    if not can_check:
                        st.caption("후보 준비 전에 신고 API의 기대 결과·버전 확인·관측 기간을 PC 검사 정책에 등록해야 합니다.")
            if "verification" in payload:
                verification = payload["verification"]
                st.subheader("적용 후 서비스 확인")
                st.write(f"{verification['status']} · 사건: {verification['incident_state']}")
                st.table(verification["checks"])
                if st.button("같은 적용 버전 다시 확인", key="repeat_remote_recovery"):
                    response = submit(f"/v1/projects/{selected}/recovery-checks", {"candidate_job_id": payload["candidate_job_id"],
                        "expected_source_run_id": status["latest_run_id"], "policy_sha256": verification["policy_sha256"]})
                    st.session_state.remote_job_id = response["job_id"]
                    st.session_state.pop(f"remote-pending:/v1/projects/{selected}/recovery-checks", None)
                    st.rerun()
            with st.expander("현재 관측·원인 후보·조사 기록"):
                st.json(result)
            review = st.selectbox("사건 지식 검토", ["선택", "승인", "반려"], key="remote_review")
            if st.button("검토 기록", disabled=review == "선택" or not result.get("run_id")):
                api.request("POST", f"/v1/memory/{selected}/{result['run_id']}/review", {"action": "approve" if review == "승인" else "reject"})
                st.success("소유자의 검토를 기록했습니다. 승인과 원인·수정 검증은 별개입니다.")
        if status["state"] in {"QUEUED", "RUNNING"} and st.button("작업 취소 요청"):
            api.request("POST", f"/v1/jobs/{job_id}/cancel", {})
            st.rerun()
        if status["state"] == "RECOVERY_REQUIRED" and st.button("PC의 확보한 결과 복구 요청"):
            api.request("POST", f"/v1/jobs/{job_id}/resume", {})
            st.rerun()
        if status["state"] == "RECOVERY_REQUIRED" and st.button("PC 작업이 종료됐음을 확인하고 대기열에서 제외"):
            api.request("POST", f"/v1/jobs/{job_id}/cancel", {})
            st.rerun()
    except (GatewayError, OSError, ValueError, httpx.HTTPError) as exc:
        st.error(str(exc))
