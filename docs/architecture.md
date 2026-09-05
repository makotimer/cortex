# Architecture

`cortex` is the MakoTimer brain: it runs scheduled jobs, sends email, scrapes the
public web behind a VPN, and accepts ad-hoc commands over IMAP. Python 3.12 in
Docker, APScheduler for scheduling, **zero cron** — every job is declared in
`local/config.json`.

## Containers

| Container | Image | Role |
|---|---|---|
| `cortex` | built from `./Dockerfile` (`python:3.12-slim`) | scheduler, runner, IMAP listener, CLI, MCP server |
| `cortex_bridge` | ProtonMail Bridge | all SMTP/IMAP; cortex never talks to Proton directly |
| `vpn` | `qmcgaw/gluetun` (ProtonVPN WireGuard) | HTTP proxy + control API for the scrapers |

`cortex` depends on `cortex_bridge` being **healthy**, not merely started — the
bridge healthcheck performs a real IMAP `LOGIN`, so cortex never comes up against a
bridge that is still syncing.

## Networks

Two, and neither is named `cortex`:

- **`mailnet`** — declared by this stack. All three containers join it; it is how
  cortex reaches `cortex_bridge:143/25` and `vpn:8888/8000`.
- **`eventbus`** — **external**, owned by `/srv/docker/eventbus`. Only `cortex`
  joins it, to publish onto Redis Streams (outbound email, site events).

The `eventbus_redis_password` Docker secret is likewise external, read from
`../eventbus/secrets/`. Both the network and the secret resolve at
`docker compose up` time, so the eventbus stack must have been started at least
once or `up` fails before any container runs.

Cortex is **not** attached to the `proxy` network and has no route to any site's
`llm-proxy` sidecar — there is no LLM reachable from here. Anything needing
classification or summarization has to solve that first.

## State

There is no database. State is files under `local/`, bind-mounted from the host at
`/srv/docker/cortex/local`:

```
local/
├── config.json   ← every job definition
├── logs/         ← activity-*.jsonl / error-*.jsonl, size-rotated
└── state/        ← heartbeat, per-module caches, skip tokens, LLM archives
```

`local/` is the **only** bind mount for application data. `service/`, `modules/` and
`tests/` are baked into the image, so host edits need `make reload` to take effect.

The scheduler uses an in-memory jobstore, so a separate CLI process cannot reach live
jobs; cross-process coordination goes through files in `local/state/` (for example the
skip tokens that let `make trigger-reading` cancel the upcoming scheduled fire).

## Liveness

The scheduler touches `local/state/heartbeat` every 60 s and the Docker `HEALTHCHECK`
watches that file (`find ... -mmin -2`). A stalled scheduler turns the container
unhealthy even though the process is still up.

## Outbound web

Scrapers (`career_watch`, `event_watch`) go through the gluetun HTTP proxy and are
**fail-closed**: if a proxy URL is configured and gluetun is unreachable, the run is
skipped rather than sent out on the host IP. Exit rotation is a separate scheduled
job (`vpn_cycle`) so a rotation failure cannot silently empty a scrape. See
`docs/vpn-instability.md` and the VPN notes in `CLAUDE.md` for the calibrated
timeouts — they are measured, not guessed.
