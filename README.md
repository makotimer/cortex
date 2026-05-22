# makotimer/cortex — The Mako Brain

Part of the **MakoTimer Network**: `cortex` is the **central orchestrator** that powers the entire network with scheduled tasks, email alerts, weather updates, and IMAP command processing.

Built in **Python** with **Docker + APScheduler + ProtonMail Bridge**, it runs **zero cron** — every job is defined in `config.json` and executed in a bulletproof container.

---

## High-Level Overview

The MakoTimer Network provides a distributed system for family task management, scheduling, and real-time updates:

- `cortex` (**this repo**) — schedules tasks, sends emails, listens for commands.
- `nexus` (Rust/Zephyr) — real-time bridge with Wi-Fi, BLE, sensors.
- `portal` (ESP32-P4 + LVGL) — 10.1" touch interface.
- `slate` (Flutter) — mobile control + Find My Phone.

`cortex` is the **only component with internet access** — it fetches weather, sends emails, and receives ad-hoc commands via IMAP. All other nodes are **offline-capable** and communicate via `nexus`.

---

## Technologies Used

| Layer | Tech |
|------|------|
| **Language** | Python 3.12 |
| **Scheduler** | APScheduler (cron, interval, daily_time, date triggers) |
| **Email** | ProtonMail Bridge (Docker sidecar) + SMTP/IMAP |
| **Container** | Docker (`python:3.12-slim`) + `tini` + non-root user |
| **Config** | `config.json` + `.env` (secrets) |
| **Logging** | JSONL activity logs (`local/logs/activity-*.jsonl`) |
| **Testing** | `pytest` + live tests |
| **Linting** | `ruff` (3ms/format) |

---

## What `cortex` Does

| Function | Description |
|--------|-----------|
| **Task Scheduling** | Runs any Python module on `cron`, `interval`, `daily_time`, or `date` triggers. |
| **Email Alerts** | Sends rich HTML emails via ProtonMail Bridge (never bakes credentials). |
| **IMAP Command Listener** | Watches `Label/Commands` → runs ad-hoc modules (e.g., "Add chore"). |
| **Dry-Run Mode** | `CORTEX_DRY_RUN=1` → zero emails. |
| **Zero-Downtime** | Bridge `socat` healthcheck gates cortex startup. |

---

## Project Layout
```
makotimer/
├── cortex/          (this repo — Python brain)
├── nexus/           (Rust/Zephyr bridge)
├── portal/          (C++ display)
├── slate/           (Dart mobile)
└── shared/          (CBOR schema, icons)

cortex/
├── local/
│   ├── config.json
│   ├── state/
│   └── logs/
├── modules/
│   ├── _shared/         ← helpers: cache, dates, email_ctx, html, http, utils
│   ├── bible_plan/      ← daily Bible reading emails (Mon-Thu / Fri-Sun schedules)
│   ├── career_watch/    ← job-board scraper; two users, VPN-rotated IPs, Mon-Sat
│   ├── example_daily/   ← minimal reference; copy to create a new module
│   └── sonos/           ← hourly chimes; volume varies by day and hour
├── service/
│   ├── cli.py
│   ├── scheduler.py
│   ├── runner.py
│   ├── imap_listener.py
│   ├── imap_commands/   ← parses + dispatches IMAP commands (LIST, RUN, CAREER REPORT)
│   ├── emailer.py
│   └── mcp_server.py
├── tests/
├── scripts/
└── Dockerfile
```

## Docker Summary
```
┌─ cortex (python:3.12-slim)
│   ├─ APScheduler → your config.json jobs
│   ├─ IMAP listener → reply-to-email commands
│   └─ Ruff + pytest
└─ cortex_bridge (ProtonMail Bridge)
    └─ socat healthcheck → bullet-proof
```

## 1. One-time local dev setup
```
git clone <this-repo>
cd cortex

make setup          # runs: install + bootstrap
```
This automatically:
 - creates .venv
 - copies local/config.example.json → local/config.json
 - copies .env.example → .env

