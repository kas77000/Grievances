"""Shared state, data access, navigation and formatting for Desk pages."""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

from core import issues as I
from core.connections import pykx_available
from core.orders import kdb_connection_for
from core.queries import DataProvider, KdbProvider, MockProvider
from core.settings import DeskSettings, load_settings
from core.tags import load_tag_dictionary

BOARD_PAGE = "app_pages/board.py"
ISSUE_PAGE = "app_pages/issue.py"
ANALYSIS_PAGE = "app_pages/analysis.py"


# ---- settings & issues ----------------------------------------------------
@st.cache_resource(ttl=60)
def settings() -> DeskSettings:
    s = load_settings()
    if s.is_demo and not s.watchdog.processed.exists():
        from demo.seed_demo import main as seed_demo  # fresh checkout: give the demo something to show
        seed_demo()
    return s


@st.cache_data(ttl=15, show_spinner=False)
def _issues(root: str, days: int) -> list[I.Issue]:
    return I.list_issues(Path(root), days)


def issues() -> list[I.Issue]:
    s = settings()
    return _issues(str(s.watchdog.processed), s.history_days)


def get_issue(issue_id: Optional[str]) -> Optional[I.Issue]:
    """Always read fresh: this is the page where people act on the issue."""
    return I.find_issue(settings().watchdog.processed, issue_id or "")


def record(issue: I.Issue, kind: str, value: str = "", text: str = "") -> None:
    I.append_event(issue.folder, me(), kind, value, text)
    _issues.clear()


# ---- new-issue alerts -----------------------------------------------------
_ALERT_JS = """<script>/* %(nonce)s */
document.title = %(title)s;
if (%(new)d > 0) {
  window.__orderAlertBeeps = (window.__orderAlertBeeps || 0) + %(new)d;
  if (%(sound)s) {
    try {  // two-tone chime; browsers allow it once the user has clicked anything on the page
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      [[660, 0], [880, 0.18]].forEach(([f, t]) => {
        const o = ctx.createOscillator(), g = ctx.createGain(), t0 = ctx.currentTime + t;
        o.frequency.value = f;
        g.gain.setValueAtTime(0.0001, t0);
        g.gain.exponentialRampToValueAtTime(0.25, t0 + 0.02);
        g.gain.exponentialRampToValueAtTime(0.0001, t0 + 0.16);
        o.connect(g).connect(ctx.destination);
        o.start(t0); o.stop(t0 + 0.17);
      });
    } catch (e) {}
  }
}
</script>"""


def watch_for_new_issues():
    """Toast + chime for issues that arrived since this browser last looked, and keep the
    unassigned count in the tab title so it shows even when the tab is in the background."""
    current = issues()
    ss = st.session_state
    known = ss.get("known_issue_ids")
    new = [] if known is None else [i for i in current if i.id not in known]  # first load: no alert flood
    ss["known_issue_ids"] = {i.id for i in current}
    for i in new[:3]:
        st.toast(f"**New {i.severity} issue** · {i.title} — {i.reporter}", icon=":material/notification_important:")
    unassigned = sum(1 for i in current if i.is_open and not i.assignee)
    st.html(_ALERT_JS % {
        "nonce": time.time_ns(),  # identical HTML would not re-run
        "title": json.dumps(f"({unassigned}) Order Alert Desk" if unassigned else "Order Alert Desk"),
        "new": len(new),
        "sound": "true" if ss.get("alert_sound", True) else "false",
    }, unsafe_allow_javascript=True)


# ---- session --------------------------------------------------------------
def init_state():
    ss = st.session_state
    qp = st.query_params
    ss.setdefault("theme", qp.get("theme") if qp.get("theme") in ("light", "dark") else "dark")
    ss.setdefault("me", qp.get("me", ""))
    ss.setdefault("source", "demo" if settings().is_demo or not pykx_available() else "kdb")
    ss.setdefault("tag_csv_path", "")
    ss.setdefault("providers", {})


def me() -> str:
    return st.session_state.get("me", "").strip()


def set_me(name: str):
    st.session_state["me"] = (name or "").strip()
    if st.session_state["me"]:
        st.query_params["me"] = st.session_state["me"]  # survives reloads and bookmarks
    elif "me" in st.query_params:
        del st.query_params["me"]


def require_me() -> bool:
    if me():
        return True
    st.warning("Pick your name in the sidebar first, so the team knows who did this.")
    return False


def go(page: str, **params):
    """switch_page drops query params, so carry identity and theme along."""
    keep = {k: st.query_params[k] for k in ("me", "theme") if k in st.query_params}
    st.switch_page(page, query_params={**keep, **{k: str(v) for k, v in params.items()}})


# ---- order data -----------------------------------------------------------
def provider_for(ref: dict) -> tuple[Optional[DataProvider], str, str]:
    """(provider, source label, badge tone) for one order reference."""
    ss = st.session_state
    if ss.get("source") == "demo":
        p = ss["providers"].setdefault("__demo__", MockProvider())
        return p, "Demo data", "warn"
    if not pykx_available():
        return None, "pykx not installed", "down"
    conn = kdb_connection_for(settings().watchdog, ref)
    if conn is None:
        return None, "No kdb+ connection configured", "down"
    p = ss["providers"].setdefault(conn.name, KdbProvider(conn))
    return p, f"{'Live RDB' if conn.kind == 'rdb' else 'Historical HDB'} · {conn.name}", "accent"


def get_tag_dict():
    path = st.session_state.get("tag_csv_path", "")
    if not path:
        return None
    try:
        return _load_tags_cached(path, os.path.getmtime(path))
    except Exception:
        return None


@st.cache_data(show_spinner=False)
def _load_tags_cached(path: str, mtime: float):
    return load_tag_dictionary(path)


# ---- formatting -----------------------------------------------------------
def ago(ts: dt.datetime) -> str:
    s = (dt.datetime.now(dt.timezone.utc) - ts).total_seconds()
    if s < 60:
        return "just now"
    if s < 3600:
        return f"{int(s // 60)}m ago"
    if s < 86400:
        return f"{int(s // 3600)}h ago"
    return f"{int(s // 86400)}d ago"


def local(ts: dt.datetime) -> str:
    return ts.astimezone().strftime("%Y-%m-%d %H:%M")


def fmt_int(x) -> str:
    try:
        return f"{int(x):,}"
    except (ValueError, TypeError):
        return "—"


def fmt_px(x, dp: int = 4) -> str:
    try:
        return f"{float(x):,.{dp}g}" if dp <= 0 else f"{float(x):,.4g}"
    except (ValueError, TypeError):
        return "—"


def fmt_time(x) -> str:
    if x is None:
        return "—"
    try:
        return pd.Timestamp(x).strftime("%H:%M:%S")
    except Exception:
        return str(x)
