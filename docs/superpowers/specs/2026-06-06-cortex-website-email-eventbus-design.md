# Cortex ↔ Websites Email Event Bus — Design

**Date:** 2026-06-06
**Status:** Approved design (pre-implementation)
**First vertical slice:** `hs.coviecraft.dev` new-user registration (admin approval + welcome)

## Problem & goal

The websites under `/srv/docker/websites` and the cortex automation brain
(`/srv/docker/cortex`) are isolated: cortex sits on its internal `mailnet`
network with the ProtonMail Bridge (SMTP + IMAP), while the sites sit on
`proxy`/`internal` and send mail directly via the Resend HTTP API. They cannot
reach each other, and there is no path for an **inbound** email to trigger an
action in a site.

The goal is a **symmetric, two-way email gateway**: sites can send email
*through* cortex, and inbound email that cortex receives can *trigger actions in
a site*. Cortex is uniquely able to **receive** mail (it owns the IMAP
listener), which is the genuinely new capability.

To keep the first build tractable, we design and build **one concrete vertical
slice end-to-end first** — hs new-user registration — which exercises every
component once. Other sites/flows replicate the pattern afterward with **no
cortex changes**.

## Decisions (locked)

| Decision | Choice | Rationale |
|---|---|---|
| Scope | Symmetric two-way gateway | User intent |
| Transport between cortex & sites | **Redis Streams + consumer groups** | Durability, acks, replay, decoupling, fan-out; chosen over an HTTP gateway |
| Email transport | **All via Proton Bridge** (`emailer.send_html`) | One transport, no new creds; inbound replies must land in the watched Proton mailbox |
| Inbound leg | **Admin approval + welcome** (both legs) | Fixed-allowlist security + genuinely useful gate; fullest exercise of the system |
| Correlation | **Self-describing token** (`hs-<random>`) | Keeps cortex stateless about correlation; hs owns/validates the token |
| Redis placement | **Dedicated top-level `/srv/docker/eventbus/` stack** | Shared cross-stack infra owned by neither cortex nor any site; **independent lifecycle** so cortex can restart/rebuild without taking the bus (and its buffered messages) down — which is the durability Redis was chosen for |

## Architecture & components

A new Redis Streams event bus that cortex and participating sites both attach
to. New/changed pieces:

| Piece | Where | What |
|---|---|---|
| **`eventbus` network** | new external Docker network | `docker network create eventbus`; both cortex and hs join it (like `proxy`) |
| **`/srv/docker/eventbus/` stack** | new dedicated top-level stack (sibling to `cortex/` and `websites/`) | Owns the **`redis` container** (Redis 7, AOF persistence, password via Docker secret, **no published host port** — reachable only on `eventbus`) and the password secret. Independent lifecycle: bringing cortex down does **not** stop the bus. |
| **`eventbus-kit/`** | shared tree at `/srv/docker/eventbus/eventbus-kit/` | Bind-mounted read-only into cortex *and* sites (the `geo-kit`/`admin-kit` precedent). Holds the Redis connection helper, `publish()`, a consumer-group loop, and the message-envelope schema — **one definition shared by both sides**. Kept inside the `eventbus/` stack so the bus is one self-contained unit. |
| **cortex email-outbound worker** | new background thread started in cortex `serve` (`service/cli.py`) | Consumes the `email.send` stream → `emailer.send_html()` → ack |
| **cortex inbound approval handler** | extends `service/imap_commands/` | Recognizes `APPROVE <token>` / `DENY <token>` from an allowlisted sender → publishes to the site's event stream |

hs gains: open registration routes, a `pending_registrations` table, an outbound
publish on signup, and a **background consumer task** (in the FastAPI app
lifespan) reading its event stream.

### Why a shared kit, not duplicated code

`eventbus-kit/` is the single source of truth for the connection logic and the
message envelope, so cortex and every site agree on the wire format. It follows
the existing pattern in this repo: a small un-vendored source tree bind-mounted
read-only into multiple containers, added to `sys.path`, imported directly
(`from eventbus import publish, consume`). It lives inside the dedicated bus
stack at `/srv/docker/eventbus/eventbus-kit/`; cortex and the website stacks
mount it via a relative bind (e.g. `../eventbus/eventbus-kit`), so the whole bus
— Redis, secret, and shared kit — is one self-contained unit.

## Data flow (the round-trip)

```
Visitor → hs POST /register
   └─ hs: insert pending_registrations (token = "hs-<random>", pw hash stored)
   └─ hs: XADD email.send  {to: ADMIN, subject, html: "reply APPROVE hs-… / DENY hs-…",
                            correlation_id: token}

cortex email-worker: XREAD email.send → emailer.send_html() via Proton → XACK
   → admin receives the approval email in Proton

Admin replies "APPROVE hs-abc123"
cortex imap_listener: sees mail in the Command folder
   └─ guard: sender ∈ approval_allowlist  AND  body matches APPROVE/DENY <token>
   └─ token prefix "hs-" → target stream "events:hs"
   └─ XADD events:hs {type: registration.decision, decision: approve, token, approver}

hs consumer: XREADGROUP events:hs (group "hs")
   └─ validate token in pending_registrations (single-use, unexpired)
   └─ approve → create family + admin user from pending;
               XADD email.send {to: visitor, "you're in → /login"}
   └─ deny    → mark denied
   → XACK

cortex email-worker → welcome email to visitor via Proton
```

