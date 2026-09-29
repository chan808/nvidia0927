"""Local owner registration shared by direct and connected project screens."""
import hashlib
import json
import os

from .project_registry import profile_data, registry_directory, save_profile


def _text(value):
    return value.strip() if isinstance(value, str) else ""


def _relative(path, root):
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)


def render_registration(st, profile=None, *, sidebar=True, namespace="registration"):
    if os.getenv("TRACEBRIDGE_ALLOW_PROJECT_REGISTRATION") != "1":
        return
    data = profile_data(profile) if profile else {}
    expected = hashlib.sha256(profile.config_path.read_bytes()).hexdigest() if profile else None
    key = lambda name: namespace + "_" + name
    container = st.sidebar if sidebar else st
    with container.expander("프로젝트 경로 등록" if sidebar else "이 PC의 경로·서비스 주소 설정"):
        st.caption("이 웹 프로그램을 실행하는 컴퓨터의 경로입니다. PC 실행기도 같은 등록 폴더를 사용해야 합니다. 다른 PC의 연결 설정은 해당 PC에서 등록합니다.")
        with st.form("register_project" if sidebar else namespace + "_form"):
            project_id = st.text_input("프로젝트 ID", value=data.get("project_id", ""), disabled=bool(profile), key=key("project_id"))
            service = st.text_input("관측 대상 서비스", value=data.get("service", "backend"), key=key("service"))
            environments = list(dict.fromkeys(["dev", "test", "staging", "prod", data.get("environment", "dev")]))
            environment = st.selectbox("등록 환경", environments, index=environments.index(data.get("environment", "dev")), key=key("environment"))
            primary_root = st.text_input("기준 저장소 경로", value=data.get("root", ""), placeholder="예: D:/work/my-app", key=key("primary_root"))
            primary_codes = ",".join(_relative(path, profile.root) for path in profile.code_roots) if profile else "" if sidebar else "."
            code_roots = st.text_input("기준 저장소의 코드 경로 (쉼표 구분)", value=primary_codes, key=key("code_roots"))
            st.write("코드·자료 경로 (서로 다른 드라이브나 디렉터리 가능)")
            defaults = [{"id": "frontend", "service": "frontend", "root": "", "code_roots": "src", "git_root": ""},
                {"id": "backend", "service": "backend", "root": "", "code_roots": "src", "git_root": ""}]
            repository_rows = [{**row, "code_roots": ",".join(_relative(path, item.root) for path in item.code_roots)}
                for row, item in zip(data.get("repositories", []), profile.repositories)] if profile else defaults
            repositories = st.data_editor(repository_rows or defaults, num_rows="dynamic", key=key("repositories"), hide_index=True)
            st.write("로그 파일 (repository는 위 id, 빈 값이면 기준 저장소)")
            logs = st.data_editor(data.get("log_sources") or [
                {"id": "backend-log", "repository": "backend", "service": "backend", "path": "", "format": "text", "timezone": "+09:00"}],
                num_rows="dynamic", key=key("logs"), hide_index=True)
            openapi = st.text_input("OpenAPI JSON 경로 (선택)", value=data.get("openapi_path", ""), key=key("openapi"))
            dto = st.text_input("DTO JSON 경로 (선택)", value=data.get("dto_path", ""), key=key("dto"))
            caller = st.text_input("호출자 근거 JSON 경로 (선택)", value=data.get("caller_evidence_path", ""), key=key("caller"))
            health_url = st.text_input("기본 서비스 상태 확인 주소 (선택)", value=data.get("health_url", ""),
                placeholder="http://127.0.0.1:8081/actuator/health", key=key("health"))
            service_urls = {item["id"]: st.text_input("서비스 상태 확인 주소 · " + item["id"], value=item.get("health_url", ""),
                key=key("health:" + item["id"])) for item in data.get("services", [])}
            version = data.get("version_observation", {"method": "none"})
            version_method = st.selectbox("기본 실행 버전 확인 방법", ["none", "json_file", "log_field"],
                index=["none", "json_file", "log_field"].index(version["method"]), key=key("version_method"))
            version_path = st.text_input("실행 버전 JSON 경로", value=version.get("path", ""), key=key("version_path"))
            version_field = st.text_input("실행 버전 필드", value=version.get("field", "version"), key=key("version_field"))
            services = st.text_area("서비스별 연결 JSON (선택)", value=json.dumps(data.get("services", []), ensure_ascii=False, indent=2) if data.get("services") else "",
                help="서비스별 로그·계약·실행 버전과 신규 서비스 주소를 지정합니다.", key=key("services"))
            submitted = st.form_submit_button("프로젝트 연결 저장")
        if submitted:
            try:
                if profile is None and (registry_directory() / (project_id + ".json")).exists():
                    raise ValueError("같은 프로젝트 ID가 이미 등록돼 있습니다. 연결된 프로젝트에서 해당 설정을 불러와 수정해 주세요")
                updated = {**data, "project_id": project_id, "service": service, "environment": environment,
                    "root": primary_root, "code_roots": [entry.strip() for entry in code_roots.split(",") if entry.strip()],
                    "policy_refs": data.get("policy_refs", []),
                    "repositories": [{"id": row["id"], "service": row["service"], "root": row["root"],
                        "code_roots": [entry.strip() for entry in _text(row.get("code_roots")).split(",") if entry.strip()],
                        **({"git_root": _text(row.get("git_root"))} if _text(row.get("git_root")) else {})}
                        for row in repositories if _text(row.get("root"))],
                    "log_sources": [{name: value for name, value in row.items() if value and name in {"id", "repository", "path", "format", "timezone", "service"}}
                        for row in logs if _text(row.get("path"))]}
                for name, value in (("openapi_path", openapi), ("dto_path", dto), ("caller_evidence_path", caller), ("health_url", health_url)):
                    updated.pop(name, None)
                    if value.strip():
                        updated[name] = value.strip()
                updated["version_observation"] = {"method": version_method, "field": version_field,
                    **({"path": version_path.strip()} if version_method == "json_file" else {})}
                updated["services"] = json.loads(services) if services.strip() else []
                old_urls = {item["id"]: item.get("health_url", "") for item in data.get("services", [])}
                for item in updated["services"]:
                    if item["id"] in service_urls and service_urls[item["id"]] != old_urls[item["id"]]:
                        item.pop("health_url", None)
                        if service_urls[item["id"]].strip():
                            item["health_url"] = service_urls[item["id"]].strip()
                saved = save_profile(updated, expected_sha256=expected)
                st.session_state.registered_profile_path = str(saved)
                st.session_state.report_agent_result = None
                st.session_state.report_agent_change = None
                st.session_state.report_project = project_id + " (등록 프로젝트)"
                st.session_state["local_connection_saved"] = project_id
                st.rerun()
            except (OSError, ValueError, KeyError, TypeError) as exc:
                st.error(str(exc))
