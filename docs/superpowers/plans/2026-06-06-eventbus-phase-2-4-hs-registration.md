# Event Bus Phase 2–4: hs Registration Round-Trip — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Complete the inbound round-trip for hs.coviecraft.dev new-user registration: a visitor registers → cortex emails the admin → the admin replies `APPROVE`/`DENY` → cortex routes the decision over the bus → hs activates the family and emails the visitor a welcome.

**Architecture:** Builds on the phase 0–1 bus. hs gains open registration (a `pending_registrations` table + `/register`), publishes an admin-notification to the existing `email.send` stream, and runs a background consumer on a new `events:hs` stream. cortex's IMAP command layer gains `APPROVE <token>` / `DENY <token>` commands (gated by a sender allowlist) that publish a `registration.decision` to `events:<site>`. A shared consumer helper is added to `eventbus-kit`.

**Tech Stack:** Python 3.12, FastAPI (hs), stdlib sqlite3, redis-py Streams (via eventbus-kit), fakeredis for tests, Proton Bridge SMTP/IMAP (cortex).

**Builds on:** `docs/superpowers/specs/2026-06-06-cortex-website-email-eventbus-design.md` (phases 2–4) and the completed phase 0–1 (`email.send` stream + cortex worker + eventbus-kit).

**Token contract (spans all phases):** A registration token is `hs-<urlsafe-random>`. It is self-describing: the prefix before the first `-` is the site, so cortex routes a decision for `hs-…` to stream `events:hs`. hs owns and validates the token.

**Event contract:** `events:hs` carries envelope `type="registration.decision"`, `payload={"decision": "approve"|"deny", "token": "hs-…", "approver": "<email>"}`, `correlation_id=token`. Consumer group `hs`.

**Operational prerequisite (documented, not coded):** cortex's IMAP listener watches the Proton `Command` folder. For the admin's `APPROVE`/`DENY` reply to be processed, it must land there. Set up a one-time Proton sieve filter routing messages whose subject/body starts with `APPROVE ` or `DENY ` into `Command`. Captured in the final task's notes.

---

## File Structure

**eventbus-kit (`/srv/docker/eventbus/eventbus-kit/`, git repo `/srv/docker/eventbus`):**
- `eventbus/consumer.py` *(new)* — generic consumer loop (`run_forever`) + `process_once` + `PermanentMessageError`, mirroring the cortex worker's dispatch/dead-letter semantics but reusable.
- `eventbus/__init__.py` — export the new consumer symbols.
- `eventbus/envelope.py` — add `events_stream(site)` helper + `REGISTRATION_DECISION` type constant.
- `tests/test_consumer.py` *(new)*.

**hs (`/srv/docker/websites/coviecraft/hs/`, git repo `hs/.git`):**
- `app/db.py` — add `pending_registrations` to `SCHEMA`.
- `app/registration.py` *(new)* — token mint + pending CRUD + approve/deny transitions.
- `app/bus.py` *(new)* — lazy `EventBus` accessor + `publish_email(...)` + `events_consumer(...)` wiring.
- `app/config.py` — add `admin_email()`, `eventbus_enabled()`.
- `app/routes/register_routes.py` *(new)* — `GET/POST /register`.
- `app/registration_consumer.py` *(new)* — the `registration.decision` handler (approve → create family/user + welcome; deny → mark denied).
- `app/main.py` — include the register router; add a lifespan that starts/stops the consumer when `HS_EVENTBUS_ENABLED=1`.
- `app/templates/register.html`, `app/templates/register_pending.html` *(new)*.
- `app/requirements.txt` — add `redis>=5,<6` (+ `fakeredis>=2.21` for tests).
- `docker-compose.yml` — join `eventbus`, mount the kit, add the redis secret + env.
- `app/tests/test_registration.py`, `app/tests/test_register_routes.py`, `app/tests/test_registration_consumer.py` *(new)*.
- `app/tests/conftest.py` — ensure `eventbus` importable + a `bus` fixture; set `HS_EVENTBUS_ENABLED=0`.

**cortex (`/srv/docker/cortex/`, git repo, branch off `main`):**
- `service/imap_commands/parser.py` — parse `APPROVE <token>` / `DENY <token>`.
- `service/site_events.py` *(new)* — `publish_decision(token, decision, approver)` (derives `events:<site>`).
- `service/imap_commands/handlers.py` — handle `APPROVE`/`DENY` with allowlist check → `publish_decision` → email reply.
- `tests/test_imap_approval.py` *(new)*, `tests/test_parser_approval.py` *(new)*.

---

## Phase 2 — hs registration → admin notification

### Task 1: `pending_registrations` schema + `registration.py` (TDD)

**Files:**
- Modify: `/srv/docker/websites/coviecraft/hs/app/db.py`
- Create: `/srv/docker/websites/coviecraft/hs/app/registration.py`
- Test: `/srv/docker/websites/coviecraft/hs/app/tests/test_registration.py`

- [ ] **Step 1: Write the failing test** — `app/tests/test_registration.py`:
```python
import registration as reg


def test_make_token_is_site_prefixed():
    t = reg.make_token()
    assert t.startswith("hs-")
    assert len(t) > 10


def test_create_and_fetch_pending(conn):
    token = reg.create_pending(conn, family_name="Price", name="Ben",
                               email="ben@example.com", password_hash="x", ttl_days=7)
    row = reg.get_pending(conn, token)
    assert row["family_name"] == "Price"
    assert row["email"] == "ben@example.com"
    assert row["status"] == "pending"


def test_get_pending_missing_returns_none(conn):
    assert reg.get_pending(conn, "hs-nope") is None


def test_email_pending_or_active(conn):
    reg.create_pending(conn, family_name="P", name="B",
                       email="ben@example.com", password_hash="x")
    assert reg.email_taken(conn, "ben@example.com") is True
    assert reg.email_taken(conn, "other@example.com") is False


def test_mark_denied(conn):
    token = reg.create_pending(conn, family_name="P", name="B",
                               email="b@example.com", password_hash="x")
    reg.mark_status(conn, token, "denied")
    assert reg.get_pending(conn, token)["status"] == "denied"
```

- [ ] **Step 2: Run it, confirm it FAILS**

Run: `cd /srv/docker/websites/coviecraft/hs && ./run_tests.sh -k registration`
Expected: FAIL — `No module named 'registration'`.

- [ ] **Step 3: Add the table to `app/db.py`**

In `SCHEMA`, after the `users` table block, add:
```sql
CREATE TABLE IF NOT EXISTS pending_registrations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    token         TEXT NOT NULL UNIQUE,
    family_name   TEXT NOT NULL,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'approved', 'denied')),
    created_at    TEXT NOT NULL,
    expires_at    TEXT NOT NULL
);
```
(`init_db` runs `executescript(SCHEMA)` with `IF NOT EXISTS`, so this creates the table on both fresh and existing DBs — no `migrate()` change needed.)

