# Order Alert: setup and operations guide

How to deploy, configure, check and run Order Alert. For what each part does, see the
[README](../README.md); for a walkthrough, [Order-Alert.pptx](Order-Alert.pptx).

**Contents**
1. [Architecture](#1-architecture)
2. [Prerequisites](#2-prerequisites)
3. [Shared drive layout and permissions](#3-shared-drive-layout-and-permissions)
4. [Server: watchdog and Desk](#4-server-watchdog-and-desk)
5. [Configuration reference](#5-configuration-reference)
6. [Rolling out to users (the form)](#6-rolling-out-to-users-the-form)
7. [Rolling out to the team (notifier)](#7-rolling-out-to-the-team-notifier)
8. [Go-live check](#8-go-live-check)
9. [Operations](#9-operations)
10. [Troubleshooting](#10-troubleshooting)
11. [Security notes](#11-security-notes)

---

## 1. Architecture

```
 Users (traders / ops)          Shared drive                         Server (service account)
 ─────────────────────          ────────────                         ────────────────────────
 Desktop icon → Edge app  ───▶  OrderAlerts\inbox\*.ticket.json ───▶ watchdog  (Python)
 (order-alert.html)                                                   ├─ kdb+ RDB/HDB (pykx)
                                OrderAlerts\processed\<date>\<id>\ ◀──┤ report, CSVs, orders.json
                                OrderAlerts\failed\                   └─ SMTP email (optional)
                                        ▲      │
                                        │      └────────────────────▶ Desk (Streamlit, :8501)
 Team (Quant & Support)                 │       reads issues, appends events.jsonl
 ──────────────────────                 │
 Browser → http://<server>:8501 ────────┘ (via the Desk)
 Notifier (PowerShell, at logon) ── reads processed\ ──▶ Windows notifications
```

The shared drive is the only integration point: no change to the trading platform, no
database. One `config.toml` drives both the watchdog and the Desk.

| Component | Runs on | Runtime | Reads | Writes |
|---|---|---|---|---|
| Form | user PC, in Edge/Chrome | browser | – | `inbox\` |
| Watchdog | server | Python 3.11+ | `inbox\`, kdb+ | `processed\`, `failed\`, email |
| Desk | server | Python 3.11+, Streamlit | `processed\`, kdb+ | `processed\…\events.jsonl` |
| Notifier | each team PC | Windows PowerShell 5.1 | `processed\` | `%LOCALAPPDATA%\OrderAlert` only |

The watchdog and the Desk can run on the same server (simplest) or on two.

## 2. Prerequisites

**Server**
- Windows or Linux with **Python 3.11+** (the watchdog uses `tomllib`) and network access to
  the share and to the kdb+ RDB/HDB processes.
- Python packages: `pip install -r watchdog/requirements.txt -r desk/requirements.txt`.
  Tested with Streamlit 1.56, pandas 2.2, plotly 5.24.
- **pykx** for kdb+ queries. The watchdog and the Desk only use IPC (`QConnection`). Without
  a kdb+ license, pykx can run in its unlicensed, IPC-only mode (environment variable
  `PYKX_UNLICENSED=true` in current versions; check the pykx docs for the version you
  install). Without pykx at all, both still run: the watchdog files reports without kdb+
  data and the Desk analyses demo data.
- A **service account** that can reach the share (see §3) and, if used, the SMTP relay.
- Inbound TCP **8501** (or your chosen port) open from the team's PCs to the Desk.

**User PCs (form):** Microsoft Edge or Google Chrome. Firefox has no folder-write support, so
the form falls back to downloading the ticket (see §10).

**Team PCs (notifier):** Windows 10 1809+ or Windows 11, Windows PowerShell 5.1 (built in).
No admin rights needed.

**Information to collect before you start**
- [ ] Share path, e.g. `\\fileserver\share\OrderAlerts`
- [ ] kdb+ RDB and HDB `host:port` per region (`.cfg.hsources[(region;"OMSR")]` / `"OMSH"` in the Argo q code), and credentials if any
- [ ] SMTP relay host/port, sender address, team address (only if you want email)
- [ ] Desk URL as the team will type it, e.g. `http://order-desk-host:8501`
- [ ] Team member names for the assignee list

## 3. Shared drive layout and permissions

Create this layout (copy `web\` and `notifier\` from the repository unchanged, keeping
them side by side: the desktop shortcut takes its icon from `..\notifier\assets`):

```
\\fileserver\share\OrderAlerts\
├─ app\
│  ├─ web\         order-alert.html, install-shortcut.ps1
│  └─ notifier\    order-alert-notifier.ps1, install-notifier.ps1, assets\
├─ inbox\          new tickets from the form
├─ processed\      one folder per issue: <date>\<id>\
└─ failed\         unreadable tickets + .error.txt
```

Permissions (NTFS + share):

| Folder | Users (form) | Team | Watchdog account | Desk account |
|---|---|---|---|---|
| `app\` | Read | Read | – | – |
| `inbox\` | **Modify** (create/write files) | – | **Modify** (rename, delete) | – |
| `processed\` | – | **Read** (notifier) | **Modify** | **Modify** (appends `events.jsonl`) |
| `failed\` | – | Read (optional) | **Modify** | – |

Users never need to see `processed\`: tickets can hold client data, so keep it to the team.

## 4. Server: watchdog and Desk

### 4.1 Install

Copy the repository to the server, for example `D:\OrderAlert\`. Keep **`watchdog\` and
`desk\` side by side**: the Desk imports the watchdog's parsing code from `..\watchdog`.

```powershell
cd D:\OrderAlert
python -m venv .venv
.venv\Scripts\pip install -r watchdog\requirements.txt -r desk\requirements.txt
copy watchdog\config.example.toml watchdog\config.toml      # then edit it, see §5
```

### 4.2 First run, by hand

```powershell
.venv\Scripts\python watchdog\order_watchdog.py -c watchdog\config.toml --once
```

This creates `inbox\`, `processed\` and `failed\` if they are missing, processes whatever is
in the inbox and exits. Check the log file (`paths.log_file`) for errors.

```powershell
cd desk
..\.venv\Scripts\python -m streamlit run streamlit_app.py --server.port 8501
```

Open `http://<server>:8501`. The sidebar must **not** show "Demo issues": if it does, the Desk
didn't find `watchdog\config.toml` (see §5.3).

### 4.3 Run them as services

Both are long-running processes. With **Task Scheduler** (built in), create two tasks:

| | Watchdog | Desk |
|---|---|---|
| Trigger | At startup | At startup |
| Run as | watchdog service account, "Run whether user is logged on or not" | Desk service account, same |
| Program | `D:\OrderAlert\.venv\Scripts\python.exe` | `D:\OrderAlert\.venv\Scripts\python.exe` |
| Arguments | `watchdog\order_watchdog.py -c watchdog\config.toml` | `-m streamlit run streamlit_app.py --server.port 8501` |
| Start in | `D:\OrderAlert` | `D:\OrderAlert\desk` (**required**: Streamlit reads `desk\.streamlit\config.toml` from here) |
| Settings | Restart every 1 min, up to 999 times; no time limit | same |

Set environment variables (`PYKX_UNLICENSED`, kdb+/SMTP password variables, optionally
`ORDER_ALERT_CONFIG`) for those accounts, at the machine or user level.

[NSSM](https://nssm.cc) works too if you prefer real Windows services: same program,
arguments and start folder. On Linux, use two systemd units.

The watchdog survives share outages: it logs "Inbox not reachable; retrying" and carries on.
Several watchdogs can share one inbox (tickets are claimed by an atomic rename), so a second
server can run one for resilience.

## 5. Configuration reference

`watchdog\config.toml`, shared by the watchdog and the Desk. Start from
`config.example.toml`. Relative paths are resolved from the config file's folder.

### 5.1 Watchdog

| Key | Default | Meaning |
|---|---|---|
| `paths.inbox` | **required** | Folder the form writes to |
| `paths.processed` | **required** | One sub-folder per issue |
| `paths.failed` | **required** | Unreadable tickets |
| `paths.log_file` | none | Rotating log (5 × 5 MB). Console only if unset |
| `watch.poll_seconds` | 5 | How often the inbox is checked (shares don't deliver reliable change events) |
| `watch.settle_seconds` | 2 | Ignore files changed more recently than this (still being written) |
| `data.default_region` | `AP` | Region when the pasted data has no region column |
| `data.max_orders` | 20 | Orders queried per ticket at most |
| `data.date_formats` | see example | `strptime` formats tried on the date column; includes the platform's `20261104-05:45:02.720` |
| `data.columns.<field>` | see example | Header aliases for `id_target`, `id_server`, `oes_oid`, `sym`, `date`, `region`. Case, spaces and punctuation are ignored. **A list here replaces the built-in list**: keep `tgtid` and `tgttime` (trading platform) in it |
| `kdb.enabled` | false | Run kdb+ lookups |
| `kdb.timeout` | 30 | Seconds per connection |
| `[[kdb.connections]]` | – | One block per endpoint: `name`, `host`, `port`, `region`, `kind` (`rdb` today / `hdb` past), optional `username`, `password_env` (name of the env var holding the password). **No other keys**: an unknown key stops the watchdog at start |
| `kdb.resolve.by_id_target` / `by_oes_oid` | see example | q used to find `id_server` (and `id_target`) when the paste lacks them |
| `kdb.queries.<name>` | see example | q run for each order; the result is saved as `<name>.csv`. `target`, `target_state`, `workorder`, `alerts` feed the one-line summary |
| `notify.email.enabled` | false | Send an email per ticket |
| `notify.email.smtp_host` / `smtp_port` | – / 25 | SMTP relay |
| `notify.email.starttls` | false | Use STARTTLS |
| `notify.email.username` / `password_env` | none | SMTP login; the password is read from that env var |
| `notify.email.from` / `to` | **required if enabled** | Sender; list of recipients |
| `notify.email.subject_prefix` | `[Order Alert]` | |
| `notify.email.max_attachment_mb` | 10 | Total attachments per email; larger files stay in the issue folder |
| `notify.email.desk_url` | none | Desk base URL; emails link straight to the issue |

Query placeholders: `{{date}}` (q date), `{{id_server}}`, `{{id_target}}` (integers),
`{{oes_oid}}`, `{{sym}}` (symbols). Only validated values are substituted; a query whose
placeholder has no value for an order is skipped for that order. Always filter on `date`
first: it is the HDB partition column.

### 5.2 Desk

| Key | Default | Meaning |
|---|---|---|
| `desk.team` | [] | Names offered in "You are" and the assignee list (others can be typed) |
| `desk.history_days` | 30 | How far back the board looks. Direct links work for any age |

Also used by the Desk: `paths.processed`, `data.default_region`, `[[kdb.connections]]`.
The Desk picks the region's RDB for today's orders and the HDB for older ones.

### 5.3 Which config file is used

1. `ORDER_ALERT_CONFIG` environment variable, if set
2. else `watchdog\config.toml` next to `desk\`
3. else the bundled demo (`desk\demo\config.toml`): the sidebar then shows "Demo issues"

Config changes are picked up by the Desk within a minute; restart the watchdog after editing.

### 5.4 Form

At the top of the script in `order-alert.html`:

| Setting | Default | Meaning |
|---|---|---|
| `DEFAULT_SEPARATOR` | `"\t"` (Tab) | The trading platform copy is Tab-separated |
| `MAX_TOTAL_ATTACHMENT_MB` | 15 | Per ticket |
| `TICKET_SUFFIX` | `.ticket.json` | Must match the watchdog |

## 6. Rolling out to users (the form)

Per user (or once through a logon script / software deployment):

```powershell
powershell -ExecutionPolicy Bypass -File \\fileserver\share\OrderAlerts\app\web\install-shortcut.ps1 `
    -HtmlPath '\\fileserver\share\OrderAlerts\app\web\order-alert.html'
```

This puts an **Order Alert** icon on the desktop that opens the form in its own Edge window.
The form is read from the share, so editing it there updates everyone.

**First send:** the browser asks the user to pick a folder: they choose
`\\fileserver\share\OrderAlerts\inbox`. The browser remembers it; in later sessions it only
asks "Allow editing?" once.

## 7. Rolling out to the team (notifier)

Each team member runs once:

```powershell
powershell -ExecutionPolicy Bypass -File \\fileserver\share\OrderAlerts\app\notifier\install-notifier.ps1 `
    -ProcessedPath '\\fileserver\share\OrderAlerts\processed' -DeskUrl 'http://order-desk-host:8501'
```

It registers the "Order Alert" notification app for that user, adds a hidden scheduled task
**Order Alert notifier** that starts at logon, starts it, and shows a sample notification.

| Option | Default | |
|---|---|---|
| `-MinSeverity` | `low` | Only notify from this severity up (`high` = high + critical) |
| `-RemindUnassignedMinutes` | 10 | Remind once about high/critical issues nobody has taken; 0 = never |
| `-Uninstall` | | Remove the task and the app registration |

The task runs the script from the share, so updating it there updates everyone at next logon.
To change someone's options, run the installer again with new values; they apply from the next logon (or run `Stop-ScheduledTask "Order Alert notifier"; Start-ScheduledTask "Order Alert notifier"`).

## 8. Go-live check

Do this once after setup, with a known order from today:

1. **Form**: open the desktop icon, fill name and title, paste the platform rows (the preview
   should show the columns with "separator: Tab"), add a screenshot, **Send alert**. Expect
   "Alert sent. Reference: …".
2. **Watchdog**: within `poll_seconds` + `settle_seconds` the file leaves `inbox\` and a folder
   appears in `processed\<today>\`. Open its `report.html`: the order line must show a summary
   (e.g. `… filled 400/1000 (40%), state=working …`), not "No orders identified" or a kdb+ error.
3. **Email** (if enabled): arrives with the summary and a working link to the Desk.
4. **Desk**: the issue is on the board; open it, **Take it**, add a note, **Analyze orders**:
   the badge must say *Live RDB* (today) and the tiles show real quantities.
5. **Notifier**: team PCs show "New order issue - …" within ~20 s; clicking opens the issue.
6. **Reminder**: leave a high/critical test issue unassigned for 10 minutes: one reminder.

To check the code itself (not your environment), run the automated tests; see the README.

## 9. Operations

**Where to look**

| What | Where |
|---|---|
| Watchdog log | `paths.log_file` |
| A ticket that could not be read | `failed\<name>` + `<name>.error.txt` |
| Email failure for an issue | `processed\…\<id>\notify_error.txt` |
| kdb+ query errors for an issue | shown in its `report.html` and the Desk |
| Who did what on an issue | `processed\…\<id>\events.jsonl` (also the Desk's Activity panel) |
| Notifier on a PC | `%LOCALAPPDATA%\OrderAlert\notifier.log`; task "Order Alert notifier" |

**Reprocess a ticket**: move it from `failed\` (or `original.ticket.json` from its issue
folder, renamed back to `<id>.ticket.json`) into `inbox\`. It gets a new folder
(`<id>_2`), leaving the old one untouched.

**Retention**: nothing is deleted automatically. `processed\` is the record of every issue;
archive old date folders as your policy requires, for example older than 180 days:

```powershell
Get-ChildItem \\fileserver\share\OrderAlerts\processed -Directory |
    Where-Object { $_.Name -match '^\d{4}-\d{2}-\d{2}$' -and [datetime]$_.Name -lt (Get-Date).AddDays(-180) } |
    Move-Item -Destination \\fileserver\archive\OrderAlerts\
```

**Upgrades**: form and notifier: replace the files in `app\` (notifier picks it up at next
logon). Watchdog and Desk: replace the code, `pip install -r …` again, restart both tasks.
Keep `config.toml`: it is not in the repository.

**Backups**: back up `processed\` (issues and their history) and `config.toml`.

## 10. Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Form says "not supported (tickets will be downloaded)" | Firefox, or a browser policy blocking folder access | Use Edge/Chrome. If it's policy, ask IT to allow file system write access for the form's location (Edge/Chrome policies `FileSystemWriteAskForUrls` / `DefaultFileSystemWriteGuardSetting`). Meanwhile users move the downloaded file into `inbox\` |
| Form keeps asking to pick the folder | Browser storage cleared, or a private window | Pick `inbox\` again; use the desktop icon, not a private window |
| Tickets stay in `inbox\` | Watchdog not running or can't reach the share | Check the task and the log; run `order_watchdog.py --once` by hand |
| Ticket in `failed\` | Not valid JSON (e.g. edited by hand, truncated) | Read the `.error.txt`; resend from the form |
| Report says "No order id column recognised. Columns …: 'X', 'Y'" | The data uses a header not in `[data.columns]` | Add it to `data.columns.id_target` (or `oes_oid`) and restart the watchdog |
| Orders found but dated the report day | Date column not recognised or format unknown | Add the header to `data.columns.date` and the format to `data.date_formats` |
| Report: "pykx is not installed" / "lookups are disabled" | pykx missing, or `kdb.enabled = false` | Install pykx in the venv; set `enabled = true` |
| kdb+ errors in the report (connection refused, timeout) | Wrong host/port, firewall, credentials | Desk → **Settings** → **Test** each connection |
| kdb+ "type" or "length" error on one query | Query template doesn't match your schema | Run the q from the report by hand; fix `[kdb.queries]` |
| `notify_error.txt` in issue folders | SMTP host/port/auth | Check `[notify.email]`; the issue is still filed and visible in the Desk |
| Desk sidebar shows "Demo issues" | Config not found | Set `ORDER_ALERT_CONFIG` for the Desk account, or place `watchdog\config.toml` next to `desk\` |
| Desk Settings: "That folder is not reachable" | Desk account lacks access to `processed\` | Grant Modify (§3) |
| Desk not reachable from team PCs | Firewall or port | Open the port; check the task runs and its "Start in" is `desk\` |
| "Take it"/notes don't save | Desk account can't write in `processed\` | Grant Modify (§3) |
| No notifications on a PC | Do not disturb / Focus, or turned off for Order Alert | Win+N: they wait in the Action Center. Windows Settings → System → Notifications → Order Alert |
| No notifications at all, nothing in the log | Task not running | `Get-ScheduledTask "Order Alert notifier"`; `Start-ScheduledTask "Order Alert notifier"`; rerun the installer |
| Script blocked: "running scripts is disabled" | Execution policy | Run with `-ExecutionPolicy Bypass` as shown. If policy is AllSigned via GPO, sign the `.ps1` files with your code-signing certificate |
| Notifications show garbled characters | A `.ps1` was edited and saved with non-ASCII characters | Keep the scripts ASCII (Windows PowerShell 5.1 reads them as ANSI) |

## 11. Security notes

- **No login on the Desk.** People pick their name; anyone who can reach the URL can view and
  update issues. Restrict the port to the team's network/PCs. If you need real
  authentication, put the Desk behind a reverse proxy with Windows authentication.
- **Pasted text never reaches q as code.** Only integers, q dates and symbols matching
  `[A-Za-z0-9_.:/#-]{1,64}` are substituted into query templates; anything else is dropped.
- **Secrets stay out of files.** Config holds environment variable *names*
  (`password_env`), not passwords.
- **Tickets can contain client data** (pasted rows, screenshots). Keep `processed\` and
  `failed\` readable by the team only (§3), and apply your retention policy (§9).
- **Read-only on trading.** Nothing here sends, modifies or cancels orders: kdb+ access is
  queries only.
