"""Private-owner queue, reopening saved jobs and durable execution timelines."""
from datetime import datetime, timedelta, timezone

import httpx
import streamlit as st

from .local_runner import GatewayError


def assessment_inputs(*, key: str, current: dict | None = None) -> dict:
    current = current or {"priority": "P2", "difficulty": "UNKNOWN", "risk": "UNKNOWN", "reason": ""}
    priority = st.selectbox("중요도", ["P0", "P1", "P2", "P3"],
        index=["P0", "P1", "P2", "P3"].index(current["priority"]), key=key + ":priority")
    difficulty = st.selectbox("작업 난이도", ["UNKNOWN", "SMALL", "MEDIUM", "LARGE"],
        index=["UNKNOWN", "SMALL", "MEDIUM", "LARGE"].index(current["difficulty"]),
        format_func=lambda value: {"UNKNOWN": "미정", "SMALL": "작음", "MEDIUM": "보통", "LARGE": "큼"}[value], key=key + ":difficulty")
    risk = st.selectbox("변경 위험", ["UNKNOWN", "LOW", "MEDIUM", "HIGH"],
        index=["UNKNOWN", "LOW", "MEDIUM", "HIGH"].index(current["risk"]),
        format_func=lambda value: {"UNKNOWN": "미정", "LOW": "낮음", "MEDIUM": "보통", "HIGH": "높음"}[value], key=key + ":risk")
    reason = st.text_input("판단 근거", value=current["reason"], max_chars=500, key=key + ":reason")
    st.caption("중요도 순서로 대기 작업을 실행합니다. 조사 시간은 작음 45초·보통/미정 90초·큼 180초입니다. 변경 위험은 검토 자료이며 등록된 수정·검사 정책을 따릅니다.")
    return {"priority": priority, "difficulty": difficulty, "risk": risk, "reason": reason}


def _when(value):
    return datetime.fromtimestamp(value, timezone(timedelta(hours=9))).strftime("%m-%d %H:%M:%S KST")


def render_work_queue(api, project_id: str) -> None:
    with st.expander("프로젝트 작업 목록·저장된 결과", expanded=True):
        try:
            response = api.request("GET", f"/v1/projects/{project_id}/jobs")
            counts = response["counts"]
            st.caption(" · ".join(f"{state}: {count}" for state, count in counts.items()) or "기록된 작업이 없습니다.")
            jobs = response["jobs"]
            if not jobs:
                return
            st.dataframe([{"작업": item["id"], "서비스": item["service"], "중요도": item["assessment"]["priority"],
                "상태": item["state"], "조사": item["investigation_status"], "후보": item["candidate_status"],
                "원본 적용": item.get("original_applied", False), "서비스 회복": item["service_recovery"], "사건": item.get("incident_state", "OPEN"),
                "접수": _when(item["created"])} for item in jobs], hide_index=True)
            choices = {item["id"]: item for item in jobs}
            choice = st.selectbox("기록된 작업 선택", list(choices), key="remote_saved_job:" + project_id,
                format_func=lambda id_: f"{choices[id_]['assessment']['priority']} · {choices[id_]['service']} · {choices[id_]['state']} · {id_[:12]}")
            if st.button("저장된 작업 열기", key="remote_open_job:" + project_id):
                st.session_state.remote_job_id = choice
                st.session_state.remote_service = choices[choice]["service"]
                st.rerun()
            if response["next_cursor"]:
                st.caption("최근 50개 작업을 표시합니다. 이전 작업은 작업 ID로 열 수 있습니다.")
            older = st.text_input("작업 ID로 열기", key="remote_lookup_job:" + project_id)
            if st.button("작업 조회", key="remote_lookup_button:" + project_id, disabled=not older.strip()):
                stored = api.request("GET", "/v1/jobs/" + older.strip())
                if stored["project_id"] != project_id:
                    st.error("선택한 프로젝트의 작업 ID를 입력해 주세요.")
                else:
                    st.session_state.remote_job_id = stored["id"]
                    st.session_state.remote_service = stored["service"]
                    st.rerun()
        except (GatewayError, ValueError, OSError, httpx.HTTPError) as exc:
            st.error(str(exc))


def render_work_details(api, status: dict) -> None:
    job_id = status["id"]
    with st.expander("작업 평가·실행 이력"):
        st.write("중요도:", status["assessment"]["priority"], "· 난이도:", status["assessment"]["difficulty"],
                 "· 변경 위험:", status["assessment"]["risk"])
        st.write(status["assessment"]["reason"])
        if status["state"] == "QUEUED":
            with st.form("assessment:" + job_id):
                assessment = assessment_inputs(key=job_id + ":edit", current=status["assessment"])
                if st.form_submit_button("대기 작업 평가 갱신"):
                    api.request("PUT", f"/v1/jobs/{job_id}/assessment",
                        {"expected_revision": status["assessment_revision"], "assessment": assessment})
                    st.rerun()
        response = api.request("GET", f"/v1/jobs/{job_id}/events?limit=200")
        st.dataframe([{"시각": _when(item["occurred"]), "기록": item["kind"],
            "이전": item["from_state"], "이후": item["to_state"], "실행 차수": item["epoch"]}
            for item in response["events"]], hide_index=True)
        if response["has_more"]:
            st.caption("처음 200개 이력을 표시합니다. 나머지는 API의 next_after로 조회할 수 있습니다.")
        st.json(response["events"], expanded=False)
