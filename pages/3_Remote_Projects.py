"""One owner workflow: project setup, connection status and text/photo reports."""
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv
import httpx
import streamlit as st

from tracebridge.local_runner import ControlClient, GatewayError
from tracebridge.knowledge_review_ui import render_review_queue
from tracebridge.work_management_ui import assessment_inputs, render_work_details, render_work_queue
from tracebridge.project_connection_ui import render_connection
from tracebridge.project_registration_ui import render_registration
from tracebridge.project_registry import list_profiles
from tracebridge.project_setup_ui import render_simple_setup, connect_local_project
from tracebridge.report_photo import encode_report_image, decode_report_image

st.set_page_config(page_title="연결된 프로젝트", page_icon="🔗", layout="wide")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
st.title("연결된 프로젝트")
st.caption("프로젝트를 연결하고, 증상 글이나 오류 화면을 보내주세요.")
url = os.getenv("TRACEBRIDGE_CONTROL_URL")
token = os.getenv("TRACEBRIDGE_OPERATOR_TOKEN")
if not token and os.getenv("TRACEBRIDGE_OPERATOR_TOKEN_FILE"):
    try:
        token = Path(os.environ["TRACEBRIDGE_OPERATOR_TOKEN_FILE"]).read_text().strip()
    except OSError:
        token = None
api = ControlClient(url, token, allow_private_http=True) if url and token else None
remote_projects = []
connection_error = None
if api:
    try:
        remote_projects = api.request("GET", "/v1/projects")
    except (GatewayError, OSError, httpx.HTTPError):
        connection_error = "프로젝트 연결 서버에 접속하지 못했습니다. 연결 설정을 확인해 주세요."
local_profiles, registration_errors = list_profiles() if os.getenv("TRACEBRIDGE_ALLOW_PROJECT_REGISTRATION") == "1" else ([], [])
local_by_id = {item.project_id: item for item in local_profiles}
by_id = {item["project_id"]: item for item in remote_projects}
for local in local_profiles:
    by_id.setdefault(local.project_id, {"project_id": local.project_id, "display_name": local.display_name,
        "default_service": local.service, "service_ids": [item.id for item in local.services] or [local.service],
        "profile_sha256": hashlib.sha256(local.config_path.read_bytes()).hexdigest(), "online": False, "local_only": True,
        "repair_enabled": False, "repair_policy_ids": [], "service_health": {}, "connection_checks": {}})


def submit(path, payload):
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    key = "remote-pending:" + path
    pending = st.session_state.get(key)
    if not pending or pending["digest"] != digest:
        pending = {"digest": digest, "key": uuid4().hex}
        st.session_state[key] = pending
    return api.request("POST", path, payload, idempotency_key=pending["key"])


@st.fragment(run_every="3s")
def wait_for_connection(api, selected, expected_hash):
    if api:
        try:
            current = next((item for item in api.request("GET", "/v1/projects") if item["project_id"] == selected), None)
            if current and current["profile_sha256"] == expected_hash:
                st.rerun(scope="app")
        except (GatewayError, OSError, httpx.HTTPError):
            pass
    st.info("프로젝트 연결 정보를 적용하고 있어요. 연결되면 제보를 보낼 수 있어요.")


def render_search_settings(api, project, selected):
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


