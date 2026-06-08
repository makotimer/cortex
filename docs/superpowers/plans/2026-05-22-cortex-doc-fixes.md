# Cortex Documentation Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix four stale/missing documentation items in cortex that the nightly doc-freshness audit has flagged since 2026-05-15.

**Architecture:** Two files need edits — `README.md` (config example + project layout) and `CLAUDE.md` (modules listing + service subpackage). No code changes.

**Tech Stack:** Markdown only.

---

## Files

| File | Change |
|------|--------|
| Modify: `/srv/docker/cortex/README.md` | Fix config example (wrong module name, missing fields); add actual modules and `imap_commands/` to project layout tree |
| Modify: `/srv/docker/cortex/CLAUDE.md` | Add `career_watch/`, `bible_plan/`, `sonos/` to modules tree; add `imap_commands/` to service description |

---

### Task 1: Fix README.md config.json example

The example at the bottom of the `## config.json – Your Entire Brain` section has two problems:
1. `"module": "modules.example"` — that module doesn't exist; the real reference implementation is `modules.example_daily`.
2. Missing fields: `timezone` (top-level, required in every real config), `summary` (present in every live job), and no mention of `email_to_env` (the alternative to `email_to` for env-var recipients).

**Files:**
- Modify: `/srv/docker/cortex/README.md:143-156`

- [ ] **Step 1: Make the edit**

Replace the config block (lines 143–156) in `README.md`:

Old:
```markdown
## config.json – Your Entire Brain
```
{
  "jobs": [
    {
      "id": "daily-example",
      "module": "modules.example",
      "trigger": { "daily_time": { "time": ["08:00"] } },
      "kwargs": { "name": "Friend" },
      "send_email": true,
      "email_to": "you@example.com"
    }
  ]
}
```
```

New:
```markdown
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
```

- [ ] **Step 2: Verify**

Run:
```bash
grep -n 'modules\.example' /srv/docker/cortex/README.md
```
Expected: no output (the old reference is gone).

Run:
```bash
grep -n '"timezone"' /srv/docker/cortex/README.md
```
Expected: at least one hit in the config block.

- [ ] **Step 3: Commit**

```bash
git -C /srv/docker/cortex add README.md
git -C /srv/docker/cortex commit -m "docs: fix config.json example — correct module name, add timezone/summary/email_to_env"
```

---

### Task 2: Fix README.md project layout tree

The project layout tree under `## Project Layout` shows `modules/` with only `_shared/` and `...`, and `service/` with no mention of `imap_commands/`. All three active production modules are invisible.

**Files:**
- Modify: `/srv/docker/cortex/README.md:58-75`

- [ ] **Step 1: Make the edit**

Replace the `cortex/` tree block:

Old:
```
cortex/
├── local/
│   ├── config.json
│   ├── state/
│   ├── logs/
│   └── ...
├── modules/
│   ├── _shared/
│   └── ...
├── service/
│   ├── cli.py
│   ├── scheduler.py
│   ├── runner.py
│   ├── imap_listener.py
│   └── ...
├── tests/
├── scripts/
└── Dockerfile
```

New:
```
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

- [ ] **Step 2: Verify**

Run:
```bash
grep -n 'career_watch\|bible_plan\|sonos\|imap_commands' /srv/docker/cortex/README.md
```
Expected: hits for all four in the layout block.

- [ ] **Step 3: Commit**

```bash
git -C /srv/docker/cortex add README.md
git -C /srv/docker/cortex commit -m "docs: add active modules and imap_commands/ to README project layout"
```

---

### Task 3: Fix CLAUDE.md project shape block

`CLAUDE.md` lists only `_shared/` and `example_daily/` under `modules/`, and the service description line doesn't mention the `imap_commands/` subpackage.

**Files:**
- Modify: `/srv/docker/cortex/CLAUDE.md:8-15`

- [ ] **Step 1: Make the edit**

Replace the project shape code block:

Old:
```
service/   — scheduler, runner, IMAP listener, emailer, MCP server, CLI entrypoint
modules/   — one subdirectory per job module (each has a run() entry point)
  _shared/ — shared helpers imported by modules (cache, dates, email_ctx, html, http, utils)
  example_daily/ — minimal reference implementation; copy this to create a new module
scripts/   — host-side utilities and container helpers
tests/     — pytest unit + optional live tests
local/     — bind-mounted at runtime: config.json, logs/, state/
```

New:
```
service/   — scheduler, runner, IMAP listener, emailer, MCP server, CLI entrypoint
  imap_commands/ — parses + dispatches IMAP commands (LIST, RUN MODULE=, CAREER REPORT)
modules/   — one subdirectory per job module (each has a run() entry point)
  _shared/      — shared helpers: cache, dates, email_ctx, html, http, utils
  example_daily/ — minimal reference implementation; copy this to create a new module
  career_watch/ — job-board scraper; two users, VPN-rotated IPs, Mon-Sat
  bible_plan/   — daily Bible reading emails; Mon-Thu and Fri-Sun schedules
  sonos/        — hourly Sonos chimes; volume varies by day and hour
scripts/   — host-side utilities and container helpers
tests/     — pytest unit + optional live tests
local/     — bind-mounted at runtime: config.json, logs/, state/
```

- [ ] **Step 2: Verify**

Run:
```bash
grep -n 'career_watch\|bible_plan\|sonos\|imap_commands' /srv/docker/cortex/CLAUDE.md
```
Expected: hits for all four.

- [ ] **Step 3: Commit**

```bash
git -C /srv/docker/cortex add CLAUDE.md
git -C /srv/docker/cortex commit -m "docs: document active modules and imap_commands/ subpackage in CLAUDE.md"
```
