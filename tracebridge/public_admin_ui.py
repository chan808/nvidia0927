"""Owner configuration; loading is explicit to preserve existing private flows."""
import streamlit as st

from .local_runner import GatewayError


def render_public_settings(api, project):
    project_id = project["project_id"]
    state_key = "public-service-policy:" + project_id
    with st.expander("공개 제보와 자동 처리 설정"):
        st.caption("등록한 서비스의 제보 접수, 작업 예산, 수정 정책을 설정합니다.")
        if st.button("설정 불러오기", key="load-public:" + project_id):
            try:
                st.session_state[state_key] = api.request("GET", f"/v1/projects/{project_id}/service-policy")
            except GatewayError as exc:
                st.error(str(exc))
        current = st.session_state.get(state_key)
        if current is None:
            return
        policy = current["policy"]
        prefix = f"public:{project_id}:{current['revision']}:"
        with st.form(prefix + "form"):
            enabled = st.checkbox("외부 사용자 제보 접수", value=policy["enabled"], key=prefix + "enabled")
            title = st.text_input("제보 화면 제목", value=policy["title"], max_chars=80, key=prefix + "title")
            services = st.multiselect("공개 접수 대상 서비스", project["service_ids"],
                default=[value for value in policy["services"] if value in project["service_ids"]], key=prefix + "services")
            names = st.text_area("사용자에게 표시할 기능 이름 (서비스 ID=이름, 한 줄에 하나)",
                value="\n".join(key + "=" + value for key, value in policy["service_labels"].items()), key=prefix + "names")
            guidance = st.text_area("입력 안내 이름 (필드=표시 이름, 한 줄에 하나)",
                value="\n".join(key + "=" + value for key, value in policy["guidance_labels"].items()), key=prefix + "guidance")
            nvidia = st.checkbox("제보자의 동의를 받은 외부 NVIDIA 분석 허용", value=policy["use_nvidia"], key=prefix + "nvidia")
            modes = ["INVESTIGATE", "PREPARE", "APPLY_NONPROD"]
            labels = {"INVESTIGATE": "조사와 안내", "PREPARE": "등록된 수정 후보 자동 준비", "APPLY_NONPROD": "허용된 작은 비운영 수정 적용·회복 확인"}
            mode = st.selectbox("자동 처리 범위", modes, index=modes.index(policy["automation"]), format_func=labels.get, key=prefix + "mode")
            ids = [None, *project.get("repair_policy_ids", [])]
            selected = policy["repair_policy_id"] if policy["repair_policy_id"] in ids else None
            repair = st.selectbox("자동 처리에 사용할 PC 수정 정책", ids, index=ids.index(selected),
                format_func=lambda value: value or "선택 안 함", key=prefix + "policy")
            review = st.checkbox("등록한 회복 검사 성공 카드의 검색 재사용 승인", value=policy["auto_review_recovered"], key=prefix + "review")
            hourly = st.number_input("프로젝트의 시간당 접수·답변 상한", min_value=1, max_value=1000, value=policy["hourly_limit"], key=prefix + "hourly")
            pending = st.number_input("대기·실행·중단 확인 중인 작업 상한", min_value=1, max_value=200, value=policy["pending_limit"], key=prefix + "pending")
            save = st.form_submit_button("설정 저장")
        if save:
            try:
                def labels(text):
                    entries = [line.split("=", 1) for line in text.splitlines() if line.strip()]
                    if any(len(entry) != 2 for entry in entries):
                        raise ValueError("안내 이름은 ID=표시 이름 형식으로 입력해 주세요.")
                    return {key.strip(): value.strip() for key, value in entries}
                updated = api.request("PUT", f"/v1/projects/{project_id}/service-policy", {
                    "expected_revision": current["revision"], "policy": {**policy, "enabled": enabled,
                        "title": title, "services": services, "use_nvidia": nvidia, "automation": mode,
                        "repair_policy_id": repair, "auto_review_recovered": review, "hourly_limit": hourly, "pending_limit": pending,
                        "service_labels": labels(names), "guidance_labels": labels(guidance)}})
                st.session_state[state_key] = updated
                st.success("설정을 저장했습니다.")
            except (GatewayError, ValueError) as exc:
                st.error(str(exc))
        st.code("/report/" + project_id, language=None)
        st.caption("API 서버 또는 연결된 HTTPS 서비스 주소에서 위 경로를 여세요. 공개 제보의 실모델 수정 검사는 PC의 Docker 격리가 필요합니다. 비운영 자동 적용에는 auto_apply_nonprod와 회복 검사도 등록해야 합니다.")
