# IMAP Domain Gate and Display Verse Command — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gate every IMAP command on `COMMAND_ALLOWED_DOMAINS`, restrict `APPROVE`/`DENY` to `APPROVAL_ALLOWED_DOMAINS`, and publish a silent `Scripture: <ref>` command onto `events:display`.

**Architecture:** The unused env var becomes the real sender gate. A second env var keeps hs/pbd approvals off the work domain. Verse mail is identified only by the first body line; cortex publishes the raw remainder and never replies. Display (other repo) looks the verse up.

**Tech Stack:** Python 3.12, IMAP command handlers, eventbus-kit, fakeredis, pytest via `./scripts/pytest.sh`.

**Spec:** `docs/superpowers/specs/2026-08-30-imap-domain-gate-and-display-verse-design.md`

## Global Constraints

- Do not load NKJV.json or parse book names in cortex. `payload.ref` is the stripped remainder after `Scripture:`.
- `SCRIPTURE` never sends a reply, including domain failure and empty ref. Return `(None, None, None)`.
- `LIST` / `RUN` / `APPROVE` still reply (including “Not authorized”).
- Delete `approval_allowlist`. Do not read it.
- Fail closed: empty domain lists reject everyone.
- Do not edit display, eventbus-kit constants, or Proton. Document the sieve only.
- Tests: `./scripts/pytest.sh tests/<file>.py -q` (runs inside the cortex container). No live IMAP.
- Commit after every task. Conventional commits.
- Do not `git push`. Do not restart unrelated stacks.

## File map

| File | Responsibility |
|---|---|
| `service/imap_commands/auth.py` | **new** — parse env domain lists; sender domain; allow check |
| `service/imap_commands/handlers.py` | domain gate; first-line `Scripture:`; silent return |
| `service/site_events.py` | `DISPLAY_VERSE`, `publish_verse(ref)` |
| `tests/test_imap_auth.py` | **new** — domain lists and allow helper |
| `tests/test_imap_approval.py` | env domains instead of `approval_allowlist`; l3harris reject |
| `tests/test_imap_scripture.py` | **new** — verse command |
| `service/imap_commands/README.md` | document env + Scripture + sieve |
| `CLAUDE.md` | drop `approval_allowlist` wording |
| `.env.example` | `APPROVAL_ALLOWED_DOMAINS` |
| `local/config.json` | remove `approval_allowlist` |

---

### Task 1: Domain allow helpers and the command gate

**Files:**
- Create: `service/imap_commands/auth.py`
- Create: `tests/test_imap_auth.py`
- Modify: `service/imap_commands/handlers.py` (top of `handle_command`, APPROVE block)
- Modify: `tests/test_imap_approval.py`

**Interfaces:**
- Consumes: `os.environ["COMMAND_ALLOWED_DOMAINS"]`, `os.environ["APPROVAL_ALLOWED_DOMAINS"]`, `from_addr`
- Produces: `parse_domains(raw: str | None) -> set[str]`, `sender_domain(from_addr: str | None) -> str | None`, `domain_allowed(from_addr: str | None, domains: set[str]) -> bool`, `command_domains() -> set[str]`, `approval_domains() -> set[str]`. `handle_command` rejects senders outside `command_domains()` before running `LIST`/`RUN`/`CAREER REPORT`/`APPROVE`/`DENY`. `APPROVE`/`DENY` also require `approval_domains()`.

- [ ] **Step 1: Write the failing helper tests**

Create `tests/test_imap_auth.py`:

```python
from service.imap_commands.auth import (
    approval_domains,
    command_domains,
    domain_allowed,
    parse_domains,
    sender_domain,
)


def test_parse_domains_splits_strips_lowercases():
    assert parse_domains("pricefam.email, L3Harris.com ,") == {
        "pricefam.email", "l3harris.com",
    }


def test_parse_domains_empty_is_empty_set():
    assert parse_domains(None) == set()
    assert parse_domains("  ") == set()


def test_sender_domain_plain_and_bracketed():
    assert sender_domain("ben@pricefam.email") == "pricefam.email"
    assert sender_domain("Ben Price <Ben@PriceFam.email>") == "pricefam.email"


def test_sender_domain_missing_is_none():
    assert sender_domain(None) is None
    assert sender_domain("not-an-email") is None


def test_domain_allowed_fail_closed():
    assert domain_allowed("a@pricefam.email", set()) is False
    assert domain_allowed("a@pricefam.email", {"pricefam.email"}) is True
    assert domain_allowed("a@l3harris.com", {"pricefam.email"}) is False


def test_command_domains_reads_env(monkeypatch):
    monkeypatch.setenv("COMMAND_ALLOWED_DOMAINS", "pricefam.email,l3harris.com")
    assert command_domains() == {"pricefam.email", "l3harris.com"}


def test_approval_domains_reads_env(monkeypatch):
    monkeypatch.setenv("APPROVAL_ALLOWED_DOMAINS", "pricefam.email")
    assert approval_domains() == {"pricefam.email"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./scripts/pytest.sh tests/test_imap_auth.py -q`

