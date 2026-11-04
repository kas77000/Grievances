"""Order-alert watchdog.

Polls the shared inbox for *.ticket.json files written by web/order-alert.html. Each ticket is
claimed (atomic rename, so several watchdogs can share one inbox), unpacked into its own folder,
enriched with the reported orders pulled from kdb+ (RDB for today, HDB for prior dates), turned
into an HTML report and emailed to the team. pykx and pandas are imported lazily so the watchdog
also runs without them (report + email only).

    python order_watchdog.py -c config.toml          # run forever
    python order_watchdog.py -c config.toml --once   # process what is in the inbox, then exit
"""
from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import html
import io
import json
import logging
import logging.handlers
import os
import re
import shutil
import smtplib
import socket
import time
import tomllib
from dataclasses import dataclass, field
from email.message import EmailMessage
from pathlib import Path
from typing import Callable, Optional

log = logging.getLogger("order_watchdog")

TICKET_SUFFIX = ".ticket.json"
CLAIM_SUFFIX = f".{socket.gethostname()}.processing"
NOT_READY_GRACE_S = 60  # an unparsable ticket younger than this is assumed to be still copying
SAFE_TOKEN = re.compile(r"^[A-Za-z0-9_.:/#\-]{1,64}$")  # what we allow inside a q symbol literal

DEFAULT_COLUMNS = {
    "id_target": ["id_target", "target id", "targetid", "tgtid", "order id", "orderid"],
    "id_server": ["id_server", "server id", "serverid", "server"],
    "oes_oid": ["oes_oid", "clordid", "client order id", "oes oid", "ext order id"],
    "sym": ["sym", "symbol", "ticker", "ric"],
    "date": ["date", "trade date", "tradedate", "tgttime"],
    "region": ["region"],
}
DEFAULT_DATE_FORMATS = ["%Y.%m.%d", "%Y-%m-%d", "%Y%m%d", "%Y%m%d-%H:%M:%S.%f", "%Y%m%d-%H:%M:%S", "%d/%m/%Y", "%d-%m-%Y"]


# ----------------------------- config -----------------------------
@dataclass
class Connection:
    name: str
    host: str
    port: int
    region: str = "AP"
    kind: str = "hdb"  # 'rdb' (live, today) or 'hdb' (historical)
    username: str = ""
    password_env: str = ""  # name of the env var holding the password, never the password itself


@dataclass
class Config:
    inbox: Path
    processed: Path
    failed: Path
    log_file: Optional[Path] = None
    poll_seconds: float = 5.0
    settle_seconds: float = 2.0
    columns: dict[str, list[str]] = field(default_factory=lambda: dict(DEFAULT_COLUMNS))
    date_formats: list[str] = field(default_factory=lambda: list(DEFAULT_DATE_FORMATS))
    default_region: str = "AP"
    max_orders: int = 20
    kdb_enabled: bool = False
    kdb_timeout: float = 30.0
    connections: list[Connection] = field(default_factory=list)
    resolve_by_id_target: str = ""
    resolve_by_oes_oid: str = ""
    queries: dict[str, str] = field(default_factory=dict)  # table name -> q template, in order
    email: dict = field(default_factory=dict)


def load_config(path: Path) -> Config:
    raw = tomllib.loads(path.read_text(encoding="utf-8"))
    base = path.parent
    p = raw.get("paths", {})

    def resolve(v):
        return (base / v) if v and not Path(v).is_absolute() else (Path(v) if v else None)

    d = raw.get("data", {})
    k = raw.get("kdb", {})
    columns = dict(DEFAULT_COLUMNS)
    columns.update({key: list(v) for key, v in d.get("columns", {}).items()})
    return Config(
        inbox=resolve(p["inbox"]),
        processed=resolve(p["processed"]),
        failed=resolve(p["failed"]),
        log_file=resolve(p.get("log_file")),
        poll_seconds=float(raw.get("watch", {}).get("poll_seconds", 5)),
        settle_seconds=float(raw.get("watch", {}).get("settle_seconds", 2)),
        columns=columns,
        date_formats=list(d.get("date_formats", DEFAULT_DATE_FORMATS)),
        default_region=str(d.get("default_region", "AP")),
        max_orders=int(d.get("max_orders", 20)),
        kdb_enabled=bool(k.get("enabled", False)),
        kdb_timeout=float(k.get("timeout", 30)),
        connections=[Connection(**c) for c in k.get("connections", [])],
        resolve_by_id_target=k.get("resolve", {}).get("by_id_target", ""),
        resolve_by_oes_oid=k.get("resolve", {}).get("by_oes_oid", ""),
        queries=dict(k.get("queries", {})),
        email=dict(raw.get("notify", {}).get("email", {})),
    )


