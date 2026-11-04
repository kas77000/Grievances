"""Desk tests: the issue store, order resolution, and every page rendered headless (AppTest)."""
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

DESK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(DESK))
os.chdir(DESK)  # AppTest resolves page paths from the working directory

from core import issues as I  # noqa: E402
from core.orders import resolve  # noqa: E402
from core.queries import MockProvider  # noqa: E402


class IssueStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.folder = self.root / "2026-11-05" / "T1"
        self.folder.mkdir(parents=True)
        (self.folder / "ticket.json").write_text(json.dumps({
            "id": "T1", "title": "x", "reporter": "r", "severity": "high",
            "created_at": "2026-11-05T01:00:00Z"}), encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_replay_and_torn_line(self):
        I.append_event(self.folder, "Bob", "assign", "Bob")
        I.append_event(self.folder, "Bob", "status", "investigating")
        with open(self.folder / I.EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write('{"ts": "2026-11-05T02:00:00+00:00", "user": "x", "ki')  # crashed writer
        I.append_event(self.folder, "Alice", "assign", "Alice")
        issue = I.find_issue(self.root, "T1")
        self.assertEqual((issue.status, issue.assignee, len(issue.events)), ("investigating", "Alice", 3))
        self.assertTrue(issue.is_open)

    def test_history_window_and_bad_ids(self):
        old = self.root / "2025-01-01" / "OLD"
        shutil.copytree(self.folder, old)
        ids = [i.id for i in I.list_issues(self.root, days=30, today=dt.date(2026, 11, 5))]
        self.assertEqual(ids, ["T1"])
        self.assertIsNotNone(I.find_issue(self.root, "OLD"))  # direct links still work past the window
        self.assertIsNone(I.find_issue(self.root, "../2026-11-05/T1"))


class ResolveTests(unittest.TestCase):
    def test_mock_resolve_by_id_target_and_oes_oid(self):
        p = MockProvider()
        self.assertEqual(resolve(p, {"date": "2026-07-17", "id_target": 100731})[0]["id_server"], 7)
        self.assertEqual(resolve(p, {"date": "2026-07-17", "oes_oid": "OES-AAPL-01"})[0]["id_target"], 100118)
        self.assertEqual(resolve(p, {"date": "2026-07-17", "id_target": 1}), [])


class PageTests(unittest.TestCase):
    """Render each page against the seeded demo data and act on an issue."""

    @classmethod
    def setUpClass(cls):
        subprocess.run([sys.executable, "demo/seed_demo.py"], check=True, capture_output=True)
        from streamlit.testing.v1 import AppTest
        cls.AppTest = AppTest
        processed = DESK / "demo" / "data" / "processed"
        cls.ids = {i.title.split()[0]: i.id for i in I.list_issues(processed)}

    def app(self, page=None, **qp):
        at = self.AppTest.from_file("streamlit_app.py", default_timeout=30)
        at.query_params.update({"me": "Chen", **qp})
        at.run()
        if page:
            at.switch_page(page)
            at.run()
        self.assertFalse(at.exception, [e.message for e in at.exception])
        return at

    def text(self, at) -> str:
        return " ".join(m.value for m in at.markdown)

    def test_board(self):
        at = self.app()
        txt = self.text(at)
        self.assertIn("Unassigned", txt)
        self.assertEqual(len(at.dataframe), 1)
        self.assertEqual(len(at.dataframe[0].value), 3)  # 3 open demo issues

    def test_issue_take_it_and_comment(self):
        at = self.app("app_pages/issue.py", id=self.ids["Toyota"])
        self.assertIn("Toyota VWAP", self.text(at))
        next(b for b in at.button if b.label == "Take it").click().run()
        issue = I.find_issue(DESK / "demo" / "data" / "processed", self.ids["Toyota"])
        self.assertEqual((issue.assignee, issue.status), ("Chen", "investigating"))

        at.text_area[0].input("Short-sell restriction confirmed")
        next(s for s in at.selectbox if s.label == "Status").set_value("waiting")
        next(b for b in at.button if b.label == "Save").click().run()
        issue = I.find_issue(DESK / "demo" / "data" / "processed", self.ids["Toyota"])
        self.assertEqual(issue.status, "waiting")
        self.assertEqual(issue.events[-1].text, "Short-sell restriction confirmed")

    def test_analysis_renders_order_monitor(self):
        at = self.app("app_pages/analysis.py", id=self.ids["Toyota"])
        txt = self.text(at)
        self.assertIn("7203.T", txt)
        self.assertIn("Demo data", txt)
        self.assertEqual(len(at.tabs), 6)
        self.assertTrue(any("Situations" in t.label for t in at.tabs))

    def test_analysis_without_orders(self):
        at = self.app("app_pages/analysis.py", id=self.ids["Basket"])
        self.assertIn("No orders to analyse", self.text(at))

    def test_settings(self):
        at = self.app("app_pages/settings.py")
        self.assertIn("demo", self.text(at))


if __name__ == "__main__":
    unittest.main()
