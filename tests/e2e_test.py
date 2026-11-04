"""End-to-end test of the whole chain, in a real browser:

    form (order-alert.html) -> ticket in inbox -> watchdog -> Desk board -> issue -> take it / note
    -> Analyze orders -> order monitor view

Also covers the form's validation, separator auto-detect, file + pasted-screenshot attachments and
the download fallback for browsers without folder access. Screenshots go to docs/screenshots/.

    python tests/e2e_test.py          (needs: pip install playwright && playwright install chromium)
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parent.parent
E2E = ROOT / ".e2e"
SHOTS = ROOT / "docs" / "screenshots"
FORM_URL = (ROOT / "web" / "order-alert.html").as_uri()
PORT = 8599
DESK_URL = f"http://localhost:{PORT}"

# Stand-in for the folder the user picks: records what the page writes (the native dialog can't be driven).
FAKE_FOLDER_JS = """
window.__written = {};
window.showDirectoryPicker = async () => ({
  name: "OrderAlerts\\\\inbox", kind: "directory",
  queryPermission: async () => "granted", requestPermission: async () => "granted",
  getFileHandle: async (name) => ({ createWritable: async () => { const parts = [];
    return { write: async (b) => parts.push(b),
             close: async () => { window.__written[name] = await new Blob(parts).text(); } }; } }),
});
"""
PASTED = "Target ID,Symbol,Side,Qty,Trade Date\n100731,7203.T,SS,180000,2026.07.17\n"

results: list[tuple[str, bool, str]] = []


def check(name: str, fn):
    try:
        fn()
        results.append((name, True, ""))
        print(f"  PASS  {name}")
    except Exception as e:  # noqa: BLE001
        results.append((name, False, f"{type(e).__name__}: {e}".splitlines()[0][:300]))
        print(f"  FAIL  {name}: {results[-1][2]}")


def shot(page: Page, name: str, full: bool = False):
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)


def setup_share():
    shutil.rmtree(E2E, ignore_errors=True)
    for d in ("inbox", "processed", "failed"):
        (E2E / d).mkdir(parents=True)
    subprocess.run([sys.executable, str(ROOT / "desk" / "demo" / "seed_demo.py")], check=True, capture_output=True)
    shutil.copytree(ROOT / "desk" / "demo" / "data" / "processed", E2E / "processed", dirs_exist_ok=True)
    (E2E / "config.toml").write_text(f"""
