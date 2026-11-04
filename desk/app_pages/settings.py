"""Settings: where the Desk reads from, kdb+ connections, and the tag dictionary."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from app_pages import common as C
from core.connections import Connection, pykx_available, test_connection
from core.tags import load_tag_dictionary
from theme import styles as T

s = C.settings()
cfg = s.watchdog

T.eyebrow("Order Alert Desk")
st.markdown("## Settings")

st.markdown("### Issues")
st.markdown(f"Config file: `{s.config_path}`" + ("  \n" + T.badge("demo", "warn") if s.is_demo else ""),
            unsafe_allow_html=True)
st.markdown(f"Reading issues from `{cfg.processed}` · last **{s.history_days}** days · "
            f"team: {', '.join(s.team) or '—'}")
if not cfg.processed.exists():
    st.error("That folder is not reachable from this server.")
st.caption("Paths, team and connections are edited in config.toml (shared with the watchdog). "
           "Changes are picked up within a minute.")

st.markdown("### kdb+ connections")
if not pykx_available():
    st.info("**pykx is not installed** on this server, so order analysis runs on demo data.")
if cfg.connections:
    st.dataframe(pd.DataFrame([{"Name": c.name, "Host": c.host, "Port": c.port, "Region": c.region,
                                "Kind": "Live RDB" if c.kind == "rdb" else "Historical HDB",
                                "User": c.username or "—"} for c in cfg.connections]),
                 hide_index=True, width="stretch")
    pick = st.selectbox("Test a connection", [c.name for c in cfg.connections])
    if st.button("Test", disabled=not pykx_available()):
        c = next(c for c in cfg.connections if c.name == pick)
        ok, msg = test_connection(Connection(c.name, c.host, c.port, c.region, c.kind))
        (st.success if ok else st.error)(f"{c.name}: {msg}")
else:
    st.caption("No connections in config.toml.")

st.divider()
st.markdown("### BCORE_Tags dictionary")
st.caption("CSV mapping tag numbers to names/descriptions. Used to decode each order's fixmsg.")
path = st.text_input("CSV path", value=st.session_state.get("tag_csv_path", ""),
                     placeholder=r"\\fileserver\share\...\BCORE_Tags.csv")
if st.button("Load"):
    td = load_tag_dictionary(path) if path.strip() else None
    if td is None:
        st.error("Could not load a valid BCORE_Tags CSV from that path (needs a 'TagNumber' column).")
    else:
        st.session_state["tag_csv_path"] = path.strip()
        st.success(f"Loaded {len(td)} tag definitions.")
