"""Run with: python -m streamlit run app.py, then open this page from the sidebar."""

from __future__ import annotations

import hashlib
import streamlit as st

from tracebridge.project_gpt import agolive_openai_settings
from tracebridge.project_investigation import investigate_agolive_report
from tracebridge.project_sources import agolive_repo_path, validate_agolive_repo


st.set_page_config(page_title="Agolive 제보 조사", page_icon="🔎", layout="wide")
st.title("Agolive 제보 조사")
st.caption("로컬 Agolive 코드와 선택한 로그를 읽기 전용으로 조사합니다. 수정·테스트·운영 변경은 수행하지 않습니다.")

try:
    repo = validate_agolive_repo(agolive_repo_path())
except (OSError, ValueError) as exc:
    st.error(f"Agolive 저장소를 찾지 못했습니다: {exc}. TRACEBRIDGE_AGOLIVE_REPO를 설정하세요.")
    st.stop()

key, model, key_source = agolive_openai_settings(repo)
st.write(f"**프로젝트:** `{repo}`")
st.write(f"**GPT:** {model} · 설정 출처: {key_source}" if key else "**GPT:** 키 없음 · 코드·로그 검색만 가능")
st.caption("GPT 분석을 켜면 제보와 선별·비식별화한 코드·로그 줄이 OpenAI API로 전송됩니다. 비식별화는 완전하지 않을 수 있으므로 원본 비밀정보를 입력하지 마세요.")

report = st.text_area("오류 제보", height=140, placeholder="예: 방 입장 시 ROOM_FULL이 뜹니다. requestId=abcd1234, 오늘 14시경")
uploaded = st.file_uploader("오류 로그 파일 (선택, .log/.txt/.jsonl)", type=["log", "txt", "jsonl"])
log_text = st.text_area("또는 로그 일부 붙여넣기 (선택)", height=120)
if uploaded is not None:
    if uploaded.size > 300_000:
        st.error("로그 파일은 300 KB 이하여야 합니다.")
        st.stop()
    log_text = uploaded.getvalue().decode("utf-8", errors="replace") + "\n" + log_text

left, right = st.columns(2)
with left:
    use_gpt = st.checkbox(
        "선별·비식별화한 제보·코드·로그를 OpenAI API로 전송해 GPT 가설을 생성합니다",
        value=False,
        disabled=not bool(key),
    )
with right:
    include_docker = st.checkbox("로컬 Docker Compose 오류 로그 조회", value=False)
since_minutes = st.number_input("Docker 로그 조회 범위 (분)", min_value=1, max_value=120, value=30, disabled=not include_docker)
signature = hashlib.sha256(
    f"{report}\0{log_text}\0{use_gpt}\0{include_docker}\0{since_minutes}".encode("utf-8")
).hexdigest()
if st.session_state.get("agolive_input_signature") != signature:
    st.session_state.agolive_investigation = None

if st.button("제보 조사 시작", type="primary"):
    st.session_state.agolive_investigation = None
    try:
        with st.spinner("Agolive 코드와 선택한 로그를 조사 중입니다..."):
            st.session_state.agolive_investigation = investigate_agolive_report(
                report,
                repo=repo,
                provided_logs=log_text,
                include_docker_logs=include_docker,
                since_minutes=int(since_minutes),
                use_gpt=use_gpt,
            )
            st.session_state.agolive_input_signature = signature
    except Exception as exc:
        st.error(f"조사 실패: {type(exc).__name__}: {exc}")

result = st.session_state.get("agolive_investigation")
if result:
    st.subheader("조사 결과")
    dirty_text = "있음" if result["source_tree_dirty"] is True else (
        "없음" if result["source_tree_dirty"] is False else "확인 불가"
    )
    st.write(f"**로컬 코드 버전:** `{result['repository_revision']}` · **소스 변경:** {dirty_text} · **실제 배포 버전:** 확인 안 됨")
    st.write(f"**분석:** {'GPT ' + result['gpt_model'] if result['gpt_used'] else '코드·로그 검색만 수행'}")
    for note in result["notes"]:
        st.info(note)

    st.subheader("원인 후보")
    if not result["hypotheses"]:
        st.write("확인 가능한 원인 후보를 만들지 못했습니다.")
    for item in result["hypotheses"]:
        with st.expander(f"{item['status']} · {item['cause']}", expanded=True):
            st.write(item["explanation"])
            st.write(f"**지지 근거:** {', '.join(item['supporting_evidence_ids']) or '없음'}")
            st.write(f"**반대 근거:** {', '.join(item['contradicting_evidence_ids']) or '없음'}")
            st.write(f"**검증 방법:** {item['verification_step']}")
            st.write(f"**후보 조치 (미적용):** {item['possible_fix']}")

    st.subheader("근거")
    st.dataframe(result["evidence"], use_container_width=True, hide_index=True)
    if result["missing_information"]:
        st.subheader("추가로 필요한 자료")
        for item in result["missing_information"]:
            st.write(f"- {item}")
    st.subheader("다음 조치")
    for item in result["next_steps"]:
        st.write(f"- {item}")
    st.caption("원인 후보와 수정 제안은 재현 전까지 확정되지 않습니다. 대상 프로젝트 파일은 변경하지 않았습니다.")
