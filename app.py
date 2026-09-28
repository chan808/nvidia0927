"""Local TraceBridge service: report, investigation, memory and prepared changes."""

import streamlit as st

page = st.navigation([
    st.Page("pages/2_Report_Agent.py", title="제보 · 조사 · 수정안", icon="🔎", default=True),
    st.Page("pages/1_Agolive_Investigation.py", title="Agolive 코드 조사"),
    st.Page("pages/0_Fixture_Demo.py", title="기존 합성 자료 데모"),
])
page.run()
