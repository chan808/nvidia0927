"""Owner project paths and factual availability/readiness diagnostics."""
from datetime import datetime, timezone
import hashlib
import os

from .project_health import assess_connection, connection_checks, inspect_project
from .project_registration_ui import render_registration
from .project_registry import list_profiles


AVAILABILITY = {"UP": "정상 상태 응답", "REACHABLE": "HTTP 정상 응답", "DOWN": "비정상 상태 응답",
    "OUT_OF_SERVICE": "서비스 사용 불가", "UNREACHABLE": "연결 실패", "NOT_REGISTERED": "주소 미등록", "UNKNOWN": "미확인"}


def render_connection(st, project, service):
    st.subheader("프로젝트 연결·진단")
    local = None
    if os.getenv("TRACEBRIDGE_ALLOW_PROJECT_REGISTRATION") == "1":
        profiles, errors = list_profiles()
        local = next((profile for profile in profiles if profile.project_id == project["project_id"]), None)
        if errors:
            st.warning("이 PC에서 읽지 못한 연결 설정: " + ", ".join(errors))
        if local:
            digest = hashlib.sha256(local.config_path.read_bytes()).hexdigest()
            render_registration(st, local, sidebar=False, namespace="connected:" + local.project_id + ":" + digest[:12])
            if digest != project["profile_sha256"]:
                st.info("이 PC의 설정과 실행기가 게시한 설정이 다릅니다. 실행기와 등록 폴더를 확인하고 새 연결 정보를 기다려 주세요.")
        else:
            render_registration(st, sidebar=False, namespace="connected:new:" + project["project_id"])
            st.caption("등록 경로는 이 웹 프로그램의 PC 기준입니다. 다른 PC의 프로젝트라면 해당 PC에서 경로를 등록합니다.")
    if saved := st.session_state.get("local_connection_saved"):
        st.caption("로컬 연결 저장 완료: " + saved + ". 실행기는 같은 등록 폴더와 페어링된 프로젝트 ID를 사용해야 합니다.")
    if st.button("PC 상태 새로 불러오기", key="connection-refresh:" + project["project_id"]):
        st.rerun()
    snapshot, origin = project, "등록 PC의 마지막 점검"
    if local:
        key = "connection-local:" + local.project_id + ":" + digest
        if st.button("이 PC에서 코드·로그·서비스 다시 점검", key="connection-doctor:" + local.project_id):
            try:
                with st.spinner("등록된 코드·자료와 서비스 응답을 확인하고 있습니다."):
                    health = inspect_project(local)
                st.session_state[key] = {"project_id": local.project_id, "online": True,
                    "health_checked_at": datetime.now(timezone.utc).isoformat(),
                    "service_health": {row["id"]: row["service_status"] for row in health["services"]},
                    "connection_checks": connection_checks(health), "details": health}
            except (OSError, ValueError) as exc:
                st.error("연결 점검을 완료하지 못했습니다: " + str(exc))
        if key in st.session_state:
            snapshot, origin = st.session_state[key], "이 웹 프로그램의 PC에서 직접 점검"
    analysis = assess_connection(snapshot, service)
    pc, availability, readiness = st.columns(3)
    pc.metric("PC 실행기", "온라인" if project["online"] else "오프라인")
    availability.metric("서비스 응답", AVAILABILITY[analysis["availability"]])
    readiness.metric("자료 조사 준비", {"READY": "기본 연결 확인", "PARTIAL": "일부 연결 필요", "UNKNOWN": "미확인"}[analysis["readiness"]])
    st.caption(origin + " · 확인 시각: " + str(analysis["checked_at"] or "미확인") + " · 응답 확인은 기능 전체의 정상 동작·제보 해결 확인과 별개입니다.")
    if not project["online"]:
        st.warning("PC 실행기가 오프라인입니다. 원격 제보는 대기합니다. 이 PC 직접 점검 결과는 실행기 접속 상태와 별개입니다.")
    for finding in analysis["findings"]:
        st.write("• " + finding)
    if analysis["next_steps"]:
        st.write("다음 연결 작업")
        for step in analysis["next_steps"]:
            st.write("• " + step)
    if analysis["facts"]:
        facts = analysis["facts"]
        st.table([{"확인 항목": "코드 경로", "결과": facts["code_status"]},
            {"확인 항목": "요청 로그", "결과": facts["log_status"] + " · " + str(facts["observed_requests"]) + "건"},
            {"확인 항목": "OpenAPI 계약", "결과": facts["contract_status"]},
            {"확인 항목": "DTO / 호출자 자료", "결과": ("등록" if facts["dto_registered"] else "미등록") + " / " + ("등록" if facts["caller_registered"] else "미등록")},
            {"확인 항목": "실행 버전 자료", "결과": facts["runtime_version_status"]},
            {"확인 항목": "서비스 HTTP", "결과": str(facts["http_status"] or "미확인")}])
    else:
        st.caption("상세 점검 자료는 갱신된 API·PC 실행기가 게시합니다. 같은 PC의 로컬 점검도 사용할 수 있습니다.")
    if local and snapshot.get("details"):
        with st.expander("이 PC의 등록 경로·점검 상세"):
            st.dataframe(snapshot["details"]["repositories"], hide_index=True)
    return local
