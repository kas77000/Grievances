"""Deterministic synthetic data that mirrors the Argo OMS + market-data schemas.

Lets the whole UI run with no kdb+ server. Numbers are stable (seeded) so screenshots and
rules are reproducible. Column names follow ORDER-MONITOR-DATA-MODEL.md.
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache

import numpy as np
import pandas as pd

TRADE_DATE = dt.date(2026, 7, 17)

# symbol -> reference config (prev close = adjclose, session, currency, exchange lunch)
SYMBOLS = {
    "7203.T": dict(region="AP", ccy="JPY", adjclose=2500.0, low_pct=-0.116,  # Toyota, dips >10%
                   sess=("09:00", "15:00"), lunch=("11:30", "12:30"), tick=0.5),
    "0700.HK": dict(region="AP", ccy="HKD", adjclose=372.0, low_pct=-0.031,   # Tencent
                    sess=("09:30", "16:00"), lunch=("12:00", "13:00"), tick=0.2),
    "AAPL":   dict(region="US", ccy="USD", adjclose=214.30, low_pct=-0.006,    # normal US name
                   sess=("09:30", "16:00"), lunch=None, tick=0.01),
}


def _session_index(sess, lunch):
    d = TRADE_DATE
    s = dt.datetime.combine(d, dt.time.fromisoformat(sess[0]))
    e = dt.datetime.combine(d, dt.time.fromisoformat(sess[1]))
    idx = pd.date_range(s, e, freq="1min")
    if lunch:
        l0 = dt.datetime.combine(d, dt.time.fromisoformat(lunch[0]))
        l1 = dt.datetime.combine(d, dt.time.fromisoformat(lunch[1]))
        idx = idx[(idx <= l0) | (idx >= l1)]
    return idx


def _qatt_for(sym: str) -> pd.DataFrame:
    cfg = SYMBOLS[sym]
    rng = np.random.default_rng(abs(hash(sym)) % (2**32))
    idx = _session_index(cfg["sess"], cfg["lunch"])
    n = len(idx)
    prev = cfg["adjclose"]
    tick = cfg["tick"]

    # Build a price path: down to the day low around 35% into the session, partial recovery.
    trough = int(n * 0.35)
    low = prev * (1 + cfg["low_pct"])
    path = np.empty(n)
    for i in range(n):
        if i <= trough:
            base = prev + (low - prev) * (i / max(trough, 1))
        else:
            base = low + (prev * 0.985 - low) * ((i - trough) / max(n - trough, 1))
        path[i] = base
    noise = rng.normal(0, prev * 0.0007, n).cumsum() * 0.15
    last = np.round((path + noise) / tick) * tick

    spread = tick * rng.integers(1, 4, n)
    qbid = np.round((last - spread / 2) / tick) * tick
    qask = np.round((last + spread / 2) / tick) * tick
    vol_step = rng.integers(2000, 9000, n)
    totvol = vol_step.cumsum()
    vwap = np.round((np.cumsum(last * vol_step) / totvol) / tick) * tick

    df = pd.DataFrame({
        "time": idx,
        "sym": sym,
        "qbid": qbid,
        "qask": qask,
        "qbsize": rng.integers(200, 4000, n),
        "qasize": rng.integers(200, 4000, n),
        "lastPrice": last,
        "lastsize": vol_step,
        "totalVolume": totvol,
        "vwap": vwap,
        "highPrice": np.maximum.accumulate(last),
        "lowPrice": np.minimum.accumulate(last),
        "netChange": last - prev,
        "pctChange": (last - prev) / prev * 100.0,
        "quoteTime": idx,
    })
    return df


def _orders() -> pd.DataFrame:
    rows = [
        dict(id_target=100731, id_server=7, sym="7203.T", side="sellshort", sidesign=-1,
             size=180000, algo="VWAP", alpha=6.5, beta=0.9, trader="jchu", basket="AP_MORNING",
             portfolio="AP_PB01", wave="wv3", otype="Algo", tif="DAY", limit_price=0.0,
             t_start="09:00", t_end="15:00", oes_oid="OES-7203-01",
             fixmsg="9001=VWAP;9012=82=B20^;9006=PCAP;10106=90000;9010=B10;54=5;9603=CLORD7203"),
        dict(id_target=100742, id_server=7, sym="0700.HK", side="buy", sidesign=1,
             size=60000, algo="SHADOW", alpha=4.0, beta=1.1, trader="jchu", basket="AP_MORNING",
             portfolio="AP_PB02", wave="wv1", otype="Algo", tif="DAY", limit_price=380.0,
             t_start="09:30", t_end="16:00", oes_oid="OES-0700-01",
             fixmsg="9001=SHADOW;9006=PCAP;10102=1.5;10106=40000;9603=CLORD0700"),
        dict(id_target=100118, id_server=3, sym="AAPL", side="buy", sidesign=1,
             size=250000, algo="VWAP", alpha=3.2, beta=1.0, trader="asmith", basket="US_CORE",
             portfolio="US_PB10", wave="wv2", otype="Algo", tif="DAY", limit_price=0.0,
             t_start="09:30", t_end="16:00", oes_oid="OES-AAPL-01",
             fixmsg="9001=VWAP;10106=250000;9010=B5;9603=CLORDAAPL"),
    ]
    df = pd.DataFrame(rows)
    df["date"] = TRADE_DATE
    df["region"] = df["sym"].map(lambda s: SYMBOLS[s]["region"])
    return df


def _fill_progress(sym, size, side_sign):
    """Cumulative fills across the day, biased to trade near the qatt path."""
    q = _qatt_for(sym)
    rng = np.random.default_rng((abs(hash(sym)) + 11) % (2**32))
    n_fills = 24
    take_idx = np.sort(rng.choice(len(q), size=n_fills, replace=False))
    per = np.diff(np.concatenate([[0], np.sort(rng.uniform(0.15, 1.0, n_fills))]))
    per = (per / per.sum() * size * 0.82).astype(int)  # ~82% filled by EOD
    fills = []
    cum = 0
    for k, gi in enumerate(take_idx):
        row = q.iloc[gi]
        fs = int(max(per[k], 100))
        cum += fs
        px = row["qask"] if side_sign > 0 else row["qbid"]
        fills.append(dict(
            t_oes_xact=row["time"], fillsize=fs, fillprice=float(px), cum_qty=cum,
            bidprice=float(row["qbid"]), askprice=float(row["qask"]),
            lastprice=float(row["lastPrice"]), target_vwap=float(row["vwap"]),
        ))
    fdf = pd.DataFrame(fills)
    fdf["cum_apr"] = (fdf["fillprice"] * fdf["fillsize"]).cumsum() / fdf["cum_qty"]
    return fdf


@lru_cache(maxsize=1)
def get_dataset() -> dict:
    orders = _orders()

    qatt = {s: _qatt_for(s) for s in SYMBOLS}

    exec_rows, wo_rows, state_rows, alert_rows, stock_rows = [], [], [], [], []
    for _, o in orders.iterrows():
        sym, sign = o["sym"], o["sidesign"]
        fdf = _fill_progress(sym, o["size"], sign)
        filled = int(fdf["cum_qty"].iloc[-1])
        avg_px = float(fdf["cum_apr"].iloc[-1])

        # executions
        f = fdf.copy()
        f["date"] = TRADE_DATE
        f["id_server"], f["id_target"], f["sym"], f["side"] = o["id_server"], o["id_target"], sym, o["side"]
        f["id_work"] = (np.arange(len(f)) % 3) + 1
        f["exec_broker"] = np.where(np.arange(len(f)) % 2 == 0, "SCB", "XABC")
        f["last_mkt"] = np.where(np.arange(len(f)) % 3 == 0, "DARK", SYMBOLS[sym]["region"] + "LIT")
        f["liquidity"] = np.where(np.arange(len(f)) % 3 == 0, "added", "removed")
        f["fixtags"] = "30=" + f["last_mkt"].astype(str)
        exec_rows.append(f)

        # workorders (child) — a few venues
        venues = ["PRIMARY", "DARK", "PMID"]
        for k, v in enumerate(venues):
            share = [0.5, 0.3, 0.2][k]
            wo_rows.append(dict(
                date=TRADE_DATE, id_server=o["id_server"], id_target=o["id_target"],
                id_work=k + 1, sym=sym, side=o["side"], venue=v, venuetype=("lit" if v == "PRIMARY" else "dark"),
                size=int(o["size"] * share), make=int(filled * share), price=float(avg_px),
                state="closed", t_on_market=o["t_start"], t_off_market=o["t_end"],
                count_chaseprice=int(3 + k * 2 + (8 if sym == "7203.T" else 0)),
            ))

        # target_state timeline (cumulative make over the day)
        ts = fdf[["t_oes_xact", "cum_qty", "cum_apr"]].copy()
        for _, r in ts.iterrows():
            state_rows.append(dict(
                date=TRADE_DATE, id_server=o["id_server"], id_target=o["id_target"], sym=sym,
                t_algo=r["t_oes_xact"], state="working", make=int(r["cum_qty"]),
                leave=int(o["size"]) - int(r["cum_qty"]), avg_fill_price=float(r["cum_apr"]),
                make_dark=int(r["cum_qty"] * 0.3), make_pmid=int(r["cum_qty"] * 0.2),
                make_take=int(r["cum_qty"] * 0.3), make_post=int(r["cum_qty"] * 0.2),
            ))

        # security master snapshot
        cfg = SYMBOLS[sym]
        stock_rows.append(dict(
            date=TRADE_DATE, id_server=o["id_server"], id_target=o["id_target"], sym=sym,
            adjclose=cfg["adjclose"], orgclose=cfg["adjclose"], currency=cfg["ccy"],
            region=cfg["region"], ticksize=cfg["tick"], adv=int(o["size"] * 20),
            adv1m=int(o["size"] * 18), lunch1=(cfg["lunch"][0] if cfg["lunch"] else None),
            lunch2=(cfg["lunch"][1] if cfg["lunch"] else None), segment="MAIN",
        ))

    # a couple of engine alerts
    alert_rows = [
        dict(date=TRADE_DATE, id_server=7, id_target=100731, sym="7203.T",
             triggertime="10:32", alerttype="PRICE_DROP", alertmode=2, ntrigger=1,
             alertstr="Stock down >10% vs prev close", limit=0.0, size=180000),
        dict(date=TRADE_DATE, id_server=7, id_target=100731, sym="7203.T",
             triggertime="10:45", alerttype="LOW_FILL", alertmode=1, ntrigger=2,
             alertstr="Fill rate lagging schedule", limit=0.0, size=180000),
        dict(date=TRADE_DATE, id_server=7, id_target=100742, sym="0700.HK",
             triggertime="13:15", alerttype="SPREAD_WIDE", alertmode=1, ntrigger=1,
             alertstr="Spread wider than 5x median", limit=0.0, size=60000),
    ]

    return dict(
        orders=orders,
        qatt=qatt,
        executions=pd.concat(exec_rows, ignore_index=True),
        workorders=pd.DataFrame(wo_rows),
        target_state=pd.DataFrame(state_rows),
        target_stock=pd.DataFrame(stock_rows),
        alerts=pd.DataFrame(alert_rows),
    )
