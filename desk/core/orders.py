"""Turn an issue's order references into full (date, id_server, id_target, sym) keys.

The pasted data may only carry id_target (or oes_oid); the watchdog resolves those when kdb+ is
on, but the Desk re-resolves against the active source so demo mode and late kdb+ setup work too.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

from core import mockdata
from core.connections import Connection
from core.queries import DataProvider, KdbProvider, MockProvider
from core.settings import wd


def ref_date(ref: dict) -> dt.date:
    try:
        return dt.date.fromisoformat(str(ref.get("date")))
    except ValueError:
        return dt.date.today()


def _int(v) -> Optional[int]:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def resolve(provider: DataProvider, ref: dict) -> list[dict]:
    """All orders matching the reference on this source (one id_target can live on several servers)."""
    date = ref_date(ref)
    id_target, id_server = _int(ref.get("id_target")), _int(ref.get("id_server"))
    oes_oid = wd.safe_token(str(ref.get("oes_oid") or "")) if ref.get("oes_oid") else None

    if isinstance(provider, MockProvider):
        o = provider.ds["orders"]
        if id_target is not None:
            o = o[o["id_target"] == id_target]
        elif oes_oid:
            o = o[o["oes_oid"] == oes_oid]
        else:
            return []
        if id_server is not None:
            o = o[o["id_server"] == id_server]
        return [dict(date=mockdata.TRADE_DATE, id_server=int(r.id_server), id_target=int(r.id_target), sym=r.sym)
                for r in o.itertuples()]

    if isinstance(provider, KdbProvider):
        d = KdbProvider._d(date)
        if id_target is not None:
            where = f"id_target={id_target}" + (f", id_server={id_server}" if id_server is not None else "")
        elif oes_oid:
            where = f'oes_oid=`$"{oes_oid}"'
        else:
            return []
        df = provider.client.query(f"select id_server,id_target,sym from target where date={d}, {where}")
        return [dict(date=date, id_server=int(r["id_server"]), id_target=int(r["id_target"]),
                     sym=r["sym"].decode() if isinstance(r["sym"], bytes) else str(r["sym"]))
                for r in df.to_dict("records")]
    return []


def kdb_connection_for(cfg: "wd.Config", ref: dict, today: Optional[dt.date] = None) -> Optional[Connection]:
    """RDB for today's orders, HDB for prior days, in the order's region."""
    c = wd.pick_connection(cfg, str(ref.get("region") or cfg.default_region), ref_date(ref), today or dt.date.today())
    if c is None:
        return None
    return Connection(name=c.name, host=c.host, port=c.port, region=c.region, kind=c.kind,
                      username=c.username, password_env=c.password_env)
