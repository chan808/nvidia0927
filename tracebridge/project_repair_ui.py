"""Local owner controls and reviewable registered-project candidate artifacts."""
import json
import os
from pathlib import Path

from .project_profile import load_project_profile
from .project_registry import profile_data, save_profile
from .project_repair import load_repair_policy, save_repair_policy, repair_blockers
from .project_sources import redact


def render_repair_registration(st, profile):
    if os.getenv("TRACEBRIDGE_ALLOW_PROJECT_REGISTRATION") != "1":
        return
    with st.sidebar.expander("프로젝트 수정·검사 설정"):
        st.caption("허용한 저장소의 별도 사본에서만 수정 후보를 준비합니다. 로컬 검사는 신뢰하는 프로젝트 코드를 실행하며 파일·네트워크 격리를 제공하지 않습니다.")
        ids = ["primary", *[item.id for item in profile.repositories if item.code_roots]]
        with st.form("register_project_repair"):
            repository = st.selectbox("수정 대상 저장소", ids, key="repair_repository")
            patterns = st.text_input("수정 허용 경로", placeholder="예: src/**/*.ts, src/**/*.tsx, app/*.py", key="repair_paths")
            mode = st.selectbox("검사 실행 방식", ["DOCKER", "TRUSTED_LOCAL"], key="repair_mode")
            image = st.text_input("Docker 로컬 이미지 (Docker 방식)", key="repair_image")
            reproduction = st.text_input("재현 검사 argv (JSON 배열)", placeholder='["python", "-m", "pytest", "tests/test_bug.py"]', key="repair_repro_argv")
            regression = st.text_input("회귀 검사 argv (JSON 배열)", placeholder='["python", "-m", "pytest", "tests"]', key="repair_regression_argv")
            cwd = st.text_input("검사 작업 디렉터리", value=".", key="repair_cwd")
            marker = st.text_input("재현 실패를 식별할 출력 문구", placeholder="예: test_signup_payload", key="repair_failure_marker")
            reproduce_success = st.text_input("재현 검사 성공 문구", placeholder="예: passed", key="repair_reproduce_success")
            regression_success = st.text_input("회귀 검사 성공 문구", placeholder="예: Test Files", key="repair_regression_success")
            copy_dependencies = st.checkbox("로컬 검사에 node_modules의 독립 사본 준비", key="repair_copy_dependencies")
            trust = st.checkbox("이 프로젝트의 코드와 등록된 검사 실행을 허용합니다", key="repair_trust")
            allow_apply = st.checkbox("검토한 수정 후보의 원본 적용을 허용합니다", key="repair_allow_apply")
            reproduction_first = st.checkbox("로그 연결이 없을 때 등록 검사로 재현부터 확인합니다", key="repair_without_logs")
            submitted = st.form_submit_button("수정·검사 설정 저장")
        if submitted:
            try:
                if not trust:
                    raise ValueError("프로젝트 소유자의 코드·검사 실행 허용이 필요합니다")
                id_ = profile.project_id + "-" + repository + "-repair"
                data = profile_data(profile)
                data["policy_refs"] = list(dict.fromkeys([*data["policy_refs"], id_]))
                proposed_policy = {"policy_id": id_, "project_id": profile.project_id, "repository_id": repository,
                    "environment": profile.environment, "enabled": True, "trust_project_code": trust, "allow_apply": allow_apply,
                    "allow_reproduction_without_logs": reproduction_first,
                    "execution_mode": mode, "docker_image": image.strip() or None,
                    "editable_paths": [value.strip() for value in patterns.split(",") if value.strip()],
                    "checks": [{"id": "reproduce", "argv": json.loads(reproduction), "cwd": cwd, "success_marker": reproduce_success,
                                "dependency_directories": ["node_modules"] if copy_dependencies else []},
                               {"id": "regression", "argv": json.loads(regression), "cwd": cwd, "success_marker": regression_success,
                                "dependency_directories": ["node_modules"] if copy_dependencies else []}],
                    "reproduction_check_id": "reproduce", "regression_check_id": "regression", "failure_marker": marker}
                from .project_repair import _validate_policy
                _validate_policy(proposed_policy)
                updated = load_project_profile(save_profile(data))
                save_repair_policy(proposed_policy, updated)
                st.session_state.registered_profile_path = str(updated.config_path)
                st.session_state.report_agent_result = None
                st.session_state.report_agent_change = None
                st.rerun()
            except (OSError, ValueError, TypeError, KeyError) as exc:
                st.error(str(exc))


def project_repair_blockers(result, profile, policy_id=None):
    try:
        from .project_profile import select_project_service
        profile = select_project_service(profile, result.get("scope", {}).get("service") or result.get("source_registration", {}).get("service"))
        policy, _ = load_repair_policy(profile, policy_id)
        return repair_blockers(result, profile, policy)
    except (OSError, ValueError, TypeError) as exc:
        return [str(exc)]


def candidate_diff(job):
    artifact = Path(job["artifact_ref"]).resolve()
    path = Path(job.get("diff", {}).get("ref", "")).resolve()
    if path.parent != artifact.parent or path.is_symlink() or path.stat().st_size > 150_000:
        raise ValueError("후보 diff의 등록 경로/크기를 확인하지 못했습니다")
    from .change_policy import sha256
    raw = path.read_bytes()
    if sha256(raw) != job["diff"]["sha256"]:
        raise ValueError("후보 diff 내용이 변경되었습니다")
    return redact(raw.decode("utf-8"))
