"""Natural text/photo intake and same-session replies to the common investigation path."""

from pathlib import Path

from dotenv import load_dotenv
import streamlit as st

from tracebridge.report_agent import follow_up_submission, investigate_submission, nvidia_settings
from tracebridge.report_contract import ReportContext
from tracebridge.incident_memory import persist_result
from tracebridge.project_sources import agolive_repo_path, validate_agolive_repo


st.set_page_config(page_title="제보 에이전트", page_icon="🔎", layout="wide")
load_dotenv(Path(__file__).resolve().parents[1] / ".env")
st.title("제보 에이전트")
st.caption("증상을 한 줄로 적거나 사진만 첨부해도 됩니다. 화면·동작·대략적인 시각을 단서로 조사합니다.")
try:
    repo = validate_agolive_repo(agolive_repo_path())
except (OSError, ValueError):
    st.error("조사할 프로젝트가 연결되지 않았습니다. 관리자에게 프로젝트 연결을 요청해 주세요.")
    st.stop()
key, _ = nvidia_settings()
text = st.text_area("증상 또는 요청 (사진이 있으면 생략 가능)", placeholder="예: 방에 들어가면 계속 튕겨요. 확인해줘.", max_chars=4000)
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
    environment_label = st.selectbox("어디에서 사용했나요?", ["모르겠어요", "개발 환경", "스테이징", "운영 서비스"])
environment = {"개발 환경": "dev", "스테이징": "staging", "운영 서비스": "prod"}.get(environment_label)
use_nvidia = st.checkbox("제보·사진과 선별된 코드·로그를 NVIDIA 분석 서비스에 전송", disabled=not bool(key))
if not key:
    st.info("현재는 로컬 관측 대조와 코드 검색이 가능합니다. 사진 해석은 NVIDIA 연결 설정이 필요합니다.")
include_docker = st.checkbox("연결된 로컬 컨테이너 로그 조회", value=False)
options = {"repo": repo, "use_nvidia": use_nvidia, "include_docker_logs": include_docker}
if st.button("조사 시작", type="primary"):
    st.session_state.report_agent_result = None
    try:
        context = ReportContext(environment=environment).with_answer(occurred)
        with st.spinner("제보와 현재 관측을 대조하고 있습니다..."):
            new_result = investigate_submission(text, image=image, context=context, **options)
        st.session_state.report_agent_result = new_result
    except (ValueError, RuntimeError) as exc:
        st.error(str(exc))
    except Exception:
        st.error("조사를 완료하지 못했습니다. 연결 상태를 확인한 뒤 다시 시도해 주세요.")

result = st.session_state.get("report_agent_result")
if result:
    route_labels = {"GUIDANCE": "입력·동작 안내", "REQUEST_CONTEXT": "추가 정보 대기", "INVESTIGATE": "추가 조사", "WORK_CANDIDATE": "담당자 작업 검토 후보"}
    run_labels = {"COMPLETED": "이번 판정 완료", "WAITING_CONTEXT": "정보 대기", "PARTIAL_FAILURE": "일부 조회 실패", "TIMED_OUT": "시간 초과", "BUDGET_EXHAUSTED": "호출 한도 도달"}
    st.subheader(route_labels[result["route"]])
    st.write(result["summary"])
    st.info(result["route_reason"])
    st.write("조사 실행: " + run_labels[result["run_status"]])
    if result.get("persistence", {}).get("status") == "FAILED":
        st.warning("조사 결과는 확보했지만 사건 기록을 저장하지 못했습니다. 아래 버튼으로 현재 결과의 저장만 재시도할 수 있습니다.")
        if st.button("이 결과 저장만 재시도", key="retry_incident_save"):
            st.session_state.report_agent_result = persist_result(result)
            st.rerun()
    for question in result["questions"]:
        st.write(f"추가 확인: {question}")
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
        submitted = st.form_submit_button("같은 사건에 답변 반영")
    if submitted:
        try:
            with st.spinner("답변을 반영해 같은 사건의 현재 관측을 다시 확인합니다..."):
                updated = follow_up_submission(result, answer, **options)
            st.session_state.report_agent_result = updated
            st.rerun()
        except (ValueError, RuntimeError) as exc:
            st.error(str(exc))
        except Exception:
            st.error("답변을 반영하지 못했습니다. 이미 확보한 조사 결과는 유지됩니다.")
    with st.expander("개발자 조사 기록"):
        st.write(f"사건: {result['incident_id']} · 갱신: {result['revision']} · 상관: {result['correlation']}")
        st.write(f"실행: {result['run_id']} · SQLite 저장: {result.get('persistence', {}).get('status', 'NOT_REQUESTED')}")
        st.write("과거 조사 단서 검색과 현재 재확인:")
        st.json(result.get("memory_search", {}))
        st.json(result["version_provenance"])
        st.json(result["log_scope"])
        st.write(f"모델 호출: {result['model_calls']}회 · 사용량: {result['usage']}")
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
    st.caption("작업 후보·검토 승인·COMPLETED는 원인 확정·수정 검증이 아닙니다. SQLite에는 최소 기록을 남기며 조회·카드 검토·재시작 후 답변은 내부용 CLI에서 가능합니다.")
