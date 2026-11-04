# Order Alert

Lets traders/ops tell the Quant & Support team about an order that is misbehaving, without the
trading platform having to support it. Four parts, joined by a shared-drive folder:
the **form** users fill in, the **watchdog** that files and enriches each report, the
**Desk** where the team triages and analyses issues, and a **desktop notifier** on each team PC. A short walkthrough deck is in
[`docs/Order-Alert.pptx`](docs/Order-Alert.pptx).

```
 user PC                        shared drive                          watchdog server
 ─────────                      ────────────                          ───────────────
 desktop icon → Edge            inbox/<id>.ticket.json   ──poll──▶   order_watchdog.py
 web/order-alert.html  ──write──▶                                      ├─ parse pasted rows → id_target …
 (title, description,           processed/<date>/<id>/   ◀──write──   ├─ kdb+: target, target_state,
  pasted rows, screenshots)       report.html, kdb/*.csv,              │        workorder, execution, alerts
                                  attachments/, ticket.json            ├─ report.html + one-line summary
                                failed/  (unreadable tickets)          └─ email the team
                                        │
                                        ▼  reads processed/, appends events.jsonl
                                Order Alert Desk (desk/, Streamlit)
                                board → issue → Analyze orders → order monitor
```

## 1. The form (`web/order-alert.html`)

A single HTML file, no install. Edge/Chrome write the ticket straight into the inbox folder
(File System Access API). The user picks the folder on the first send; the browser remembers
it and afterwards only asks "allow editing?" once per session. If the browser can't write
(policy or a non-Chromium browser) the ticket is downloaded instead and the page says to move
it into the inbox.

- Paste the rows from the trading platform: a live preview shows rows × columns. If only one
  column is found it offers the separator that looks right.
- **Advanced: data format** (collapsed) changes the separator / header row for data from other
  systems. The default separator is `CONFIG.DEFAULT_SEPARATOR` at the top of the script (Tab).
- Screenshots: drag & drop, choose a file, or just **Ctrl+V** a screenshot anywhere on the page.
- The reporter's name is remembered on that PC.

