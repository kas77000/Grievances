"""Issue board: what is open, how urgent, and who has it."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from app_pages import common as C
from core import issues as I
from theme import styles as T

SEV_RANK = {s: i for i, s in enumerate(I.SEVERITIES)}
VIEWS = ["Open", "Mine", "Unassigned", "Resolved", "All"]


@st.fragment(run_every="30s")  # new tickets show up without anyone pressing refresh
def board():
    issues = C.issues()
    me = C.me()
    open_ = [i for i in issues if i.is_open]
    today = dt.datetime.now().astimezone().date()

    st.markdown(T.tiles([
        T.tile("Open", str(len(open_))),
        T.tile("Unassigned", str(sum(1 for i in open_ if not i.assignee)),
               tone="down" if any(not i.assignee for i in open_) else ""),
        T.tile("High / critical open", str(sum(1 for i in open_ if i.severity in ("high", "critical")))),
        T.tile("Mine", str(sum(1 for i in open_ if me and i.assignee == me)) if me else "—",
               "" if me else "pick your name in the sidebar"),
        T.tile("Reported today", str(sum(1 for i in issues if i.created_at.astimezone().date() == today))),
    ]), unsafe_allow_html=True)

    f1, f2, f3 = st.columns([2.2, 1.6, 1.6], vertical_alignment="bottom")
    view = f1.segmented_control("View", VIEWS, default="Open", key="board_view", required=True)
    sev = f2.multiselect("Severity", I.SEVERITIES, format_func=str.capitalize, key="board_sev",
                         placeholder="All severities")
    text = f3.text_input("Search", key="board_q", placeholder="Title, reporter, order id…")

    rows = issues
    if view == "Open":
        rows = open_
    elif view == "Mine":
        rows = [i for i in open_ if me and i.assignee == me]
    elif view == "Unassigned":
        rows = [i for i in open_ if not i.assignee]
    elif view == "Resolved":
        rows = [i for i in issues if not i.is_open]
    if sev:
        rows = [i for i in rows if i.severity in sev]
    if text.strip():
        q = text.strip().lower()
        rows = [i for i in rows if q in " ".join([
            i.title, i.reporter, i.assignee, i.id, i.data.get("raw", ""),
            " ".join(str(o.get("id_target") or o.get("oes_oid") or "") for o in i.orders)]).lower()]

    if not rows:
        T.empty_state("Nothing here", "No issues match this view." if issues else
                      "No issues reported yet. They appear here as soon as the watchdog picks them up.")
        return

    rows = sorted(rows, key=lambda i: (not i.is_open, SEV_RANK.get(i.severity, 9), -i.created_at.timestamp()))
    df = pd.DataFrame([{
        "Severity": i.severity.capitalize(),
        "Status": I.STATUS_LABEL.get(i.status, i.status),
        "Title": i.title,
        "Assignee": i.assignee or "— unassigned —",
        "Reporter": i.reporter,
        "Orders": len(i.orders),
        "Reported": i.created_at.astimezone().replace(tzinfo=None),
        "Last activity": C.ago(i.updated_at),
        "id": i.id,
    } for i in rows])

    event = st.dataframe(
        df, hide_index=True, width="stretch", on_select="rerun", selection_mode="single-row",
        key="board_table", column_order=[c for c in df.columns if c != "id"],
        column_config={
            "Title": st.column_config.TextColumn(width="large"),
            "Orders": st.column_config.NumberColumn(width="small"),
            "Reported": st.column_config.DatetimeColumn(format="YYYY-MM-DD HH:mm"),
        },
    )
    st.caption(f"{len(rows)} issue(s) · click a row to open it · refreshes every 30 s")
    if event.selection.rows:
        C.go(C.ISSUE_PAGE, id=df.iloc[event.selection.rows[0]]["id"])


T.eyebrow("Order Alert Desk")
st.markdown("## Issues")
board()
