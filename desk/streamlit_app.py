"""Order Alert Desk — entry point.

Team-facing view of the issues raised through the Order Alert form: who is handling what,
the full report, and an order-monitor style analysis of the reported orders from kdb+.
"""
from __future__ import annotations

import streamlit as st

from app_pages import common as C
from core.connections import pykx_available
from theme import styles as T

st.set_page_config(page_title="Order Alert Desk", page_icon="◆", layout="wide",
                   initial_sidebar_state="expanded")

C.init_state()
T.inject()

page = st.navigation([
    st.Page(C.BOARD_PAGE, title="Issues", icon=":material/inbox:", default=True),
    st.Page(C.ISSUE_PAGE, title="Issue", url_path="issue", visibility="hidden"),
    st.Page(C.ANALYSIS_PAGE, title="Order analysis", url_path="analysis", visibility="hidden"),
    st.Page("app_pages/settings.py", title="Settings", icon=":material/settings:", url_path="settings"),
])

BRAND_SVG = (
    '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" '
    'xmlns="http://www.w3.org/2000/svg" style="vertical-align:-4px">'
    '<rect x="2" y="13" width="4" height="8" rx="1" fill="var(--accent)"/>'
    '<rect x="9" y="8" width="4" height="13" rx="1" fill="var(--down)"/>'
    '<rect x="16" y="4" width="4" height="17" rx="1" fill="var(--muted)"/></svg>'
)

with st.sidebar:
    st.markdown(f"<div style='font-weight:700;font-size:15.5px;margin:.2rem 0 1.1rem'>"
                f"{BRAND_SVG}&nbsp; Order Alert Desk</div>", unsafe_allow_html=True)

    team = list(C.settings().team)
    if C.me() and C.me() not in team:
        team.append(C.me())
    picked = st.selectbox("You are", team, index=team.index(C.me()) if C.me() in team else None,
                          placeholder="Pick or type your name", accept_new_options=True)
    if (picked or "") != C.me():
        C.set_me(picked or "")
        st.rerun()

    st.divider()
    T.eyebrow("Order data source")
    options = ["demo", "kdb"]
    src = st.segmented_control("Order data source", options, default=st.session_state["source"],
                               format_func=lambda s: "Demo data" if s == "demo" else "kdb+",
                               label_visibility="collapsed", required=True,
                               disabled=not pykx_available())
    if src and src != st.session_state["source"]:
        st.session_state["source"] = src
        st.rerun()
    if not pykx_available():
        st.caption("pykx is not installed, so order analysis uses demo data.")
    if C.settings().is_demo:
        st.markdown(T.badge("Demo issues", "warn"), unsafe_allow_html=True)
        st.caption("No config.toml found: showing the bundled demo issues.")

    T.vspace(.6)
    cur = st.session_state.get("theme", "dark")
    if st.button(f"Switch to {'light' if cur == 'dark' else 'dark'} mode", width="stretch"):
        T.toggle_theme()

page.run()
