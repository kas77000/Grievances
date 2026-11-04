import base64
import datetime as dt
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import order_watchdog as w  # noqa: E402


class FakeFrame:
    """Just enough of a DataFrame for the watchdog (no pandas needed in tests)."""

    def __init__(self, rows):
        self.rows = rows
        self.columns = list(rows[0]) if rows else []

    def __len__(self):
        return len(self.rows)

    def to_dict(self, orient):
        return list(self.rows)

    def head(self, n):
        return FakeFrame(self.rows[:n])

    def to_csv(self, path, index=False):
        Path(path).write_text(",".join(self.columns) + "\n", encoding="utf-8")


class FakeClient:
    def __init__(self, conn):
        self.conn = conn
        self.queries = []

    def query(self, q):
        self.queries.append(q)
        if q.startswith("select date,id_server,id_target,sym from target"):
            return FakeFrame([{"date": None, "id_server": 7, "id_target": 123, "sym": "6758.T"}])
        if "from target_state" in q:
            return FakeFrame([{"make": 100, "state": "working"}, {"make": 400, "state": "working"}])
        if "from target " in q:
            return FakeFrame([{"sym": "6758.T", "side": "buy", "size": 1000, "algo": "VWAP"}])
        if "from workorder" in q:
            return FakeFrame([{"id_work": 1}, {"id_work": 2}])
        if "from alerts" in q:
            return FakeFrame([{"alertstr": "behind schedule"}])
        if "from execution" in q:
            raise RuntimeError("'execution")
        return FakeFrame([])

    def close(self):
        pass


def make_cfg(root: Path, **kw) -> w.Config:
    cfg = w.Config(inbox=root / "inbox", processed=root / "processed", failed=root / "failed",
                   settle_seconds=0, **kw)
    for d in (cfg.inbox, cfg.processed, cfg.failed):
        d.mkdir(parents=True)
    return cfg


def write_ticket(cfg, raw, sep="\t", **extra):
    ticket = {
        "schema": 1, "id": "20261105-101500-abc123", "created_at": "2026-11-05T01:15:00.000Z",
        "reporter": "Dana", "severity": "high", "title": "Order stuck", "description": "40% filled <b>",
        "data": {"raw": raw, "separator": sep, "has_header": True},
        "attachments": [{"name": "../evil name.png", "type": "image/png", "size": 3,
                         "data_base64": base64.b64encode(b"PNG").decode()}],
    }
    ticket.update(extra)
    p = cfg.inbox / (ticket["id"] + w.TICKET_SUFFIX)
    p.write_text(json.dumps(ticket), encoding="utf-8")
    old = time.time() - 120
    os.utime(p, (old, old))
    return p