Expected: FAIL with `ModuleNotFoundError: service.imap_commands.auth`

- [ ] **Step 3: Write `auth.py`**

Create `service/imap_commands/auth.py`:

```python
from __future__ import annotations

import os
import re

_BRACKET = re.compile(r"<([^>]+)>")


def parse_domains(raw: str | None) -> set[str]:
    if not raw:
        return set()
    return {p.strip().lower() for p in raw.split(",") if p.strip()}


def command_domains() -> set[str]:
    return parse_domains(os.getenv("COMMAND_ALLOWED_DOMAINS"))


def approval_domains() -> set[str]:
    return parse_domains(os.getenv("APPROVAL_ALLOWED_DOMAINS"))


def sender_domain(from_addr: str | None) -> str | None:
    if not from_addr:
        return None
    s = from_addr.strip()
    m = _BRACKET.search(s)
    if m:
        s = m.group(1).strip()
    if "@" not in s:
        return None
    return s.rsplit("@", 1)[-1].lower()


def domain_allowed(from_addr: str | None, domains: set[str]) -> bool:
    d = sender_domain(from_addr)
    return bool(d and d in domains)
```

- [ ] **Step 4: Run helper tests**

Run: `./scripts/pytest.sh tests/test_imap_auth.py -q`

Expected: PASS

- [ ] **Step 5: Update existing approval tests to the domain gate**

In `tests/test_imap_approval.py`, add an autouse fixture and stop passing `approval_allowlist` as the thing that grants access. Keep `cfg` as `{}` (or any dict). Change the stranger test to a **wrong domain**. Add a work-domain reject and a family-domain allow that is not webmaster.

Replace the file’s fixture area and the three tests that talk about allowlists. Keep the glued-Proton and body-overrides tests; they only need the env fixture and `cfg = {}`.

Add at the top of the file (after imports):

```python
@pytest.fixture(autouse=True)
def _domains(monkeypatch):
    monkeypatch.setenv("COMMAND_ALLOWED_DOMAINS", "example.com,l3harris.com")
    monkeypatch.setenv("APPROVAL_ALLOWED_DOMAINS", "example.com")
```

Change every `cfg = {"approval_allowlist": ["admin@example.com"]}` to `cfg = {}`.

Replace `test_decision_from_non_allowlisted_sender_is_rejected` with:

```python
def test_decision_from_other_domain_is_rejected(bus, monkeypatch):
    monkeypatch.setattr("service.site_events.get_bus", lambda: bus)
    bus.ensure_group(events_stream("hs"), "hs")

    to, subj, html = handlers.handle_command(
        _email("APPROVE hs-abc123"), {}, None, from_addr="stranger@evil.com")

    assert bus.read(events_stream("hs"), "hs", "c1", block_ms=10) == []
    assert "not authorized" in (html or "").lower()


def test_approve_from_l3harris_is_rejected_even_when_command_domain_allows(bus, monkeypatch):
    monkeypatch.setattr("service.site_events.get_bus", lambda: bus)
    bus.ensure_group(events_stream("hs"), "hs")

    to, subj, html = handlers.handle_command(
        _email("APPROVE hs-abc123"), {}, None, from_addr="ben@l3harris.com")

    assert bus.read(events_stream("hs"), "hs", "c1", block_ms=10) == []
    assert "not authorized" in (html or "").lower()


def test_approve_from_any_address_at_approval_domain_publishes(bus, monkeypatch):
    monkeypatch.setattr("service.site_events.get_bus", lambda: bus)
    bus.ensure_group(events_stream("hs"), "hs")

    handlers.handle_command(
        _email("APPROVE hs-abc123"), {}, None, from_addr="other@example.com")

    msgs = bus.read(events_stream("hs"), "hs", "c1", block_ms=10)
    assert len(msgs) == 1
    assert msgs[0].payload["token"] == "hs-abc123"


def test_list_from_wrong_domain_is_not_authorized():
    to, subj, html = handlers.handle_command(
        _email("LIST"), {}, None, from_addr="eve@evil.com")
    assert "not authorized" in (html or "").lower()
    assert subj == "Not authorized"
```

