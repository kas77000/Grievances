"""Situation-detection engine.

Each rule reads the day's qatt curve + the order + the security-master reference and returns
Situation objects. Rules that would need a live regulatory feed (short-sell state, halt state,
price-limit tables, VI/VCM state) run in INFERRED mode from qatt price action and are labelled
as such, so nothing pretends to be authoritative.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

SEV_ORDER = {"critical": 0, "high": 1, "warn": 2, "info": 3}


@dataclass
class Situation:
    code: str
    name: str
    severity: str          # critical | high | warn | info
    detail: str
    scope: str = "general"  # general | JP | HK | CN | KR | TW | US
    inferred: bool = False
    at: Optional[str] = None
    values: dict = field(default_factory=dict)


# ---- market inference & price-limit bands ---------------------------------
def market_of(sym: str, region: str) -> str:
    s = (sym or "").upper()
    if s.endswith(".T") or s.endswith(".JP"):
        return "JP"
    if s.endswith(".HK"):
        return "HK"
    if s.endswith(".SS") or s.endswith(".SZ"):
        return "CN"
    if s.endswith(".KS") or s.endswith(".KQ"):
        return "KR"
    if s.endswith(".TW"):
        return "TW"
    return region or "US"


# TSE static daily price-limit table (base price ¥ -> ± limit ¥). Approximate; make it config.
_JP_LIMIT_TABLE = [
    (100, 30), (200, 50), (500, 80), (700, 100), (1000, 150), (1500, 300),
    (2000, 400), (3000, 500), (5000, 700), (7000, 1000), (10000, 1500),
    (15000, 3000), (20000, 4000), (30000, 5000), (50000, 7000), (10**9, 10000),
]


def _jp_limit(prev_close: float) -> float:
    for hi, lim in _JP_LIMIT_TABLE:
        if prev_close < hi:
            return lim
    return 10000.0


def price_band(market: str, prev_close: float) -> Optional[tuple[float, float]]:
    """Return (limit_down, limit_up) or None if the market has no fixed daily limit."""
    if prev_close is None or prev_close <= 0:
        return None
    if market == "JP":
        lim = _jp_limit(prev_close)
        return (prev_close - lim, prev_close + lim)
    pct = {"CN": 0.10, "KR": 0.30, "TW": 0.10}.get(market)
    if pct is None:
        return None  # HK / US: no static limit (VCM / LULD instead)
    return (prev_close * (1 - pct), prev_close * (1 + pct))


# ---- the engine ------------------------------------------------------------
def evaluate(order: pd.Series, qatt: pd.DataFrame, stock: Optional[pd.Series],
             alerts: pd.DataFrame) -> list[Situation]:
    out: list[Situation] = []
    if qatt is None or qatt.empty:
        return out

    sym = str(order.get("sym", ""))
    region = str(order.get("region", stock.get("region") if stock is not None else "US"))
    market = market_of(sym, region)
    side = str(order.get("side", ""))
    prev_close = float(stock["adjclose"]) if stock is not None and pd.notna(stock.get("adjclose")) else None

    last = qatt["lastPrice"].to_numpy()
    pct = qatt["pctChange"].to_numpy()
    spread = (qatt["qask"] - qatt["qbid"]).to_numpy()
    t = qatt["time"]

    def tstr(i):
        return pd.Timestamp(t.iloc[int(i)]).strftime("%H:%M")

    # G1 direction
    last_pct = float(pct[-1])
    arrow = "up" if last_pct >= 0 else "down"
    out.append(Situation(
        "G_DIRECTION", f"Stock {arrow} {abs(last_pct):.2f}% on day", "info",
        f"Last {last[-1]:.4g} vs prev close {prev_close:.4g}." if prev_close else
        f"Last {last[-1]:.4g}.", values={"pct": last_pct}))

    # G2 large move
    imin, imax = int(np.argmin(pct)), int(np.argmax(pct))
    if pct[imin] <= -5:
        out.append(Situation("G_DROP", f"Intraday drop {pct[imin]:.1f}%", "warn",
                             f"Reached {pct[imin]:.1f}% vs prev close.", at=tstr(imin),
                             values={"pct": float(pct[imin])}))
    if pct[imax] >= 5:
        out.append(Situation("G_SPIKE", f"Intraday spike +{pct[imax]:.1f}%", "warn",
                             f"Reached +{pct[imax]:.1f}% vs prev close.", at=tstr(imax)))

    # G3/G4 locked / crossed
    locked = int((spread == 0).sum())
    crossed = int((spread < 0).sum())
    if crossed:
        out.append(Situation("G_CROSSED", "Crossed market observed", "high",
                             f"{crossed} sample(s) with bid > ask (data/venue anomaly)."))
    elif locked:
        out.append(Situation("G_LOCKED", "Locked market observed", "info",
                             f"{locked} sample(s) with bid == ask."))

    # G5 wide spread
    med = float(np.median(spread[spread > 0])) if (spread > 0).any() else 0.0
    if med > 0:
        wmax = float(np.max(spread))
        if wmax >= 5 * med:
            j = int(np.argmax(spread))
            out.append(Situation("G_WIDE_SPREAD", "Spread spike (>5x median)", "warn",
                                 f"Max spread {wmax:.4g} vs median {med:.4g}.", at=tstr(j)))

    # G8 opening gap
    if prev_close:
        gap = (float(last[0]) - prev_close) / prev_close * 100
        if abs(gap) >= 2:
            out.append(Situation("G_GAP", f"Opening gap {gap:+.1f}%", "info",
                                 f"First print {last[0]:.4g} vs prev close {prev_close:.4g}.",
                                 at=tstr(0)))

    # G9 at day low/high
    if last[-1] <= qatt["lowPrice"].iloc[-1] + (med or 0):
        out.append(Situation("G_AT_LOW", "Trading at/near session low", "info",
                             f"Last {last[-1]:.4g}, day low {qatt['lowPrice'].iloc[-1]:.4g}."))

    # G_VOL vs ADV
    if stock is not None and pd.notna(stock.get("adv")) and float(stock["adv"]) > 0:
        part = float(qatt["totalVolume"].iloc[-1]) / float(stock["adv"]) * 100
        out.append(Situation("G_VOL", f"Day volume {part:.0f}% of ADV", "info",
                             f"Traded {int(qatt['totalVolume'].iloc[-1]):,} vs ADV {int(stock['adv']):,}."))

    # G_HALT from engine alerts
    if alerts is not None and not alerts.empty and "alerttype" in alerts:
        if alerts["alerttype"].astype(str).str.contains("HALT", case=False).any():
            out.append(Situation("G_HALT", "Trading halt flagged", "critical",
                                 "Engine alert indicates a halt window."))

    # ---- limit up/down (any market with a band) ----
    band = price_band(market, prev_close) if prev_close else None
    if band:
        lo, hi = band
        width = hi - lo
        dist_lo = (float(last.min()) - lo) / width
        if dist_lo <= 0:
            out.append(Situation("A_LIMIT_DOWN", "Hit daily limit-down", market and "high" or "high",
                                 f"Price reached limit-down {lo:.4g}.", scope=market, inferred=True))
        elif dist_lo <= 0.10:
            j = int(np.argmin(last))
            out.append(Situation("A_LIMIT_NEAR", "Approaching daily limit-down", "warn",
                                 f"Low {last.min():.4g} within 10% of limit-down band {lo:.4g}"
                                 f" (band {lo:.4g}–{hi:.4g}).", scope=market, inferred=True,
                                 at=tstr(j)))

    # ---- Japan short-sell price restriction (uptick trigger) ----
    if market == "JP" and prev_close:
        trig = np.where(pct <= -10.0)[0]
        if len(trig):
            first = int(trig[0])
            short = "short" in side.lower() or "5" in str(order.get("fixmsg", "")).split("54=")[-1][:1]
            sev = "high" if short else "warn"
            note = ("Order is a short sale — from the trigger, shorts must be at an uptick "
                    "(above last published price)." if short else
                    "Short sales in this name must be at an uptick from the trigger time.")
            out.append(Situation(
                "A_JP_SHORTSELL", "Japan short-sell price restriction triggered", sev,
                f"Stock traded ≤ −10% vs prev close ({pct[first]:.1f}% at {tstr(first)}). {note}",
                scope="JP", inferred=True, at=tstr(first),
                values={"trigger_pct": float(pct[first])}))

    # ---- Japan lunch-break awareness ----
    if market == "JP" and stock is not None and stock.get("lunch1"):
        out.append(Situation(
            "A_JP_LUNCH", "Lunch-break session", "info",
            f"Midday close {stock.get('lunch1')}–{stock.get('lunch2')}; "
            f"suppress no-fill / stale-quote alerts in this window.", scope="JP"))

    # ---- Hong Kong VCM (inferred) ----
    if market == "HK" and len(last) > 6:
        s = pd.Series(last)
        roll = (s / s.shift(5) - 1.0) * 100
        hit = roll.abs() >= 10
        if hit.any():
            j = int(hit.idxmax())
            out.append(Situation(
                "A_HK_VCM", "Possible VCM cooling-off (±10% in 5 min)", "warn",
                f"5-minute move {roll.iloc[j]:+.1f}% may trigger the Volatility Control "
                f"Mechanism.", scope="HK", inferred=True, at=tstr(j)))

    out.sort(key=lambda s: SEV_ORDER.get(s.severity, 9))
    return out
