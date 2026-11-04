"""Create demo issues for the Desk by sending sample tickets through the real watchdog.

    python demo/seed_demo.py          # from desk/; wipes and recreates demo/data
"""
from __future__ import annotations

import base64
import datetime as dt
import json
import shutil
import sys
from pathlib import Path

DEMO = Path(__file__).resolve().parent
sys.path.insert(0, str(DEMO.parent))
from core import issues as I  # noqa: E402
from core.settings import DEMO_CONFIG, wd  # noqa: E402

NOW = dt.datetime.now(dt.timezone.utc)

TICKETS = [
    dict(minutes_ago=12, reporter="Kenji (AP Ops)", severity="critical",
         title="Toyota VWAP sell-short way behind schedule",
         description="Client is chasing. Order is ~30% behind the VWAP curve since the open and the stock "
                     "is down hard. Is the short-sell price restriction blocking us?",
         raw="Target ID\tServer\tSymbol\tSide\tQty\tTrade Date\n100731\t7\t7203.T\tSS\t180000\t2026.07.17\n",
         sep="\t", events=[]),
    dict(minutes_ago=48, reporter="Mei (HK desk)", severity="high",
         title="Tencent buy not filling near the limit",
         description="Limit 380, stock trading 372–375 but fills are very slow. Engine says working.",
         raw="Target ID;Symbol;Limit\n100742;0700.HK;380\n", sep=";",
         events=[("Bob", "assign", "Bob", ""), ("Bob", "status", "investigating", "Looking at the routing now.")]),
    dict(minutes_ago=180, reporter="Sam (US PT)", severity="medium",
         title="AAPL VWAP fills look expensive vs benchmark",
         description="Slippage looks worse than usual today, can you check?",
         raw="Order ID,Sym,Algo\n100118,AAPL,VWAP\n", sep=",",
         events=[("Alice", "assign", "Alice", ""), ("Alice", "status", "investigating", ""),
                 ("Alice", "comment", "", "Spread was wide on the open, fills mostly passive afterwards."),
                 ("Alice", "status", "resolved", "Within expected range; explained to Sam.")]),
    dict(minutes_ago=25, reporter="Kenji (AP Ops)", severity="low",
         title="Basket export from the other system",
         description="Not sure which column is the order id here.",
         raw="Ref|Ticker|Comment\nA-1|7203.T|late\n", sep="|", events=[]),
]


def main():
    data = DEMO / "data"
    shutil.rmtree(data, ignore_errors=True)
    cfg = wd.load_config(DEMO_CONFIG)
    for d in (cfg.inbox, cfg.processed, cfg.failed):
        d.mkdir(parents=True)

    log_txt = base64.b64encode(b"09:00:01 order accepted\n09:31:12 child rejected: price outside band\n").decode()
    for n, t in enumerate(TICKETS):
        created = NOW - dt.timedelta(minutes=t["minutes_ago"])
        tid = f"{created.astimezone():%Y%m%d-%H%M%S}-demo{n}"
        ticket = {
            "schema": 1, "id": tid, "created_at": created.isoformat().replace("+00:00", "Z"),
            "created_local": created.astimezone().strftime("%a %b %d %Y %H:%M:%S"),
            "reporter": t["reporter"], "severity": t["severity"], "title": t["title"],
            "description": t["description"],
            "data": {"raw": t["raw"], "separator": t["sep"], "has_header": True},
            "attachments": [{"name": "platform-log.txt", "type": "text/plain", "size": 64,
                             "data_base64": log_txt}] if n == 0 else [],
        }
        (cfg.inbox / (tid + wd.TICKET_SUFFIX)).write_text(json.dumps(ticket), encoding="utf-8")

    cfg.settle_seconds = 0
    print(f"watchdog processed {wd.poll_once(cfg)} demo ticket(s)")

    for issue in I.list_issues(cfg.processed):
        t = TICKETS[int(issue.id.rsplit("demo", 1)[1])]
        for k, (user, kind, value, text) in enumerate(t["events"]):
            ts = NOW - dt.timedelta(minutes=t["minutes_ago"]) + dt.timedelta(minutes=5 + 7 * k)
            with open(issue.folder / I.EVENTS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": ts.isoformat(), "user": user, "kind": kind,
                                    "value": value, "text": text}) + "\n")
    print(f"demo issues in {cfg.processed}")


if __name__ == "__main__":
    main()