The token is **self-describing** (`hs-…` → cortex publishes to `events:hs`) so
cortex stays stateless about correlation; hs owns and validates the token.
*Alternative (not chosen):* cortex keeps a `token→stream` map in
`local/state/`.

## Message envelope (shared schema in eventbus-kit)

Common envelope fields on every message: `id` (uuid), `type`, `ts`, `source`,
`correlation_id`.

- **`email.send`** (sites → cortex)
  - Fields: `to[]`, `subject`, `html`, `correlation_id`
  - Consumer group: `cortex-emailer`
- **`events:<site>`** (cortex → one site)
  - Fields: `type` (`registration.decision`), `decision` (`approve`|`deny`),
    `token`, `approver`
  - Consumer group: the site name (e.g. `hs`)

Adding a second site later = it joins `eventbus`, publishes to `email.send`, and
reads its own `events:<site>` — **no cortex changes required**. That is the
payoff of doing the slice properly.

## Security

- **Redis**: password (Docker secret), bound to the `eventbus` network only,
  never exposed to the host or internet (no port mapping).
- **Inbound is double-gated**:
  1. Sender must be on cortex's `approval_allowlist` (config), **and**
  2. The reply must carry the unguessable single-use `token`.

  A spoofed `From` fails without the token; a leaked token fails without an
  allowlisted sender. Inbound mail also passes through Proton's own DMARC/spam
  filtering before cortex sees it.
- **Outbound trust model**: anyone on the bus holding the Redis password can
  publish `email.send`, and cortex sends the HTML it is given — so bus
  participants are **trusted-by-network**. This is explicit and acceptable for a
  homelab. (If untrusted publishers ever join, add per-source signing.)
- **Tokens**: random, single-use, 7-day expiry, stored in hs's
  `pending_registrations`. Consumed on first successful decision.

## Error handling & durability

- Redis Streams + consumer groups give **at-least-once** delivery: unacked
  messages stay pending and are re-claimed (`XAUTOCLAIM`) after a consumer
  restart — the durability Redis was chosen for.
- **Handlers must be idempotent**: the token is single-use, so a redelivered
  approval is a no-op; a redelivered welcome email is acceptable.
- After 3 failed delivery/processing attempts, move the message to a `dead`
  stream and log it. Cortex's existing logging covers the worker; hs logs via its app
  logger.

## hs-specific changes

hs today has **no open registration** — only `/setup`, a one-time bootstrap that
creates the single admin family when the DB is empty, then 404s. `users.role` is
CHECK-constrained to `'admin'`. This slice introduces **open multi-family
registration** gated by cortex approval.

- **Schema**: new `pending_registrations` table
  (`token`, `family_name`, `name`, `email`, `password_hash`, `status`
  `pending|approved|denied`, `created_at`, `expires_at`). Promotes to
  `families` + `users` only on approval, so unapproved signups never pollute the
  real tables. Applied via the existing idempotent `migrate()`.
- **Routes**: `GET/POST /register` (open signup → pending row + publish
  admin-notify). Keep `/setup` for the very first admin bootstrap.
- **Consumer**: a background asyncio task in the app lifespan reading
  `events:hs` (group `hs`); on approve → create family+user from pending +
  publish welcome; on deny → mark denied. Writes to sqlite via the existing
  connection pattern.
- **Welcome email**: points the visitor at `/login` (password was chosen at
  registration and carried in the pending row, so no separate set-password
  step).

## Testing

- **eventbus-kit**: unit tests against `fakeredis` (publish / consume / ack).
- **cortex**: email-worker test under `CORTEX_DRY_RUN=1`; allowlist +
  `APPROVE`/`DENY` parser tests mirroring existing `imap_commands` tests.
- **hs**: registration routes + the consumer handler tested with a fake bus
  stub, on the existing hermetic `:memory:` sqlite harness (`./run_tests.sh`).

## Suggested build phasing (one spec, staged plan)

0. **Infra** — create the `eventbus` network + the dedicated `/srv/docker/eventbus/`
   stack (Redis + secret + `eventbus-kit`) + cortex joins the network.
   Smoke-test publish/consume.
1. **Outbound** — cortex email-worker consuming `email.send` (publish a test
   message → receive an email).
2. **hs registration (outbound #1)** — `pending_registrations` + `/register` +
   publish admin-notify.
3. **Inbound** — cortex approval command + allowlist → publish `events:hs`.
4. **hs consumer + activation + welcome (outbound #2)** — round-trip closed.

"Not finishing right now" is fine — the phases are independently shippable, and
phase 0–1 are useful on their own (any site can then send mail through cortex).

## Out of scope (this spec)

- Replicating to other sites (discernandgrow, topnotch, etc.) — trivial once the
  pattern exists.
- Switching existing Resend senders over to cortex.
- Inbound triggers other than registration approval.
- Per-source message signing (only needed if untrusted publishers join the bus).
