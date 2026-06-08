# Nightly Report Follow-up (2026-05-16) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update stale dependencies flagged by the 2026-05-16 Nightly System Report dependency audit.

**Architecture:** Mechanical pin bumps in `pyproject.toml`. Minor/patch updates are batched (low risk). Major-version bumps for IMAPClient and mail-parser are isolated in their own tasks to allow focused regression testing of IMAP and email-parsing behavior.

**Tech Stack:** Python 3.12, pyproject.toml, IMAPClient, mail-parser, Docker Compose, pytest.

> **Note:** The 16:20 bible_plan re-trigger (host `811bc8833c11`) reported as an anomaly was intentional functionality testing — not a bug.

---

## Report Summary: What Needs Fixing

From the dependency audit section of the 2026-05-16 Nightly System Report:

| Package | Pinned | Latest | Gap |
|---|---|---|---|
| apscheduler | 3.10.4 | 3.11.2 | minor |
| requests | 2.32.3 | 2.34.2 | minor |
| beautifulsoup4 | 4.12.3 | 4.14.3 | minor |
| google-api-python-client | 2.149.0 | 2.196.0 | minor |
| google-auth | 2.34.0 | 2.53.0 | minor |
| google-auth-oauthlib | 1.2.1 | 1.4.0 | minor |
| ics | 0.7.2 | 0.7.3 | patch |
| **IMAPClient** | **2.3.1** | **3.1.0** | **major** |
| **mail-parser** | **3.15.0** | **4.2.1** | **major** |

---

## File Map

| File | Change |
|---|---|
| `pyproject.toml` | Bump all pins above |
| `service/imap_listener.py` | Possibly add `normalise_times=True` for IMAPClient 3.x |
| `service/mcp_server.py` | Possibly add `normalise_times=True`; verify mail-parser attribute names |

---

## Task 1: Update minor and patch dependencies

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Update the pins in `pyproject.toml`**

In the `[project] dependencies` block, change:

```toml
"apscheduler==3.10.4",               →  "apscheduler==3.11.2",
"requests==2.32.3",                  →  "requests==2.34.2",
"beautifulsoup4==4.12.3",            →  "beautifulsoup4==4.14.3",
"google-api-python-client==2.149.0", →  "google-api-python-client==2.196.0",
"google-auth==2.34.0",               →  "google-auth==2.53.0",
"google-auth-oauthlib==1.2.1",       →  "google-auth-oauthlib==1.4.0",
"ics==0.7.2",                        →  "ics==0.7.3",
```

Leave `IMAPClient` and `mail-parser` at their current pins for now (Tasks 2 and 3).

- [ ] **Step 2: Rebuild the container**

```bash
make rebuild
# Expected: clean install, no pip errors
```

- [ ] **Step 3: Run the full test suite**

```bash
make test
# Expected: all tests pass
```

- [ ] **Step 4: Spot-check APScheduler (largest behavioral surface)**

```bash
docker compose run --rm cortex python -m service.cli list-jobs
# Expected: job table prints cleanly — no scheduler exceptions
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml
git commit -m "chore(deps): bump minor/patch dependencies (apscheduler, requests, bs4, google-*, ics)"
```

---

## Task 2: Migrate IMAPClient 2.3.1 → 3.1.0

**Context:** IMAPClient 3.x is a major version. Code that uses it:
- `service/imap_listener.py` — context-manager usage, `login`, `list_folders`, `select_folder`, `search`, `fetch`, `idle`, `idle_check`, `idle_done`
- `service/mcp_server.py` — same API surface, manual `logout()` in a `suppress`

The most likely breaking change: `normalise_times` default flipped from `True` to `False` in 3.x. This means `envelope.date` may now be a naïve `datetime` if the server doesn't include a timezone. The `strftime` call in `mcp_server.py:132` still works on naïve datetimes, so it may be a non-issue — but verify.

**Files:**
- Modify: `pyproject.toml`
- Possibly modify: `service/imap_listener.py`, `service/mcp_server.py`

- [ ] **Step 1: Bump the pin**

```toml
"IMAPClient==2.3.1",  →  "IMAPClient==3.1.0",
```

- [ ] **Step 2: Rebuild**

```bash
make rebuild
# Expected: clean install
```

- [ ] **Step 3: Run tests**

```bash
make test
# Expected: pass — unit tests don't exercise IMAPClient directly
```

- [ ] **Step 4: Smoke-test IMAP via MCP server**

```bash
docker compose exec cortex python -c "
from service.mcp_server import list_folders
print(list_folders())
"
# Expected: folder listing prints without error
```

- [ ] **Step 5: Smoke-test envelope date formatting**

```bash
docker compose exec cortex python -c "
from service.mcp_server import list_emails
print(list_emails('INBOX', limit=3))
"
# Expected: 3 rows with correctly formatted dates (YYYY-MM-DD HH:MM)
```

If you see a `TypeError` on `env.date`, add `normalise_times=True` to the `IMAPClient(...)` constructor in both files:

```python
# service/imap_listener.py ~line 169
with IMAPClient(host, port, ssl=False, normalise_times=True) as client:

# service/mcp_server.py ~line 34
c = IMAPClient(host, port, ssl=False, normalise_times=True)
```

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml service/imap_listener.py service/mcp_server.py
git commit -m "chore(deps): upgrade IMAPClient 2.3.1 → 3.1.0"
```

---

## Task 3: Migrate mail-parser 3.15.0 → 4.2.1

**Context:** `mail-parser` is used in one place — `service/mcp_server.py`:
```python
mail = mailparser.parse_from_bytes(raw)
```
The `parse_from_bytes` function exists in 4.x, but attribute names on the returned object may have changed. Verify before committing.

**Files:**
- Modify: `pyproject.toml`
- Possibly modify: `service/mcp_server.py`

- [ ] **Step 1: Bump the pin**

```toml
"mail-parser==3.15.0",  →  "mail-parser==4.2.1",
```

- [ ] **Step 2: Rebuild**

```bash
make rebuild
```

- [ ] **Step 3: Check attribute names**

```bash
docker compose run --rm cortex python -c "
import mailparser
raw = b'From: test@example.com\r\nSubject: Test\r\n\r\nHello'
m = mailparser.parse_from_bytes(raw)
print('from_:', m.from_)
print('subject:', m.subject)
print('text_plain:', m.text_plain)
print('has text_html:', hasattr(m, 'text_html'))
"
# Expected: all attributes accessible without AttributeError
```

If any attribute is missing or renamed, find all usages:

```bash
grep -n "mail\." /srv/docker/cortex/service/mcp_server.py
```

Update the attribute references in `service/mcp_server.py` to match 4.x names.

- [ ] **Step 4: Run tests + smoke-test `read_email`**

```bash
make test
docker compose exec cortex python -c "
from service.mcp_server import read_email
print(read_email('INBOX', 342)[:400])
"
# Expected: Nightly System Report content prints without error
```

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml service/mcp_server.py
git commit -m "chore(deps): upgrade mail-parser 3.15.0 → 4.2.1"
```