## 2. One-time ProtonMail Bridge login
```
# One-time ProtonMail login
# 1. Start just the bridge
docker compose up -d cortex_bridge

# 2. Open the interactive CLI
docker exec -it cortex_bridge protonmail-bridge --cli

# 3. Inside the CLI, type:
#    login
#    → Username: you@protonmail.com
#    → Password: [hidden]
#    → 2FA: 123456

# IMPORTANT: choose username that will be sending/receiving emails, cannot be alias.
#            (e.g., you@your.domain != you@proton.me != you@pm.me)
# NOTE:      might be a good time to take a bio break, if the sync will take a while...

#    info
#    → copy the **Username** and **Password** lines

#    exit
```

## 3. Fill in .env (Proton creds, OpenAI key, etc.)
```
# 4. Edit .env (Proton creds, OpenAI key, etc.)
# BRIDGE_USERNAME=abcdefghijklmnopqrstuvwxyz@pm.me
# BRIDGE_PASSWORD=super-secret-imap-password
# OPENAI_API_KEY=sk-...
```

## 4. Start Everything
```
make reload-bridge
make logs-f
```

You’re live.
Every job in `local/config.json` now runs + emails.

## config.json – Your Entire Brain
```
{
  "timezone": "America/Chicago",
  "jobs": [
    {
      "id": "daily-example",
      "module": "modules.example_daily",
      "trigger": { "daily_time": { "time": ["08:00"] } },
      "kwargs": { "name": "Friend" },
      "send_email": true,
      "email_to": "you@example.com",
      "summary": "Example daily job (08:00)"
    }
  ]
}
```

`email_to_env` can be used instead of `email_to` to pull the recipient address from an environment variable:
```json
"email_to_env": "BIBLE_PLAN_EMAILS"
```

### Supported Triggers – Pick One

| Type | When it runs | Example |
|------|--------------|---------|
| `cron` | Classic cron syntax | Hourly on weekdays |
| | ```json { "cron": "0 * * * mon-fri" } ``` |
| `interval` | Every X seconds/minutes/hours | Every 90 minutes |
| | ```json { "interval": { "minutes": 90 } } ``` |
| `date` | One-shot at exact timestamp | Jan 1st 2026 @ 00:00 |
| | ```json { "date": "2026-01-01T00:00:00" } ``` |
| `daily_time` | Same clock time(s) every day | 05:00 + 06:30 (Mon-Sat) |
| | ```json { "daily_time": { "time": ["05:00", "06:30"], "day_of_week": "mon-sat" } } ``` |

## .env – Secrets (never baked)
```
BRIDGE_USERNAME=you@protonmail.com
BRIDGE_PASSWORD=your-app-password
BRIDGE_DISPLAY="Scheduled Bot"

OPENAI_API_KEY=sk-...
SEND_EMAIL=1
```

## Features

| Done | Feature |
|------|---------|
| ✅ | **Zero-downtime restarts** — bridge `socat` healthcheck gates startup |
| ✅ | **One-time Proton login** — credentials persist forever |
| ✅ | **IMAP commands** — instant ad-hoc |
| ✅ | **Blazing-fast lint/format** — **Ruff** → 3 ms on save, 600 files/sec |
| ✅ | **Bullet-proof tests** — **Pytest** → 100 % isolated DB, `--live` for real email |
| ✅ | **Audit-ready logs** — structured JSONL → `./local/logs/activity-*.jsonl` |
| ✅ | **Dry-run toggle** — `CORTEX_DRY_RUN=1` → zero emails |


## Perfect VSCode Setup (copy-paste)
```
// .vscode/settings.json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/.venv/bin/python",
  "ruff.enable": true,
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff",
    "editor.formatOnSave": true,
    "editor.codeActionsOnSave": {
      "source.organizeImports": "explicit",
      "source.fixAll": "explicit"
    }
  },
  "ruff.format.enable": true
}
```
```
# pyproject.toml (Ruff only)
[tool.ruff.format]
preview = true
```

## Development

```bash
# Install (editable + dev tools)
make install

# Run tests
make test

# Live tests
make live-tests           # run all live tests in container
make live-test bae        # run a single live test by keyword

# Lint
make lint

# Rebuild & restart
make rebuild
make logs-f               # follow logs
```
