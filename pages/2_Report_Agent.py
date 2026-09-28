"""Natural text/photo intake and same-session replies to the common investigation path."""

from pathlib import Path
import os
import sqlite3

from dotenv import load_dotenv
import streamlit as st

from tracebridge.report_agent import confirm_candidate, investigate_submission, nvidia_settings
from tracebridge.report_contract import KST, ReportContext, event_time, presentation_status
from tracebridge.incident_memory import IncidentStore, db_location, persist_result, export_manual
from tracebridge.project_profile import load_project_profile
from tracebridge.project_sources import agolive_repo_path, validate_agolive_repo
from tracebridge.report_service import auto_prepare_submission, follow_up_service as follow_up_submission, prepare_submission, preparation_blockers
from tracebridge.change_worker import persist_change_result
from tracebridge.change_policy import WORKSPACE, safe_path
from tracebridge.seed_project import capture_seed_action


st.set_page_config(page_title="제보 에이전트", page_icon="🔎", layout="wide")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
st.title("제보 에이전트")
st.caption("증상을 한 줄로 적거나 사진만 첨부해도 됩니다. 화면·동작·대략적인 시각을 단서로 조사합니다.")
profiles = ["Agolive", "등록된 개발 가입 사례 (씨드)"]
configured_profile = None
if profile_path := os.getenv("TRACEBRIDGE_PROJECT_PROFILE"):
    try:
        configured_profile = load_project_profile(profile_path)
        profiles.append(configured_profile.project_id + " (등록 프로젝트)")
    except (OSError, ValueError):
        st.error("등록 프로젝트 설정을 읽지 못했습니다. 관리자에게 연결 설정 확인을 요청해 주세요.")
        st.stop()
default_seed = os.getenv("TRACEBRIDGE_SERVICE_PROJECT", "seed" if not os.getenv("TRACEBRIDGE_AGOLIVE_REPO") and not os.getenv("TRACEBRIDGE_EVENTS_FILE") else "agolive") == "seed"
profile = st.selectbox("연결된 프로젝트", profiles, index=2 if configured_profile else 1 if default_seed else 0, key="report_project")
seed = profile == profiles[1]
custom_project = bool(configured_profile and profile == profiles[2])
if st.session_state.get("active_report_project") != profile:
    st.session_state.active_report_project = profile
    st.session_state.report_agent_result = None
    st.session_state.report_agent_change = None
    st.session_state.use_nvidia_analysis = False
    st.session_state.automatic_seed_change = False
    st.session_state.seed_action = None
repo = None
if seed:
    st.caption("통제된 개발 씨드 사례입니다. 가입 동작과 별도 사본의 수정안을 검증하며 원본 적용은 검토 대기로 남깁니다.")
elif not custom_project:
    try:
        repo = validate_agolive_repo(agolive_repo_path())
    except (OSError, ValueError):
        st.error("조사할 프로젝트가 연결되지 않았습니다. 관리자에게 프로젝트 연결을 요청해 주세요.")
        st.stop()
key, _ = nvidia_settings()
text = st.text_area("증상 또는 요청 (사진이 있으면 생략 가능)", placeholder="예: 방금 가입이 안 돼요. 알아보고 고쳐줘.", max_chars=4000)
photo = st.file_uploader("오류 화면 사진 (선택)", type=["png", "jpg", "jpeg"])
image = None
if photo:
    if photo.size > 8_000_000:
        st.error("사진은 8 MB 이하여야 합니다.")
        st.stop()
    image = photo.getvalue()
    st.image(image, width=600)
with st.expander("기억나는 상황 (선택)"):
    occurred = st.text_input("대략적인 발생 시각", placeholder="예: 2026-09-28 오전 10시 20분")
    environment_label = st.selectbox("어디에서 사용했나요?", ["모르겠어요", "개발 환경", "스테이징", "운영 서비스"], key="report_environment")