- [ ] **Step 4: Create `app/registration.py`**
```python
"""Pending self-service registrations: token mint + CRUD + status transitions.

A pending row holds the would-be family/admin-user until an admin approves it
(see registration_consumer). The token is site-prefixed ('hs-…') so cortex can
route an approval decision back to this site's event stream.
"""
import secrets
import sqlite3


def make_token() -> str:
    return "hs-" + secrets.token_urlsafe(24)


def create_pending(conn: sqlite3.Connection, *, family_name: str, name: str,
                   email: str, password_hash: str, ttl_days: int = 7) -> str:
    token = make_token()
    conn.execute(
        "INSERT INTO pending_registrations "
        "(token, family_name, name, email, password_hash, status, created_at, expires_at) "
        "VALUES (?, ?, ?, ?, ?, 'pending', datetime('now'), datetime('now', ?))",
        (token, family_name, name, email, password_hash, f"+{int(ttl_days)} days"),
    )
    conn.commit()
    return token


def get_pending(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM pending_registrations WHERE token = ?", (token,)
    ).fetchone()


def get_active_pending(conn: sqlite3.Connection, token: str) -> sqlite3.Row | None:
    """A pending row that is still actionable: status='pending' and not expired."""
    return conn.execute(
        "SELECT * FROM pending_registrations "
        "WHERE token = ? AND status = 'pending' AND expires_at > datetime('now')",
        (token,),
    ).fetchone()


def email_taken(conn: sqlite3.Connection, email: str) -> bool:
    """True if the email already belongs to a user or a pending registration."""
    u = conn.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone()
    if u:
        return True
    p = conn.execute(
        "SELECT 1 FROM pending_registrations WHERE email = ? AND status = 'pending'",
        (email,),
    ).fetchone()
    return p is not None


def mark_status(conn: sqlite3.Connection, token: str, status: str) -> None:
    conn.execute(
        "UPDATE pending_registrations SET status = ? WHERE token = ?", (status, token)
    )
    conn.commit()
```

- [ ] **Step 5: Run, confirm PASS**

Run: `cd /srv/docker/websites/coviecraft/hs && ./run_tests.sh -k registration`
Expected: 5 passed.

- [ ] **Step 6: Commit**
```bash
cd /srv/docker/websites/coviecraft/hs
git add app/db.py app/registration.py app/tests/test_registration.py
git commit -m "hs: pending_registrations table + registration helpers"
```
End with a blank line then: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

### Task 2: hs bus accessor + config (TDD)

**Files:**
- Create: `/srv/docker/websites/coviecraft/hs/app/bus.py`
- Modify: `/srv/docker/websites/coviecraft/hs/app/config.py`
- Modify: `/srv/docker/websites/coviecraft/hs/app/tests/conftest.py`
- Test: `/srv/docker/websites/coviecraft/hs/app/tests/test_bus.py`

- [ ] **Step 1: Make `eventbus` importable in hs tests + add a `bus` fixture**

In `app/tests/conftest.py`, after the existing `sys.path.insert(...)` line add:
```python
# The shared event-bus client lives outside the hs repo; put it on the path for tests.
_KIT = Path(__file__).resolve().parents[4] / "eventbus" / "eventbus-kit"
if _KIT.exists():
    sys.path.insert(0, str(_KIT))

# Never start the background consumer during tests.
os.environ.setdefault("HS_EVENTBUS_ENABLED", "0")
```
And append a fixture:
```python
@pytest.fixture
def bus():
    import fakeredis
    from eventbus import EventBus
    return EventBus(fakeredis.FakeStrictRedis(decode_responses=True), source="hs")
```
Note: `parents[4]` from `app/tests/conftest.py` = `/srv/docker` (tests → app → hs → coviecraft → websites → … check: conftest is at `hs/app/tests/conftest.py`; `parents[0]=tests, [1]=app, [2]=hs, [3]=coviecraft, [4]=websites`). The kit is at `/srv/docker/eventbus/eventbus-kit`, so from `websites` it is `../eventbus/eventbus-kit`. Use `parents[4].parent / "eventbus" / "eventbus-kit"` (i.e. `/srv/docker/eventbus/eventbus-kit`). VERIFY the path resolves in Step 3; adjust the `parents[]` index until `_KIT.exists()`.

- [ ] **Step 2: Write the failing test** — `app/tests/test_bus.py`:
```python
import bus as hsbus
from eventbus import EMAIL_SEND


def test_publish_email_puts_message_on_email_send(bus, monkeypatch):
    monkeypatch.setattr(hsbus, "get_bus", lambda: bus)
    bus.ensure_group(EMAIL_SEND, "cortex-emailer")
    hsbus.publish_email(to=["a@b.com"], subject="Hi", html="<p>x</p>", correlation_id="hs-1")

    msgs = bus.read(EMAIL_SEND, "cortex-emailer", "c1", block_ms=10)
    assert len(msgs) == 1
    assert msgs[0].payload["to"] == ["a@b.com"]
    assert msgs[0].payload["subject"] == "Hi"
    assert msgs[0].correlation_id == "hs-1"
```

- [ ] **Step 3: Add config accessors to `app/config.py`**
```python
def admin_email() -> str:
    """Where new-registration approval requests are emailed. Empty if unset."""
    return os.environ.get("HS_ADMIN_EMAIL", "").strip()


def eventbus_enabled() -> bool:
    return os.environ.get("HS_EVENTBUS_ENABLED", "0") == "1"
```

- [ ] **Step 4: Create `app/bus.py`**
```python
"""Thin accessor over the shared eventbus-kit client for hs.

`get_bus()` lazily builds and caches an EventBus from env (EVENTBUS_REDIS_*).
`publish_email()` enqueues an outbound message for cortex's worker to deliver.
"""
from eventbus import EMAIL_SEND, EventBus

_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus.from_env(source="hs")
    return _bus


def publish_email(*, to: list[str], subject: str, html: str,
                  correlation_id: str | None = None) -> str:
    return get_bus().publish(
        EMAIL_SEND, "email.send",
        payload={"to": to, "subject": subject, "html": html},
        correlation_id=correlation_id,
    )
```

- [ ] **Step 5: Run, confirm PASS**

