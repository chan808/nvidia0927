"""Owner review queue for saved incident cards; a review never asserts current evidence."""

import httpx
import streamlit as st

from .local_runner import GatewayError


OUTCOMES = {
    None: "검토 결과 선택",
    "GUIDANCE_CONFIRMED": "사용 안내가 맞음 (담당자 확인)",
    "DEFECT_CONFIRMED_BY_OWNER": "제품 결함 확인 (담당자 확인)",
    "RECOVERY_VERIFIED": "등록된 검사로 서비스 회복 확인",
    "INCONCLUSIVE": "근거 부족·재사용 제외",
}


def render_review_queue(api, project_id: str):
    with st.expander("사건 지식 검토"):
        st.caption("조사 기록은 검토 전에는 재사용되지 않습니다. 승인은 과거 단서의 검색을 허용하며 현재 원인 확인은 별개입니다.")
        loaded = "knowledge-loaded:" + project_id
        if st.button("사건 카드 불러오기", key="knowledge-load:" + project_id):
            st.session_state[loaded] = True
        if not st.session_state.get(loaded):
            st.caption("검토할 때 대기열을 불러옵니다.")
            return
        status = st.selectbox("카드 상태", ["PENDING", "APPROVED", "EDITED", "REJECTED"],
            format_func={"PENDING": "검토 대기", "APPROVED": "승인", "EDITED": "수정 승인", "REJECTED": "재사용 제외"}.get,
            key="knowledge-status:" + project_id)
        try:
            data = api.request("GET", f"/v1/memory/{project_id}/cards?status={status}&limit=20")
        except (GatewayError, OSError, httpx.HTTPError, ValueError) as exc:
            st.error("사건 카드를 불러오지 못했습니다: " + str(exc))
            return
        cards = data["cards"]
        if not cards:
            st.info("이 상태의 사건 카드가 없습니다.")
            return
        by_id = {card["card_id"]: card for card in cards}
        chosen = st.selectbox("검토할 사건", list(by_id),
            format_func=lambda card_id: (by_id[card_id]["symptom"] or "증상 요약 없음")[:75] + " · " + card_id[:8],
            key="knowledge-card:" + project_id + ":" + status)
        card = by_id[chosen]
        st.write("**저장된 판단:** " + (card["finding"] or "확인된 판단 없음"))
        st.write("**다음 조치:** " + (card["next_action"] or "추가 확인 필요"))
        st.caption("범위: " + str(card["scope"] or "등록 정보 없음") + " · 카드 종류: " + card["card_kind"])
        st.table([{"근거 단계": key, "기록 상태": value or "미확인"} for key, value in card["verification"].items()])
        with st.form("knowledge-review:" + project_id + ":" + chosen + ":" + str(card["review"]["revision"])):
            outcome = st.selectbox("담당자 검토 결과", list(OUTCOMES), format_func=OUTCOMES.get)
            note = st.text_area("검토 근거·주의사항", max_chars=300, height=100)
            checked = st.checkbox("현재 자료와 적용 범위를 확인했습니다")
            approve = st.form_submit_button("검색 재사용 승인")
            reject = st.form_submit_button("재사용 제외")
        if not (approve or reject):
            return
        if not checked or not note.strip() or approve and outcome in {None, "INCONCLUSIVE"}:
            st.warning("자료 확인, 검토 결과와 근거 메모를 입력해 주세요.")
            return
        if approve and outcome == "RECOVERY_VERIFIED" and card["verification"].get("service_recovery") != "RECORDED_VERIFIED":
            st.warning("등록된 서비스 회복 검사 기록이 있어야 회복 확인으로 승인할 수 있습니다.")
            return
        payload = {"action": "approve" if approve else "reject", "outcome": outcome if approve else "INCONCLUSIVE",
            "note": note.strip(), "expected_revision": card["review"]["revision"]}
        try:
            api.request("POST", f"/v1/memory/{project_id}/{chosen}/review", payload)
        except (GatewayError, OSError, httpx.HTTPError, ValueError) as exc:
            st.error("검토를 저장하지 못했습니다. 카드를 새로 불러와 확인해 주세요: " + str(exc))
            return
        st.rerun()