**Deploy:** put the file on the share (e.g. `\\fileserver\share\OrderAlerts\app\`), then for each
user (or via a login script):

```powershell
powershell -ExecutionPolicy Bypass -File web\install-shortcut.ps1 -HtmlPath '\\fileserver\share\OrderAlerts\app\order-alert.html'
```

This creates a desktop icon that opens the form in its own Edge window (`msedge --app=…`).
Updating the HTML on the share updates everyone.

## 2. The watchdog (`watchdog/`)

Python 3.11+, standard library only; `pykx` + `pandas` for the kdb+ part.

```bash
cd watchdog
pip install -r requirements.txt
copy config.example.toml config.toml   # then edit paths, kdb endpoints, email
python order_watchdog.py -c config.toml          # runs forever
python order_watchdog.py -c config.toml --once   # one pass, handy for testing
python -m unittest discover -s tests             # tests (no kdb+ needed)
```

Run it as a service with Task Scheduler ("At startup", run whether user is logged on or not)
or NSSM, under an account that can read/write the share.

What it does per ticket:

1. **Claims** it with an atomic rename, so two watchdogs on the same inbox never both process it.
2. Unpacks it to `processed/<date>/<id>/`: `ticket.json`, `pasted_data.txt`, `attachments/`.
3. Parses the pasted rows with the ticket's separator, finds the order-id column by header
   aliases, and extracts `id_target` (+ `id_server`, `oes_oid`, `sym`, `date`, `region` if present).
4. kdb+ (if enabled): picks the region's **RDB for today, HDB for past dates**. If `id_server`
   is missing it resolves it from `target` first (one id_target on several servers fans out),
   then runs every query in `[kdb.queries]`. Each result is saved as `kdb/<order>/<table>.csv`.
   A failing query is noted in the report and doesn't stop the others.
5. Writes `report.html` with a one-line status per order, e.g.
   `6758.T buy 1000 VWAP, filled 400/1000 (40%), state=working, 2 child orders, 1 alerts (last: behind schedule)`,
   then the full tables and screenshots.
6. Emails the team (summary + attachments + CSVs) if `[notify.email]` is enabled. A mail
   failure is logged to `notify_error.txt` and does not reprocess the ticket.

Unreadable tickets go to `failed/` with an `.error.txt` next to them.

It also writes `orders.json` (the order keys found or resolved) for the Desk, and, when
`desk_url` is set, the email links straight to the issue in the Desk.

## 3. The Desk (`desk/`)

A Streamlit app for the team, built on the Algo Order Monitor's providers, rules engine,
theme and charts (copied into `desk/core` and `desk/theme`).

```bash
cd desk
pip install -r requirements.txt        # + pykx for real kdb+ data
streamlit run streamlit_app.py         # http://<server>:8501
```

- **Issues board**: open / unassigned / high-critical / mine / today, views (Open, Mine,
  Unassigned, Resolved, All), severity filter, search; refreshes every 30 s. Click a row to open it.
- **Issue page**: the description, orders found, pasted rows and screenshots as the user sent
  them; **Take it** (assign to me + Investigating); assignee / status / note; activity log.
- **Analyze orders**: the order-monitor view (Situations, Order, Routing, Fills, Market,
  Alerts) for each order of the issue, resolving `id_server` when only `id_target`/`oes_oid`
  was pasted. Today's orders come from the region's RDB, older ones from the HDB.

It reads the **same `config.toml` as the watchdog** (`$ORDER_ALERT_CONFIG`, else
`watchdog/config.toml`; the `[desk]` section holds the team list and history window). With no
config it runs on bundled demo issues and demo order data, so it can be tried immediately.

Handling state is an append-only `events.jsonl` in each issue folder: one line per action
(who, when, what). Several people can work at once without overwriting each other, and it is
the audit trail. Everyone picks their name in the sidebar; it is kept in the URL (`?me=`).

While the Desk is open (any page), a new issue pops an in-app notification and a chime (sidebar
toggle), and the tab title carries the unassigned count, e.g. `(3) Order Alert Desk`.

## 4. Desktop notifications (`notifier/`)

Windows notifications on each team member's PC, read straight from the share: no email, no
install (Windows PowerShell 5.1). Each member runs once:

```powershell
powershell -ExecutionPolicy Bypass -File \\fileserver\share\OrderAlerts\notifier\install-notifier.ps1 `
    -ProcessedPath '\\fileserver\share\OrderAlerts\processed' -DeskUrl 'http://order-desk-host:8501'
```

This adds a hidden logon task for that user (no admin) and shows a sample notification.
Options: `-MinSeverity high` (only high/critical), `-RemindUnassignedMinutes 0` (no reminders),
`-Uninstall`.

- Notifications come from their own app, **Order Alert**, with its logo and a severity badge
  (registered per user under `HKCU\Software\Classes\AppUserModelId`), so they can be muted or
  tuned in Windows Settings > Notifications like any app.
- Clicking opens the issue in the Desk; high/critical stay on screen until dismissed.
- A reminder fires once if a high/critical issue is still unassigned after 10 minutes; it reads
  the same `events.jsonl` as the Desk, so it stops as soon as someone clicks *Take it*.
- On first start only issues from the last hour are announced (no flood); state and a log are
  in `%LOCALAPPDATA%\OrderAlert`.
- Icons: `notifier/assets/`, regenerated with `python notifier/make_icons.py`. The form's
  desktop shortcut uses the same logo.

Windows "Do not disturb" (e.g. during full-screen apps) holds notifications back; they then
wait in the Action Center (Win+N).

## Tests

```bash
python -m unittest discover -s watchdog/tests     # watchdog: parsing, kdb+ (fake), quarantine…
cd desk && python -m unittest tests.test_desk     # issue store, order resolution, every page (AppTest)
python tests/e2e_test.py                          # real browser: form → watchdog → notifier → Desk
```

The end-to-end test needs `pip install playwright && playwright install chromium`. It writes
the screenshots in `docs/screenshots/`. The notifier is checked in dry-run mode (what it would
show, to whom, and when). Real kdb+ and SMTP are not exercised: kdb+ is covered with a fake
connection and demo data.

## Things to fill in once known

- **Trading platform column names.** The pasted data will contain `id_target` under another
  header. Add that header to `[data.columns] id_target` in `config.toml`. Matching ignores
  case, spaces and punctuation. Until then, the report of any ticket lists the column names it
  saw, so the first real ticket tells you what to add.
- **Separator** of the platform copy: `CONFIG.DEFAULT_SEPARATOR` in the HTML.
- kdb+ endpoints per region (`.cfg.hsources[(region;"OMSR")]` / `"OMSH"`), and set `kdb.enabled = true`.
- SMTP relay and team address.

## Query safety

Queries are plain q templates in the config with `{{date}} {{id_server}} {{id_target}} {{oes_oid}} {{sym}}`.
Only validated values are substituted: integers, q date literals, and symbols matching
`[A-Za-z0-9_.:/#-]{1,64}`. Pasted text can't inject q.