- [ ] **Step 6: Run approval tests to see the new cases fail**

Run: `./scripts/pytest.sh tests/test_imap_approval.py -q`

Expected: FAIL on the l3harris / any-address tests (handler still uses `approval_allowlist`).

- [ ] **Step 7: Wire the gate in `handle_command`**

At the top of `service/imap_commands/handlers.py` add:

```python
from service.imap_commands.auth import approval_domains, command_domains, domain_allowed
```

Immediately **after** `cmd = parse_command_line(command_line)` and **before** the LIST branch, insert:

```python
    if cmd.get("command") in ("LIST", "CAREER REPORT", "RUN", "APPROVE", "DENY"):
        if not domain_allowed(sender, command_domains()):
            logger.warning("Rejected %s from non-command-domain %s", cmd["command"], sender)
            return sender, "Not authorized", (
                "<p>Your address is not authorized to send commands. "
                "No action taken.</p>"
            )
```

Replace the APPROVE/DENY allowlist block with:

```python
    elif cmd["command"] in ("APPROVE", "DENY"):
        token = cmd["token"]
        if not domain_allowed(sender, approval_domains()):
            logger.warning("Rejected %s for %s from non-approval-domain %s",
                           cmd["command"], token, sender)
            subj = "Not authorized"
            html = ("<p>Your address is not authorized to approve registrations. "
                    "No action taken.</p>")
        else:
            decision = "approve" if cmd["command"] == "APPROVE" else "deny"
            sender_lc = (sender or "").strip().lower()
            try:
                site_events.publish_decision(token, decision, sender_lc)
                subj = f"Recorded: {decision} {token}"
                html = f"<p>Decision <b>{decision}</b> recorded for <code>{token}</code>.</p>"
            except Exception as exc:
                logger.exception("Failed to publish decision for %s", token)
                subj = "Decision failed"
                detail = html_escape(str(exc))
                html = f"<p>Could not record decision for <code>{token}</code>:</p><pre>{detail}</pre>"
```

Delete any remaining `cfg.get("approval_allowlist"` use.

- [ ] **Step 8: Run approval + auth tests**

Run: `./scripts/pytest.sh tests/test_imap_auth.py tests/test_imap_approval.py -q`

Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add service/imap_commands/auth.py service/imap_commands/handlers.py \
  tests/test_imap_auth.py tests/test_imap_approval.py
git commit -m "feat: gate IMAP commands on COMMAND_ALLOWED_DOMAINS"
```

---

### Task 2: Silent `Scripture:` command publishes `display.verse`

**Files:**
- Modify: `service/site_events.py`
- Modify: `service/imap_commands/handlers.py`
- Create: `tests/test_imap_scripture.py`

**Interfaces:**
- Consumes: first non-blank line of `_strip_quoted_reply(text)`; `command_domains()`
- Produces: `DISPLAY_VERSE = "display.verse"`; `publish_verse(ref: str) -> str` publishes to `events:display` with `payload={"ref": ref}`. `handle_command` returns `(None, None, None)` for every SCRIPTURE outcome.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_imap_scripture.py`:

```python
import fakeredis
import pytest
from eventbus import EventBus, events_stream

from service.imap_commands import handlers


@pytest.fixture
def bus():
    return EventBus(fakeredis.FakeStrictRedis(decode_responses=True), source="cortex")


@pytest.fixture(autouse=True)
def _domains(monkeypatch):
    monkeypatch.setenv("COMMAND_ALLOWED_DOMAINS", "example.com,l3harris.com")
    monkeypatch.setenv("APPROVAL_ALLOWED_DOMAINS", "example.com")


def _mail(body: str, subject: str = "hello", from_addr: str = "ben@example.com") -> bytes:
    return (f"Subject: {subject}\r\nFrom: {from_addr}\r\n"
            f"Content-Type: text/plain\r\n\r\n{body}\r\n").encode()


def test_scripture_first_line_publishes_ref_and_is_silent(bus, monkeypatch):
    monkeypatch.setattr("service.site_events.get_bus", lambda: bus)
    bus.ensure_group(events_stream("display"), "display")

    to, subj, html = handlers.handle_command(
        _mail("Scripture: Jn 3:16-18\r\n\r\nthanks\r\n", subject="LIST"),
        {}, None, from_addr="ben@example.com")

    assert to is None and subj is None and html is None
    msgs = bus.read(events_stream("display"), "display", "c1", block_ms=10)
    assert len(msgs) == 1
    assert msgs[0].type == "display.verse"
    assert msgs[0].payload == {"ref": "Jn 3:16-18"}


def test_scripture_subject_alone_does_not_publish(bus, monkeypatch):
    monkeypatch.setattr("service.site_events.get_bus", lambda: bus)
    bus.ensure_group(events_stream("display"), "display")

    to, subj, html = handlers.handle_command(
        _mail("please ignore\r\n", subject="Scripture: John 3:16"),
        {}, None, from_addr="ben@example.com")

    assert bus.read(events_stream("display"), "display", "c1", block_ms=10) == []
    assert html is not None  # unknown / other command may still reply


def test_empty_ref_is_silent_and_publishes_nothing(bus, monkeypatch):
    monkeypatch.setattr("service.site_events.get_bus", lambda: bus)
    bus.ensure_group(events_stream("display"), "display")

    to, subj, html = handlers.handle_command(
        _mail("Scripture:   \r\n"), {}, None, from_addr="ben@example.com")

    assert (to, subj, html) == (None, None, None)
    assert bus.read(events_stream("display"), "display", "c1", block_ms=10) == []


def test_scripture_wrong_domain_is_silent(bus, monkeypatch):
    monkeypatch.setattr("service.site_events.get_bus", lambda: bus)
    bus.ensure_group(events_stream("display"), "display")

    to, subj, html = handlers.handle_command(
        _mail("Scripture: John 3:16"), {}, None, from_addr="eve@evil.com")

    assert (to, subj, html) == (None, None, None)
    assert bus.read(events_stream("display"), "display", "c1", block_ms=10) == []


def test_scripture_allows_l3harris_command_domain(bus, monkeypatch):
    monkeypatch.setattr("service.site_events.get_bus", lambda: bus)
    bus.ensure_group(events_stream("display"), "display")

    to, subj, html = handlers.handle_command(
        _mail("scripture: Ps 23:1"), {}, None, from_addr="ben@l3harris.com")

    assert (to, subj, html) == (None, None, None)
    msgs = bus.read(events_stream("display"), "display", "c1", block_ms=10)
    assert msgs[0].payload == {"ref": "Ps 23:1"}


def test_quoted_scripture_below_first_line_does_not_win(bus, monkeypatch):
    monkeypatch.setattr("service.site_events.get_bus", lambda: bus)
    bus.ensure_group(events_stream("display"), "display")

    body = (
        "LIST\r\n\r\n"
        "-------- Original Message --------\r\n"
        "Scripture: John 3:16\r\n"
    )
    handlers.handle_command(_mail(body, subject="COMMAND"), {}, None,
                            from_addr="ben@example.com")
    assert bus.read(events_stream("display"), "display", "c1", block_ms=10) == []
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./scripts/pytest.sh tests/test_imap_scripture.py -q`

Expected: FAIL (`publish_verse` missing and/or LIST runs / unknown reply instead of silent publish)

- [ ] **Step 3: Add `publish_verse`**

In `service/site_events.py` add `DISPLAY_VERSE = "display.verse"` next to `REGISTRATION_DECISION` (import it as a local constant; do not change eventbus-kit). Add:

```python
def publish_verse(ref: str) -> str:
    """Publish a wall-verse request to events:display."""
    return get_bus().publish(
        events_stream("display"), DISPLAY_VERSE,
        payload={"ref": ref},
    )
```

Keep the existing `REGISTRATION_DECISION` import from eventbus.

- [ ] **Step 4: Detect `Scripture:` on the first body line**

In `handle_command`, after `text = extract_text_from_email(msg)` and `lines = ...`, replace the `command_line = _find_command_line(...) or subject` block with:

```python
    own_text = _strip_quoted_reply(text)
    first = next((ln.strip() for ln in own_text.splitlines() if ln.strip()), "")
    m_scripture = re.match(r"^scripture\s*:\s*(.*)$", first, re.IGNORECASE)
    if m_scripture:
        cmd = {"command": "SCRIPTURE", "ref": m_scripture.group(1).strip()}
        command_line = first
    else:
        command_line = _find_command_line(own_text) or subject
        cmd = parse_command_line(command_line)
```

(`re` is already imported in handlers.py.)

Before the LIST branch, add a SCRIPTURE branch **above** the generic command-domain check so verse can stay silent:

```python
    if cmd.get("command") == "SCRIPTURE":
        ref = (cmd.get("ref") or "").strip()
        if not domain_allowed(sender, command_domains()) or not ref:
            logger.warning("Rejected SCRIPTURE from %s ref=%r", sender, ref)
            return None, None, None
        try:
            site_events.publish_verse(ref)
        except Exception:
            logger.exception("Failed to publish display.verse ref=%r", ref)
        return None, None, None
```

Leave the existing `if cmd.get("command") in ("LIST", ...)` domain check in place for the other commands.

- [ ] **Step 5: Run scripture tests**

Run: `./scripts/pytest.sh tests/test_imap_scripture.py tests/test_imap_approval.py -q`

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add service/site_events.py service/imap_commands/handlers.py tests/test_imap_scripture.py
git commit -m "feat: publish Scripture: IMAP command to events:display"
```

---

### Task 3: Docs, config, env example

**Files:**
- Modify: `service/imap_commands/README.md`
- Modify: `CLAUDE.md` (the Command-folder / `approval_allowlist` bullets)
- Modify: `.env.example`
- Modify: `local/config.json` (delete `approval_allowlist` only; do not reformat the jobs list)

**Interfaces:** none

- [ ] **Step 1: README**

Replace the “Sending a command” paragraph that mentions `COMMAND_ALLOWED_DOMAINS` as if it were already enforced, and add a Scripture section. The sending section must say:

- Sender domain must be in `COMMAND_ALLOWED_DOMAINS`.
- `APPROVE`/`DENY` also need `APPROVAL_ALLOWED_DOMAINS` (do not put `l3harris.com` there).
- Commands still have to land in `Labels/Command`.
- Verse: first body line `Scripture: <ref>`, subject ignored, **no reply**. Proton sieve: body starts with `Scripture:` → Command.

- [ ] **Step 2: CLAUDE.md**

In the “Registration approval replies must reach the Command folder” bullet, replace `approval_allowlist` with `APPROVAL_ALLOWED_DOMAINS` / `COMMAND_ALLOWED_DOMAINS`. Add one sentence that `Scripture:` is a silent Command-folder command.

- [ ] **Step 3: `.env.example`**

Under the IMAP listener block, keep `COMMAND_ALLOWED_DOMAINS` and add:

```
APPROVAL_ALLOWED_DOMAINS=pricefam.email
```

- [ ] **Step 4: `local/config.json`**

Delete the top-level `"approval_allowlist": [ "webmaster@pricefam.email" ]` key (and its comma so the JSON stays valid). Do not add a replacement key.

- [ ] **Step 5: Set live `.env`**

If `APPROVAL_ALLOWED_DOMAINS` is missing from `/srv/docker/cortex/.env`, append `APPROVAL_ALLOWED_DOMAINS=pricefam.email`. Do not print or commit `.env`. `COMMAND_ALLOWED_DOMAINS` is already set.

- [ ] **Step 6: Run the IMAP tests once more**

Run: `./scripts/pytest.sh tests/test_imap_auth.py tests/test_imap_approval.py tests/test_imap_scripture.py -q`

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add service/imap_commands/README.md CLAUDE.md .env.example local/config.json
git commit -m "docs: IMAP domain gate and Scripture command"
```

Do not `git add .env`.

---

## Done when

- [ ] `./scripts/pytest.sh tests/test_imap_auth.py tests/test_imap_approval.py tests/test_imap_scripture.py -q` passes
- [ ] `approval_allowlist` is gone from `local/config.json` and from Python
- [ ] `SCRIPTURE` publishes `display.verse` `{ref}` and returns silence
- [ ] `@l3harris.com` can set a verse (once display ships) but cannot `APPROVE`