# ----------------------------- pasted data -----------------------------
def parse_rows(raw: str, sep: str, has_header: bool) -> tuple[list[str], list[dict[str, str]]]:
    """Parse the pasted block with the same quoting rules as the web form."""
    sep = sep or "\t"
    if len(sep) == 1:
        rows = list(csv.reader(io.StringIO(raw), delimiter=sep))
    else:
        rows = [line.split(sep) for line in raw.splitlines()]
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return [], []
    # Grids often end each copied line with a separator: drop trailing columns that are empty everywhere.
    width = max(i + 1 for r in rows for i, c in enumerate(r) if c.strip())
    rows = [r[:width] for r in rows]
    if has_header:
        header, body = [h.strip() for h in rows[0]], rows[1:]
    else:
        header, body = [], rows
    header += [""] * (width - len(header))
    seen: dict[str, int] = {}
    for i, h in enumerate(header):
        h = h or f"col{i + 1}"
        seen[h] = seen.get(h, 0) + 1
        header[i] = h if seen[h] == 1 else f"{h}_{seen[h]}"
    return header, [dict(zip(header, r + [""] * (width - len(r)))) for r in body]


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def find_column(header: list[str], aliases: list[str]) -> Optional[str]:
    wanted = {_norm(a) for a in aliases}
    return next((h for h in header if _norm(h) in wanted), None)


def parse_date(value: str, formats: list[str]) -> Optional[dt.date]:
    v = value.strip()
    for candidate in (v, v[:10], v.split(" ")[0], v.split("T")[0]):
        for fmt in formats:
            try:
                return dt.datetime.strptime(candidate, fmt).date()
            except ValueError:
                continue
    return None


def parse_int(value: str) -> Optional[int]:
    v = value.strip().replace(",", "").replace(" ", "")
    return int(float(v)) if re.fullmatch(r"\d+(\.0+)?", v) else None


def safe_token(value: str) -> Optional[str]:
    v = value.strip()
    return v if SAFE_TOKEN.match(v) else None


@dataclass(frozen=True)
class OrderRef:
    date: dt.date
    region: str
    id_target: Optional[int] = None
    id_server: Optional[int] = None
    oes_oid: Optional[str] = None
    sym: Optional[str] = None

    def label(self) -> str:
        if self.id_target is not None:
            return f"{self.date:%Y%m%d}_{self.id_server if self.id_server is not None else 'x'}_{self.id_target}"
        return f"{self.date:%Y%m%d}_oid_{re.sub(r'[^A-Za-z0-9_-]', '_', self.oes_oid or '')}"


def extract_orders(header: list[str], rows: list[dict[str, str]], cfg: Config,
                   default_date: dt.date) -> tuple[list[OrderRef], list[str]]:
    cols = {key: find_column(header, aliases) for key, aliases in cfg.columns.items()}
    warnings: list[str] = []
    if not (cols.get("id_target") or cols.get("oes_oid")):
        if rows:
            warnings.append("No order id column recognised. Columns in the pasted data: "
                            + ", ".join(f"'{h}'" for h in header)
                            + ". Add the right one to [data.columns] id_target in the watchdog config.")
        return [], warnings

    out: list[OrderRef] = []
    for i, r in enumerate(rows, start=1):
        get = lambda key: r.get(cols[key], "") if cols.get(key) else ""  # noqa: E731
        id_target = parse_int(get("id_target")) if cols.get("id_target") else None
        oes_oid = safe_token(get("oes_oid")) if cols.get("oes_oid") else None
        if id_target is None and oes_oid is None:
            warnings.append(f"Row {i}: no usable order id, skipped.")
            continue
        date = parse_date(get("date"), cfg.date_formats) if get("date") else None
        sym = safe_token(get("sym")) if get("sym") else None
        ref = OrderRef(
            date=date or default_date,
            region=(get("region").strip().upper() or cfg.default_region),
            id_target=id_target,
            id_server=parse_int(get("id_server")) if get("id_server") else None,
            oes_oid=oes_oid,
            sym=sym,
        )
        if ref not in out:
            out.append(ref)
    if len(out) > cfg.max_orders:
        warnings.append(f"{len(out)} orders found; only the first {cfg.max_orders} were queried.")
        out = out[:cfg.max_orders]
    return out, warnings