def render_results(api, selected, project, service, use_nvidia, policy_id):
    job_id = st.session_state.get("remote_job_id")
    if job_id:
        if st.button("진행 상태 새로 확인", key="refresh_remote_job"):
            st.rerun()
        try:
            status = api.request("GET", f"/v1/jobs/{job_id}")
            payload = status.get("result")
            if status["project_id"] != selected:
                st.info("목록에서 이 제보의 프로젝트를 선택해 주세요.")
                return
            if service != status["service"]:
                matching = [id_ for id_ in project.get("repair_policy_ids", [])
                    if project.get("repair_policy_services", {}).get(id_, status["service"]) == status["service"]]
                policy_id = matching[0] if len(matching) == 1 else None
            st.subheader("제보 처리")
            st.write({"QUEUED": "접수됐어요. 프로젝트에서 확인을 기다리고 있어요.", "RUNNING": "문제를 확인하고 있어요.",
                "SUCCEEDED": "확인 결과가 도착했어요.", "FAILED": "확인을 마치지 못했어요. 처리 상세에서 원인을 확인할 수 있어요.",
                "CANCELLED": "제보 처리가 중단됐어요.", "RECOVERY_REQUIRED": "진행 상황을 확인해야 해요."}.get(status["state"], "제보를 처리하고 있어요."))
            if payload:
                result = payload.get("run", payload)
                if summary := result.get("summary"):
                    st.write(summary)
                questions = result.get("questions", [])
                if questions:
                    st.write("조금만 더 알려주세요")
                    for question in questions:
                        st.write("• " + question)
                with st.form("report-answer:" + job_id):
                    answer = st.text_input("추가 설명", key="remote_answer")
                    send_answer = st.form_submit_button("추가 설명 보내기", disabled=status["state"] != "SUCCEEDED")
                if send_answer:
                    next_job = submit(f"/v1/projects/{selected}/reports", {"text": answer, "service": status["service"],
                        "use_nvidia": use_nvidia, "previous_job_id": job_id})
                    st.session_state.remote_job_id = next_job["job_id"]
                    st.session_state["latest_job:" + selected] = next_job["job_id"]
                    st.rerun()
            with st.expander("처리 상세·관리자 검토"):
                st.write("작업 상태:", status["state"])
                payload = status.get("result")
                if status["project_id"] != selected:
                    st.info("다른 프로젝트의 사건입니다. 해당 프로젝트를 선택해 주세요.")
                    st.stop()
                render_work_details(api, status)
                if payload:
                    result = payload.get("run", payload)
                    if project["repair_enabled"] and policy_id and "job" not in payload and status["state"] == "SUCCEEDED":
                        if st.button("수정안 만들기", disabled=not use_nvidia):
                            next_job = submit(f"/v1/projects/{selected}/changes", {"text": "검토할 수정 후보를 준비해 주세요.", "service": status["service"],
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
                    st.caption("사건 카드는 고급 설정의 사건 지식 검토에서 근거를 확인한 뒤 승인하거나 제외합니다.")
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


left, right = st.columns([1.7, 1], gap="large")
with left:
    if not by_id or st.session_state.get("project_add"):
        render_simple_setup(st, api, namespace="simple:new")
    if by_id:
        if pending := st.session_state.pop("pending_project_selection", None):
            if pending in by_id:
                st.session_state.remote_project = pending
        if st.session_state.get("remote_project") not in by_id:
            st.session_state.remote_project = next(iter(by_id))
        selected = st.selectbox("프로젝트", list(by_id), key="remote_project",
            format_func=lambda id_: by_id[id_].get("display_name") or (local_by_id[id_].display_name if id_ in local_by_id else None) or id_)
        if st.session_state.get("active_report_project") != selected:
            previous_project = st.session_state.get("active_report_project")
            st.session_state.active_report_project = selected
            if previous_project is not None or not st.session_state.get("remote_job_id"):
                st.session_state.remote_job_id = st.session_state.get("latest_job:" + selected)
            st.session_state.remote_service = None
            st.session_state.remote_policy = None
        project = by_id[selected]
        local_profile = local_by_id.get(selected)
        local_hash = hashlib.sha256(local_profile.config_path.read_bytes()).hexdigest() if local_profile else None
        connection_pending = project.get("local_only", False) or local_hash is not None and local_hash != project["profile_sha256"]
        if connection_pending:
            if st.session_state.get("project_connect_error:" + selected):
                st.warning("프로젝트 폴더는 등록됐지만 연결을 완료하지 못했어요. 고급 설정에서 다시 연결해 주세요.")
            else:
                wait_for_connection(api, selected, local_hash or project["profile_sha256"])
        if os.getenv("TRACEBRIDGE_ALLOW_PROJECT_REGISTRATION") == "1":
            if st.button("프로젝트 추가", key="project-add"):
                st.session_state.project_add = True
                st.rerun()
            if local_profile:
                render_simple_setup(st, api, local_profile, namespace="simple:" + selected + ":" + project["profile_sha256"][:12])
        intake = st.container()
        result_space = st.container()
        with st.expander("고급 설정"):
            st.caption("로그·분석 범위·수정 검사 권한과 관리 기능이 필요할 때만 설정합니다.")
            if connection_error:
                st.error(connection_error)
            if registration_errors:
                st.warning("읽지 못한 설정: " + ", ".join(registration_errors))
            if api and not project.get("local_only"):
                render_work_queue(api, selected, expanded=False)
                render_review_queue(api, selected)
            if st.session_state.get("remote_service") not in [None, *project["service_ids"]]:
                st.session_state.remote_service = None
            override = st.selectbox("분석 범위 (선택)", [None, *project["service_ids"]], key="remote_service",
                format_func=lambda value: "프로젝트 기본 범위" if value is None else value)
            service = override or project.get("default_service") or project["service_ids"][0]
            scoped_policies = [id_ for id_ in project.get("repair_policy_ids", [])
                if project.get("repair_policy_services", {}).get(id_, service) == service]
            if st.session_state.get("remote_policy") not in [None, *scoped_policies]:
                st.session_state.remote_policy = None
            chosen = st.selectbox("수정 검사 설정 (선택)", [None, *scoped_policies], key="remote_policy",
                format_func=lambda value: "등록된 검사 자동 선택" if value is None else value) if scoped_policies else None
            policy_id = chosen or (scoped_policies[0] if len(scoped_policies) == 1 else None)
            with st.expander("제보 작업 평가"):
                assessment = assessment_inputs(key="remote_new:" + selected)
            if local_profile:
                render_registration(st, local_profile, sidebar=False,
                    namespace="connected:" + selected + ":" + hashlib.sha256(local_profile.config_path.read_bytes()).hexdigest()[:12])
                from tracebridge.project_repair_ui import render_repair_registration
                render_repair_registration(st, local_profile, sidebar=False)
                if api and not project["online"] and st.button("이 프로젝트 다시 연결", key="project-reconnect:" + selected):
                    try:
                        connect_local_project(api, local_profile)
                        st.session_state.pop("project_connect_error:" + selected, None)
                        st.rerun()
                    except (GatewayError, OSError, ValueError, httpx.HTTPError) as exc:
                        st.error("프로젝트 연결을 완료하지 못했습니다: " + str(exc))
            if api and not project.get("local_only"):
                from tracebridge.public_admin_ui import render_public_settings
                render_public_settings(api, project)
                render_search_settings(api, project, selected)
            if api:
                with st.expander("다른 컴퓨터 연결"):
                    ids = st.text_input("연결할 프로젝트 ID (쉼표 구분)", key="remote_pair_projects")
                    if st.button("5분 유효 페어링 코드 생성"):
                        try:
                            pairing = api.request("POST", "/v1/pairings", {"project_ids": [value.strip() for value in ids.split(",") if value.strip()]})
                            st.code(pairing["code"])
                        except (GatewayError, ValueError, httpx.HTTPError) as exc:
                            st.error(str(exc))
            if st.session_state.get("tracebridge_navigation"):
                st.page_link("pages/2_Report_Agent.py", label="개발자용 직접 조사")
                st.page_link("pages/1_Agolive_Investigation.py", label="Agolive 조사 도구")
                st.page_link("pages/0_Fixture_Demo.py", label="합성 자료 데모")
        with intake:
            st.subheader("문제 알려주기")
            with st.form("connected-report", border=True):
                report = st.text_area("어떤 문제가 있었나요?", placeholder="어떤 동작을 하다가 무엇이 안 됐는지 알려주세요. 사진만 보내도 됩니다.",
                    max_chars=4000, key="remote_report_text", height=150)
                photo = st.file_uploader("오류 화면 사진 (선택)", type=["png", "jpg", "jpeg"], max_upload_size=8, key="remote_report_photo")
                encoded_photo = None
                photo_valid = True
                if photo:
                    try:
                        encoded_photo = encode_report_image(photo.getvalue())
                        st.image(decode_report_image(encoded_photo), width=220)
                    except ValueError as exc:
                        st.error(str(exc))
                        photo_valid = False
                use_nvidia = st.checkbox("AI로 사진·원인 분석", key="remote_nvidia",
                    help="선택하면 제보·사진과 필요한 코드·로그를 NVIDIA 분석 서비스에 전송합니다.")
                st.caption("사진 내용을 자동으로 읽으려면 AI 분석을 선택해 주세요.")
                send = st.form_submit_button("제보 접수", type="primary", disabled=api is None or connection_pending or not photo_valid)
            if send:
                try:
                    if not report.strip() and photo is None:
                        raise ValueError("증상 글이나 오류 화면 사진을 보내주세요.")
                    payload = {"text": report, "service": override, "use_nvidia": use_nvidia, "assessment": assessment}
                    if photo is not None:
                        payload["image_b64"] = encoded_photo
                    response = submit(f"/v1/projects/{selected}/reports", payload)
                    st.session_state.remote_job_id = response["job_id"]
                    st.session_state["latest_job:" + selected] = response["job_id"]
                    st.rerun()
                except (GatewayError, ValueError, httpx.HTTPError) as exc:
                    st.error(str(exc))
            if api is None:
                st.info("프로젝트 등록은 준비됐습니다. 서버 연결을 설정하면 제보를 처리할 수 있어요.")
        with result_space:
            if api:
                render_results(api, selected, project, service, use_nvidia, policy_id)
    else:
        st.info("프로젝트를 먼저 등록해 주세요.")
with right:
    with st.container(border=True):
        if by_id and not st.session_state.get("project_add"):
            render_connection(st, project, compact=True)
        else:
            st.subheader("연결 상태")
            st.caption("프로젝트를 등록하면 연결 상태를 확인할 수 있어요.")
