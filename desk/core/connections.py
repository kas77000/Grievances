"""kdb+ connection registry and pykx transport.

Connections are stored as {name, host, port, region, kind} where kind is 'rdb' (live) or
'hdb' (historical). The registry persists to config/connections.json. pykx is imported
lazily so the app runs fine without it (mock-data mode).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "connections.json"

REGIONS = ["US", "EU", "AP"]
KINDS = ["rdb", "hdb"]  # rdb = live/real-time, hdb = historical


@dataclass
class Connection:
    name: str
    host: str
    port: int
    region: str = "AP"
    kind: str = "hdb"  # 'rdb' (live) or 'hdb' (historical)
    username: str = ""
    password_env: str = ""  # env var holding the password

    def key(self) -> str:
        return self.name.strip()


# ----------------------------- registry (persisted) -----------------------------
def load_connections() -> list[Connection]:
    if not CONFIG_PATH.exists():
        return []
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    out = []
    for r in raw:
        try:
            out.append(Connection(
                name=str(r["name"]), host=str(r["host"]), port=int(r["port"]),
                region=str(r.get("region", "AP")), kind=str(r.get("kind", "hdb")),
            ))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def save_connections(conns: list[Connection]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps([asdict(c) for c in conns], indent=2), encoding="utf-8"
    )


def upsert_connection(conns: list[Connection], new: Connection) -> list[Connection]:
    out = [c for c in conns if c.key() != new.key()]
    out.append(new)
    return out


def delete_connection(conns: list[Connection], name: str) -> list[Connection]:
    return [c for c in conns if c.key() != name.strip()]


# ----------------------------- pykx transport -----------------------------
def pykx_available() -> bool:
    try:
        import pykx  # noqa: F401
        return True
    except Exception:
        return False


def test_connection(conn: Connection, timeout_s: float = 3.0) -> tuple[bool, str]:
    """Open a short-lived pykx connection and run a trivial query."""
    if not pykx_available():
        return False, "pykx not installed (running in mock-data mode)."
    try:
        import pykx as kx
        with kx.QConnection(host=conn.host, port=int(conn.port), timeout=timeout_s) as q:
            val = q("1+1").py()
        ok = (val == 2)
        return ok, "Connected." if ok else "Connected but sanity query failed."
    except Exception as e:  # noqa: BLE001
        return False, f"{type(e).__name__}: {e}"


class KdbClient:
    """Thin holder around a pykx QConnection. Opened on demand, reused across queries."""

    def __init__(self, conn: Connection):
        self.conn = conn
        self._q = None

    def _handle(self):
        if self._q is None:
            import os
            import pykx as kx
            kwargs = {"host": self.conn.host, "port": int(self.conn.port)}
            if self.conn.username:
                kwargs["username"] = self.conn.username
                kwargs["password"] = os.environ.get(self.conn.password_env, "") if self.conn.password_env else ""
            self._q = kx.QConnection(**kwargs)
        return self._q

    def query(self, expr: str, **kwargs):
        """Run a q expression, return a pandas DataFrame when possible."""
        q = self._handle()
        res = q(expr, **kwargs) if kwargs else q(expr)
        try:
            return res.pd()
        except Exception:
            return res

    def close(self):
        if self._q is not None:
            try:
                self._q.close()
            except Exception:
                pass
            self._q = None
