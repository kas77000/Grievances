"""Issue store over the watchdog's processed/ tree.

Every ticket folder processed/<date>/<id>/ is one issue. Handling state (assignee, status,
comments) lives in an append-only events.jsonl in that folder: each team member's app appends
one line per action and the current state is the replay. Concurrent edits never overwrite each
other, and the file doubles as the audit trail.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

STATUSES = ["new", "investigating", "waiting", "resolved"]
OPEN_STATUSES = {"new", "investigating", "waiting"}
STATUS_LABEL = {"new": "New", "investigating": "Investigating", "waiting": "Waiting on others",
                "resolved": "Resolved"}
STATUS_TONE = {"new": "accent", "investigating": "warn", "waiting": "info", "resolved": "up"}
SEVERITIES = ["critical", "high", "medium", "low"]
SEVERITY_TONE = {"critical": "critical", "high": "high", "medium": "warn", "low": "info"}

EVENTS_FILE = "events.jsonl"
_DATE_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass
class Event:
    ts: dt.datetime
    user: str
    kind: str          # 'assign' | 'status' | 'comment'
    value: str = ""
    text: str = ""


@dataclass
class Issue:
    id: str
    folder: Path
    title: str
    reporter: str
    severity: str
    created_at: dt.datetime
    description: str = ""
    data: dict = field(default_factory=dict)
    attachments: list[Path] = field(default_factory=list)
    orders: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    events: list[Event] = field(default_factory=list)
    status: str = "new"
    assignee: str = ""

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    @property
    def updated_at(self) -> dt.datetime:
        return self.events[-1].ts if self.events else self.created_at


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_ts(s: str) -> dt.datetime:
    try:
        ts = dt.datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return ts if ts.tzinfo else ts.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return _now()


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def read_events(folder: Path) -> list[Event]:
    out = []
    try:
        lines = (folder / EVENTS_FILE).read_text(encoding="utf-8").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            e = json.loads(line)
            out.append(Event(_parse_ts(e["ts"]), str(e.get("user", "")), str(e["kind"]),
                             str(e.get("value", "")), str(e.get("text", ""))))
        except (json.JSONDecodeError, KeyError, TypeError):
            continue  # a torn line from a crashed writer must not hide the rest
    return sorted(out, key=lambda e: e.ts)


def replay(events: list[Event]) -> tuple[str, str]:
    status, assignee = "new", ""
    for e in events:
        if e.kind == "status" and e.value in STATUSES:
            status = e.value
        elif e.kind == "assign":
            assignee = e.value
    return status, assignee


def load_issue(folder: Path) -> Optional[Issue]:
    t = _read_json(folder / "ticket.json")
    if not t:
        return None
    orders = _read_json(folder / "orders.json")
    events = read_events(folder)
    status, assignee = replay(events)
    att_dir = folder / "attachments"
    return Issue(
        id=str(t.get("id") or folder.name), folder=folder,
        title=str(t.get("title") or "(no title)"), reporter=str(t.get("reporter", "")),
        severity=str(t.get("severity", "medium")).lower(),
        created_at=_parse_ts(t.get("created_at", "")),
        description=str(t.get("description", "")), data=t.get("data") or {},
        attachments=sorted(att_dir.iterdir()) if att_dir.is_dir() else [],
        orders=list(orders.get("orders", [])), warnings=list(orders.get("warnings", [])),
        events=events, status=status, assignee=assignee,
    )


def _date_dirs(root: Path, days: Optional[int], today: dt.date) -> list[Path]:
    if not root.is_dir():
        return []
    cutoff = today - dt.timedelta(days=days) if days else dt.date.min
    dirs = [d for d in root.iterdir() if d.is_dir() and _DATE_DIR.match(d.name)
            and dt.date.fromisoformat(d.name) >= cutoff]
    return sorted(dirs, reverse=True)


def list_issues(root: Path, days: Optional[int] = 30, today: Optional[dt.date] = None) -> list[Issue]:
    issues = []
    for d in _date_dirs(root, days, today or dt.date.today()):
        for folder in d.iterdir():
            if folder.is_dir():
                issue = load_issue(folder)
                if issue:
                    issues.append(issue)
    return sorted(issues, key=lambda i: i.created_at, reverse=True)


def find_issue(root: Path, issue_id: str) -> Optional[Issue]:
    if not re.fullmatch(r"[A-Za-z0-9._ -]{1,120}", issue_id or ""):
        return None
    for d in _date_dirs(root, None, dt.date.today()):
        if (d / issue_id).is_dir():
            return load_issue(d / issue_id)
    return None


def append_event(folder: Path, user: str, kind: str, value: str = "", text: str = "") -> None:
    line = json.dumps({"ts": _now().isoformat(), "user": user, "kind": kind,
                       "value": value, "text": text}, ensure_ascii=False)
    path = folder / EVENTS_FILE
    try:
        with open(path, "rb") as f:
            f.seek(-1, 2)
            torn = f.read(1) != b"\n"  # a crashed writer left half a line: start on a fresh one
    except OSError:
        torn = False
    with open(path, "a", encoding="utf-8") as f:
        f.write(("\n" if torn else "") + line + "\n")
