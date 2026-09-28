"""Run with: .venv\\Scripts\\python -m streamlit run app.py"""

from __future__ import annotations

import streamlit as st

from tracebridge.agent import has_api_key, run_live, run_offline
from tracebridge.fixtures import get_case, list_cases
from tracebridge.repro import demonstrate_red_green


st.set_page_config(page_title="TraceBridge", page_icon="🔎", layout="wide")
st.title("🔎 TraceBridge")
st.caption("제보가 있거나 없는 합성 API 사건의 증거를 조사하는 데모")

options = {f"{title} · {trace_id}": trace_id for trace_id, title in list_cases()}
options["증거 부족 · unknown"] = "unknown"
selected = st.selectbox("데모 사례", list(options))
trace_id = options[selected]
case = get_case(trace_id)
if st.session_state.get("selected_trace_id") != trace_id:
    st.session_state.selected_trace_id = trace_id
    st.session_state.report_claim = case["claim"] if case else "회원가입 API에서 500이 납니다"
    st.session_state.analysis = None
    st.session_state.repro = None
input_type = st.radio("조사 시작점", ["오류 제보", "제보 없음 · 관측 사건 조사"], horizontal=True)
claim = st.text_input("오류 제보", key="report_claim") if input_type == "오류 제보" else None
if st.session_state.get("selected_input_type") != input_type or (
    st.session_state.get("analysis") and st.session_state.analysis["verdict"]["claim"] != claim
):
    st.session_state.selected_input_type = input_type
    st.session_state.analysis = None
    st.session_state.repro = None
st.caption("합성 데모 전용입니다. 실제 로그, 개인정보, 비밀정보를 입력하지 마세요. NVIDIA NIM 모드에서는 입력한 제보가 외부 API로 전송됩니다.")
st.text_input("Trace ID", value=trace_id, disabled=True)

live_ready = has_api_key()
mode = st.radio(
    "실행 방식",
    ["NVIDIA NIM 실제 호출", "오프라인 자료 검증"],
    index=0 if live_ready else 1,
    horizontal=True,
)
if not live_ready:
    st.info(".env에 NVIDIA_API_KEY를 넣으면 실제 모델 호출을 선택할 수 있습니다.")

if st.button("증거 조사 시작", type="primary"):
    if mode == "NVIDIA NIM 실제 호출":
        try:
            st.session_state.analysis = run_live(trace_id, claim)
        except Exception as exc:
            st.error(f"NVIDIA API 실행 실패: {type(exc).__name__}: {exc}")
            st.stop()
    else:
        st.session_state.analysis = run_offline(trace_id, claim)
    st.session_state.repro = None

analysis = st.session_state.get("analysis")
if analysis:
    verdict = analysis["verdict"]
    st.subheader("판정")
    c1, c2, c3 = st.columns(3)
    c1.metric("제보 항목 판정", verdict["claim_status"])
    c2.metric("원인 근거", verdict["finding_status"])
    repro = st.session_state.get("repro")
    if repro and repro["verified_in_demo"]:
        repro_status = "CANDIDATE_FIX_PASSED"
    elif repro and repro["before"]["exit_code"] == 1:
        repro_status = "REPRODUCED"
    elif repro:
        repro_status = "NOT_REPRODUCED"
    else:
        repro_status = verdict["repro_status"]
    c3.metric("재현", repro_status)
    st.write(f"**분류:** {verdict['diagnosis_type']}")
    st.write(f"**다음 조치:** {verdict['next_action']}")
    st.write(f"**실행:** {analysis['mode']} · {analysis['elapsed_ms']} ms · {analysis.get('usage', {})}")

    if verdict["claim_items"]:
        st.subheader("제보 항목별 대조")
        st.table(verdict["claim_items"])
        st.caption("현재는 명시된 HTTP 상태·메서드·경로·'○○ API' 작업명만 추출합니다. 나머지 자연어는 확인된 것으로 취급하지 않습니다.")

    st.subheader("증거")
    for item in verdict["evidence"]:
        st.markdown(f"- `{item['source']}` — {item['fact']}")

    with st.expander("에이전트 도구 호출 기록", expanded=True):
        for step in analysis["steps"]:
            st.markdown(f"**{step['tool']}** — {step['reason']}")
            st.json(step["result"])
    if analysis["agent_message"]:
        st.subheader("검증된 요약")
        st.write(analysis["agent_message"])

    if verdict["repro_eligible"]:
        if st.button("재현 테스트 생성·실행"):
            st.session_state.repro = demonstrate_red_green(verdict["diagnosis_type"])
            st.rerun()
        if repro:
            st.subheader("수정 전후 검증 · 격리된 샘플 환경")
            st.code(repro["test_source"], language="python")
            left, right = st.columns(2)
            left.write(f"**수정 전:** {'실패' if repro['before']['exit_code'] else '통과'}")
            left.code(repro["before"]["output"])
            right.write(f"**후보 수정 후:** {'통과' if repro['after_candidate_fix']['exit_code'] == 0 else '실패'}")
            right.code(repro["after_candidate_fix"]["output"])
            st.caption("이 결과는 샘플 코드·DB에서만 검증했습니다. 실제 서비스가 수정됐다는 뜻은 아닙니다.")
