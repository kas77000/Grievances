"""Order monitor view (adapted from algo-order-monitor's detail page): order / routing / fills /
market / alerts / situations for one fully-keyed order. Imported by the analysis page."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from app_pages import common as C
from core import rules
from core.queries import DataProvider
from core.tags import decode_order_tags
from theme import styles as T
from theme.charts import bid_ask_figure, fill_progress_figure


@st.cache_data(ttl=60, show_spinner="Loading order from kdb+…")
def load_order(_p: DataProvider, source: str, date, id_server: int, id_target: int, sym: str) -> dict:
    """All tables for one order. `source` is part of the cache key; RDB data refreshes each minute."""
    return dict(
        order=_p.get_order(date, id_server, id_target),
        stock=_p.get_target_stock(date, id_server, id_target),
        qatt=_p.get_qatt(date, sym),
        fills=_p.get_executions(date, id_server, id_target),
        wos=_p.get_workorders(date, id_server, id_target),
        state=_p.get_target_state(date, id_server, id_target),
        alerts=_p.get_alerts(date, id_server, id_target),
    )


def _combine(date, hhmm):
    """'09:00' + date -> Timestamp; pass through if already a timestamp."""
    if hhmm is None:
        return None
    if isinstance(hhmm, (pd.Timestamp, dt.datetime)):
        return pd.Timestamp(hhmm)
    try:
        return pd.Timestamp(dt.datetime.combine(date, dt.time.fromisoformat(str(hhmm))))
    except Exception:
        return None


def _situation_row(s: rules.Situation):
    tone = T.SEV_TONE.get(s.severity, "info")
    tags = T.badge(s.severity.upper(), tone)
    if s.scope not in ("general",):
        tags += " " + T.badge(s.scope, "accent")
    if s.inferred:
        tags += " " + T.badge("inferred", "info")
    meta_html = f'<div class="meta">at {T.esc(s.at)}</div>' if s.at else ""
    st.markdown(
        f'<div class="amx-sit"><div class="body">'
        f'<div class="name">{T.esc(s.name)} &nbsp;{tags}</div>'
        f'<div class="detail">{T.esc(s.detail)}</div>{meta_html}</div></div>',
        unsafe_allow_html=True)


def render_order(p: DataProvider, source: str, date, id_server: int, id_target: int, sym: str):
    d = load_order(p, source, date, id_server, id_target, sym)
    order, stock, qatt, fills, wos, state, alerts = (
        d["order"], d["stock"], d["qatt"], d["fills"], d["wos"], d["state"], d["alerts"])
    if order is None:
        st.warning(f"Order {id_server}/{id_target} not found on {source} for {date}.")
        return
    sym = str(order.get("sym", sym))
    th = st.session_state.get("theme", "dark")

    T.eyebrow(f"{order.get('region', '')} · {order.get('algo', '')} · {order.get('trader', '')} · {date}")
    st.markdown(f"### {T.esc(sym)} &nbsp;<span style='color:var(--muted);font-weight:500'>"
                f"#{id_target} · server {id_server}</span>", unsafe_allow_html=True)

    size = int(order.get("size", 0) or 0)
    filled = int(state["make"].iloc[-1]) if (state is not None and not state.empty and "make" in state) else 0
    avg = float(state["avg_fill_price"].iloc[-1]) if (state is not None and not state.empty and "avg_fill_price" in state) else float("nan")
    day_pct = float(qatt["pctChange"].iloc[-1]) if (qatt is not None and not qatt.empty and "pctChange" in qatt) else None
    pct_disp, tone = T.signed_pct(day_pct)
    prev_close = float(stock["adjclose"]) if (stock is not None and pd.notna(stock.get("adjclose"))) else None

    st.markdown(T.tiles([
        T.tile("Side", str(order.get("side", "—")).upper()),
        T.tile("Order qty", C.fmt_int(size)),
        T.tile("Filled", f"{C.fmt_int(filled)}", f"{(filled / size * 100 if size else 0):.0f}% of order"),
        T.tile("Avg fill px", C.fmt_px(avg)),
        T.tile("Stock on day", pct_disp, f"prev close {C.fmt_px(prev_close)}" if prev_close else "", tone),
        T.tile("Limit", C.fmt_px(order.get("limit_price")) if float(order.get("limit_price", 0) or 0) else "—"),
    ]), unsafe_allow_html=True)

    with st.expander("Order tags (decoded from fixmsg)", expanded=False):
        tagdict = C.get_tag_dict()
        if tagdict is None:
            st.caption("No BCORE_Tags CSV loaded — showing built-in tag names. Load it on the Settings page.")
        tdf = decode_order_tags(order.get("fixmsg"), tagdict)
        if tdf.empty:
            st.caption("This order has no fixmsg tags.")
        else:
            st.dataframe(tdf, hide_index=True, width="stretch")

    sits = rules.evaluate(order, qatt, stock, alerts)
    tabs = st.tabs([f"Situations ({len(sits)})", "Order", "Routing", "Fills", "Market",
                    f"Alerts ({0 if alerts is None else len(alerts)})"])

    # Situations first: it is the quickest answer to "what is wrong with this order?"
    with tabs[0]:
        if not sits:
            st.info("No situations detected.")
        else:
            st.caption(f"{len(sits)} situation(s). Rules marked *inferred* are derived from "
                       "price action, not a regulatory feed.")
            for s in sits:
                _situation_row(s)

    with tabs[1]:
        left, right = st.columns([1.1, 1])
        with left:
            T.eyebrow("Parameters")
            keys = ["algo", "alpha", "beta", "tif", "otype", "wave", "basket", "portfolio",
                    "t_start", "t_end", "oes_oid"]
            params = {k: str(order.get(k)) for k in keys if k in order.index}
            st.dataframe(pd.Series(params, name="value").to_frame(), width="stretch")
        with right:
            T.eyebrow("Fill progress")
            if state is not None and not state.empty and "t_algo" in state:
                st.plotly_chart(fill_progress_figure(state, size=size, theme=th), width="stretch")
            if state is not None and not state.empty:
                last = state.iloc[-1]
                chans = {k.replace("make_", ""): int(last.get(k, 0))
                         for k in ["make_take", "make_post", "make_dark", "make_pmid"] if k in state.columns}
                if chans:
                    T.eyebrow("Liquidity by channel")
                    st.markdown(T.tiles([T.tile(k, C.fmt_int(v)) for k, v in chans.items()]),
                                unsafe_allow_html=True)

    with tabs[2]:
        if wos is None or wos.empty:
            st.info("No child orders (workorders) for this order.")
        else:
            st.dataframe(wos, hide_index=True, width="stretch")
            try:
                import plotly.express as px
                g = wos.copy()
                g["Start"] = g["t_on_market"].map(lambda x: _combine(date, x))
                g["Finish"] = g["t_off_market"].map(lambda x: _combine(date, x))
                g = g.dropna(subset=["Start", "Finish"])
                if not g.empty:
                    tok = T.TOKENS[th]
                    fig = px.timeline(g, x_start="Start", x_end="Finish", y="venue", color="venue")
                    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                                      font_color=tok["text"], height=200, showlegend=False,
                                      margin=dict(l=8, r=8, t=8, b=8))
                    fig.update_xaxes(gridcolor=tok["border"])
                    fig.update_yaxes(gridcolor=tok["border"])
                    st.plotly_chart(fig, width="stretch")
            except Exception:
                pass

    with tabs[3]:
        if fills is None or fills.empty:
            st.info("No executions for this order.")
        else:
            sign = 1 if str(order.get("side", "")).lower().startswith("b") else -1
            if "target_vwap" in fills.columns:
                slip = (fills["fillprice"] - fills["target_vwap"]) / fills["target_vwap"] * 1e4 * -sign
                avg_slip = float(slip.mean())
                st.markdown(T.tiles([
                    T.tile("Fills", C.fmt_int(len(fills))),
                    T.tile("Filled qty", C.fmt_int(int(fills["fillsize"].sum()))),
                    T.tile("Avg slippage vs VWAP", f"{avg_slip:+.1f} bps",
                           "positive = better than VWAP", "up" if avg_slip >= 0 else "down"),
                ]), unsafe_allow_html=True)
            show_cols = [c for c in ["t_oes_xact", "fillsize", "fillprice", "cum_qty", "cum_apr",
                                     "exec_broker", "last_mkt", "liquidity", "fixtags"] if c in fills.columns]
            st.dataframe(fills[show_cols], hide_index=True, width="stretch")

    with tabs[4]:
        if qatt is None or qatt.empty:
            st.info("No market data (qatt) for this symbol/date.")
        else:
            mkt = rules.market_of(sym, str(order.get("region", "")))
            band = rules.price_band(mkt, prev_close) if prev_close else None
            buy_side = str(order.get("side", "")).lower().startswith("b")
            st.plotly_chart(bid_ask_figure(qatt, fills=fills, theme=th, prev_close=prev_close,
                                           band=band, buy_side=buy_side), width="stretch")
            st.caption("Bid/ask (stepped) with shaded spread, last & VWAP; fills marked; "
                       "dashed = prev close; dotted = daily price-limit band where applicable.")

    with tabs[5]:
        if alerts is None or alerts.empty:
            st.success("No engine alerts fired for this order.")
        else:
            for _, a in alerts.iterrows():
                st.markdown(
                    f'<div class="amx-sit"><div class="body">'
                    f'<div class="name">{T.esc(a.get("alerttype", "alert"))} {T.badge("alert", "warn")}</div>'
                    f'<div class="detail">{T.esc(a.get("alertstr", ""))}</div>'
                    f'<div class="meta">at {T.esc(a.get("triggertime", "—"))} · '
                    f'triggers {T.esc(a.get("ntrigger", "—"))}</div></div></div>',
                    unsafe_allow_html=True)