environment = {"개발 환경": "dev", "스테이징": "staging", "운영 서비스": "prod"}.get(environment_label)
use_nvidia = st.checkbox("제보·사진과 선별된 코드·로그를 NVIDIA 분석 서비스에 전송", disabled=not bool(key), key="use_nvidia_analysis")
if not key:
    st.info("현재는 로컬 관측 대조와 코드 검색이 가능합니다. 사진 해석은 NVIDIA 연결 설정이 필요합니다.")
include_docker = st.checkbox("연결된 로컬 컨테이너 로그 조회", value=False, disabled=seed or custom_project)
memory_enabled = st.checkbox("검토된 과거 사건을 조사 단서로 검색", value=True, key="report_memory_enabled")
if not memory_enabled:
    st.caption("이번 조사에 과거 카드를 전달하지 않습니다. 현재 사건의 저장은 계속됩니다.")
automatic = st.checkbox("작업 후보이면 허용된 수정안까지 준비", value=False, disabled=not seed or not use_nvidia, key="automatic_seed_change")
options = {"repo": repo, "use_nvidia": use_nvidia, "include_docker_logs": include_docker if not seed else False}
if seed:
    options = {"registered_seed": True, "use_nvidia": use_nvidia}
elif custom_project:
    options = {"project_profile": configured_profile, "use_nvidia": use_nvidia}
options.update(memory_enabled=memory_enabled, db_path=db_location())
if metrics_dir := os.getenv("TRACEBRIDGE_METRICS_DIR"):
    options["observer_output_dir"] = Path(metrics_dir)


def save_active(new_result):
    st.session_state.report_agent_result = new_result
    try:
        prepared = auto_prepare_submission(new_result, enabled=automatic, live=use_nvidia)
    except (ValueError, RuntimeError, OSError, sqlite3.Error):
        st.warning("조사 결과는 확보했지만 수정안 준비를 연결하지 못했습니다. 저장 상태와 개발 정책을 확인해 주세요.")
        return
    if prepared:
        st.session_state.report_agent_change = prepared


def time_label(value):
    try:
        return event_time(value).astimezone(KST).strftime("%m월 %d일 %H:%M:%S") + " (한국 시각)"
    except ValueError:
        return "시각 미확인"


if st.button("조사 시작", type="primary", key="start_report"):
    st.session_state.report_agent_result = None
    st.session_state.report_agent_change = None
    try:
        context = ReportContext(environment=environment).with_answer(occurred)
        with st.spinner("제보와 현재 관측을 대조하고 있습니다..."):
            new_result = investigate_submission(text, image=image, context=context, **options)
        save_active(new_result)
    except (ValueError, RuntimeError) as exc:
        st.error(str(exc))
    except Exception:
        st.error("조사를 완료하지 못했습니다. 연결 상태를 확인한 뒤 다시 시도해 주세요.")

if seed:
    with st.expander("개발 가입 동작 확인"):
        st.write("등록된 개발 코드의 가입 동작을 실행해 현재 관측을 남깁니다. 실행 후 '방금 가입이 안 돼요'처럼 제보할 수 있습니다.")
        if st.button("개발 가입 동작 실행", key="capture_seed_action"):
            try:
                observed = capture_seed_action()
                st.session_state.seed_action = observed
                st.session_state.report_agent_result = None
                st.session_state.report_agent_change = None
                st.rerun()
            except (OSError, ValueError):
                st.error("등록된 개발 동작을 확인하지 못했습니다. 개발 프로젝트 연결을 확인해 주세요.")
        if observation := st.session_state.get("seed_action"):
            st.write("가입 결과:", observation["event"]["trace"]["response_status"])
            st.write("발생 시각:", time_label(observation["event"]["trace"]["occurred_at"]))