# ----------------------------- kdb+ -----------------------------
def pykx_available() -> bool:
    try:
        import pykx  # noqa: F401
        return True
    except Exception:
        return False


class KdbClient:
    """Thin holder around a pykx QConnection. Opened on demand, reused across queries."""

    def __init__(self, conn: Connection, timeout: float = 30.0):
        self.conn = conn
        self.timeout = timeout
        self._q = None

    def _handle(self):
        if self._q is None:
            import pykx as kx
            kwargs = {"host": self.conn.host, "port": int(self.conn.port), "timeout": self.timeout}
            if self.conn.username:
                kwargs["username"] = self.conn.username
                kwargs["password"] = os.environ.get(self.conn.password_env, "") if self.conn.password_env else ""
            self._q = kx.QConnection(**kwargs)
        return self._q

    def query(self, expr: str):
        """Run a q expression, return a pandas DataFrame when possible."""
        res = self._handle()(expr)
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


def pick_connection(cfg: Config, region: str, date: dt.date, today: dt.date) -> Optional[Connection]:
    kind = "rdb" if date >= today else "hdb"
    for match in (lambda c: c.region == region and c.kind == kind,
                  lambda c: c.kind == kind,
                  lambda c: True):
        found = next((c for c in cfg.connections if match(c)), None)
        if found:
            return found
    return None


def q_date(d: dt.date) -> str:
    return f"{d.year:04d}.{d.month:02d}.{d.day:02d}"


def render_q(template: str, ref: OrderRef) -> Optional[str]:
    """Fill {{placeholders}}. Returns None when the template needs a value the order lacks.

    Only validated values reach the q text: ints, q date literals, and symbols matching SAFE_TOKEN.
    """
    values = {
        "date": q_date(ref.date),
        "id_target": str(ref.id_target) if ref.id_target is not None else None,
        "id_server": str(ref.id_server) if ref.id_server is not None else None,
        "oes_oid": f'`$"{ref.oes_oid}"' if ref.oes_oid else None,
        "sym": f'`$"{ref.sym}"' if ref.sym else None,
    }
    out = template
    for name in re.findall(r"\{\{(\w+)\}\}", template):
        if values.get(name) is None:
            return None
        out = out.replace("{{" + name + "}}", values[name])
    return out


def _records(df) -> list[dict]:
    try:
        return df.to_dict("records")
    except Exception:
        return []


def _decode(v):
    return v.decode() if isinstance(v, bytes) else v


def resolve_orders(client: KdbClient, ref: OrderRef, cfg: Config) -> list[OrderRef]:
    """Fill in id_server (and id_target from oes_oid) via the target table. May fan out."""
    if ref.id_target is not None and ref.id_server is not None:
        return [ref]
    template = cfg.resolve_by_id_target if ref.id_target is not None else cfg.resolve_by_oes_oid
    q = render_q(template, ref) if template else None
    if not q:
        return [ref]
    found = []
    for row in _records(client.query(q)):
        found.append(OrderRef(
            date=ref.date, region=ref.region,
            id_target=int(row["id_target"]), id_server=int(row["id_server"]),
            oes_oid=ref.oes_oid, sym=_decode(row.get("sym")) or ref.sym,
        ))
    return found or [ref]


@dataclass
class OrderResult:
    ref: OrderRef
    connection: str = ""
    tables: dict = field(default_factory=dict)   # name -> DataFrame (or raw q result)
    errors: dict = field(default_factory=dict)   # name -> message
    headline: str = ""


