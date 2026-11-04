"""Data provider abstraction.

The UI talks only to a DataProvider. MockProvider serves synthetic data (no server needed);
KdbProvider runs q queries over pykx against an RDB (live) or HDB (historical). Both return
plain pandas DataFrames so the UI is identical either way.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

import pandas as pd

from . import mockdata
from .connections import Connection, KdbClient


class DataProvider:
    label = "base"

    def available_dates(self) -> list[dt.date]:
        raise NotImplementedError

    def list_orders(self, date: dt.date, filters: dict) -> pd.DataFrame:
        raise NotImplementedError

    def get_order(self, date, id_server, id_target) -> Optional[pd.Series]:
        raise NotImplementedError

    def get_target_state(self, date, id_server, id_target) -> pd.DataFrame:
        raise NotImplementedError

    def get_workorders(self, date, id_server, id_target) -> pd.DataFrame:
        raise NotImplementedError

    def get_executions(self, date, id_server, id_target) -> pd.DataFrame:
        raise NotImplementedError

    def get_alerts(self, date, id_server, id_target) -> pd.DataFrame:
        raise NotImplementedError

    def get_qatt(self, date, sym, t0=None, t1=None) -> pd.DataFrame:
        raise NotImplementedError

    def get_target_stock(self, date, id_server, id_target) -> Optional[pd.Series]:
        raise NotImplementedError


# ----------------------------------------------------------------------------
class MockProvider(DataProvider):
    label = "mock"

    def __init__(self):
        self.ds = mockdata.get_dataset()

    def available_dates(self):
        return [mockdata.TRADE_DATE]

    def list_orders(self, date, filters):
        o = self.ds["orders"].copy()
        # attach filled/avg from the final target_state row
        st = self.ds["target_state"].sort_values("t_algo").groupby("id_target").last()
        o = o.merge(st[["make", "avg_fill_price"]], left_on="id_target",
                    right_index=True, how="left")
        o["make"] = o["make"].fillna(0).astype(int)
        o = o.rename(columns={"make": "filled"})
        o["fill_pct"] = (o["filled"] / o["size"] * 100).round(1)
        f = filters or {}
        if f.get("region") and f["region"] != "All":
            o = o[o["region"] == f["region"]]
        if f.get("id_target"):
            o = o[o["id_target"].astype(str).str.contains(str(f["id_target"]).strip())]
        if f.get("sym"):
            o = o[o["sym"].str.upper().str.startswith(f["sym"].strip().upper())]
        if f.get("trader"):
            o = o[o["trader"].str.contains(f["trader"].strip(), case=False)]
        if f.get("algo") and f["algo"] != "All":
            o = o[o["algo"] == f["algo"]]
        if f.get("side") and f["side"] != "All":
            o = o[o["side"] == f["side"]]
        if f.get("unfilled_only"):
            o = o[o["filled"] < o["size"]]
        cols = ["id_target", "id_server", "sym", "region", "side", "size", "filled",
                "fill_pct", "avg_fill_price", "algo", "alpha", "trader", "basket",
                "portfolio", "t_start", "t_end"]
        return o[cols].reset_index(drop=True)

    def get_order(self, date, id_server, id_target):
        o = self.ds["orders"]
        r = o[(o["id_target"] == int(id_target)) & (o["id_server"] == int(id_server))]
        return r.iloc[0] if len(r) else None

    def get_target_state(self, date, id_server, id_target):
        st = self.ds["target_state"]
        return st[(st["id_target"] == int(id_target)) &
                  (st["id_server"] == int(id_server))].sort_values("t_algo").reset_index(drop=True)

    def get_workorders(self, date, id_server, id_target):
        w = self.ds["workorders"]
        return w[(w["id_target"] == int(id_target)) &
                 (w["id_server"] == int(id_server))].reset_index(drop=True)

    def get_executions(self, date, id_server, id_target):
        e = self.ds["executions"]
        return e[(e["id_target"] == int(id_target)) &
                 (e["id_server"] == int(id_server))].sort_values("t_oes_xact").reset_index(drop=True)

    def get_alerts(self, date, id_server, id_target):
        a = self.ds["alerts"]
        return a[a["id_target"] == int(id_target)].reset_index(drop=True)

    def get_qatt(self, date, sym, t0=None, t1=None):
        q = self.ds["qatt"].get(sym)
        if q is None:
            return pd.DataFrame()
        q = q.copy()
        if t0 is not None:
            q = q[q["time"] >= t0]
        if t1 is not None:
            q = q[q["time"] <= t1]
        return q.reset_index(drop=True)

    def get_target_stock(self, date, id_server, id_target):
        s = self.ds["target_stock"]
        r = s[(s["id_target"] == int(id_target)) & (s["id_server"] == int(id_server))]
        return r.iloc[0] if len(r) else None


# ----------------------------------------------------------------------------
class KdbProvider(DataProvider):
    """Queries a live RDB or historical HDB via pykx. Every query is date-constrained first.

    Query text is written to match ORDER-MONITOR-DATA-MODEL.md; column availability may vary
    by region/version, so the UI tolerates missing columns.
    """
    label = "kdb"

    def __init__(self, conn: Connection):
        self.conn = conn
        self.client = KdbClient(conn)

    @staticmethod
    def _d(date: dt.date) -> str:
        return f"{date.year:04d}.{date.month:02d}.{date.day:02d}"

    def available_dates(self):
        try:
            df = self.client.query("exec distinct date from target")
            return sorted(pd.to_datetime(df.iloc[:, 0]).dt.date.tolist())
        except Exception:
            return [dt.date.today()]

    def list_orders(self, date, filters):
        d = self._d(date)
        expr = (
            f"st:select last make, last avg_fill_price by id_server,id_target "
            f"from target_state where date={d};"
            f"o:select date,id_server,id_target,sym,side,size,algo,alpha,trader,basket,"
            f"portfolio,t_start,t_end from target where date={d};"
            f"o lj `id_server`id_target xkey st"
        )
        try:
            df = self.client.query(expr)
        except Exception:
            return pd.DataFrame()
        if "make" in df:
            df = df.rename(columns={"make": "filled"})
            df["filled"] = df["filled"].fillna(0).astype("int64")
            df["fill_pct"] = (df["filled"] / df["size"] * 100).round(1)
        # client-side filtering keeps the q simple
        f = filters or {}
        if f.get("id_target"):
            df = df[df["id_target"].astype(str).str.contains(str(f["id_target"]).strip())]
        if f.get("sym"):
            df = df[df["sym"].astype(str).str.upper().str.startswith(f["sym"].strip().upper())]
        if f.get("trader"):
            df = df[df["trader"].astype(str).str.contains(f["trader"].strip(), case=False)]
        if f.get("unfilled_only") and "filled" in df:
            df = df[df["filled"] < df["size"]]
        return df.reset_index(drop=True)

    def get_order(self, date, id_server, id_target):
        d = self._d(date)
        df = self.client.query(
            f"select from target where date={d}, id_server={int(id_server)}, "
            f"id_target={int(id_target)}")
        return df.iloc[0] if len(df) else None

    def get_target_state(self, date, id_server, id_target):
        d = self._d(date)
        return self.client.query(
            f"select from target_state where date={d}, id_server={int(id_server)}, "
            f"id_target={int(id_target)}")

    def get_workorders(self, date, id_server, id_target):
        d = self._d(date)
        return self.client.query(
            f"select from workorder where date={d}, id_server={int(id_server)}, "
            f"id_target={int(id_target)}")

    def get_executions(self, date, id_server, id_target):
        d = self._d(date)
        return self.client.query(
            f"select from execution where date={d}, id_server={int(id_server)}, "
            f"id_target={int(id_target)}")

    def get_alerts(self, date, id_server, id_target):
        d = self._d(date)
        return self.client.query(
            f"select from alerts where date={d}, id_target={int(id_target)}")

    def get_qatt(self, date, sym, t0=None, t1=None):
        d = self._d(date)
        return self.client.query(f'select from qatt where date={d}, sym=`$"{sym}"')

    def get_target_stock(self, date, id_server, id_target):
        d = self._d(date)
        df = self.client.query(
            f"select from target_stock where date={d}, id_server={int(id_server)}, "
            f"id_target={int(id_target)}")
        return df.iloc[0] if len(df) else None


def make_provider(active_conn: Optional[Connection], force_mock: bool) -> DataProvider:
    """Pick a provider: mock unless a live/historical connection is active and usable."""
    from .connections import pykx_available
    if force_mock or active_conn is None or not pykx_available():
        return MockProvider()
    return KdbProvider(active_conn)