class ParsingTests(unittest.TestCase):
    def test_quoted_fields_and_header(self):
        header, rows = w.parse_rows('Target ID;Sym;Note\n123;6758.T;"a;b"\n', ";", True)
        self.assertEqual(header, ["Target ID", "Sym", "Note"])
        self.assertEqual(rows, [{"Target ID": "123", "Sym": "6758.T", "Note": "a;b"}])

    def test_no_header_and_ragged(self):
        header, rows = w.parse_rows("1\t2\n3\n", "\t", False)
        self.assertEqual(header, ["col1", "col2"])
        self.assertEqual(rows[1], {"col1": "3", "col2": ""})

    def test_extract_orders_aliases_dates_and_rejects_injection(self):
        cfg = w.Config(inbox=Path("."), processed=Path("."), failed=Path("."))
        header, rows = w.parse_rows(
            "Order ID\tServer\tTrade Date\tSymbol\n1,234\t7\t2026.11.04\tAAPL\nabc\t\t\t\n"
            "55\t\t\tx\";exit 0;\"\n", "\t", True)
        orders, warnings = w.extract_orders(header, rows, cfg, dt.date(2026, 11, 5))
        self.assertEqual(orders[0], w.OrderRef(dt.date(2026, 11, 4), "AP", 1234, 7, None, "AAPL"))
        self.assertEqual(orders[1].sym, None)  # unsafe symbol dropped, order kept
        self.assertEqual(orders[1].date, dt.date(2026, 11, 5))
        self.assertTrue(any("Row 2" in x for x in warnings))

    def test_trading_platform_copy(self):
        """Layout of a real platform copy: tabs, trailing spaces, empty fields, a stray tab per line."""
        raw = ("TgtID\tTgtStatus\tBasketID\tHALTST\tPendingCancel\tClientID\tManagerName\tTgtTime\tEST_STIME\tLFTIME\t\r\n"
               "400002029\tRejected\tSILK-ALPHAGRP.SYN.SG.A \t\tN\tALPHAGRP.SYN.SG.A\tAPIC\t20261104-05:45:02.720\t\t\t\r\n"
               "700001063\tRejected\tSILK-ALPHAGRP.SYN.SG.A \t\tN\tALPHAGRP.SYN.SG.A\tAPIH\t20261104-06:19:41.729\t\t\t\r\n"
               "400002453\tCancel\tSILK-ALPHAGRP.SYN.SG.A \t\tN\tALPHAGRP.SYN.SG.A\tAPIC\t20261104-06:19:54.535\t\t\t\r\n"
               "700001075\tFilled\tSILK-ALPHAGRP.SYN.SG.A \t\tN\tALPHAGRP.SYN.SG.A\tAPIH\t20261104-06:23:50.190\t\t\t\r\n")
        header, rows = w.parse_rows(raw, "\t", True)
        self.assertEqual(len(header), 10)  # trailing tab doesn't add an empty column
        self.assertEqual(header[0], "TgtID")
        self.assertEqual(rows[0]["HALTST"], "")
        cfg = w.Config(inbox=Path("."), processed=Path("."), failed=Path("."))
        orders, warnings = w.extract_orders(header, rows, cfg, dt.date(2026, 11, 5))
        self.assertEqual([o.id_target for o in orders], [400002029, 700001063, 400002453, 700001075])
        self.assertTrue(all(o.date == dt.date(2026, 11, 4) for o in orders))  # from TgtTime, not the report date
        self.assertEqual(warnings, [])

    def test_render_q_skips_when_value_missing(self):
        ref = w.OrderRef(dt.date(2026, 1, 2), "AP", id_target=5)
        self.assertEqual(w.render_q("select from t where date={{date}}, id_target={{id_target}}", ref),
                         "select from t where date=2026.01.02, id_target=5")
        self.assertIsNone(w.render_q("x={{id_server}}", ref))

    def test_pick_connection_rdb_today_hdb_past(self):
        cfg = w.Config(inbox=Path("."), processed=Path("."), failed=Path("."), connections=[
            w.Connection("AP-RDB", "h", 1, "AP", "rdb"), w.Connection("AP-HDB", "h", 2, "AP", "hdb")])
        today = dt.date(2026, 11, 5)
        self.assertEqual(w.pick_connection(cfg, "AP", today, today).name, "AP-RDB")
        self.assertEqual(w.pick_connection(cfg, "AP", dt.date(2026, 11, 4), today).name, "AP-HDB")


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ticket_without_kdb(self):
        cfg = make_cfg(self.root)
        write_ticket(cfg, "Order ID\tSym\n123\t6758.T\n")
        self.assertEqual(w.poll_once(cfg), 1)
        self.assertEqual(list(cfg.inbox.iterdir()), [])
        work = cfg.processed / "2026-11-05" / "20261105-101500-abc123"
        self.assertTrue((work / "original.ticket.json").exists())
        self.assertEqual((work / "attachments" / "01_evil name.png").read_bytes(), b"PNG")
        meta = json.loads((work / "ticket.json").read_text(encoding="utf-8"))
        self.assertNotIn("data_base64", meta["attachments"][0])
        report = (work / "report.html").read_text(encoding="utf-8")
        self.assertIn("40% filled &lt;b&gt;", report)
        self.assertIn("kdb+ lookups are disabled", report)

    def test_ticket_with_kdb_resolves_and_summarises(self):
        cfg = make_cfg(self.root, kdb_enabled=True,
                       connections=[w.Connection("AP-HDB", "h", 1, "AP", "hdb")],
                       resolve_by_id_target="select date,id_server,id_target,sym from target where date={{date}}, id_target={{id_target}}",
                       queries={
                           "target": "select from target where date={{date}}, id_server={{id_server}}, id_target={{id_target}}",
                           "target_state": "select from target_state where date={{date}}, id_server={{id_server}}, id_target={{id_target}}",
                           "workorder": "select from workorder where date={{date}}, id_server={{id_server}}, id_target={{id_target}}",
                           "execution": "select from execution where date={{date}}, id_server={{id_server}}, id_target={{id_target}}",
                           "alerts": "select from alerts where date={{date}}, id_server={{id_server}}, id_target={{id_target}}",
                       })
        write_ticket(cfg, "Target ID,Trade Date\n123,2026-11-04\n", sep=",")
        clients = []
        n = w.poll_once(cfg, today=dt.date(2026, 11, 5),
                        client_factory=lambda c: clients.append(FakeClient(c)) or clients[-1])
        self.assertEqual(n, 1)
        work = cfg.processed / "2026-11-05" / "20261105-101500-abc123"
        report = (work / "report.html").read_text(encoding="utf-8")
        self.assertIn("6758.T buy 1000 VWAP, filled 400/1000 (40%), state=working, 2 child orders, "
                      "1 alerts (last: behind schedule)", report)
        self.assertIn("execution: RuntimeError", report)  # one failing query doesn't sink the ticket
        self.assertTrue((work / "kdb" / "20261104_7_123" / "target.csv").exists())
        self.assertIn("date=2026.11.04, id_server=7, id_target=123", clients[0].queries[1])

    def test_garbage_ticket_is_quarantined(self):
        cfg = make_cfg(self.root)
        p = cfg.inbox / ("bad" + w.TICKET_SUFFIX)
        p.write_text("{not json", encoding="utf-8")
        old = time.time() - 120
        os.utime(p, (old, old))
        w.poll_once(cfg)
        self.assertTrue((cfg.failed / p.name).exists())
        self.assertTrue((cfg.failed / (p.name + ".error.txt")).exists())

    def test_fresh_partial_ticket_is_left_for_retry(self):
        cfg = make_cfg(self.root)
        p = cfg.inbox / ("partial" + w.TICKET_SUFFIX)
        p.write_text('{"id": ', encoding="utf-8")
        w.poll_once(cfg)
        self.assertTrue(p.exists())

    def test_example_config_loads(self):
        cfg = w.load_config(Path(__file__).resolve().parent.parent / "config.example.toml")
        self.assertEqual([c.kind for c in cfg.connections], ["rdb", "hdb"])
        self.assertIn("alerts", cfg.queries)
        self.assertEqual(str(cfg.inbox), r"\\fileserver\share\OrderAlerts\inbox")


if __name__ == "__main__":
    unittest.main()