[paths]
inbox = "inbox"
processed = "processed"
failed = "failed"
log_file = "watchdog.log"
[data]
default_region = "AP"
[kdb]
enabled = false
[desk]
team = ["Alice", "Bob", "Chen"]
history_days = 30
""", encoding="utf-8")


# ---------------------------------------------------------------- 1. the form
def form_tests(pw):
    browser = pw.chromium.launch()
    ctx = browser.new_context(viewport={"width": 1000, "height": 1250}, color_scheme="light")
    ctx.add_init_script(FAKE_FOLDER_JS)
    page = ctx.new_page()
    page.goto(FORM_URL)

    def validation():
        page.click("#submit")
        expect(page.locator("#status")).to_contain_text("Please fill in your name and a title")
    check("form: refuses to send without name/title", validation)

    def error_clears():
        page.fill("#reporter", "K")
        expect(page.locator("#status")).to_be_hidden()
    check("form: the error disappears once the user starts fixing it", error_clears)

    page.fill("#reporter", "Kenji (AP Ops)")
    page.select_option("#severity", "critical")
    page.fill("#title", "7203.T sell-short stuck ~30% behind VWAP")
    page.fill("#description", "Client is chasing. ~30% behind the VWAP curve since the open and the stock "
                              "is down hard. Is the short-sell price restriction blocking us?")
    page.fill("#data", PASTED)

    def separator_guess():
        expect(page.locator("#dataMeta")).to_contain_text("only one column found")
        page.click("#useGuess")
        expect(page.locator("#dataMeta")).to_contain_text("1 row × 5 columns (separator: comma)")
        expect(page.locator("#preview th").first).to_have_text("Target ID")
    check("form: wrong separator detected, one click fixes it, preview shows the table", separator_guess)

    def attachments():
        png = E2E / "platform.png"
        page.locator("#preview").screenshot(path=str(png))
        page.set_input_files("#fileInput", str(png))
        shot_b64 = base64.b64encode(page.locator("#description").screenshot()).decode()
        page.evaluate("""async (b64) => {
            const blob = await (await fetch('data:image/png;base64,' + b64)).blob();
            const dt = new DataTransfer(); dt.items.add(new File([blob], 'image.png', {type: 'image/png'}));
            document.dispatchEvent(new ClipboardEvent('paste', {clipboardData: dt, bubbles: true}));
        }""", shot_b64)
        expect(page.locator("#files li")).to_have_count(2)
        expect(page.locator("#files li").nth(1)).to_contain_text("screenshot-")
    check("form: file attachment + Ctrl+V screenshot become attachments", attachments)
    shot(page, "01_form_filled", full=True)

    def send():
        page.click("#submit")
        expect(page.locator("#status")).to_contain_text("Alert sent. Reference")
        written = page.evaluate("window.__written")
        assert len(written) == 1, written
        name, body = next(iter(written.items()))
        assert name.endswith(".ticket.json"), name
        t = json.loads(body)
        assert t["data"]["separator"] == "," and len(t["attachments"]) == 2 and t["severity"] == "critical"
        (E2E / "inbox" / name).write_text(body, encoding="utf-8")
        expect(page.locator("#title")).to_have_value("")
        expect(page.locator("#reporter")).to_have_value("Kenji (AP Ops)")  # remembered
    check("form: sends one ticket file to the inbox folder and resets", send)
    shot(page, "02_form_sent")
    ctx.close()

    def fallback():
        c2 = browser.new_context(accept_downloads=True)
        c2.add_init_script("window.showDirectoryPicker = undefined;")
        p2 = c2.new_page()
        p2.goto(FORM_URL)
        expect(p2.locator("#inboxName")).to_contain_text("not supported")
        p2.fill("#reporter", "Sam (US PT)")
        p2.fill("#title", "Fallback check: order id typed by hand")
        p2.fill("#data", "Target ID\n100118\n")
        with p2.expect_download() as dl:
            p2.click("#submit")
        d = dl.value
        assert d.suggested_filename.endswith(".ticket.json")
        d.save_as(str(E2E / "inbox" / d.suggested_filename))
        expect(p2.locator("#status")).to_contain_text("Move that file into the shared inbox")
        c2.close()
    check("form: browsers without folder access download the ticket instead", fallback)
    browser.close()


# ---------------------------------------------------------------- 2. the watchdog
def watchdog_tests():
    def run():
        time.sleep(2.5)  # the watchdog ignores files younger than settle_seconds (2 s) so it never reads half a copy
        r = subprocess.run([sys.executable, str(ROOT / "watchdog" / "order_watchdog.py"),
                            "-c", str(E2E / "config.toml"), "--once"], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, r.stderr
        assert not list((E2E / "inbox").glob("*.ticket.json")), "inbox not emptied"
        folders = [f for f in (E2E / "processed").glob("*/*") if (f / "orders.json").exists()
                   and json.loads((f / "ticket.json").read_text(encoding="utf-8"))["reporter"] in ("Kenji (AP Ops)", "Sam (US PT)")
                   and "demo" not in f.name]
        assert len(folders) == 2, folders
        toyota = next(f for f in folders if "7203.T" in (f / "ticket.json").read_text(encoding="utf-8"))
        orders = json.loads((toyota / "orders.json").read_text(encoding="utf-8"))["orders"]
        assert orders[0]["id_target"] == 100731 and orders[0]["sym"] == "7203.T", orders
        assert len(list((toyota / "attachments").iterdir())) == 2
        assert (toyota / "report.html").exists()
        global E2E_ISSUE
        E2E_ISSUE = toyota.name
    check("watchdog: picks up both tickets, extracts id_target, unpacks attachments, writes report", run)


# ---------------------------------------------------------------- 3. the desktop notifier
def notifier(*args) -> list[dict]:
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        str(ROOT / "notifier" / "order-alert-notifier.ps1"), "-ProcessedPath", str(E2E / "processed"),
                        "-DeskUrl", DESK_URL, "-StateDir", str(E2E / "notifier-state"), "-Once", "-DryRun", *args],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr
    return [json.loads(x) for x in r.stdout.splitlines() if x.strip().startswith("{")]


def notifier_tests():
    def new_issues():
        out = notifier("-MinSeverity", "high", "-RemindUnassignedMinutes", "0")
        titles = {n["title"] for n in out if n["kind"] == "new"}
        assert "7203.T sell-short stuck ~30% behind VWAP" in titles, out
        assert "Fallback check: order id typed by hand" not in titles, "medium issue shown despite -MinSeverity high"
        e2e = next(n for n in out if n["title"].startswith("7203.T"))
        assert e2e["url"] == f"{DESK_URL}/issue?id={E2E_ISSUE}", e2e
        assert notifier("-MinSeverity", "high", "-RemindUnassignedMinutes", "0") == [], "notified twice"
    check("notifier: one notification per new issue, filtered by severity, linking to the Desk", new_issues)

    def reminders():
        out = notifier("-RemindUnassignedMinutes", "5")
        reminded = {n["title"] for n in out if n["kind"] == "reminder"}
        assert "Toyota VWAP sell-short way behind schedule" in reminded, out      # critical, nobody on it
        assert "Tencent buy not filling near the limit" not in reminded, out       # Bob took it in the Desk
        assert notifier("-RemindUnassignedMinutes", "5") == [], "reminded twice"
    check("notifier: reminds once about urgent unassigned issues, not ones someone has taken", reminders)


# ---------------------------------------------------------------- 4. the desk
def wait_http(url, timeout=60):
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(url, timeout=2)
            return
        except Exception:
            time.sleep(0.5)
    raise TimeoutError(url)


def desk_tests(pw):
    env = dict(os.environ, ORDER_ALERT_CONFIG=str(E2E / "config.toml"))
    server = subprocess.Popen([sys.executable, "-m", "streamlit", "run", "streamlit_app.py",
                               "--server.port", str(PORT), "--server.headless", "true"],
                              cwd=ROOT / "desk", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_http(f"{DESK_URL}/_stcore/health")
        browser = pw.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 950})

        def board():
            page.goto(f"{DESK_URL}/?me=Chen")
            expect(page.get_by_text("High / critical open")).to_be_visible(timeout=30000)
            expect(page.locator(".amx-tile").first).to_contain_text("5")  # 3 demo + 2 new open issues
            expect(page.locator("[data-testid='stDataFrame']")).to_be_visible()
            page.wait_for_function("document.title === '(4) Order Alert Desk'", timeout=15000)
            page.wait_for_timeout(1500)
        check("desk: board lists the new issues; tab title shows the unassigned count", board)
        shot(page, "03_board")

        def open_from_board():
            grid = page.locator("[data-testid='stDataFrame']").bounding_box()
            page.mouse.click(grid["x"] + 12, grid["y"] + 35 + 17)  # row selector of the first (most urgent) row
            expect(page).to_have_url(lambda u: "/issue" in u, timeout=15000) if False else \
                page.wait_for_url("**/issue?**", timeout=15000)
            expect(page.get_by_role("heading", name="7203.T sell-short stuck ~30% behind VWAP")).to_be_visible()
        check("desk: clicking a board row opens the issue", open_from_board)

        def issue_page():
            if "/issue" not in page.url:
                page.goto(f"{DESK_URL}/issue?id={E2E_ISSUE}&me=Chen")
            expect(page.get_by_text("Unassigned", exact=True)).to_be_visible(timeout=20000)
            expect(page.get_by_text("Kenji (AP Ops)").first).to_be_visible()
            expect(page.locator("[data-testid='stImage']")).to_have_count(2)
        check("desk: issue shows report, orders, pasted data and screenshots", issue_page)

        def take_it():
            page.get_by_role("button", name="Take it").click()
            expect(page.get_by_text("Handled by Chen")).to_be_visible(timeout=15000)
            expect(page.get_by_text("Investigating").first).to_be_visible()
        check("desk: 'Take it' assigns to me and moves the issue to Investigating", take_it)

        def note():
            page.get_by_label("Note").fill("Short-sell price restriction confirmed on 7203.T; asked IT for the tick-test log.")
            page.get_by_role("button", name="Save").click()
            expect(page.get_by_text("asked IT for the tick-test log")).to_be_visible(timeout=15000)
            log = next((E2E / "processed").glob(f"*/{E2E_ISSUE}/events.jsonl"))
            for _ in range(20):
                lines = log.read_text(encoding="utf-8").splitlines()
                if len(lines) == 3:
                    break
                time.sleep(0.25)
            assert [(json.loads(x)["user"], json.loads(x)["kind"]) for x in lines] ==                 [("Chen", "assign"), ("Chen", "status"), ("Chen", "comment")], lines
            expect(page.get_by_label("Note")).to_have_value("")  # cleared, so it can't be saved twice
        check("desk: notes are saved to the shared activity log with who/when", note)
        page.wait_for_timeout(800)
        shot(page, "04_issue", full=True)

        def analyze():
            page.get_by_role("button", name="Analyze orders (1)").click()
            page.wait_for_url("**/analysis?**", timeout=15000)
            expect(page.get_by_text("Demo data", exact=True)).to_be_visible(timeout=20000)
            expect(page.locator("h3").filter(has_text="7203.T")).to_be_visible()
            expect(page.get_by_role("tab", name="Situations", exact=False)).to_be_visible()
            page.wait_for_timeout(1000)
        check("desk: 'Analyze orders' opens the order-monitor view for the reported order", analyze)
        shot(page, "05_analysis_situations", full=True)

        def market():
            page.get_by_role("tab", name="Market").click()
            expect(page.locator("[data-testid='stPlotlyChart']:visible").first).to_be_visible(timeout=20000)
            page.wait_for_timeout(1500)
        check("desk: Market tab shows the bid/ask chart with fills", market)
        shot(page, "06_analysis_market", full=True)

        def multi_order_and_back():
            page.goto(f"{DESK_URL}/?me=Chen")
            expect(page.locator(".amx-tile").nth(3)).to_contain_text("1", timeout=20000)  # Mine: Chen has it now
            expect(page.locator(".amx-tile").nth(1)).to_contain_text("3")  # 4 unassigned before Chen took one
        check("desk: board reflects the change (Mine = 1 for Chen, one fewer unassigned)", multi_order_and_back)

        def live_alert():
            page.get_by_text("Order data source").first.click()  # any click lets the browser play the chime
            t = {"schema": 1, "id": "20991231-235959-live01", "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                 "reporter": "Mei (HK desk)", "severity": "critical", "title": "HK order rejected by the exchange",
                 "description": "", "data": {"raw": "Target ID\n100742\n", "separator": "\t", "has_header": True},
                 "attachments": []}
            (E2E / "inbox" / "20991231-235959-live01.ticket.json").write_text(json.dumps(t), encoding="utf-8")
            time.sleep(2.5)
            subprocess.run([sys.executable, str(ROOT / "watchdog" / "order_watchdog.py"), "-c", str(E2E / "config.toml"),
                            "--once"], check=True, capture_output=True, timeout=60)
            expect(page.get_by_text("HK order rejected by the exchange").first).to_be_visible(timeout=45000)
            page.wait_for_function("(window.__orderAlertBeeps || 0) >= 1", timeout=10000)
            page.wait_for_function("document.title === '(4) Order Alert Desk'", timeout=10000)
            shot(page, "07_desk_alert")
        check("desk: an issue arriving while the Desk is open pops a toast, chimes and updates the tab title", live_alert)
        browser.close()
    finally:
        server.terminate()
        server.wait(timeout=20)


E2E_ISSUE = ""

if __name__ == "__main__":
    setup_share()
    with sync_playwright() as pw:
        print("1. Form")
        form_tests(pw)
        print("2. Watchdog")
        watchdog_tests()
        print("3. Notifier")
        notifier_tests()
        print("4. Desk")
        desk_tests(pw)
    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    sys.exit(1 if failed else 0)