def summarise(res: OrderResult) -> str:
    """One line a human can act on: what the order is and how far it got."""
    parts = []
    target = _records(res.tables.get("target"))
    if target:
        t = target[0]
        parts.append(" ".join(str(_decode(t.get(c, ""))) for c in ("sym", "side", "size", "algo") if t.get(c) is not None))
    state = _records(res.tables.get("target_state"))
    if state:
        last = state[-1]
        make, size = last.get("make"), (target[0].get("size") if target else None)
        if make is not None and size:
            parts.append(f"filled {make}/{size} ({float(make) / float(size):.0%})")
        if last.get("state") is not None:
            parts.append(f"state={_decode(last['state'])}")
    if "workorder" in res.tables:
        parts.append(f"{len(_records(res.tables['workorder']))} child orders")
    alerts = _records(res.tables.get("alerts"))
    if "alerts" in res.tables:
        parts.append(f"{len(alerts)} alerts" + (f" (last: {_decode(alerts[-1].get('alertstr', ''))})" if alerts else ""))
    if not target and "target" in res.tables:
        parts.append("NOT FOUND in target")
    return ", ".join(p for p in parts if p) or "no data"


def enrich(orders: list[OrderRef], cfg: Config, today: dt.date,
           client_factory: Callable[[Connection], KdbClient]) -> list[OrderResult]:
    clients: dict[str, KdbClient] = {}
    results: list[OrderResult] = []
    try:
        for ref in orders:
            conn = pick_connection(cfg, ref.region, ref.date, today)
            if conn is None:
                results.append(OrderResult(ref, errors={"connection": "no kdb+ connection configured"}))
                continue
            client = clients.setdefault(conn.name, client_factory(conn))
            try:
                resolved = resolve_orders(client, ref, cfg)
            except Exception as e:  # noqa: BLE001
                results.append(OrderResult(ref, conn.name, errors={"resolve": f"{type(e).__name__}: {e}"}))
                continue
            for r in resolved:
                res = OrderResult(r, conn.name)
                for name, template in cfg.queries.items():
                    q = render_q(template, r)
                    if q is None:
                        res.errors[name] = "skipped: order is missing a value this query needs"
                        continue
                    try:
                        res.tables[name] = client.query(q)
                    except Exception as e:  # noqa: BLE001
                        res.errors[name] = f"{type(e).__name__}: {e}"
                res.headline = summarise(res)
                results.append(res)
    finally:
        for c in clients.values():
            c.close()
    return results


# ----------------------------- report -----------------------------
def _table_html(header: list[str], rows: list[dict], limit: int = 200) -> str:
    if not rows:
        return "<p class='muted'>(empty)</p>"
    head = "".join(f"<th>{html.escape(h)}</th>" for h in header)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(str(r.get(h, '')))}</td>" for h in header) + "</tr>"
                   for r in rows[:limit])
    more = f"<p class='muted'>… {len(rows) - limit} more rows in the CSV</p>" if len(rows) > limit else ""
    return f"<div class='scroll'><table><tr>{head}</tr>{body}</table></div>{more}"


def _result_html(obj, limit: int = 50) -> str:
    try:
        cols = [str(c) for c in obj.columns]
        rows = [{str(k): _decode(v) for k, v in r.items()} for r in obj.head(limit).to_dict("records")]
        extra = f"<p class='muted'>{len(obj)} rows, first {limit} shown; full data in the CSV</p>" if len(obj) > limit else ""
        return _table_html(cols, rows, limit) + extra
    except Exception:
        return f"<pre>{html.escape(str(obj))}</pre>"


REPORT_CSS = """
body{font:14px/1.5 'Segoe UI',system-ui,sans-serif;color:#1d2330;background:#fff;margin:24px;max-width:1100px}
h1{font-size:20px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px;border-bottom:1px solid #d9dce1;padding-bottom:4px}
h3{font-size:14px;margin:16px 0 4px}.muted{color:#5f6878;font-size:12px}
.sev{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600;background:#eef1f5}
.sev-high,.sev-critical{background:#fde8e8;color:#a11d1d}.sev-medium{background:#fff4e0;color:#8a4b00}
.scroll{overflow:auto;max-height:420px;border:1px solid #d9dce1;border-radius:6px}
table{border-collapse:collapse;font-size:12px}th,td{border-bottom:1px solid #e5e7eb;padding:3px 8px;text-align:left;white-space:nowrap}
th{background:#f0f2f5;position:sticky;top:0}pre{white-space:pre-wrap;background:#f6f7f9;padding:10px;border-radius:6px}
.headline{font-weight:600}.err{color:#a11d1d;font-size:12px}img{max-width:100%;border:1px solid #d9dce1;border-radius:6px;margin:4px 0}
"""


