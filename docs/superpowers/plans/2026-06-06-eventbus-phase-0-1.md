# Event Bus Phase 0–1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the Redis Streams event bus and the cortex outbound email worker so that *any* container on the `eventbus` network can publish an `email.send` message and have cortex deliver it via the Proton Bridge.

**Architecture:** A dedicated top-level `/srv/docker/eventbus/` stack owns a password-protected Redis 7 container (no host port) and the shared `eventbus-kit` Python package. Cortex joins the new external `eventbus` network, mounts the kit read-only, and runs a new background worker thread (mirroring the existing `imap_listener` pattern) that consumes the `email.send` stream with a consumer group, sends each message through `service.emailer.send_html`, and acks. Failures are retried via `XAUTOCLAIM` and dead-lettered to `email.send.dead` after 3 attempts.

**Tech Stack:** Python 3.12, `redis` (redis-py) Streams + consumer groups, `fakeredis` for tests, Docker Compose, Proton Bridge SMTP (existing `service.emailer`).

**Scope note:** This is phases 0–1 of the spec `docs/superpowers/specs/2026-06-06-cortex-website-email-eventbus-design.md`. Phases 2–4 (hs registration, inbound approval router, hs consumer) are a **separate follow-up plan** and are out of scope here. After this plan, the bus exists and sites can send mail through cortex; nothing receives inbound triggers yet.

---

## File Structure

**New stack — `/srv/docker/eventbus/` (its own git repo):**
- `docker-compose.yml` — defines the `eventbus-redis` container, the external `eventbus` network, and the `eventbus_redis_password` secret.
- `secrets/eventbus_redis_password` — plain-text Redis password (gitignored).
- `.gitignore` — ignores `secrets/`, `__pycache__/`, `.venv/`, `data/`.
- `README.md` — what the bus is, how to bring it up, how to publish/consume.
- `eventbus-kit/eventbus/__init__.py` — public API re-exports.
- `eventbus-kit/eventbus/envelope.py` — envelope build + JSON encode/decode + stream-name constants.
- `eventbus-kit/eventbus/connection.py` — build a `redis.Redis` from env + Docker secret.
- `eventbus-kit/eventbus/client.py` — `EventBus` + `Message`: publish / ensure_group / read / claim_stale / ack / delivery_count.
- `eventbus-kit/pyproject.toml` — standalone dev/test config (pytest + fakeredis).
- `eventbus-kit/tests/test_envelope.py` — envelope round-trip.
- `eventbus-kit/tests/test_client.py` — publish/read/ack/claim against fakeredis.

**Modified — cortex repo (`/srv/docker/cortex`, branch `eventbus-design`):**
- `service/email_outbound.py` *(new)* — the outbound worker (loop + per-message handler + dead-letter).
- `service/cli.py` — start/stop the worker inside `cmd_serve`.
- `docker-compose.yaml` — join `eventbus` network, mount the kit, add the Redis secret + env.
- `pyproject.toml` — add `redis` runtime dep + `fakeredis` dev dep.
- `tests/test_email_outbound.py` *(new)* — worker handler + dead-letter tests (fakeredis + `stub_emailer`).
- `tests/conftest.py` — add a `bus` fixture (fakeredis-backed `EventBus`).

---

## Wire contract (shared by both sides)

A stream message stores the whole envelope as a single JSON field named `json`:

```json
{
  "id": "0f1e2d…",
  "type": "email.send",
  "source": "hs",
  "ts": "2026-06-06T17:00:00+00:00",
  "correlation_id": "hs-abc123",
  "payload": { "to": ["a@b.com"], "subject": "Hi", "html": "<p>…</p>" }
}
```

- Stream: `email.send`. Consumer group: `cortex-emailer`. Dead-letter stream: `email.send.dead`.
- `payload` for `email.send`: `to` (list[str]), `subject` (str), `html` (str). `cc`/`bcc` optional.

---

## Task 1: Create the eventbus stack skeleton + Redis container

**Files:**
- Create: `/srv/docker/eventbus/docker-compose.yml`
- Create: `/srv/docker/eventbus/secrets/eventbus_redis_password`
- Create: `/srv/docker/eventbus/.gitignore`
- Create: `/srv/docker/eventbus/README.md`

- [ ] **Step 1: Create the external network**

Run:
```bash
docker network create eventbus 2>/dev/null || echo "exists"
```
Expected: a network id, or `exists`.

- [ ] **Step 2: Write the Redis password secret**

```bash
mkdir -p /srv/docker/eventbus/secrets
# 32 random bytes, hex; no trailing newline
python3 -c "import secrets; open('/srv/docker/eventbus/secrets/eventbus_redis_password','w').write(secrets.token_hex(32))"
```

- [ ] **Step 3: Write `.gitignore`**

```gitignore
secrets/
data/
__pycache__/
.venv/
*.pyc
```

- [ ] **Step 4: Write `docker-compose.yml`**

