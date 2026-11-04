"""Themed Plotly charts (fully theme-controlled, so they follow light/dark)."""
from __future__ import annotations

from typing import Optional

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from .styles import TOKENS

_FONT = "Inter, -apple-system, Segoe UI, system-ui, sans-serif"


def _rgba(hex_color: str, alpha: float) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


def bid_ask_figure(qatt: pd.DataFrame, fills: Optional[pd.DataFrame] = None,
                   theme: str = "dark", prev_close: Optional[float] = None,
                   band: Optional[tuple] = None, buy_side: bool = True) -> go.Figure:
    t = TOKENS[theme]
    bid_c, ask_c, last_c = t["accent"], t["warn"], t["text"]
    grid, muted = t["border"], t["muted"]

    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, row_heights=[0.76, 0.24],
                        vertical_spacing=0.05)
    x = qatt["time"]
    fig.add_trace(go.Scatter(x=x, y=qatt["qbid"], name="Bid",
                  line=dict(color=bid_c, width=1.3, shape="hv"), mode="lines"), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=qatt["qask"], name="Ask",
                  line=dict(color=ask_c, width=1.3, shape="hv"), mode="lines",
                  fill="tonexty", fillcolor=_rgba(t["accent"], 0.10)), row=1, col=1)
    fig.add_trace(go.Scatter(x=x, y=qatt["lastPrice"], name="Last",
                  line=dict(color=last_c, width=1.8), mode="lines"), row=1, col=1)
    if "vwap" in qatt:
        fig.add_trace(go.Scatter(x=x, y=qatt["vwap"], name="VWAP",
                      line=dict(color=muted, width=1.3, dash="dot"), mode="lines"), row=1, col=1)

    if prev_close:
        fig.add_hline(y=prev_close, line=dict(color=muted, width=1, dash="dash"),
                      annotation_text="prev close", annotation_font=dict(color=muted, size=10),
                      annotation_position="right", row=1, col=1)
    if band:
        lo, hi = band
        for y, lbl in [(lo, "limit ↓"), (hi, "limit ↑")]:
            fig.add_hline(y=y, line=dict(color=t["down"], width=1, dash="dot"),
                          annotation_text=lbl, annotation_font=dict(color=t["down"], size=10),
                          annotation_position="right", row=1, col=1)

    if fills is not None and not fills.empty and "t_oes_xact" in fills:
        fig.add_trace(go.Scatter(
            x=fills["t_oes_xact"], y=fills["fillprice"], name="Fills", mode="markers",
            marker=dict(color=(t["up"] if buy_side else t["down"]),
                        symbol=("triangle-up" if buy_side else "triangle-down"), size=8,
                        line=dict(color=t["surface"], width=0.6)),
            hovertemplate="%{x|%H:%M}<br>%{y}<br>%{customdata:,} sh<extra>fill</extra>",
            customdata=fills["fillsize"]), row=1, col=1)

    fig.add_trace(go.Bar(x=x, y=qatt["lastsize"], name="Volume",
                  marker=dict(color=_rgba(muted, 0.5)), showlegend=False), row=2, col=1)

    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=t["text"], size=12, family=_FONT),
        margin=dict(l=14, r=78, t=30, b=10), height=520, hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0, font=dict(size=11)),
        bargap=0.1,
    )
    fig.update_xaxes(gridcolor=grid, zeroline=False, showspikes=True, spikecolor=muted,
                     spikethickness=1, row=1, col=1)
    fig.update_xaxes(gridcolor=grid, zeroline=False, row=2, col=1)
    fig.update_yaxes(gridcolor=grid, zeroline=False, title_text="Price", automargin=True, row=1, col=1)
    fig.update_yaxes(gridcolor=grid, zeroline=False, title_text="Vol", automargin=True, row=2, col=1)
    return fig


def fill_progress_figure(state: pd.DataFrame, size: Optional[int] = None,
                         theme: str = "dark") -> go.Figure:
    t = TOKENS[theme]
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=state["t_algo"], y=state["make"], mode="lines", name="Filled",
        line=dict(color=t["accent"], width=2), fill="tozeroy",
        fillcolor=_rgba(t["accent"], 0.10)))
    if size:
        fig.add_hline(y=size, line=dict(color=t["muted"], width=1, dash="dash"),
                      annotation_text="order qty", annotation_font=dict(color=t["muted"], size=10),
                      annotation_position="right")
    fig.update_layout(
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        font=dict(color=t["text"], size=12, family=_FONT),
        margin=dict(l=10, r=60, t=12, b=8), height=240, showlegend=False)
    fig.update_xaxes(gridcolor=t["border"], zeroline=False, automargin=True)
    fig.update_yaxes(gridcolor=t["border"], zeroline=False, automargin=True, title_text="filled")
    return fig