def render_report(ticket: dict, header: list[str], rows: list[dict], warnings: list[str],
                  results: list[OrderResult], attachment_files: list[Path], work_dir: Path,
                  kdb_note: str, for_email: bool = False) -> str:
    e = html.escape
    sev = str(ticket.get("severity", "medium"))
    out = [f"<!doctype html><html><head><meta charset='utf-8'><title>{e(ticket.get('title', ''))}</title>"
           f"<style>{REPORT_CSS}</style></head><body>",
           f"<h1>{e(ticket.get('title', '(no title)'))}</h1>",
           f"<p><span class='sev sev-{e(sev)}'>{e(sev.upper())}</span> &nbsp;reported by <b>{e(ticket.get('reporter', '?'))}</b> "
           f"at {e(ticket.get('created_local') or ticket.get('created_at', ''))} · ref <code>{e(ticket.get('id', ''))}</code></p>",
           f"<p class='muted'>Folder: {e(str(work_dir))}</p>"]
    if ticket.get("description", "").strip():
        out += ["<h2>Description</h2>", f"<pre>{e(ticket['description'])}</pre>"]

    if results:
        out.append("<h2>Orders (kdb+)</h2><ul>")
        out += [f"<li><code>{e(r.ref.label())}</code> — <span class='headline'>{e(r.headline or 'no data')}</span></li>" for r in results]
        out.append("</ul>")
    elif kdb_note:
        out += ["<h2>Orders (kdb+)</h2>", f"<p class='muted'>{e(kdb_note)}</p>"]
    for w in warnings:
        out.append(f"<p class='err'>⚠ {e(w)}</p>")

    if header:
        out += ["<h2>Pasted data</h2>", _table_html(header, rows)]
    if not for_email:
        for r in results:
            out.append(f"<h2>Order {e(r.ref.label())}</h2><p class='muted'>source: {e(r.connection or '-')}</p>")
            for name, msg in r.errors.items():
                out.append(f"<p class='err'>{e(name)}: {e(msg)}</p>")
            for name, obj in r.tables.items():
                out += [f"<h3>{e(name)}</h3>", _result_html(obj)]
    if attachment_files:
        out.append("<h2>Attachments</h2>")
        for f in attachment_files:
            rel = f.relative_to(work_dir).as_posix()
            if not for_email and f.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"}:
                out.append(f"<p>{e(f.name)}<br><img src='{e(rel)}' alt=''></p>")
            else:
                out.append(f"<p>{e(f.name)}{' (attached)' if for_email else ''}</p>")
    out.append("</body></html>")
    return "\n".join(out)


# ----------------------------- notification -----------------------------
def send_email(cfg: Config, ticket: dict, results: list[OrderResult], report_email_html: str,
               work_dir: Path, files: list[Path]) -> None:
    em = cfg.email
    if not em.get("enabled"):
        return
    msg = EmailMessage()
    sev = str(ticket.get("severity", "medium")).upper()
    msg["Subject"] = f"{em.get('subject_prefix', '[Order Alert]')} [{sev}] {ticket.get('title', '')} — {ticket.get('reporter', '')}"
    msg["From"] = em["from"]
    msg["To"] = ", ".join(em["to"])
    lines = [f"{ticket.get('title', '')}", f"Severity: {sev}   Reporter: {ticket.get('reporter', '')}",
             f"Ref: {ticket.get('id', '')}", f"Folder: {work_dir}"]
    if desk_link(cfg, str(ticket.get("id", ""))):
        lines.append(f"Open in Order Alert Desk: {desk_link(cfg, str(ticket.get('id', '')))}")
    lines += ["", ticket.get("description", ""), ""]
    lines += [f"- {r.ref.label()}: {r.headline}" for r in results]
    msg.set_content("\n".join(lines))
    msg.add_alternative(report_email_html, subtype="html")

    budget = int(float(em.get("max_attachment_mb", 10)) * 1024 * 1024)
    for f in files:
        size = f.stat().st_size
        if size > budget:
            log.info("Not attaching %s to email (attachment budget exhausted)", f.name)
            continue
        budget -= size
        maintype, subtype = ("image", f.suffix.lower().lstrip(".").replace("jpg", "jpeg")) \
            if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif"} else ("application", "octet-stream")
        msg.add_attachment(f.read_bytes(), maintype=maintype, subtype=subtype, filename=f.name)

    with smtplib.SMTP(em["smtp_host"], int(em.get("smtp_port", 25)), timeout=30) as s:
        if em.get("starttls"):
            s.starttls()
        if em.get("username"):
            s.login(em["username"], os.environ.get(em.get("password_env", ""), ""))
        s.send_message(msg)