```yaml
services:
  eventbus-redis:
    image: redis:7-alpine
    container_name: eventbus-redis
    restart: unless-stopped
    command: >
      sh -c 'exec redis-server --appendonly yes
      --requirepass "$$(cat /run/secrets/eventbus_redis_password)"'
    secrets:
      - eventbus_redis_password
    volumes:
      - eventbus_redis_data:/data
    networks:
      - eventbus
    healthcheck:
      test: ["CMD-SHELL", "redis-cli -a \"$$(cat /run/secrets/eventbus_redis_password)\" ping | grep -q PONG"]
      interval: 10s
      timeout: 5s
      retries: 5

secrets:
  eventbus_redis_password:
    file: ./secrets/eventbus_redis_password

volumes:
  eventbus_redis_data:

networks:
  eventbus:
    external: true
```

Note: `$$` escapes Compose interpolation so the literal `$(cat …)` runs in the shell.

- [ ] **Step 5: Write `README.md`**

```markdown
# eventbus

Redis Streams message bus shared by cortex and the websites.

- `eventbus-redis` — Redis 7, password-protected (Docker secret), AOF persistence,
  **no published host port** (reachable only on the external `eventbus` network).
- `eventbus-kit/` — the shared Python client (`from eventbus import EventBus`),
  bind-mounted read-only into cortex and participating sites.

## Bring it up
```bash
docker network create eventbus 2>/dev/null || true
cd /srv/docker/eventbus && docker compose up -d
```

## Streams
- `email.send` (sites → cortex, group `cortex-emailer`) — outbound email requests.
- `email.send.dead` — messages that failed 3 delivery attempts.
- `events:<site>` (cortex → a site, group = site name) — added in a later phase.
```

- [ ] **Step 6: Bring up Redis and verify**

Run:
```bash
cd /srv/docker/eventbus && docker compose up -d
sleep 3
PW=$(cat secrets/eventbus_redis_password)
docker exec eventbus-redis redis-cli -a "$PW" ping
```
Expected: `PONG` (a `Warning: Using a password...` line on stderr is fine).

- [ ] **Step 7: Init the repo and commit**

```bash
cd /srv/docker/eventbus
git init && git add -A && git status   # confirm secrets/ is NOT staged
git commit -m "Add eventbus stack: password-protected Redis 7 container + network"
```

---

## Task 2: eventbus-kit — envelope module (TDD)

**Files:**
- Create: `/srv/docker/eventbus/eventbus-kit/eventbus/__init__.py`
- Create: `/srv/docker/eventbus/eventbus-kit/eventbus/envelope.py`
- Create: `/srv/docker/eventbus/eventbus-kit/pyproject.toml`
- Test: `/srv/docker/eventbus/eventbus-kit/tests/test_envelope.py`

- [ ] **Step 1: Write `pyproject.toml` for standalone testing**

```toml
[project]
name = "eventbus-kit"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["redis>=5,<6"]

[project.optional-dependencies]
dev = ["pytest", "fakeredis>=2.21"]

[tool.pytest.ini_options]
pythonpath = ["."]
testpaths = ["tests"]
```

- [ ] **Step 2: Write the failing test**

`tests/test_envelope.py`:
```python
import json
from eventbus import envelope as env


def test_make_envelope_has_required_fields():
    e = env.make_envelope(type="email.send", source="hs",
                          payload={"to": ["a@b.com"]}, correlation_id="hs-1")
    assert e["type"] == "email.send"
    assert e["source"] == "hs"
    assert e["payload"] == {"to": ["a@b.com"]}
    assert e["correlation_id"] == "hs-1"
    assert e["id"]                      # non-empty
    assert e["ts"].endswith("+00:00")   # tz-aware UTC iso


def test_encode_decode_round_trip():
    e = env.make_envelope(type="x", source="s", payload={"n": 1})
    fields = env.encode(e)
    assert set(fields) == {"json"}
    assert json.loads(fields["json"]) == e
    assert env.decode(fields) == e


def test_decode_accepts_bytes_keys_and_values():
    e = env.make_envelope(type="x", source="s")
    raw = env.encode(e)["json"]
    assert env.decode({b"json": raw.encode()}) == e
```

- [ ] **Step 3: Run it to confirm it fails**

Run:
```bash
cd /srv/docker/eventbus/eventbus-kit
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]" -q
.venv/bin/python -m pytest tests/test_envelope.py -q
```
Expected: FAIL — `ModuleNotFoundError: No module named 'eventbus.envelope'`.

- [ ] **Step 4: Write `envelope.py`**

```python
"""Message envelope: build, JSON-encode for Redis streams, and decode back."""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

EMAIL_SEND = "email.send"
EMAIL_SEND_DEAD = "email.send.dead"


def make_envelope(
    *,
    type: str,
    source: str,
    payload: dict[str, Any] | None = None,
    correlation_id: str | None = None,
    msg_id: str | None = None,
    ts: str | None = None,
) -> dict[str, Any]:
    return {
        "id": msg_id or uuid.uuid4().hex,
        "type": type,
        "source": source,
        "ts": ts or datetime.now(timezone.utc).isoformat(),
        "correlation_id": correlation_id,
        "payload": payload or {},
    }


def encode(envelope: dict[str, Any]) -> dict[str, str]:
    """Redis stream fields must be flat strings; store the envelope as one JSON field."""
    return {"json": json.dumps(envelope)}


def decode(fields: dict) -> dict[str, Any]:
    raw = fields.get("json")
    if raw is None:
        raw = fields.get(b"json")
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw)
```

