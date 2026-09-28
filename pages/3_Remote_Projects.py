"""Private-owner preview: reports on server and work on paired local runners."""
import os
from pathlib import Path
from uuid import uuid4

import streamlit as st
import httpx
from tracebridge.local_runner import ControlClient, GatewayError
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
service = st.selectbox("조사 대상 서비스", project["service_ids"], key="remote_service")
policy_id = st.selectbox("수정·검사 정책", project["repair_policy_ids"], key="remote_policy") if project.get("repair_policy_ids") else None
st.write("PC:", "온라인" if project["online"] else "오프라인 · 제보는 대기합니다", "· 자료 연결:", project["status"])
report = st.text_area("증상·기대한 동작·대략적인 시각", max_chars=4000, key="remote_report_text")
use_nvidia = st.checkbox("제보와 선별된 코드·로그를 서버의 NVIDIA 분석에 전송", key="remote_nvidia")
if st.button("제보 접수", type="primary"):
    try:
        response = submit(f"/v1/projects/{selected}/reports", {"text": report, "service": service, "use_nvidia": use_nvidia})
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
                st.caption("원본 적용은 PC에서 검토한 diff 해시로 별도 실행합니다. 배포·서비스 회복은 수행하지 않았습니다.")
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
    except (GatewayError, OSError, ValueError, httpx.HTTPError) as exc:
        st.error(str(exc))