# ----------------------------- ticket processing -----------------------------
def write_orders_json(path: Path, orders: list[OrderRef], results: list[OrderResult], warnings: list[str]) -> None:
    """The order keys the Desk app analyses: resolved ones when kdb+ ran, else what was pasted."""
    def ref_dict(r: OrderRef) -> dict:
        return {"date": r.date.isoformat(), "region": r.region, "id_target": r.id_target,
                "id_server": r.id_server, "oes_oid": r.oes_oid, "sym": r.sym, "label": r.label()}
    items = ([dict(ref_dict(r.ref), headline=r.headline, connection=r.connection) for r in results]
             if results else [ref_dict(r) for r in orders])
    path.write_text(json.dumps({"orders": items, "warnings": warnings}, indent=2), encoding="utf-8")


def desk_link(cfg: Config, ticket_id: str) -> str:
    url = str(cfg.email.get("desk_url", "")).rstrip("/")
    return f"{url}/issue?id={ticket_id}" if url else ""


class TicketNotReady(Exception):
    pass


def safe_name(name: str, fallback: str = "file") -> str:
    name = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(name).name).strip(" .")
    return name[:120] or fallback


def unique_dir(path: Path) -> Path:
    candidate, n = path, 1
    while candidate.exists():
        n += 1
        candidate = path.with_name(f"{path.name}_{n}")
    candidate.mkdir(parents=True)
    return candidate


def ticket_local_date(ticket: dict) -> dt.date:
    try:
        return dt.datetime.fromisoformat(ticket["created_at"].replace("Z", "+00:00")).astimezone().date()
    except Exception:
        return dt.date.today()