- [ ] **Step 5: Write `__init__.py`**

```python
from .client import EventBus, Message
from .envelope import EMAIL_SEND, EMAIL_SEND_DEAD, decode, encode, make_envelope

__all__ = [
    "EventBus", "Message",
    "EMAIL_SEND", "EMAIL_SEND_DEAD",
    "make_envelope", "encode", "decode",
]
```

Note: `__init__.py` imports `client`, written in Task 3. Until then, run the envelope test by importing the submodule directly (the test does `from eventbus import envelope`, which does **not** execute `__init__`'s `client` import only if Python imports the package first — so temporarily comment the `.client` line, or do Task 3 before re-running the full suite). Simplest: create a stub `client.py` now with `class EventBus: ...` / `class Message: ...` placeholders, replaced in Task 3.

- [ ] **Step 6: Create a placeholder `client.py` so the package imports**

`eventbus/client.py`:
```python
class Message:  # replaced in Task 3
    pass


class EventBus:  # replaced in Task 3
    pass
```

- [ ] **Step 7: Run the test to confirm it passes**

Run:
```bash
.venv/bin/python -m pytest tests/test_envelope.py -q
```
Expected: PASS (3 passed).

- [ ] **Step 8: Commit**

```bash
cd /srv/docker/eventbus
git add eventbus-kit/eventbus/__init__.py eventbus-kit/eventbus/envelope.py \
        eventbus-kit/eventbus/client.py eventbus-kit/pyproject.toml \
        eventbus-kit/tests/test_envelope.py
git commit -m "eventbus-kit: message envelope encode/decode"
```

---

## Task 3: eventbus-kit — connection + EventBus client (TDD)

**Files:**
- Create: `/srv/docker/eventbus/eventbus-kit/eventbus/connection.py`
- Modify: `/srv/docker/eventbus/eventbus-kit/eventbus/client.py` (replace placeholders)
- Test: `/srv/docker/eventbus/eventbus-kit/tests/test_client.py`

- [ ] **Step 1: Write the failing test**

`tests/test_client.py`:
```python
import fakeredis
import pytest
from eventbus import EventBus, Message

STREAM = "email.send"
GROUP = "cortex-emailer"


@pytest.fixture
def bus():
    return EventBus(fakeredis.FakeStrictRedis(decode_responses=True), source="test")


def test_publish_then_read_round_trips_envelope(bus):
    bus.ensure_group(STREAM, GROUP)
    mid = bus.publish(STREAM, "email.send",
                      payload={"to": ["a@b.com"], "subject": "Hi", "html": "<p>x</p>"},
                      correlation_id="hs-1")
    assert isinstance(mid, str)

    msgs = bus.read(STREAM, GROUP, "c1", count=10, block_ms=10)
    assert len(msgs) == 1
    m = msgs[0]
    assert isinstance(m, Message)
    assert m.type == "email.send"
    assert m.payload["subject"] == "Hi"
    assert m.correlation_id == "hs-1"


def test_ensure_group_is_idempotent(bus):
    bus.ensure_group(STREAM, GROUP)
    bus.ensure_group(STREAM, GROUP)   # must not raise (BUSYGROUP swallowed)


def test_ack_removes_from_pending(bus):
    bus.ensure_group(STREAM, GROUP)
    bus.publish(STREAM, "email.send", payload={})
    m = bus.read(STREAM, GROUP, "c1", block_ms=10)[0]
    assert bus.delivery_count(STREAM, GROUP, m.id) == 1
    bus.ack(STREAM, GROUP, m.id)
    assert bus.delivery_count(STREAM, GROUP, m.id) == 0   # no longer pending


def test_claim_stale_returns_unacked_messages(bus):
    bus.ensure_group(STREAM, GROUP)
    bus.publish(STREAM, "email.send", payload={"subject": "S"})
    bus.read(STREAM, GROUP, "c1", block_ms=10)            # delivered, not acked
    claimed = bus.claim_stale(STREAM, GROUP, "c2", min_idle_ms=0)
    assert len(claimed) == 1
    assert claimed[0].payload["subject"] == "S"
```

- [ ] **Step 2: Run it to confirm it fails**

Run:
```bash
cd /srv/docker/eventbus/eventbus-kit
.venv/bin/python -m pytest tests/test_client.py -q
```
Expected: FAIL — `EventBus()` placeholder takes no args / has no methods.

- [ ] **Step 3: Write `connection.py`**

```python
"""Build a redis.Redis from env + Docker secret."""
from __future__ import annotations

import os
from pathlib import Path

import redis


def _read_password() -> str | None:
    path = os.getenv("EVENTBUS_REDIS_PASSWORD_FILE", "/run/secrets/eventbus_redis_password")
    p = Path(path)
    if p.exists():
        pw = p.read_text().strip()
        if pw:
            return pw
    return os.getenv("EVENTBUS_REDIS_PASSWORD") or None


def get_redis(**overrides) -> redis.Redis:
    host = os.getenv("EVENTBUS_REDIS_HOST", "eventbus-redis")
    port = int(os.getenv("EVENTBUS_REDIS_PORT", "6379"))
    return redis.Redis(
        host=host, port=port, password=_read_password(),
        decode_responses=True, **overrides,
    )
```

- [ ] **Step 4: Replace `client.py`**

```python
"""EventBus: a thin Redis Streams wrapper (publish + consumer-group consume)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import redis

from . import envelope as _env


@dataclass
class Message:
    id: str
    stream: str
    envelope: dict[str, Any]

    @property
    def type(self) -> str | None:
        return self.envelope.get("type")

    @property
    def payload(self) -> dict[str, Any]:
        return self.envelope.get("payload", {})

    @property
    def correlation_id(self) -> str | None:
        return self.envelope.get("correlation_id")


class EventBus:
    def __init__(self, redis_client: redis.Redis, source: str):
        self.r = redis_client
        self.source = source

    @classmethod
    def from_env(cls, source: str) -> "EventBus":
        from .connection import get_redis
        return cls(get_redis(), source)

    def publish(self, stream: str, type: str, *, payload: dict | None = None,
                correlation_id: str | None = None) -> str:
        e = _env.make_envelope(type=type, source=self.source,
                               payload=payload, correlation_id=correlation_id)
        return self.r.xadd(stream, _env.encode(e))

    def ensure_group(self, stream: str, group: str) -> None:
        try:
            self.r.xgroup_create(stream, group, id="0", mkstream=True)
        except redis.ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

    def read(self, stream: str, group: str, consumer: str, *,
             count: int = 10, block_ms: int = 5000) -> list[Message]:
        resp = self.r.xreadgroup(group, consumer, {stream: ">"},
                                 count=count, block=block_ms)
        return self._to_messages(resp)

    def claim_stale(self, stream: str, group: str, consumer: str, *,
                    min_idle_ms: int = 10000, count: int = 10) -> list[Message]:
        res = self.r.xautoclaim(stream, group, consumer,
                                min_idle_time=min_idle_ms, start_id="0", count=count)
        # redis-py returns (next_cursor, claimed_messages[, deleted_ids])
        claimed = res[1] if len(res) >= 2 else []
        return [Message(id=mid, stream=stream, envelope=_env.decode(fields))
                for mid, fields in claimed if fields]

    def ack(self, stream: str, group: str, msg_id: str) -> None:
        self.r.xack(stream, group, msg_id)

    def delivery_count(self, stream: str, group: str, msg_id: str) -> int:
        info = self.r.xpending_range(stream, group, min=msg_id, max=msg_id, count=1)
        return int(info[0]["times_delivered"]) if info else 0

    def to_dead(self, dead_stream: str, msg: Message) -> str:
        return self.r.xadd(dead_stream, _env.encode(msg.envelope))

    def _to_messages(self, resp) -> list[Message]:
        out: list[Message] = []
        for stream, entries in resp or []:
            for mid, fields in entries:
                out.append(Message(id=mid, stream=stream, envelope=_env.decode(fields)))
        return out
```

- [ ] **Step 5: Run the test to confirm it passes**

Run:
```bash
.venv/bin/python -m pytest tests/ -q
```
Expected: PASS (envelope + client tests, all green).

- [ ] **Step 6: Commit**

```bash
cd /srv/docker/eventbus
git add eventbus-kit/eventbus/connection.py eventbus-kit/eventbus/client.py \
        eventbus-kit/tests/test_client.py
git commit -m "eventbus-kit: EventBus client (publish/consume/ack/claim) over Redis Streams"
```

---

## Task 4: Live smoke test against the real Redis container

**Files:** none (verification only).

- [ ] **Step 1: Publish and consume against the running container**

Run (from the host, talking to the published-only-on-network Redis via a one-off container on the `eventbus` net):
```bash
cd /srv/docker/eventbus
PW=$(cat secrets/eventbus_redis_password)
docker run --rm --network eventbus -e EVENTBUS_REDIS_PASSWORD="$PW" \
  -v "$PWD/eventbus-kit":/kit -w /kit python:3.12-slim sh -c '
    pip install -e . -q &&
    python -c "
from eventbus import EventBus
b = EventBus.from_env(\"smoke\")
b.ensure_group(\"email.send\", \"cortex-emailer\")
mid = b.publish(\"email.send\", \"email.send\", payload={\"subject\": \"smoke\"})
print(\"published\", mid)
m = b.read(\"email.send\", \"cortex-emailer\", \"smoke-c\", block_ms=200)[0]
print(\"read\", m.payload)
b.ack(\"email.send\", \"cortex-emailer\", m.id)
print(\"acked\")
"'
```
Expected: prints `published …`, `read {'subject': 'smoke'}`, `acked`. Confirms env-based connection + auth work end to end.

- [ ] **Step 2: Clean the smoke message group (optional)**

Run:
```bash
PW=$(cat secrets/eventbus_redis_password)
docker exec eventbus-redis redis-cli -a "$PW" DEL email.send
```
Expected: `(integer) 1`.

---

## Task 5: cortex deps + network/kit wiring

**Files:**
- Modify: `/srv/docker/cortex/pyproject.toml`
- Modify: `/srv/docker/cortex/docker-compose.yaml`

- [ ] **Step 1: Add deps to `pyproject.toml`**

Add to `dependencies` (after `"markdown>=3.7",`):
```toml
    "redis>=5,<6",
```
Add to `[project.optional-dependencies] dev` list:
```toml
    "fakeredis>=2.21",
```

- [ ] **Step 2: Wire the cortex service in `docker-compose.yaml`**

In the `cortex` service block, add the eventbus network, the kit mount, the secret, and env. Concretely:

Under the cortex service `networks:` add `- eventbus`.
Under `volumes:` add:
```yaml
      - ../eventbus/eventbus-kit:/eventbus-kit:ro
```
Under `environment:` add:
```yaml
      - PYTHONPATH=/eventbus-kit
      - EVENTBUS_REDIS_HOST=eventbus-redis
      - EVENTBUS_REDIS_PORT=6379
```
Add a `secrets:` entry to the cortex service:
```yaml
    secrets:
      - eventbus_redis_password
```
At the top level of the compose file, declare the secret and the external network:
```yaml
secrets:
  eventbus_redis_password:
    file: ../eventbus/secrets/eventbus_redis_password

networks:
  mailnet:
  eventbus:
    external: true
```
(Keep the existing `mailnet` network definition; add `eventbus` alongside it.)

- [ ] **Step 3: Verify the kit imports inside the container after rebuild**

Run:
```bash
cd /srv/docker/cortex && make reload
docker compose exec cortex python -c "from eventbus import EventBus; print('ok', EventBus)"
```
Expected: `ok <class 'eventbus.client.EventBus'>`. (If `make reload` requires a healthy bridge, it is fine for this check as long as the cortex container starts.)

- [ ] **Step 4: Commit (cortex repo)**

```bash
cd /srv/docker/cortex
git add pyproject.toml docker-compose.yaml
git commit -m "cortex: join eventbus network, mount eventbus-kit, add redis dep"
```

---

## Task 6: cortex outbound worker — per-message handler (TDD)

**Files:**
- Create: `/srv/docker/cortex/service/email_outbound.py`
- Modify: `/srv/docker/cortex/tests/conftest.py` (add `bus` fixture)
- Test: `/srv/docker/cortex/tests/test_email_outbound.py`

- [ ] **Step 1: Add a `bus` fixture to `tests/conftest.py`**

Append:
```python
@pytest.fixture
def bus():
    """A fakeredis-backed EventBus for worker tests."""
    import fakeredis
    from eventbus import EventBus
    return EventBus(fakeredis.FakeStrictRedis(decode_responses=True), source="cortex")
```

Note: this requires `/eventbus-kit` on `sys.path` when running tests on the host. Run cortex tests with `PYTHONPATH=../eventbus/eventbus-kit` (see Step 4), or inside the container where `PYTHONPATH=/eventbus-kit` is already set.

- [ ] **Step 2: Write the failing test**

`tests/test_email_outbound.py`:
```python
import pytest

from eventbus import EMAIL_SEND, EMAIL_SEND_DEAD
from service import email_outbound

GROUP = "cortex-emailer"


def _publish(bus, payload):
    bus.ensure_group(EMAIL_SEND, GROUP)
    return bus.publish(EMAIL_SEND, "email.send", payload=payload, correlation_id="c-1")


def test_handle_message_sends_email(bus, stub_emailer, monkeypatch):
    monkeypatch.setenv("CORTEX_DRY_RUN", "0")
    monkeypatch.setenv("SEND_EMAIL", "1")
    _publish(bus, {"to": ["a@b.com"], "subject": "Hi", "html": "<p>x</p>"})
    msg = bus.read(EMAIL_SEND, GROUP, "c1", block_ms=10)[0]

    email_outbound.handle_message(msg)

    assert len(stub_emailer.sent["messages"]) == 1
    sent = stub_emailer.sent["messages"][0]
    assert sent["to"] == ["a@b.com"]
    assert sent["subject"] == "Hi"
    assert sent["html"] == "<p>x</p>"


def test_handle_message_dry_run_does_not_send(bus, stub_emailer, monkeypatch):
    monkeypatch.setenv("CORTEX_DRY_RUN", "1")
    _publish(bus, {"to": ["a@b.com"], "subject": "Hi", "html": "<p>x</p>"})
    msg = bus.read(EMAIL_SEND, GROUP, "c1", block_ms=10)[0]

    email_outbound.handle_message(msg)

    assert stub_emailer.sent["messages"] == []   # suppressed


def test_handle_message_missing_fields_raises(bus, monkeypatch):
    monkeypatch.setenv("CORTEX_DRY_RUN", "0")
    monkeypatch.setenv("SEND_EMAIL", "1")
    _publish(bus, {"subject": "no recipients"})   # no 'to'
    msg = bus.read(EMAIL_SEND, GROUP, "c1", block_ms=10)[0]

    with pytest.raises(email_outbound.InvalidMessage):
        email_outbound.handle_message(msg)


def test_process_once_acks_on_success(bus, stub_emailer, monkeypatch):
    monkeypatch.setenv("CORTEX_DRY_RUN", "0")
    monkeypatch.setenv("SEND_EMAIL", "1")
    mid = _publish(bus, {"to": ["a@b.com"], "subject": "Hi", "html": "<p>x</p>"})

    email_outbound.process_once(bus, "c1")

    assert bus.delivery_count(EMAIL_SEND, GROUP, mid) == 0   # acked


def test_process_once_dead_letters_after_three_attempts(bus, monkeypatch):
    monkeypatch.setenv("CORTEX_DRY_RUN", "0")
    monkeypatch.setenv("SEND_EMAIL", "1")
    _publish(bus, {"subject": "broken — no recipients"})

    # Three passes: each redelivers the still-pending message (min_idle 0).
    for _ in range(3):
        email_outbound.process_once(bus, "c1")

    # On the 3rd failed attempt it is moved to the dead stream and acked.
    assert bus.r.xlen(EMAIL_SEND_DEAD) == 1
    pending = bus.r.xpending(EMAIL_SEND, GROUP)
    assert pending["pending"] == 0
```

- [ ] **Step 3: Run it to confirm it fails**

Run:
```bash
cd /srv/docker/cortex
PYTHONPATH=../eventbus/eventbus-kit python -m pytest tests/test_email_outbound.py -q
```
Expected: FAIL — `No module named 'service.email_outbound'`.

- [ ] **Step 4: Write `service/email_outbound.py`**

```python
# service/email_outbound.py
"""Outbound email worker: consume the `email.send` stream and deliver via Proton.

Mirrors service.imap_listener's thread/controller pattern. Runs as a background
thread started by `service.cli` during `serve`. At-least-once: a failed message
stays pending and is retried via XAUTOCLAIM; after MAX_ATTEMPTS it is moved to
the `email.send.dead` stream and acked so it cannot block the group.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
from typing import Any

from eventbus import EMAIL_SEND, EMAIL_SEND_DEAD, EventBus, Message

from service.emailer import EmailSendError, send_html

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.addHandler(logging.NullHandler())

GROUP = "cortex-emailer"
MAX_ATTEMPTS = 3
_MIN_IDLE_MS = int(os.getenv("EMAIL_OUTBOUND_RETRY_IDLE_MS", "10000"))


class InvalidMessage(Exception):
    """The message payload is missing required fields (never retried)."""


def _dry_run() -> bool:
    return os.getenv("CORTEX_DRY_RUN") == "1" or os.getenv("SEND_EMAIL") == "0"


def handle_message(msg: Message) -> None:
    """Send one email. Raises InvalidMessage (permanent) or EmailSendError (transient)."""
    p: dict[str, Any] = msg.payload or {}
    to = p.get("to") or []
    subject = p.get("subject")
    html = p.get("html")
    if not to or not subject or not html:
        raise InvalidMessage(f"missing to/subject/html in {msg.id}")

    if _dry_run():
        logger.info("[email-out] DRY-RUN would send to=%s subject=%r", to, subject)
        return

    send_html(subject=subject, html=html, to=to, cc=p.get("cc"), bcc=p.get("bcc"))
    logger.info("[email-out] sent to=%s subject=%r corr=%s", to, subject, msg.correlation_id)


def _dispatch(bus: EventBus, msg: Message) -> None:
    """Run the handler for one message; ack on success, dead-letter on terminal failure."""
    try:
        handle_message(msg)
        bus.ack(EMAIL_SEND, GROUP, msg.id)
    except InvalidMessage:
        logger.error("[email-out] invalid message %s -> dead", msg.id, exc_info=True)
        bus.to_dead(EMAIL_SEND_DEAD, msg)
        bus.ack(EMAIL_SEND, GROUP, msg.id)
    except EmailSendError:
        attempts = bus.delivery_count(EMAIL_SEND, GROUP, msg.id)
        if attempts >= MAX_ATTEMPTS:
            logger.error("[email-out] message %s failed %d attempts -> dead", msg.id, attempts)
            bus.to_dead(EMAIL_SEND_DEAD, msg)
            bus.ack(EMAIL_SEND, GROUP, msg.id)
        else:
            logger.warning("[email-out] send failed (attempt %d) for %s; will retry",
                           attempts, msg.id)
            # leave unacked: a later claim_stale pass redelivers it


def process_once(bus: EventBus, consumer: str) -> None:
    """One reclaim+read cycle. Used by the loop and by tests."""
    bus.ensure_group(EMAIL_SEND, GROUP)
    for msg in bus.claim_stale(EMAIL_SEND, GROUP, consumer, min_idle_ms=_MIN_IDLE_MS):
        _dispatch(bus, msg)
    for msg in bus.read(EMAIL_SEND, GROUP, consumer, count=10, block_ms=0):
        _dispatch(bus, msg)


# --------------------------------------------------------------------------- #
# Thread controller (mirrors imap_listener)
# --------------------------------------------------------------------------- #
_thread: threading.Thread | None = None
_stop_event = threading.Event()


class WorkerController:
    def __init__(self, thread: threading.Thread, stop_event: threading.Event):
        self._thread = thread
        self._stop = stop_event

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)


def start(bus: EventBus | None = None) -> WorkerController:
    global _thread
    if _thread and _thread.is_alive():
        logger.info("[email-out] worker already running")
        return WorkerController(_thread, _stop_event)
    _stop_event.clear()
    consumer = os.getenv("HOSTNAME", socket.gethostname() or "cortex")
    t = threading.Thread(
        target=_loop, name="email-outbound", args=(bus, consumer, _stop_event), daemon=True,
    )
    t.start()
    _thread = t
    return WorkerController(t, _stop_event)


def _loop(bus: EventBus | None, consumer: str, stop_event: threading.Event) -> None:
    backoff = 5
    while not stop_event.is_set():
        try:
            if bus is None:
                bus = EventBus.from_env(source="cortex")
            bus.ensure_group(EMAIL_SEND, GROUP)
            backoff = 5
            while not stop_event.is_set():
                # Blocking read wakes every block_ms so we can observe stop_event.
                bus.ensure_group(EMAIL_SEND, GROUP)
                for msg in bus.claim_stale(EMAIL_SEND, GROUP, consumer, min_idle_ms=_MIN_IDLE_MS):
                    _dispatch(bus, msg)
                for msg in bus.read(EMAIL_SEND, GROUP, consumer, count=10, block_ms=5000):
                    _dispatch(bus, msg)
        except Exception as e:
            if stop_event.is_set():
                break
            logger.error("[email-out] loop error: %r; retrying in %ds", e, backoff)
            bus = None  # force reconnect
            slept = 0
            while slept < backoff and not stop_event.is_set():
                time.sleep(1)
                slept += 1
            backoff = min(backoff * 2, 120)
    logger.info("[email-out] worker stopped")
```

- [ ] **Step 5: Run the tests to confirm they pass**

Run:
```bash
cd /srv/docker/cortex
PYTHONPATH=../eventbus/eventbus-kit python -m pytest tests/test_email_outbound.py -q
```
Expected: PASS (5 passed).

- [ ] **Step 6: Lint**

Run:
```bash
cd /srv/docker/cortex
PYTHONPATH=../eventbus/eventbus-kit ruff check service/email_outbound.py tests/test_email_outbound.py
```
Expected: `All checks passed!` (fix any line-length >115 or import-order findings).

- [ ] **Step 7: Commit (cortex repo)**

```bash
cd /srv/docker/cortex
git add service/email_outbound.py tests/test_email_outbound.py tests/conftest.py
git commit -m "cortex: outbound email worker (consume email.send, send via Proton, dead-letter)"
```

---

## Task 7: Start the worker in `serve` (TDD)

**Files:**
- Modify: `/srv/docker/cortex/service/cli.py` (`cmd_serve` + imports + shutdown)
- Test: `/srv/docker/cortex/tests/test_cli_serve_starts_worker.py`

- [ ] **Step 1: Write the failing test**

`tests/test_cli_serve_starts_worker.py`:
```python
import threading

from service import cli


def test_serve_starts_and_stops_email_worker(monkeypatch, write_min_config):
    started = {"called": False}
    stopped = {"called": False}

    class FakeController:
        def stop(self):
            stopped["called"] = True
        def join(self, timeout=None):
            pass

    def fake_email_start(*a, **k):
        started["called"] = True
        return FakeController()

    # Neutralize the other long-running components.
    monkeypatch.setattr(cli._emailbus, "start", fake_email_start)
    monkeypatch.setattr(cli._imap, "start", lambda **k: FakeController())

    class FakeSched:
        _scheduler = object()
        def stop(self): pass
        def join(self, timeout=None): pass
    monkeypatch.setattr(cli._scheduler, "start", lambda *a, **k: FakeSched())

    # Let the serve loop run briefly, then signal shutdown from another thread.
    import service.cli as _c
    orig_sleep = _c.time.sleep
    calls = {"n": 0}
    def fake_sleep(_s):
        calls["n"] += 1
        if calls["n"] >= 2:
            _c.cmd_serve.__wrapped_stop__()   # set via closure below
    # Simpler: drive stop via the module stop_event by monkeypatching the wait.

    # Run serve in a thread and stop it quickly.
    import types
    args = types.SimpleNamespace(config=str(write_min_config))
    t = threading.Thread(target=cli.cmd_serve, args=(args,), daemon=True)
    t.start()
    # Give it a moment to start components, then send SIGTERM-equivalent.
    import time, signal, os
    time.sleep(0.5)
    os.kill(os.getpid(), signal.SIGTERM)
    t.join(timeout=5)

    assert started["called"] is True
    assert stopped["called"] is True
```

Note: if signal-driven shutdown is awkward to test in-process, simplify the test to assert only that `cmd_serve` registers and calls `_emailbus.start` by stubbing the wait loop — keep the test deterministic. The essential assertions are `started["called"]` and `stopped["called"]`.

- [ ] **Step 2: Run it to confirm it fails**

Run:
```bash
cd /srv/docker/cortex
PYTHONPATH=../eventbus/eventbus-kit python -m pytest tests/test_cli_serve_starts_worker.py -q
```
Expected: FAIL — `cli` has no attribute `_emailbus`.

- [ ] **Step 3: Wire the worker into `cli.py`**

Add the import near the other service imports (after `from service import imap_listener as _imap`):
```python
from service import email_outbound as _emailbus
```

In `cmd_serve`, after the IMAP listener is started (after the `running.imap_thread = imap_handle` block), add:
```python
        # Start the outbound email worker (consumes the eventbus `email.send` stream)
        try:
            email_handle = _emailbus.start()
            running.email_worker = email_handle
            LOG.info("Email outbound worker started: %r", running.email_worker)
        except Exception:
            LOG.exception("Email outbound worker failed to start; continuing without it")
            running.email_worker = None
```

Add `email_worker=None` to the initial `SimpleNamespace(...)`:
```python
    running = SimpleNamespace(sched=None, imap_thread=None, email_worker=None)
```

In `_graceful_shutdown`, add a stop call:
```python
        _safe_stop("email_worker", getattr(running, "email_worker", None))
```

And in the normal stop path (after the two existing `_safe_stop(...)` calls near the end of the `try`):
```python
        _safe_stop("email_worker", running.email_worker)
```

- [ ] **Step 4: Run the test to confirm it passes**

Run:
```bash
cd /srv/docker/cortex
PYTHONPATH=../eventbus/eventbus-kit python -m pytest tests/test_cli_serve_starts_worker.py -q
```
Expected: PASS.

- [ ] **Step 5: Run the full cortex suite**

Run:
```bash
cd /srv/docker/cortex
PYTHONPATH=../eventbus/eventbus-kit python -m pytest -q
```
Expected: all pass (no regressions). If a pre-existing test needs the kit on path and fails to import `eventbus`, that confirms the `PYTHONPATH` requirement — keep it set.

- [ ] **Step 6: Commit (cortex repo)**

```bash
cd /srv/docker/cortex
git add service/cli.py tests/test_cli_serve_starts_worker.py
git commit -m "cortex: start/stop the outbound email worker during serve"
```

---

## Task 8: End-to-end verification (real containers)

**Files:** none (verification only).

- [ ] **Step 1: Bring up the bus and reload cortex**

Run:
```bash
cd /srv/docker/eventbus && docker compose up -d
cd /srv/docker/cortex && make reload
docker compose logs --tail=20 cortex | grep -i "email outbound" || true
```
Expected: a log line `Email outbound worker started: …`.

- [ ] **Step 2: Publish a real send request and confirm delivery**

With `CORTEX_DRY_RUN=1` set in cortex `.env` first (so this verification does **not** send a real email), publish a message and confirm the worker consumes it (look for the DRY-RUN log line):
```bash
cd /srv/docker/eventbus
PW=$(cat secrets/eventbus_redis_password)
docker run --rm --network eventbus -e EVENTBUS_REDIS_PASSWORD="$PW" \
  -v "$PWD/eventbus-kit":/kit -w /kit python:3.12-slim sh -c '
    pip install -e . -q &&
    python -c "
from eventbus import EventBus
b = EventBus.from_env(\"verify\")
b.publish(\"email.send\", \"email.send\",
          payload={\"to\": [\"you@example.com\"], \"subject\": \"bus test\", \"html\": \"<p>hi</p>\"})
print(\"published\")
"'
sleep 2
cd /srv/docker/cortex && docker compose logs --tail=20 cortex | grep -i "email-out"
```
Expected: a `[email-out] DRY-RUN would send to=['you@example.com'] subject='bus test'` line, and the message no longer pending:
```bash
PW=$(cat /srv/docker/eventbus/secrets/eventbus_redis_password)
docker exec eventbus-redis redis-cli -a "$PW" XPENDING email.send cortex-emailer
```
Expected: `0` pending.

- [ ] **Step 3 (optional): real send**

Set `CORTEX_DRY_RUN=0` / `SEND_EMAIL=1` in cortex `.env`, `make reload`, re-publish with your real address, and confirm the email arrives. Revert the env afterward if you don't want the worker sending by default.

---

## Self-Review

**Spec coverage (phases 0–1 only):**
- Phase 0 infra (network, dedicated `/srv/docker/eventbus` stack, Redis, `eventbus-kit`, cortex joins) → Tasks 1, 5. ✅
- `eventbus-kit` (connection helper, `publish()`, consumer-group loop, envelope schema) → Tasks 2, 3. ✅
- Phase 1 outbound worker (consume `email.send` → `emailer.send_html` → ack) → Tasks 6, 7. ✅
- Durability: at-least-once + `XAUTOCLAIM` retry + 3-attempt dead-letter → Tasks 3 (`claim_stale`/`delivery_count`/`to_dead`), 6 (`_dispatch`). ✅
- Smoke/e2e proof → Tasks 4, 8. ✅
- Out of scope by design: phases 2–4 (hs registration, inbound approval router, hs consumer) — separate plan.

**Placeholder scan:** No TODO/TBD; every code step shows complete code. Task 7's test carries an explicit fallback note because in-process signal testing is environment-sensitive — the required assertions are stated concretely.

**Type/name consistency:** `EventBus` methods (`publish`, `ensure_group`, `read`, `claim_stale`, `ack`, `delivery_count`, `to_dead`) are defined in Task 3 and used unchanged in Task 6. Stream constants `EMAIL_SEND` / `EMAIL_SEND_DEAD` and group `cortex-emailer` are consistent across kit and worker. `handle_message` / `process_once` / `start` / `WorkerController` names match between worker and tests/cli.
