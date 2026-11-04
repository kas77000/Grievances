"""Order analysis for one issue: pick one of its orders and inspect it from kdb+."""
from __future__ import annotations

import streamlit as st

from app_pages import common as C
from app_pages.order_detail import load_order, render_order
from core import issues as I
from core.orders import resolve
from theme import styles as T

issue = C.get_issue(st.query_params.get("id"))
if issue is None:
    st.warning("Issue not found.")
    if st.button("← All issues"):
        C.go(C.BOARD_PAGE)
    st.stop()

top = st.columns([6, 1.3, 1.3], vertical_alignment="bottom")
with top[0]:
    T.eyebrow(f"Order analysis · {issue.severity} · {I.STATUS_LABEL.get(issue.status, issue.status)}"
              + (f" · {issue.assignee}" if issue.assignee else ""))
    st.markdown(f"## {T.esc(issue.title)}", unsafe_allow_html=True)
with top[1]:
    if st.button("Reload data", width="stretch", icon=":material/refresh:"):
        load_order.clear()
        st.session_state.pop("resolved", None)
with top[2]:
    if st.button("← Issue", width="stretch"):
        C.go(C.ISSUE_PAGE, id=issue.id)

if not issue.orders:
    T.empty_state("No orders to analyse", "No order ids were recognised in the pasted data. "
                  "Add the platform's id column name to [data.columns] in config.toml.")
    st.stop()

# ---- which order --------------------------------------------------------------
labels = [o.get("label") or str(i) for i, o in enumerate(issue.orders)]
if len(issue.orders) > 1:
    def describe(label: str) -> str:
        o = issue.orders[labels.index(label)]
        return " · ".join(str(x) for x in (o.get("sym"), o.get("id_target") or o.get("oes_oid")) if x)
    qp_order = st.query_params.get("order")
    choice = st.segmented_control("Order", labels, default=qp_order if qp_order in labels else labels[0],
                                  format_func=describe, required=True, key="analysis_order")
    ref = issue.orders[labels.index(choice)]
else:
    ref = issue.orders[0]

provider, source, tone = C.provider_for(ref)
st.markdown(T.badge(source, tone) + (f"&nbsp; <span style='color:var(--muted);font-size:12.5px'>"
            f"watchdog: {T.esc(ref['headline'])}</span>" if ref.get("headline") else ""),
            unsafe_allow_html=True)
if provider is None:
    st.error(f"Cannot load order data: {source}. Check the kdb+ connections on the Settings page.")
    st.stop()

# Resolve the pasted reference to full order keys once per source, not on every rerun.
cache = st.session_state.setdefault("resolved", {})
key = (source, issue.id, ref.get("label"))
if key not in cache:
    try:
        cache[key] = resolve(provider, ref)
    except Exception as e:  # noqa: BLE001
        st.error(f"Lookup failed on {source}: {type(e).__name__}: {e}")
        st.stop()
matches = cache[key]

if not matches:
    st.warning(f"Order {ref.get('id_target') or ref.get('oes_oid')} ({ref.get('date')}) was not found on {source}.")
    if st.session_state.get("source") == "demo":
        st.caption("Demo data only contains id_target 100731 (7203.T), 100742 (0700.HK) and 100118 (AAPL). "
                   "Switch the sidebar to kdb+ for real orders.")
    st.stop()

m = matches[0]
if len(matches) > 1:
    srv = st.selectbox("This id_target exists on several servers", [x["id_server"] for x in matches],
                       format_func=lambda s: f"server {s}")
    m = next(x for x in matches if x["id_server"] == srv)

st.divider()
render_order(provider, source, m["date"], m["id_server"], m["id_target"], m["sym"])
