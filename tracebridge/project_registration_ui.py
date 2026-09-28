"""Explicit local administrator UI; paths refer to the executing server."""
import os

from .project_registry import save_profile


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def render_registration(st):
    if os.getenv("TRACEBRIDGE_ALLOW_PROJECT_REGISTRATION") != "1":
        return
    with st.sidebar.expander("프로젝트 경로 등록"):
        st.caption("경로는 이 프로그램을 실행 중인 컴퓨터 기준입니다. 클라우드 웹에서 다른 PC를 조사하려면 별도 로컬 실행기 연결이 필요합니다.")
        with st.form("register_project"):
            project_id = st.text_input("프로젝트 ID", key="registration_project_id")
            service = st.text_input("관측 대상 서비스", value="backend", key="registration_service")
            environment = st.selectbox("등록 환경", ["dev", "test", "staging", "prod"], key="registration_environment")
            primary_root = st.text_input("기준 저장소 경로", placeholder="예: /projects/backend 또는 D:/work/backend", key="registration_primary_root")
            st.write("코드·자료 경로 (서로 다른 드라이브나 디렉터리 가능)")
            repositories = st.data_editor([
                {"id": "frontend", "service": "frontend", "root": "", "code_roots": "src", "git_root": ""},
                {"id": "backend", "service": "backend", "root": "", "code_roots": "src", "git_root": ""},
            ], num_rows="dynamic", key="registration_repositories", hide_index=True)
            st.write("로그 파일 (repository는 위 id, 빈 값이면 기준 저장소)")
            logs = st.data_editor([
                {"id": "backend-log", "repository": "backend", "service": "backend", "path": "", "format": "text", "timezone": "+09:00"},
            ], num_rows="dynamic", key="registration_logs", hide_index=True)
            openapi = st.text_input("OpenAPI JSON 경로 (선택)", key="registration_openapi")
            dto = st.text_input("DTO JSON 경로 (선택)", key="registration_dto")
            caller = st.text_input("호출자 근거 JSON 경로 (선택)", key="registration_caller")
            services = st.text_input("서비스별 연결 JSON (선택)", placeholder='[{"id":"backend","log_source_ids":["backend-log"]}]', key="registration_services")
            submitted = st.form_submit_button("프로젝트 연결 저장")
        if submitted:
            try:
                data = {"project_id": project_id, "service": service, "environment": environment,
                        "root": primary_root, "code_roots": [], "policy_refs": [],
                        "repositories": [{"id": row["id"], "service": row["service"], "root": row["root"],
                                          "code_roots": [entry.strip() for entry in _text(row.get("code_roots")).split(",") if entry.strip()],
                                          **({"git_root": _text(row.get("git_root"))} if _text(row.get("git_root")) else {})}
                                         for row in repositories if _text(row.get("root"))],
                        "log_sources": [{key: value for key, value in row.items() if value and key in {"id", "repository", "path", "format", "timezone", "service"}}
                                        for row in logs if _text(row.get("path"))]}
                for name, value in (("openapi_path", openapi), ("dto_path", dto), ("caller_evidence_path", caller)):
                    if value.strip():
                        data[name] = value.strip()
                if services.strip():
                    import json
                    data["services"] = json.loads(services)
                saved = save_profile(data)
                st.session_state.registered_profile_path = str(saved)
                st.session_state.report_agent_result = None
                st.session_state.report_agent_change = None
                st.session_state.report_project = project_id + " (등록 프로젝트)"
                st.rerun()
            except (OSError, ValueError, KeyError, TypeError) as exc:
                st.error(str(exc))