result = st.session_state.get("report_agent_result")
if result:
    route_labels = {"GUIDANCE": "입력·동작 안내", "REQUEST_CONTEXT": "추가 정보 대기", "INVESTIGATE": "추가 조사", "WORK_CANDIDATE": "담당자 작업 검토 후보"}
    run_labels = {"COMPLETED": "이번 조사 종료", "WAITING_CONTEXT": "정보 대기", "PARTIAL_FAILURE": "일부 조회 실패", "TIMED_OUT": "시간 초과", "BUDGET_EXHAUSTED": "호출 한도 도달"}
    st.subheader(route_labels[result["route"]])
    st.write(result["summary"].replace(" 재현 테스트는 아직 실행하지 않았습니다.", ""))
    st.info(result["route_reason"])
    st.write("조사 실행: " + run_labels[result["run_status"]])
    status = presentation_status(result, (st.session_state.get("report_agent_change") or {}).get("job"))
    agreement = {"MATCHED": "현재 응답과 일치", "CONTRADICTED": "현재 응답과 다름", "UNVERIFIABLE": "확인 대기"}
    st.write("제보의 HTTP 상태:", status["reported_http_status"] or "미입력", "·", agreement.get(status["report_status_verification"], "확인 대기"))
    st.write("현재 관측 응답:", ", ".join(str(value) for value in status["observed_http_statuses"]) or "미관측")
    st.write("실제 증상:", {"REQUEST_REJECTED": "요청 실패 관측", "SERVER_ERROR_OBSERVED": "서버 오류 응답 관측",
             "HTTP_SUCCESS_OBSERVED": "HTTP 성공 응답 관측", "UNOBSERVED": "현재 증상 관측 대기"}.get(status["actual_symptom_status"], "현재 근거 확인 필요"))
    st.write("제품 판단:", {"CALLER_DEFECT_OBSERVED": "호출자 결함 근거 확보", "MIGRATION_MISMATCH_OBSERVED": "마이그레이션 불일치 근거 확보",
             "UNCONFIRMED": "제품 정상·결함 미확정"}.get(status["product_status"], "현재 근거 확인 필요"))
    if status["collection_status"] == "INCOMPLETE" or status["observations_conflict"]:
        st.warning("관측이 불완전하거나 충돌해 안내·작업을 확정할 수 없습니다. 확보한 기록을 유지하며 추가 자료를 확인합니다.")
    if result.get("persistence", {}).get("status") == "FAILED":
        st.warning("조사 결과는 확보했지만 사건 기록을 저장하지 못했습니다. 아래 버튼으로 현재 결과의 저장만 재시도할 수 있습니다.")
        if st.button("이 결과 저장만 재시도", key="retry_incident_save"):
            st.session_state.report_agent_result = persist_result(result)
            st.rerun()
    for question in result["questions"]:
        st.write(f"추가 확인: {question}")
    if result["correlation"] != "EXACT_ID" and result.get("candidates") and not result.get("log_scope", {}).get("aggregate", {}).get("conflicts"):
        st.write("문제가 났던 동작을 확인하면 현재 자료를 다시 조회합니다.")
        candidates = {item["trace_id"]: item["scope"] for item in result["candidates"]}
        def action_label(id_):
            scope = candidates[id_]
            environment_name = {"dev": "개발 환경", "staging": "스테이징", "prod": "운영 서비스"}.get(scope.get("environment"), "환경 미확인")
            return " · ".join([scope.get("operation", "동작"), time_label(scope.get("occurred_at")), environment_name])
        selected = st.selectbox("문제가 난 동작 기록", list(candidates), format_func=action_label, key="report_candidate")
        if st.button("이 동작이 맞아요", key="confirm_report_candidate", disabled=status["collection_status"] == "INCOMPLETE" or status["observations_conflict"]):
            try:
                with st.spinner("확인한 동작의 현재 관측을 다시 대조합니다..."):
                    save_active(confirm_candidate(result, selected, **options))
                st.rerun()
            except (ValueError, RuntimeError, sqlite3.Error):
                st.error("후보를 확인하지 못했습니다. 최신 관측과 사건 기록을 확인해 주세요.")
    for hypothesis in result["hypotheses"]:
        with st.expander(f"원인 후보 · {hypothesis['cause']}", expanded=True):
            st.write(hypothesis["explanation"])
            st.write(f"검증 방법: {hypothesis['verification_step']}")
            for limitation in hypothesis.get("limitations", []):
                st.write(limitation)
    if result.get("hypothesis_updates"):
        st.write("이전 가설 재검토:", result["hypothesis_updates"])
    with st.form("same_incident_reply", clear_on_submit=True):
        answer = st.text_area("이 사건에 추가 답변", placeholder="예: 개발 환경의 방 입장 화면이었고, 오늘 오전 10시 30분쯤 정원 초과 문구가 나왔어요.", max_chars=4000, key="report_follow_up")
        submitted = st.form_submit_button("같은 사건에 답변 반영", key="reply_report")
    if submitted:
        try:
            with st.spinner("답변을 반영해 같은 사건의 현재 관측을 다시 확인합니다..."):
                updated = follow_up_submission(result, answer, **options)
            save_active(updated)
            st.rerun()
        except (ValueError, RuntimeError) as exc:
            st.error(str(exc))
        except Exception:
            st.error("답변을 반영하지 못했습니다. 이미 확보한 조사 결과는 유지됩니다.")
    if seed and result["route"] == "WORK_CANDIDATE" and not st.session_state.get("report_agent_change"):
        blockers = preparation_blockers(result)
        if blockers:
            st.caption("수정안 준비 대기: " + ", ".join(blockers))
        if st.button("허용된 수정안 준비", disabled=not use_nvidia or bool(blockers), key="prepare_report_change"):
            try:
                with st.spinner("별도 사본에 수정안을 만들고 같은 검사와 회귀를 실행합니다..."):
                    st.session_state.report_agent_change = prepare_submission(result, live=True)
                st.rerun()
            except (ValueError, RuntimeError, sqlite3.Error):
                st.error("수정안을 준비하지 못했습니다. 저장 상태와 개발 정책을 확인해 주세요.")
    prepared = st.session_state.get("report_agent_change")
    if prepared:
        job = prepared["job"]
        st.subheader("수정안 · 검토 대기")
        st.write(f"{job['status']} / {job['review_status']}")
        if job["candidate_fix_verified"]:
            st.success("별도 사본에서 수정 전 실패 → 수정 후 통과 및 회귀 통과를 확인했습니다.")
        else:
            st.warning("수정안 검증을 완료하지 못했습니다. 아래 실행 결과를 확인해 주세요.")
        st.table([{key: check.get(key) for key in ("phase", "status", "exit_code", "elapsed_ms")} for check in job["checks"]])
        if job["diff"].get("ref"):
            try:
                path = safe_path(WORKSPACE, job["diff"]["ref"], file=True)
                if path.stat().st_size <= 4096:
                    st.code(path.read_text(encoding="utf-8"), language="diff")
            except (OSError, ValueError):
                st.warning("후보 diff 파일을 조회하지 못했습니다.")
        st.caption("후보 검증은 등록된 씨드 사본 한정입니다. 원본 적용·배포·서비스 회복은 미실행입니다.")
        if prepared.get("persistence", {}).get("status") == "FAILED":
            st.warning("작업 기록 저장에 실패했습니다. 확보한 결과의 저장만 재시도할 수 있습니다.")
            if st.button("작업 결과 저장만 재시도", key="retry_change_save"):
                st.session_state.report_agent_change = persist_change_result(prepared, db_location())
                st.rerun()

    with st.expander("내부 검토 · 사건 기억"):
        try:
            card_id = prepared["job"]["result_run_id"] if prepared and prepared.get("persistence", {}).get("status") in {"SAVED", "ALREADY_SAVED"} else result["run_id"]
            with IncidentStore() as store:
                card = store.get_card(result["project_id"], card_id)
            st.write("검토 상태:", card["review"]["status"])
            st.write(card["finding"])
            reviewer = st.text_input("검토자", value="local-maintainer", key="memory_reviewer")
            action = st.selectbox("카드 검토", ["선택", "승인", "반려"], key="memory_review_action")
            if st.button("검토 반영", key="review_memory_card") and action != "선택":
                with IncidentStore() as store:
                    store.review_card(result["project_id"], card_id, {"승인": "approve", "반려": "reject"}[action], reviewer=reviewer)
                st.rerun()
            st.caption("검토된 카드는 다음 사건의 단서로 검색됩니다. 승인 상태와 원인·수정 검증 상태는 별개입니다.")
            manual = export_manual(result["project_id"], db_path=db_location())
            st.download_button("검토된 프로젝트 매뉴얼 내려받기", manual["markdown"],
                               file_name="incident-manual.md", mime="text/markdown", key="download_incident_manual")
            st.write("매뉴얼의 검토 카드:", manual["card_count"], "개")
        except (ValueError, OSError, sqlite3.Error):
            st.warning("사건 카드를 조회하지 못했습니다. 현재 결과의 저장 상태를 확인해 주세요.")
    with st.expander("개발자 조사 기록"):
        st.write(f"사건: {result['incident_id']} · 갱신: {result['revision']} · 상관: {result['correlation']}")
        st.write(f"실행: {result['run_id']} · SQLite 저장: {result.get('persistence', {}).get('status', 'NOT_REQUESTED')}")
        st.write("과거 조사 단서 검색과 현재 재확인:")
        st.json(result.get("memory_search", {}))
        st.json(result["version_provenance"])
        st.json(result["log_scope"])
        st.json(status)
        st.json(result.get("observability", result.get("log_scope", {}).get("observability", {})))
        st.write(f"이 접수·조사의 모델 호출: {result['model_calls']}회 · 사용량: {result['usage']}")
        if prepared:
            st.write("수정 제안의 모델 호출:", prepared["job"]["model"]["actual_calls"], "회 · 사용량:", prepared["job"]["model"].get("usage", {}))
        st.dataframe(result["steps"], hide_index=True, use_container_width=True)
        st.json(result["observations"])
        st.json(result["report_clues"])
        st.json(result["evidence"])
        st.json(result["service_calls"])
        st.write("담당자가 확인할 자료:", result["missing_information"])
        st.write("다음 조치:", result["next_steps"])
        for note in result["notes"]:
            st.write(note)
        if result["history"]:
            st.json(result["history"])
    st.caption("작업 후보·검토 승인·COMPLETED는 원인 확정·수정 검증이 아닙니다. 사건 기억과 후보 검증은 현재 근거·원본 적용 상태와 별도로 기록합니다.")

with st.sidebar.expander("저장된 사건 이어보기"):
    try:
        project_id = result["project_id"] if result else "tracebridge-seed-signup" if seed else configured_profile.project_id if custom_project else "agolive"
        with IncidentStore() as store:
            incidents = store.list_incidents(project_id)
        if incidents:
            rows = {item["incident_id"]: item for item in incidents}
            selected_incident = st.selectbox("저장된 사건", list(rows), format_func=lambda id_: rows[id_]["updated_at"] + " · " + id_[:8], key="resume_report_incident")
            if st.button("이 사건 불러오기", key="resume_report"):
                with IncidentStore() as store:
                    resumed = store.resume_result(project_id, selected_incident)
                    changes = store.list_changes(project_id, selected_incident)
                resumed["persistence"] = {"status": "ALREADY_SAVED", "run_id": resumed["run_id"]}
                st.session_state.report_agent_result = resumed
                st.session_state.report_agent_change = {"job": changes[0], "persistence": {"status": "ALREADY_SAVED"}} if changes else None
                st.rerun()
        else:
            st.write("저장된 사건이 없습니다.")
    except (ValueError, OSError, sqlite3.Error):
        st.warning("저장된 사건을 조회하지 못했습니다.")
