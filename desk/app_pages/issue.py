"""Issue page: the report as the user sent it, who is handling it, and the activity log."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app_pages import common as C
from core import issues as I
from core.settings import wd
from theme import styles as T

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}
EVENT_VERB = {"assign": "assigned", "status": "set status", "comment": "commented"}

issue = C.get_issue(st.query_params.get("id"))
if issue is None:
    st.warning("Issue not found. It may be older than the board's history window or the link is wrong.")
    if st.button("← All issues"):
        C.go(C.BOARD_PAGE)
    st.stop()

# ---- header ----------------------------------------------------------------
top = st.columns([6, 1.3], vertical_alignment="bottom")
with top[0]:
    T.eyebrow(f"Issue · ref {issue.id}")
    st.markdown(f"## {T.esc(issue.title)}", unsafe_allow_html=True)
    st.markdown(
        T.badge(issue.severity.upper(), I.SEVERITY_TONE.get(issue.severity, "info")) + " " +
        T.badge(I.STATUS_LABEL.get(issue.status, issue.status), I.STATUS_TONE.get(issue.status, "info")) + " " +
        T.badge(f"Handled by {issue.assignee}" if issue.assignee else "Unassigned",
                "accent" if issue.assignee else "down") +
        f"&nbsp; <span style='color:var(--muted);font-size:12.5px'>reported by <b>{T.esc(issue.reporter)}</b> · "
        f"{C.local(issue.created_at)} ({C.ago(issue.created_at)})</span>",
        unsafe_allow_html=True)
with top[1]:
    if st.button("← All issues", width="stretch"):
        C.go(C.BOARD_PAGE)

actions = st.columns([1.6, 1.4, 4])
with actions[0]:
    if st.button(f"Analyze orders ({len(issue.orders)})", type="primary", width="stretch",
                 icon=":material/query_stats:", disabled=not issue.orders,
                 help=None if issue.orders else "No order ids were found in the pasted data"):
        C.go(C.ANALYSIS_PAGE, id=issue.id)
with actions[1]:
    mine = C.me() and issue.assignee == C.me()
    if st.button("Take it", width="stretch", icon=":material/front_hand:", disabled=bool(mine) or not issue.is_open):
        if C.require_me():
            C.record(issue, "assign", C.me())
            if issue.status == "new":
                C.record(issue, "status", "investigating")
            st.rerun()

st.divider()
main, side = st.columns([1.75, 1], gap="large")

# ---- the report ----------------------------------------------------------------
with main:
    T.eyebrow("What the user reported")
    st.markdown(f"<div class='amx-panel' style='white-space:pre-wrap'>"
                f"{T.esc(issue.description) if issue.description.strip() else '<i>No description.</i>'}</div>",
                unsafe_allow_html=True)

    T.eyebrow(f"Orders identified ({len(issue.orders)})")
    if issue.orders:
        st.dataframe(pd.DataFrame([{
            "Order": o.get("label"), "Date": o.get("date"), "Region": o.get("region"),
            "id_target": o.get("id_target"), "id_server": o.get("id_server"),
            "oes_oid": o.get("oes_oid"), "sym": o.get("sym"), "Watchdog summary": o.get("headline"),
        } for o in issue.orders]).dropna(axis=1, how="all"), hide_index=True, width="stretch")
    else:
        st.info("No order ids were recognised in the pasted data.")
    for w in issue.warnings:
        st.caption(f"⚠ {w}")

    data = issue.data or {}
    if data.get("raw", "").strip():
        header, rows = wd.parse_rows(data["raw"], data.get("separator", "\t"), bool(data.get("has_header", True)))
        with st.expander(f"Pasted data ({len(rows)} rows)", expanded=not issue.orders):
            st.dataframe(pd.DataFrame(rows, columns=header), hide_index=True, width="stretch")

    if issue.attachments:
        T.eyebrow(f"Attachments ({len(issue.attachments)})")
        for f in issue.attachments:
            if f.suffix.lower() in IMAGE_EXT:
                st.image(str(f), caption=f.name)
            else:
                st.download_button(f"⬇ {f.name}", f.read_bytes(), file_name=f.name, key=f"att_{f.name}")

    report = issue.folder / "report.html"
    if report.exists():
        st.download_button("Download watchdog report (HTML)", report.read_bytes(), file_name=f"{issue.id}.html",
                           mime="text/html", icon=":material/description:")
    st.caption(f"Folder: `{issue.folder}`")

# ---- handling + activity ------------------------------------------------------
with side:
    T.eyebrow("Handling")
    with st.form("handling", border=True, clear_on_submit=True):
        team = list(C.settings().team)
        for n in (issue.assignee, C.me()):
            if n and n not in team:
                team.append(n)
        assignee = st.selectbox("Assignee", ["", *team], index=(["", *team]).index(issue.assignee),
                                format_func=lambda n: n or "— unassigned —", accept_new_options=True)
        status = st.selectbox("Status", I.STATUSES, index=I.STATUSES.index(issue.status),
                              format_func=I.STATUS_LABEL.get)
        note = st.text_area("Note", placeholder="What you found, what you did, who you are waiting on…",
                            height=90)
        if st.form_submit_button("Save", type="primary", width="stretch") and C.require_me():
            changed = False
            if (assignee or "") != issue.assignee:
                C.record(issue, "assign", assignee or "")
                changed = True
            if status != issue.status:
                C.record(issue, "status", status, note.strip())
                changed = True
            elif note.strip():
                C.record(issue, "comment", text=note.strip())
                changed = True
            if changed:
                st.rerun()

    T.eyebrow("Activity")
    for e in reversed(issue.events):
        what = EVENT_VERB.get(e.kind, e.kind)
        if e.kind == "assign":
            what = f"assigned it to <b>{T.esc(e.value)}</b>" if e.value else "unassigned it"
        elif e.kind == "status":
            what = f"set status to <b>{T.esc(I.STATUS_LABEL.get(e.value, e.value))}</b>"
        body = f'<div class="detail" style="white-space:pre-wrap">{T.esc(e.text)}</div>' if e.text else ""
        st.markdown(f'<div class="amx-sit"><div class="body"><div class="name" style="font-weight:500">'
                    f'<b>{T.esc(e.user or "?")}</b> {what}</div>{body}'
                    f'<div class="meta">{C.local(e.ts)} · {C.ago(e.ts)}</div></div></div>',
                    unsafe_allow_html=True)
    st.markdown(f'<div class="amx-sit"><div class="body"><div class="name" style="font-weight:500">'
                f'<b>{T.esc(issue.reporter)}</b> reported the issue</div>'
                f'<div class="meta">{C.local(issue.created_at)} · {C.ago(issue.created_at)}</div></div></div>',
                unsafe_allow_html=True)