def process_ticket(claimed: Path, cfg: Config, *, today: Optional[dt.date] = None,
                   client_factory: Optional[Callable[[Connection], KdbClient]] = None) -> Path:
    ticket = json.loads(claimed.read_text(encoding="utf-8"))
    tid = safe_name(str(ticket.get("id") or claimed.name.split(TICKET_SUFFIX)[0]), "ticket")
    created = ticket_local_date(ticket)
    work = unique_dir(cfg.processed / created.isoformat() / tid)
    log.info("Processing %s -> %s", tid, work)

    attachment_files = []
    if ticket.get("attachments"):
        (work / "attachments").mkdir()
    for i, a in enumerate(ticket.get("attachments", []), start=1):
        target = work / "attachments" / f"{i:02d}_{safe_name(a.get('name', ''), 'attachment')}"
        target.write_bytes(base64.b64decode(a.get("data_base64", "")))
        attachment_files.append(target)

    data = ticket.get("data", {}) or {}
    (work / "pasted_data.txt").write_text(data.get("raw", ""), encoding="utf-8")
    meta = dict(ticket)
    meta["attachments"] = [{k: v for k, v in a.items() if k != "data_base64"} for a in ticket.get("attachments", [])]
    (work / "ticket.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    header, rows = parse_rows(data.get("raw", ""), data.get("separator", "\t"), bool(data.get("has_header", True)))
    orders, warnings = extract_orders(header, rows, cfg, created)

    results: list[OrderResult] = []
    kdb_note = ""
    if not orders:
        kdb_note = "No orders identified in the pasted data."
    elif not cfg.kdb_enabled:
        kdb_note = f"{len(orders)} order(s) identified; kdb+ lookups are disabled in the config."
    elif client_factory is None and not pykx_available():
        kdb_note = "pykx is not installed on the watchdog host; kdb+ lookups skipped."
    else:
        factory = client_factory or (lambda c: KdbClient(c, cfg.kdb_timeout))
        results = enrich(orders, cfg, today or dt.date.today(), factory)
        for r in results:
            for name, obj in r.tables.items():
                if hasattr(obj, "to_csv"):
                    out_dir = work / "kdb" / r.ref.label()
                    out_dir.mkdir(parents=True, exist_ok=True)
                    obj.to_csv(out_dir / f"{name}.csv", index=False)

    write_orders_json(work / "orders.json", orders, results, warnings)
    (work / "report.html").write_text(
        render_report(ticket, header, rows, warnings, results, attachment_files, work, kdb_note), encoding="utf-8")
    shutil.move(str(claimed), str(work / "original.ticket.json"))

    try:
        csvs = sorted((work / "kdb").rglob("*.csv")) if (work / "kdb").exists() else []
        send_email(cfg, ticket, results,
                   render_report(ticket, header, rows, warnings, results, attachment_files, work, kdb_note, for_email=True),
                   work, attachment_files + csvs)
    except Exception as e:  # noqa: BLE001 - the ticket is processed; a mail outage must not re-run it
        log.exception("Email notification failed for %s", tid)
        (work / "notify_error.txt").write_text(f"{type(e).__name__}: {e}\n", encoding="utf-8")
    log.info("Done %s: %d order(s), %s", tid, len(results), "; ".join(r.headline for r in results) or kdb_note)
    return work


def quarantine(claimed: Path, cfg: Config, exc: BaseException) -> None:
    cfg.failed.mkdir(parents=True, exist_ok=True)
    original = claimed.name[: -len(CLAIM_SUFFIX)] if claimed.name.endswith(CLAIM_SUFFIX) else claimed.name
    dest = cfg.failed / original
    if dest.exists():
        dest = cfg.failed / f"{dt.datetime.now():%Y%m%d%H%M%S}_{original}"
    shutil.move(str(claimed), str(dest))
    dest.with_name(dest.name + ".error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")


def release_stale_claims(cfg: Config) -> None:
    """Put back tickets this host claimed but never finished (e.g. it was killed mid-ticket)."""
    for p in cfg.inbox.glob("*" + TICKET_SUFFIX + CLAIM_SUFFIX):
        try:
            p.rename(p.with_name(p.name[: -len(CLAIM_SUFFIX)]))
            log.warning("Released stale claim %s", p.name)
        except OSError:
            pass


def poll_once(cfg: Config, **kwargs) -> int:
    done = 0
    for p in sorted(cfg.inbox.glob("*" + TICKET_SUFFIX)):
        try:
            age = time.time() - p.stat().st_mtime
        except FileNotFoundError:
            continue
        if age < cfg.settle_seconds:
            continue
        claimed = p.with_name(p.name + CLAIM_SUFFIX)
        try:
            p.rename(claimed)  # atomic: if another watchdog got it first, this fails
        except OSError:
            continue
        try:
            process_ticket(claimed, cfg, **kwargs)
            done += 1
        except json.JSONDecodeError as e:
            if age < NOT_READY_GRACE_S:
                claimed.rename(p)  # probably still being copied; retry next poll
                continue
            log.error("Unreadable ticket %s: %s", p.name, e)
            quarantine(claimed, cfg, e)
        except Exception as e:  # noqa: BLE001
            log.exception("Failed to process %s", p.name)
            quarantine(claimed, cfg, e)
    return done


def setup_logging(cfg: Config) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)
    if cfg.log_file:
        cfg.log_file.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(cfg.log_file, maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-c", "--config", default="config.toml", type=Path)
    ap.add_argument("--once", action="store_true", help="process the current inbox and exit")
    args = ap.parse_args(argv)

    cfg = load_config(args.config)
    setup_logging(cfg)
    for d in (cfg.inbox, cfg.processed, cfg.failed):
        d.mkdir(parents=True, exist_ok=True)
    if cfg.kdb_enabled and not pykx_available():
        log.warning("kdb.enabled = true but pykx is not installed; tickets will be reported without kdb+ data")
    release_stale_claims(cfg)
    log.info("Watching %s (poll %ss)", cfg.inbox, cfg.poll_seconds)

    while True:
        try:
            poll_once(cfg)
        except OSError:
            log.exception("Inbox not reachable; retrying")  # share blips must not kill the service
        if args.once:
            return 0
        try:
            time.sleep(cfg.poll_seconds)
        except KeyboardInterrupt:
            log.info("Stopped")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