Run: `cd /srv/docker/websites/coviecraft/hs && ./run_tests.sh deps -k bus` (the `deps` arg rebuilds the venv so it picks up the kit path; if `fakeredis`/`redis` aren't installed yet, add them in Step 6 of Task tasks — for now install into the venv: `.venv/bin/pip install redis fakeredis -q`, then rerun without `deps`).
Expected: 1 passed.

- [ ] **Step 6: Commit**
```bash
cd /srv/docker/websites/coviecraft/hs
git add app/bus.py app/config.py app/tests/conftest.py app/tests/test_bus.py
git commit -m "hs: eventbus accessor (publish_email) + admin_email/eventbus_enabled config"
```
End with the Co-Authored-By trailer.

---

### Task 3: `/register` routes + templates (TDD)

**Files:**
- Create: `/srv/docker/websites/coviecraft/hs/app/routes/register_routes.py`
- Create: `/srv/docker/websites/coviecraft/hs/app/templates/register.html`
- Create: `/srv/docker/websites/coviecraft/hs/app/templates/register_pending.html`
- Modify: `/srv/docker/websites/coviecraft/hs/app/main.py` (include the router)
- Test: `/srv/docker/websites/coviecraft/hs/app/tests/test_register_routes.py`

- [ ] **Step 1: Write the failing test** — `app/tests/test_register_routes.py`:
```python
from fastapi.testclient import TestClient

import main
import registration as reg
from conftest import TEST_SECRET

GOOD_PW = "familypizzanight"


def make_client(tmp_path, monkeypatch, sent):
    app = main.create_app(db_path=str(tmp_path / "hs.db"), session_secret=TEST_SECRET)
    # Stub the outbound publish so no Redis is needed and we can assert on it.
    import bus as hsbus

    def fake_publish_email(*, to, subject, html, correlation_id=None):
        sent.append({"to": to, "subject": subject, "html": html, "corr": correlation_id})
        return "msg-1"

    monkeypatch.setattr(hsbus, "publish_email", fake_publish_email)
    monkeypatch.setenv("HS_ADMIN_EMAIL", "admin@example.com")
    return TestClient(app)


def test_get_register_form(tmp_path, monkeypatch):
    client = make_client(tmp_path, monkeypatch, [])
    r = client.get("/register")
    assert r.status_code == 200
    assert "register" in r.text.lower()


def test_post_register_creates_pending_and_emails_admin(tmp_path, monkeypatch):
    sent = []
    client = make_client(tmp_path, monkeypatch, sent)
    r = client.post("/register", data={
        "family_name": "Price", "name": "Ben",
        "email": "ben@example.com", "password": GOOD_PW, "website": ""},
        follow_redirects=False)
    assert r.status_code == 200
    assert "pending" in r.text.lower()
    # One admin email was published, addressed to the admin, carrying the token.
    assert len(sent) == 1
    assert sent[0]["to"] == ["admin@example.com"]
    assert "APPROVE hs-" in sent[0]["html"]
    assert sent[0]["corr"].startswith("hs-")


def test_post_register_rejects_duplicate_email(tmp_path, monkeypatch):
    sent = []
    client = make_client(tmp_path, monkeypatch, sent)
    data = {"family_name": "P", "name": "B", "email": "ben@example.com",
            "password": GOOD_PW, "website": ""}
    client.post("/register", data=data)
    r = client.post("/register", data=data)         # second time
    assert r.status_code == 200
    assert "already" in r.text.lower()
    assert len(sent) == 1                            # no second email


def test_post_register_honeypot_silently_ok(tmp_path, monkeypatch):
    sent = []
    client = make_client(tmp_path, monkeypatch, sent)
    r = client.post("/register", data={
        "family_name": "P", "name": "B", "email": "bot@example.com",
        "password": GOOD_PW, "website": "http://spam"})   # honeypot filled
    assert r.status_code == 200
    assert len(sent) == 0                            # no email, treated as spam


def test_post_register_rejects_weak_password(tmp_path, monkeypatch):
    sent = []
    client = make_client(tmp_path, monkeypatch, sent)
    r = client.post("/register", data={
        "family_name": "P", "name": "B", "email": "ok@example.com",
        "password": "short", "website": ""})
    assert r.status_code == 200
    assert "at least 10" in r.text
    assert len(sent) == 0
```

- [ ] **Step 2: Run, confirm FAIL**

Run: `cd /srv/docker/websites/coviecraft/hs && ./run_tests.sh -k register_routes`
Expected: FAIL — 404s / no `/register` route.

- [ ] **Step 3: Create `app/routes/register_routes.py`**
```python
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse

import auth
import bus as hsbus
import config
import registration as reg
from deps import get_db

router = APIRouter()


def _admin_html(token: str, family_name: str, name: str, email: str) -> str:
    return (
        f"<p>New homeschool registration awaiting approval:</p>"
        f"<ul><li>Family: {family_name}</li><li>Name: {name}</li>"
        f"<li>Email: {email}</li></ul>"
        f"<p>To approve, reply with exactly:</p><pre>APPROVE {token}</pre>"
        f"<p>To deny, reply with:</p><pre>DENY {token}</pre>"
    )


@router.get("/register", response_class=HTMLResponse)
async def register_form(request: Request):
    return request.app.state.templates.TemplateResponse(request, "register.html", {})


@router.post("/register", response_class=HTMLResponse)
async def register_submit(
    request: Request,
    family_name: str = Form(...),
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    website: str = Form(""),          # honeypot — real users never fill this
    conn=Depends(get_db),
):
    tmpl = request.app.state.templates
    # Honeypot: pretend success, send nothing.
    if website.strip():
        return tmpl.TemplateResponse(request, "register_pending.html", {})

    if not auth.valid_email(email):
        return tmpl.TemplateResponse(request, "register.html",
                                     {"error": "Please enter a valid email address."})
    email = auth.normalize_email(email)
    pw_error = auth.validate_password(password, login=email, family_name=family_name)
    if pw_error:
        return tmpl.TemplateResponse(request, "register.html", {"error": pw_error})
    if reg.email_taken(conn, email):
        return tmpl.TemplateResponse(
            request, "register.html",
            {"error": "That email is already registered or pending approval."})

    token = reg.create_pending(
        conn, family_name=family_name.strip(), name=name.strip(),
        email=email, password_hash=auth.hash_password(password))

    admin = config.admin_email()
    if admin:
        hsbus.publish_email(
            to=[admin],
            subject=f"[hs] New registration: {family_name.strip()}",
            html=_admin_html(token, family_name.strip(), name.strip(), email),
            correlation_id=token)
    return tmpl.TemplateResponse(request, "register_pending.html", {})
```

- [ ] **Step 4: Create the templates**

`app/templates/register.html`:
```html
{% extends "base.html" %}
{% block content %}
<h1>Register</h1>
{% if error %}<p class="error">{{ error }}</p>{% endif %}
<form method="post" action="/register">
  <label>Family name <input name="family_name" required></label>
  <label>Your name <input name="name" required></label>
  <label>Email <input type="email" name="email" required></label>
  <label>Password <input type="password" name="password" required></label>
  <input type="text" name="website" class="hp" tabindex="-1" autocomplete="off" aria-hidden="true">
  <button type="submit">Request access</button>
</form>
{% endblock %}
```
`app/templates/register_pending.html`:
```html
{% extends "base.html" %}
{% block content %}
<h1>Request received</h1>
<p>Thanks! Your registration is pending approval. You'll get an email once it's approved.</p>
{% endblock %}
```
Note: the honeypot input needs to be visually hidden via CSS (CSP forbids inline styles). Add to the existing site CSS (find the stylesheet `base.html` links, e.g. `app/static/css/*.css`) a rule:
```css
.hp { position: absolute; left: -9999px; width: 1px; height: 1px; overflow: hidden; }
```
Read `base.html` to confirm the `{% block content %}` name and the CSS file path; adjust the templates to match the existing block/structure if different.

- [ ] **Step 5: Register the router in `app/main.py`**

In `create_app`, where the other routers are imported/included, add `register_routes` to the import and include it:
```python
    from routes import auth_routes, children, learner, parent, records, register_routes, room, ws
    app.include_router(auth_routes.router)
    app.include_router(register_routes.router)
```
(Keep the other `include_router` calls.)

- [ ] **Step 6: Run, confirm PASS**

Run: `cd /srv/docker/websites/coviecraft/hs && ./run_tests.sh -k "register_routes or registration or bus"`
Expected: all pass.

- [ ] **Step 7: Full suite (no regressions)**

Run: `cd /srv/docker/websites/coviecraft/hs && ./run_tests.sh`
Expected: all prior tests still pass + the new ones.

- [ ] **Step 8: Commit**
```bash
cd /srv/docker/websites/coviecraft/hs
git add app/routes/register_routes.py app/templates/register.html \
        app/templates/register_pending.html app/main.py \
        app/tests/test_register_routes.py app/static
git commit -m "hs: open /register flow — pending row + admin-notification email via bus"
```
End with the Co-Authored-By trailer. (Adjust the `git add` for the actual CSS file you edited.)

---

### Task 4: hs compose/deps wiring + deploy (outbound half live)

**Files:**
- Modify: `/srv/docker/websites/coviecraft/hs/app/requirements.txt`
- Modify: `/srv/docker/websites/coviecraft/hs/docker-compose.yml`

- [ ] **Step 1: Add deps**

In `app/requirements.txt` add:
```
redis>=5,<6
fakeredis>=2.21
```

- [ ] **Step 2: Wire `docker-compose.yml`** (hs service)

Add to the `hs` service:
- `networks:` — add `- eventbus` (keep `- proxy`).
- `volumes:` — add `- ../../../eventbus/eventbus-kit:/eventbus-kit:ro`.
- `environment:` — add:
  ```yaml
      - PYTHONPATH=/eventbus-kit
      - EVENTBUS_REDIS_HOST=eventbus-redis
      - EVENTBUS_REDIS_PORT=6379
      - HS_EVENTBUS_ENABLED=1
      - HS_ADMIN_EMAIL=${HS_ADMIN_EMAIL:-}
  ```
- `secrets:` — add `- eventbus_redis_password`.

Top level:
- `secrets:` — add:
  ```yaml
    eventbus_redis_password:
      file: ../../../eventbus/secrets/eventbus_redis_password
  ```
- `networks:` — add `eventbus: {external: true}` (keep `proxy: {external: true}`).

VERIFY the relative paths: hs compose dir is `/srv/docker/websites/coviecraft/hs`; the kit is `/srv/docker/eventbus/eventbus-kit` ⇒ `../../../eventbus/eventbus-kit`. Confirm with `ls ../../../eventbus/eventbus-kit` from the hs dir before relying on it.

Set `HS_ADMIN_EMAIL` in the hs `.env` (create/edit `/srv/docker/websites/coviecraft/hs/.env`) to the real admin address. Document this in the final notes.

- [ ] **Step 3: Validate + deploy**
```bash
cd /srv/docker/websites/coviecraft/hs
ls ../../../eventbus/eventbus-kit/eventbus    # confirm path resolves
docker compose config >/dev/null && echo "compose OK"
docker compose up -d --build --force-recreate
sleep 4
docker compose exec hs python -c "from eventbus import EventBus; print('kit ok')"
```
Expected: `compose OK` then `kit ok`. (Use dangerouslyDisableSandbox for docker.)

- [ ] **Step 4: Commit**
```bash
cd /srv/docker/websites/coviecraft/hs
git add app/requirements.txt docker-compose.yml
git commit -m "hs: join eventbus network, mount kit, add redis dep + admin email env"
```
End with the Co-Authored-By trailer.

**After Phase 2:** registering on hs creates a pending row and emails the admin a token. Inbound approval is built next.

---

## Phase 3 — cortex inbound approval router

### Task 5: parse `APPROVE`/`DENY` commands (TDD)

**Files:**
- Modify: `/srv/docker/cortex/service/imap_commands/parser.py`
- Test: `/srv/docker/cortex/tests/test_parser_approval.py`

Work in `/srv/docker/cortex` on a fresh branch off `main`: `git checkout main && git checkout -b eventbus-phase2`.

- [ ] **Step 1: Write the failing test** — `tests/test_parser_approval.py`:
```python
from service.imap_commands.parser import parse_command_line


def test_parse_approve():
    assert parse_command_line("APPROVE hs-abc123") == {"command": "APPROVE", "token": "hs-abc123"}


def test_parse_deny_case_insensitive():
    assert parse_command_line("deny hs-XYZ_9") == {"command": "DENY", "token": "hs-XYZ_9"}


def test_parse_approve_requires_token():
    # bare APPROVE with no token is not a valid single-word command match
    out = parse_command_line("APPROVE")
    assert out.get("command") != "APPROVE"


def test_existing_list_still_parses():
    assert parse_command_line("LIST") == {"command": "LIST"}
```

- [ ] **Step 2: Run, confirm FAIL**

Run: `cd /srv/docker/cortex && PYTHONPATH=../eventbus/eventbus-kit .venv/bin/python -m pytest tests/test_parser_approval.py -q`
Expected: FAIL (APPROVE not recognized; currently a bare `APPROVE` would match the single-word rule, and `APPROVE hs-…` returns `{"command": None}`).

- [ ] **Step 3: Add parsing to `parser.py`**

In `parse_command_line`, BEFORE the single-word `re.fullmatch(r"\w+", line ...)` block, add:
```python
    # --- APPROVE / DENY <token> (token is site-prefixed, e.g. hs-…) ---
    m = re.match(r"^(APPROVE|DENY)\s+([A-Za-z0-9][A-Za-z0-9._-]*)\s*$", line, re.IGNORECASE)
    if m:
        return {"command": m.group(1).upper(), "token": m.group(2)}
```
(Placing it before the single-word match means `APPROVE` alone still falls through to the single-word rule and is later treated as unknown — which `test_parse_approve_requires_token` asserts.)

- [ ] **Step 4: Run, confirm PASS**

Run: `cd /srv/docker/cortex && PYTHONPATH=../eventbus/eventbus-kit .venv/bin/python -m pytest tests/test_parser_approval.py -q`
Expected: 4 passed.

- [ ] **Step 5: Commit**
```bash
cd /srv/docker/cortex
git add service/imap_commands/parser.py tests/test_parser_approval.py
git commit -m "cortex: parse APPROVE/DENY <token> IMAP commands"
```
End with the Co-Authored-By trailer.

---

### Task 6: decision publisher + handler with allowlist (TDD)

**Files:**
- Create: `/srv/docker/cortex/service/site_events.py`
- Modify: `/srv/docker/cortex/service/imap_commands/handlers.py`
- Modify: `/srv/docker/eventbus/eventbus-kit/eventbus/envelope.py` (add helpers — see Step 3)
- Test: `/srv/docker/cortex/tests/test_imap_approval.py`

- [ ] **Step 1: Add stream/type helpers to `eventbus/envelope.py`**

Append:
```python
REGISTRATION_DECISION = "registration.decision"


def events_stream(site: str) -> str:
    """Per-site inbound event stream name, e.g. events:hs."""
    return f"events:{site}"


def site_from_token(token: str) -> str:
    """The site prefix of a token, e.g. 'hs' from 'hs-abc123'."""
    return token.split("-", 1)[0]
```
And export them in `eventbus/__init__.py` (`from .envelope import ... REGISTRATION_DECISION, events_stream, site_from_token`). Run the kit tests to confirm no breakage: `cd /srv/docker/eventbus/eventbus-kit && .venv/bin/python -m pytest -q`. Commit in the eventbus repo:
```bash
cd /srv/docker/eventbus
git add eventbus-kit/eventbus/envelope.py eventbus-kit/eventbus/__init__.py
git commit -m "eventbus-kit: events_stream/site_from_token helpers + registration.decision type"
```
(with the Co-Authored-By trailer)

- [ ] **Step 2: Write the failing test** — `tests/test_imap_approval.py`:
```python
import fakeredis
import pytest

from eventbus import EventBus, events_stream
from service.imap_commands import handlers


@pytest.fixture
def bus():
    return EventBus(fakeredis.FakeStrictRedis(decode_responses=True), source="cortex")


def _email(subject: str) -> bytes:
    return (f"Subject: {subject}\r\nFrom: admin@example.com\r\n"
            f"Content-Type: text/plain\r\n\r\n{subject}\r\n").encode()


def test_approve_from_allowlisted_sender_publishes_decision(bus, monkeypatch):
    monkeypatch.setattr("service.site_events.get_bus", lambda: bus)
    cfg = {"approval_allowlist": ["admin@example.com"]}
    bus.ensure_group(events_stream("hs"), "hs")

    to, subj, html = handlers.handle_command(
        _email("APPROVE hs-abc123"), cfg, None, from_addr="admin@example.com")

    msgs = bus.read(events_stream("hs"), "hs", "c1", block_ms=10)
    assert len(msgs) == 1
    assert msgs[0].type == "registration.decision"
    assert msgs[0].payload == {"decision": "approve", "token": "hs-abc123",
                               "approver": "admin@example.com"}
    assert "approve" in (html or "").lower()


def test_decision_from_non_allowlisted_sender_is_rejected(bus, monkeypatch):
    monkeypatch.setattr("service.site_events.get_bus", lambda: bus)
    cfg = {"approval_allowlist": ["admin@example.com"]}
    bus.ensure_group(events_stream("hs"), "hs")

    to, subj, html = handlers.handle_command(
        _email("APPROVE hs-abc123"), cfg, None, from_addr="stranger@evil.com")

    assert bus.read(events_stream("hs"), "hs", "c1", block_ms=10) == []   # nothing published
    assert "not authorized" in (html or "").lower()
```

- [ ] **Step 2b: Run, confirm FAIL**

Run: `cd /srv/docker/cortex && PYTHONPATH=../eventbus/eventbus-kit .venv/bin/python -m pytest tests/test_imap_approval.py -q`
Expected: FAIL — `service.site_events` missing / handlers don't handle APPROVE.

- [ ] **Step 3: Create `service/site_events.py`**
```python
"""Publish inbound IMAP decisions onto a site's event stream."""
from __future__ import annotations

from eventbus import REGISTRATION_DECISION, EventBus, events_stream, site_from_token

_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus.from_env(source="cortex")
    return _bus


def publish_decision(token: str, decision: str, approver: str) -> str:
    """Route a decision (approve|deny) to events:<site> derived from the token prefix."""
    stream = events_stream(site_from_token(token))
    return get_bus().publish(
        stream, REGISTRATION_DECISION,
        payload={"decision": decision, "token": token, "approver": approver},
        correlation_id=token)
```

- [ ] **Step 4: Handle APPROVE/DENY in `handlers.py`**

Add an import near the top: `from service import site_events`.
In `handle_command`, after the `RUN` branch and before the final `else` (unknown), add:
```python
    # ===================================================================
    # APPROVE / DENY <token>  (gated by sender allowlist)
    # ===================================================================
    elif cmd["command"] in ("APPROVE", "DENY"):
        allow = {a.strip().lower() for a in cfg.get("approval_allowlist", []) if a}
        sender_lc = (sender or "").strip().lower()
        token = cmd["token"]
        if sender_lc not in allow:
            logger.warning("Rejected %s for %s from non-allowlisted %s",
                           cmd["command"], token, sender)
            subj = "Not authorized"
            html = ("<p>Your address is not authorized to approve registrations. "
                    "No action taken.</p>")
        else:
            decision = "approve" if cmd["command"] == "APPROVE" else "deny"
            try:
                site_events.publish_decision(token, decision, sender_lc)
                subj = f"Recorded: {decision} {token}"
                html = f"<p>Decision <b>{decision}</b> recorded for <code>{token}</code>.</p>"
            except Exception as exc:
                logger.exception("Failed to publish decision for %s", token)
                subj = "Decision failed"
                html = f"<p>Could not record decision for <code>{token}</code>:</p><pre>{exc}</pre>"
```
(`sender` is already computed near the top of `handle_command` as `from_addr or msg["From"]`.)

Also: the command-line detection in `handle_command` currently only scans for the words `RUN`/`LIST`/`CAREER`/`REPORT` to pick the command line out of the subject/body. Add `APPROVE`/`DENY` to BOTH word lists so an approval in the subject or body is found:
```python
    if not any(word in command_line.upper() for word in ("RUN", "LIST", "CAREER", "REPORT", "APPROVE", "DENY")):
        for line in lines:
            if any(word in line.upper() for word in ("RUN", "LIST", "CAREER", "REPORT", "APPROVE", "DENY")):
                command_line = line
                break
```

- [ ] **Step 5: Run, confirm PASS + full suite + lint**
```bash
cd /srv/docker/cortex
PYTHONPATH=../eventbus/eventbus-kit .venv/bin/python -m pytest tests/test_imap_approval.py tests/test_parser_approval.py -q   # expect all pass
PYTHONPATH=../eventbus/eventbus-kit .venv/bin/python -m pytest -q                                                              # full suite, no regressions
PYTHONPATH=../eventbus/eventbus-kit .venv/bin/ruff check service/site_events.py service/imap_commands/handlers.py service/imap_commands/parser.py
```

- [ ] **Step 6: Commit (cortex)**
```bash
cd /srv/docker/cortex
git add service/site_events.py service/imap_commands/handlers.py tests/test_imap_approval.py
git commit -m "cortex: route allowlisted APPROVE/DENY replies to events:<site>"
```
End with the Co-Authored-By trailer.

- [ ] **Step 7: Add `approval_allowlist` to cortex config**

Read `/srv/docker/cortex/local/config.json` and `service/config_schema.py`. Add a top-level `"approval_allowlist": ["<admin-email>"]` to `local/config.json`. If `config_schema` strictly validates unknown top-level keys, add `approval_allowlist` (array of strings, optional, default `[]`) to the schema so validation passes; otherwise no schema change is needed. Verify: `docker compose run --rm cortex python -m service.cli validate-config` → `OK`. Commit any schema change:
```bash
cd /srv/docker/cortex
git add service/config_schema.py 2>/dev/null; git commit -m "cortex: allow approval_allowlist in config" || echo "no schema change needed"
```
(`local/` is gitignored — the config.json value is host state, not committed.)

---

## Phase 4 — hs consumer: activate + welcome

### Task 7: generic consumer helper in eventbus-kit (TDD)

**Files:**
- Create: `/srv/docker/eventbus/eventbus-kit/eventbus/consumer.py`
- Modify: `/srv/docker/eventbus/eventbus-kit/eventbus/__init__.py`
- Test: `/srv/docker/eventbus/eventbus-kit/tests/test_consumer.py`

- [ ] **Step 1: Write the failing test** — `tests/test_consumer.py`:
```python
import fakeredis
import pytest

from eventbus import EventBus, PermanentMessageError, process_once

STREAM = "events:hs"
GROUP = "hs"
DEAD = "events:hs.dead"


@pytest.fixture
def bus():
    return EventBus(fakeredis.FakeStrictRedis(decode_responses=True), source="t")


def _pub(bus, payload):
    bus.ensure_group(STREAM, GROUP)
    return bus.publish(STREAM, "x", payload=payload)


def test_handler_runs_and_acks(bus):
    seen = []
    _pub(bus, {"n": 1})
    process_once(bus, STREAM, GROUP, "c1", lambda m: seen.append(m.payload["n"]),
                 dead_stream=DEAD)
    assert seen == [1]
    assert bus.r.xpending(STREAM, GROUP)["pending"] == 0


def test_permanent_error_dead_letters_immediately(bus):
    _pub(bus, {"bad": True})

    def handler(m):
        raise PermanentMessageError("nope")

    process_once(bus, STREAM, GROUP, "c1", handler, dead_stream=DEAD)
    assert bus.r.xlen(DEAD) == 1
    assert bus.r.xpending(STREAM, GROUP)["pending"] == 0


def test_transient_error_retries_then_dead_letters(bus):
    _pub(bus, {"x": 1})

    def handler(m):
        raise RuntimeError("transient")

    for _ in range(3):
        process_once(bus, STREAM, GROUP, "c1", handler, dead_stream=DEAD,
                     max_attempts=3, min_idle_ms=0)
    assert bus.r.xlen(DEAD) == 1
    assert bus.r.xpending(STREAM, GROUP)["pending"] == 0
```

- [ ] **Step 2: Run, confirm FAIL**

Run: `cd /srv/docker/eventbus/eventbus-kit && .venv/bin/python -m pytest tests/test_consumer.py -q`
Expected: FAIL — `cannot import name 'process_once'/'PermanentMessageError'`.

- [ ] **Step 3: Create `eventbus/consumer.py`**
```python
"""Generic consumer-group loop: claim stale + read new, dispatch to a handler,
ack on success, dead-letter on terminal failure. Shared by services that consume
a stream (mirrors cortex's bespoke email worker, generalized)."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from .client import EventBus, Message

logger = logging.getLogger(__name__)

Handler = Callable[[Message], None]


class PermanentMessageError(Exception):
    """Raised by a handler for a message that must never be retried (dead-letter now)."""


def _dispatch(bus: EventBus, stream: str, group: str, msg: Message, handler: Handler,
              dead_stream: str | None, max_attempts: int) -> None:
    try:
        handler(msg)
        bus.ack(stream, group, msg.id)
    except PermanentMessageError:
        logger.error("[consumer:%s] permanent failure %s -> dead", group, msg.id, exc_info=True)
        if dead_stream:
            bus.to_dead(dead_stream, msg)
        bus.ack(stream, group, msg.id)
    except Exception:
        attempts = bus.delivery_count(stream, group, msg.id)
        if attempts >= max_attempts:
            logger.error("[consumer:%s] %s failed %d attempts -> dead", group, msg.id, attempts)
            if dead_stream:
                bus.to_dead(dead_stream, msg)
            bus.ack(stream, group, msg.id)
        else:
            logger.warning("[consumer:%s] handler failed (attempt %d) for %s; will retry",
                           group, attempts, msg.id, exc_info=True)


def process_once(bus: EventBus, stream: str, group: str, consumer: str, handler: Handler,
                 *, dead_stream: str | None = None, max_attempts: int = 3,
                 min_idle_ms: int = 10000, block_ms: int = 0) -> None:
    bus.ensure_group(stream, group)
    for msg in bus.claim_stale(stream, group, consumer, min_idle_ms=min_idle_ms):
        _dispatch(bus, stream, group, msg, handler, dead_stream, max_attempts)
    for msg in bus.read(stream, group, consumer, count=10, block_ms=block_ms):
        _dispatch(bus, stream, group, msg, handler, dead_stream, max_attempts)


def run_forever(bus_factory: Callable[[], EventBus], stream: str, group: str, consumer: str,
                handler: Handler, stop_event: threading.Event, *, dead_stream: str | None = None,
                max_attempts: int = 3, min_idle_ms: int = 10000, block_ms: int = 5000) -> None:
    backoff = 5
    bus: EventBus | None = None
    while not stop_event.is_set():
        try:
            if bus is None:
                bus = bus_factory()
            backoff = 5
            while not stop_event.is_set():
                process_once(bus, stream, group, consumer, handler,
                             dead_stream=dead_stream, max_attempts=max_attempts,
                             min_idle_ms=min_idle_ms, block_ms=block_ms)
        except Exception as e:
            if stop_event.is_set():
                break
            logger.error("[consumer:%s] loop error: %r; retry in %ds", group, e, backoff)
            bus = None
            slept = 0
            while slept < backoff and not stop_event.is_set():
                time.sleep(1)
                slept += 1
            backoff = min(backoff * 2, 120)
    logger.info("[consumer:%s] stopped", group)
```

- [ ] **Step 4: Export in `eventbus/__init__.py`**
```python
from .consumer import PermanentMessageError, process_once, run_forever
```
(add to imports and to `__all__`).

- [ ] **Step 5: Run, confirm PASS (kit suite)**

Run: `cd /srv/docker/eventbus/eventbus-kit && .venv/bin/python -m pytest -q`
Expected: all pass (envelope + client + consumer).

- [ ] **Step 6: Commit (eventbus repo)**
```bash
cd /srv/docker/eventbus
git add eventbus-kit/eventbus/consumer.py eventbus-kit/eventbus/__init__.py eventbus-kit/tests/test_consumer.py
git commit -m "eventbus-kit: generic consumer (process_once/run_forever + PermanentMessageError)"
```
End with the Co-Authored-By trailer.

---

### Task 8: hs registration consumer handler (TDD)

**Files:**
- Create: `/srv/docker/websites/coviecraft/hs/app/registration_consumer.py`
- Test: `/srv/docker/websites/coviecraft/hs/app/tests/test_registration_consumer.py`

- [ ] **Step 1: Write the failing test** — `app/tests/test_registration_consumer.py`:
```python
import auth
import registration as reg
import registration_consumer as rc


def _decision(token, decision="approve", approver="admin@example.com"):
    return {"type": "registration.decision",
            "payload": {"decision": decision, "token": token, "approver": approver}}


class FakeMsg:
    def __init__(self, env):
        self.envelope = env
        self.payload = env["payload"]
        self.type = env["type"]
        self.correlation_id = env["payload"]["token"]


def test_approve_creates_family_and_user_and_sends_welcome(conn, monkeypatch):
    sent = []
    monkeypatch.setattr(rc, "publish_email",
                        lambda **k: sent.append(k) or "m")
    token = reg.create_pending(conn, family_name="Price", name="Ben",
                               email="ben@example.com",
                               password_hash=auth.hash_password("familypizzanight"))

    rc.handle_decision(conn, FakeMsg(_decision(token)))

    fam = conn.execute("SELECT * FROM families WHERE name='Price'").fetchone()
    assert fam is not None
    user = conn.execute("SELECT * FROM users WHERE email='ben@example.com'").fetchone()
    assert user is not None and user["role"] == "admin"
    assert reg.get_pending(conn, token)["status"] == "approved"
    assert len(sent) == 1 and sent[0]["to"] == ["ben@example.com"]
    assert "/login" in sent[0]["html"]


def test_deny_marks_denied_no_user(conn, monkeypatch):
    monkeypatch.setattr(rc, "publish_email", lambda **k: "m")
    token = reg.create_pending(conn, family_name="P", name="B",
                               email="b@example.com", password_hash="x")
    rc.handle_decision(conn, FakeMsg(_decision(token, decision="deny")))
    assert reg.get_pending(conn, token)["status"] == "denied"
    assert conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0


def test_approve_twice_is_idempotent(conn, monkeypatch):
    monkeypatch.setattr(rc, "publish_email", lambda **k: "m")
    token = reg.create_pending(conn, family_name="P", name="B",
                               email="b@example.com",
                               password_hash=auth.hash_password("familypizzanight"))
    rc.handle_decision(conn, FakeMsg(_decision(token)))
    rc.handle_decision(conn, FakeMsg(_decision(token)))   # redelivery
    assert conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 1


def test_unknown_token_is_noop(conn, monkeypatch):
    monkeypatch.setattr(rc, "publish_email", lambda **k: "m")
    rc.handle_decision(conn, FakeMsg(_decision("hs-nonexistent")))
    assert conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] == 0
```

- [ ] **Step 2: Run, confirm FAIL**

Run: `cd /srv/docker/websites/coviecraft/hs && ./run_tests.sh -k registration_consumer`
Expected: FAIL — no module.

- [ ] **Step 3: Create `app/registration_consumer.py`**
```python
"""Apply registration decisions arriving on events:hs: approve -> create the
family + admin user from the pending row and email a welcome; deny -> mark denied.
Idempotent: a single-use pending row is only acted on while status='pending'."""
import logging

import registration as reg
from bus import publish_email   # re-exported for monkeypatching in tests

logger = logging.getLogger(__name__)

WELCOME_HTML = (
    "<p>Your homeschool account is approved! "
    "You can now sign in here: "
    "<a href=\"https://hs.coviecraft.dev/login\">https://hs.coviecraft.dev/login</a></p>"
)


def handle_decision(conn, msg) -> None:
    payload = msg.payload or {}
    token = payload.get("token")
    decision = payload.get("decision")
    if not token or decision not in ("approve", "deny"):
        logger.warning("[hs-consumer] malformed decision: %r", payload)
        return

    row = reg.get_active_pending(conn, token)
    if row is None:
        logger.info("[hs-consumer] no actionable pending for %s (already handled?)", token)
        return

    if decision == "deny":
        reg.mark_status(conn, token, "denied")
        logger.info("[hs-consumer] denied %s", token)
        return

    # approve: create family + admin user from the pending row (mirrors /setup)
    cur = conn.execute(
        "INSERT INTO families (name, created_at) VALUES (?, datetime('now'))",
        (row["family_name"],))
    family_id = cur.lastrowid
    conn.execute(
        "INSERT INTO users (family_id, name, email, password_hash, role, created_at) "
        "VALUES (?, ?, ?, ?, 'admin', datetime('now'))",
        (family_id, row["name"], row["email"], row["password_hash"]))
    reg.mark_status(conn, token, "approved")
    conn.commit()
    logger.info("[hs-consumer] approved %s -> family %s", token, family_id)

    publish_email(to=[row["email"]],
                  subject="Your homeschool account is approved",
                  html=WELCOME_HTML, correlation_id=token)
```
Note: `from bus import publish_email` binds the name into this module so the tests can `monkeypatch.setattr(rc, "publish_email", ...)`.

- [ ] **Step 4: Run, confirm PASS + full suite**
```bash
cd /srv/docker/websites/coviecraft/hs
./run_tests.sh -k registration_consumer
./run_tests.sh
```
Expected: new tests pass; full suite green.

- [ ] **Step 5: Commit**
```bash
cd /srv/docker/websites/coviecraft/hs
git add app/registration_consumer.py app/tests/test_registration_consumer.py
git commit -m "hs: registration decision handler — approve activates family + welcome; deny"
```
End with the Co-Authored-By trailer.

---

### Task 9: start the consumer in hs lifespan + deploy (TDD where practical)

**Files:**
- Modify: `/srv/docker/websites/coviecraft/hs/app/main.py`
- Modify: `/srv/docker/websites/coviecraft/hs/app/bus.py` (add the consumer runner)
- Test: `/srv/docker/websites/coviecraft/hs/app/tests/test_consumer_gate.py`

- [ ] **Step 1: Add a consumer runner to `app/bus.py`**

Append:
```python
import threading

from eventbus import events_stream, run_forever

import config
import db
import registration_consumer as _rc

_consumer_thread: threading.Thread | None = None
_consumer_stop = threading.Event()


def start_consumer(db_path: str) -> None:
    """Start the events:hs consumer in a daemon thread (no-op if disabled or running)."""
    global _consumer_thread
    if not config.eventbus_enabled():
        return
    if _consumer_thread and _consumer_thread.is_alive():
        return
    _consumer_stop.clear()

    def _handle(msg):
        conn = db.get_connection(db_path)
        try:
            _rc.handle_decision(conn, msg)
        finally:
            conn.close()

    stream = events_stream("hs")

    def _run():
        run_forever(lambda: get_bus(), stream, "hs", "hs-worker", _handle,
                    _consumer_stop, dead_stream=stream + ".dead")

    _consumer_thread = threading.Thread(target=_run, name="hs-events", daemon=True)
    _consumer_thread.start()


def stop_consumer() -> None:
    _consumer_stop.set()
```

- [ ] **Step 2: Write the gate test** — `app/tests/test_consumer_gate.py`:
```python
import bus as hsbus


def test_start_consumer_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("HS_EVENTBUS_ENABLED", "0")
    started = {"v": False}
    monkeypatch.setattr(hsbus.threading, "Thread",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not start")))
    hsbus.start_consumer(":memory:")   # must not raise / must not create a thread
    assert started["v"] is False
```

- [ ] **Step 3: Run, confirm it passes once the import resolves**

Run: `cd /srv/docker/websites/coviecraft/hs && ./run_tests.sh -k consumer_gate`
Expected: pass (consumer is gated off in tests via `HS_EVENTBUS_ENABLED=0` from conftest).

- [ ] **Step 4: Wire the lifespan in `app/main.py`**

Add an import: `from contextlib import asynccontextmanager` and `import bus as hsbus`. Define a lifespan and pass it to `FastAPI(...)`:
```python
@asynccontextmanager
async def _lifespan(app: FastAPI):
    hsbus.start_consumer(app.state.db_path)
    try:
        yield
    finally:
        hsbus.stop_consumer()
```
Change `app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` to
`app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=_lifespan)`.
(The existing tests build the client as `TestClient(app)` WITHOUT a `with` block, so the lifespan does not run in them; the `HS_EVENTBUS_ENABLED=0` default is a second guard.)

- [ ] **Step 5: Full suite + deploy**
```bash
cd /srv/docker/websites/coviecraft/hs
./run_tests.sh                                   # all green
docker compose up -d --build --force-recreate    # deploy (dangerouslyDisableSandbox if needed)
sleep 5
docker compose logs --tail=30 hs | grep -i "consumer\|hs-events\|error" || echo "(check logs)"
```
Expected: no crash; the consumer thread starts (HS_EVENTBUS_ENABLED=1 in compose). Confirm the group exists on Redis:
```bash
PW=$(cat /srv/docker/eventbus/secrets/eventbus_redis_password)
docker exec eventbus-redis redis-cli --no-auth-warning -a "$PW" XINFO GROUPS events:hs
```
Expected: a group `hs` (created lazily on first `ensure_group`; if the stream doesn't exist yet it appears after the first decision — acceptable).

- [ ] **Step 6: Commit**
```bash
cd /srv/docker/websites/coviecraft/hs
git add app/bus.py app/main.py app/tests/test_consumer_gate.py
git commit -m "hs: run events:hs consumer in lifespan (gated by HS_EVENTBUS_ENABLED)"
```
End with the Co-Authored-By trailer.

---

### Task 10: full round-trip end-to-end verification (live)

**Files:** none (verification + ops docs).

- [ ] **Step 1: Deploy cortex with the approval router**

The cortex changes (Tasks 5–6) are on branch `eventbus-phase2`. Merge to `main` (or run from the branch) and `make reload`:
```bash
cd /srv/docker/cortex
PYTHONPATH=../eventbus/eventbus-kit .venv/bin/python -m pytest -q   # confirm green first
git checkout main && git merge --no-ff eventbus-phase2 -m "Merge eventbus-phase2: cortex APPROVE/DENY router"
make reload
```

- [ ] **Step 2: Drive the round-trip in dry-run**

Set `CORTEX_DRY_RUN=1` in cortex `.env` AND ensure hs has `HS_ADMIN_EMAIL` set (use a real mailbox you control, or your Proton address so you can reply). Back up both `.env` files first (`cp -a .env .env.bak-rt`). Then:
1. `curl -s -X POST https://hs.coviecraft.dev/register -d 'family_name=Test&name=T&email=YOUR_REAL_EMAIL&password=familypizzanight&website='` (or use the form in a browser).
2. Confirm the admin email arrives (cortex worker delivers it; in dry-run it logs instead — to actually receive it, this step needs real send, see Step 3).
3. Reply / send `APPROVE hs-<token>` into the Proton `Command` folder.
4. Watch cortex logs for the decision publish; watch hs logs for `[hs-consumer] approved`.
5. Confirm a `families`/`users` row was created in hs (`docker compose exec hs python -c "import db,config; c=db.get_connection(config.db_path()); print(c.execute('select email,role from users').fetchall())"`).

Because the round-trip inherently requires real email to receive/reply, do this verification with real send enabled (`CORTEX_DRY_RUN=0`, `SEND_EMAIL=1`) using your own address. RESTORE both `.env` files afterward (`cp -a .env.bak-rt .env` in each, `make reload` / `docker compose up -d`).

- [ ] **Step 3: Document the Proton filter prerequisite**

Add to `/srv/docker/cortex/CLAUDE.md` (Gotchas) and `/srv/docker/eventbus/README.md`:
```
Approval replies must land in the Proton `Command` folder for the IMAP listener to
see them. Create a one-time Proton sieve filter: if subject or body starts with
"APPROVE " or "DENY ", move/label the message into Command.
```
Also note in hs's repo (`hs/CLAUDE.md` if present, else a comment in `docker-compose.yml`) that `HS_ADMIN_EMAIL` must be set for registrations to notify an admin.

- [ ] **Step 4: Commit docs**
```bash
cd /srv/docker/cortex && git add CLAUDE.md && git commit -m "docs: approval-reply Proton filter prerequisite"
cd /srv/docker/eventbus && git add README.md && git commit -m "docs: approval-reply Proton filter prerequisite"
```
(Co-Authored-By trailers.)

---

## Self-Review

**Spec coverage (phases 2–4):**
- Phase 2 — hs open registration + `pending_registrations` + outbound admin-notify → Tasks 1–4. ✅
- Phase 3 — cortex `APPROVE/DENY` parse + allowlist + publish `events:<site>` → Tasks 5–6. ✅
- Phase 4 — hs consumer + activation + welcome (outbound #2) + lifespan wiring → Tasks 7–9. ✅
- Round-trip e2e + ops docs (Proton filter, admin email) → Task 10. ✅
- Token contract (`hs-…` self-describing) consistent: minted in Task 1 (`registration.make_token`), routed in Task 6 (`site_from_token`), validated in Task 8 (`get_active_pending`). ✅
- Security: sender allowlist (Task 6) + single-use unexpired token (Tasks 1, 8) — the double gate from the spec. ✅
- Durability: generic consumer reuses the proven claim_stale/retry/dead-letter pattern (Task 7). ✅

**Placeholder scan:** No TODO/TBD. Paths flagged for verification (kit relative path in conftest `parents[]`; hs compose `../../../eventbus`; base.html block name/CSS file) include explicit "VERIFY/adjust" steps because they depend on the exact on-disk layout — the engineer confirms them at that step rather than guessing.

**Type/name consistency:** `EventBus`/`Message` API unchanged from phase 0–1. New shared symbols (`events_stream`, `site_from_token`, `REGISTRATION_DECISION`, `process_once`, `run_forever`, `PermanentMessageError`) are defined in Tasks 6–7 before use in Tasks 8–9. `publish_email` is defined in Task 2 and reused in Tasks 3, 8. `handle_decision(conn, msg)` signature matches between Task 8 (def) and Task 9 (caller via `_handle`). `registration` helpers (`create_pending`, `get_pending`, `get_active_pending`, `email_taken`, `mark_status`, `make_token`) defined in Task 1, used in Tasks 3, 8.

**Scope:** Phases 2–4 form one feature (the round-trip) but are staged so Phase 2 is independently shippable (registration emails the admin even before the inbound half exists).
