# CLAUDE.md — cortex

## Project shape

Python 3.12 container. APScheduler runs jobs defined in `local/config.json`. ProtonMail Bridge sidecar (`cortex_bridge`) handles all SMTP/IMAP. An IMAP listener watches `Labels/Command` for ad-hoc run requests. No database — state lives in `local/`.

```
service/   — scheduler, runner, IMAP listener, emailer, MCP server, CLI entrypoint
  imap_commands/  — parses + dispatches IMAP commands (LIST, RUN MODULE=, CAREER REPORT)
    parser.py     — tokenizes and parses raw IMAP command strings
    handlers.py   — dispatches parsed commands and builds reply payloads
    templates.py  — response message templates used by handlers
  config_schema.py — JSON schema definition + validation for local/config.json
  logging_utils.py — shared logging helpers used across service modules
modules/   — one subdirectory per job module (each has a run() entry point)
  _shared/      — shared helpers: cache, dates, email_ctx, html, http, utils
  example_daily/ — minimal reference implementation; copy this to create a new module
  career_watch/ — job-board scraper; two users, VPN-rotated IPs, Mon-Sat
  bible_plan/   — daily prayer-and-study emails; Mon-Thu and Fri-Sun schedules; links to study.coviecraft.dev + a rotating weekday prayer focus (no LLM)
  sonos/        — hourly Sonos chimes; volume varies by day and hour
scripts/   — host-side utilities and container helpers
tests/     — pytest unit + optional live tests
local/     — bind-mounted at runtime: config.json, logs/, state/
```

## Planned: event injectors for discoverbcs.org

Approved design, not started. A new module (`modules/event_watch/`) scrapes public
event calendars and publishes them onto `events:discoverbcs`, where the site's worker
validates and stores them. The receiving site is **finished and waiting** — it holds
zero events until this exists.

**Read the design before starting:**
`/srv/docker/websites/discoverbcs/docs/superpowers/specs/2026-08-12-bcs-library-event-injector-design.md`
and the contract it targets, `/srv/docker/websites/discoverbcs/docs/intake-contract.md`.

It follows `career_watch` deliberately: a scraper family keyed by `kind`, the gluetun
proxy with per-run rotation, and fail-closed on VPN health. First source is the BCS
library's Tockify feed. `vpn_client.py` should move from `career_watch/lib/` to
`modules/_shared/` when this lands — it will have a second consumer.

## Entrypoint

```
python -m service.cli serve       # what the container runs
service/cli.py                    # argparse main; also exposes `run` and `list` subcommands
```

## Key commands

```bash
make setup           # create .venv, install deps, bootstrap local/config.json
make install         # install deps in editable mode + dev tools (also run by make setup)
make test            # run pytest in container (skips live tests)
make live-tests      # run all live tests in container
make live-test bae   # run a single live test by keyword
make lint            # ruff check + mypy
make format          # ruff format
make up              # start all services (docker compose up -d)
make down            # stop all services
make reload          # rebuild and restart cortex container only
make reload-bridge   # rebuild and restart the bridge container
make tail            # last 100 lines of cortex logs
make tail-f          # last 100 lines, then follow
make logs            # last 100 lines of cortex logs (non-following)
make rebuild         # pull base images, rebuild, force-recreate
make logs-f          # follow cortex container logs (alias for tail-f)
make career-report   # run career report script locally (outside container)
make trigger-reading # send today's Bible reading now + dedup the upcoming scheduled fire
make clean           # docker compose down -v --remove-orphans
```

Run anything inside the container:
```bash
docker compose run --rm cortex python -m service.cli <args>
docker compose exec cortex python scripts/proton_query.py list-folders
```

## MCP server

`service/mcp_server.py` is a FastMCP server that exposes the ProtonMail Bridge IMAP account as Claude Code tools. It is **not** started by the container — it is invoked on-demand by Claude Code:

```bash
docker exec -i cortex-cortex-1 python -m service.mcp_server
```

Available tools: `list_folders`, `list_emails`, `read_email`, `search_emails`, `move_email`, `move_emails`, `send_email`.

The server is registered at user scope in `~/.claude.json` (`claude mcp add --scope user`), so it is available in every Claude Code session regardless of working directory. If the tools aren't showing up, restart the session — MCP servers connect at startup.

## .env requirements

Copy `.env.example` and fill in real values. Minimum to start:
- `BRIDGE_USERNAME` / `BRIDGE_PASSWORD` — from `docker exec -it cortex_bridge protonmail-bridge --cli` → `info`
- `BRIDGE_HOST` / `BRIDGE_SMTP_PORT` / `BRIDGE_IMAP_PORT` — typically `cortex_bridge` / `25` / `143`
- `SEND_EMAIL` — set to `1` to enable outbound mail
- `OPENAI_API_KEY` — used by the shared `modules/_shared/utils.OpenAIChat` facade (not used by `bible_plan`, which no longer calls an LLM)
- `LLM_MD_ENABLE` — set to `1` to archive each LLM response as a `.md` file under `LLM_MD_DIR` (default `/app/local/state/llm`)
- `LLM_MD_MAX` — max number of archived files to keep per run; `0` = unlimited

See `.env.example` for all keys with descriptions.

## Gotchas

- **Bridge must be logged in** before cortex will send or receive mail. One-time setup: `docker compose up -d cortex_bridge` then `docker exec -it cortex_bridge protonmail-bridge --cli` → `login`.
- **`local/` is a bind-mount** from the host at `/srv/docker/cortex/local/`. It is not inside the image. Never put secrets in the image.
- **Heartbeat** — the scheduler writes `local/state/heartbeat` every 60 s. The Docker `HEALTHCHECK` watches this file (`find ... -mmin -2`). If the scheduler stalls the container goes unhealthy.
- **IMAP command format** — send an email to yourself with subject matching a command (e.g. `LIST`, `RUN MODULE=modules.example_daily`). The listener polls `Labels/Command`.
- **Dry-run** — set `CORTEX_DRY_RUN=1` in `.env` to suppress all outbound email.
- **VPN sidecar** — the `vpn` service (gluetun/ProtonVPN WireGuard) must be running for `career_watch` to scrape. If `CAREER_WATCH_PROXY_URL` is set and gluetun is unreachable, `career_watch` skips the run (fail-closed). Bring it up with `docker compose up -d vpn`.
- **VPN peer keys go stale** — ProtonVPN rotates WireGuard peer public keys periodically. If gluetun's server list is old, the tunnel will appear up (`tun0` gets an IP, `public_ip` returns empty) but pass no traffic. The server-list updater is `UPDATER_PERIOD=6h` in `docker-compose.yaml` (gluetun v3 renamed the old `SERVER_UPDATE_PERIOD`; that key no longer does anything). Symptom: gluetun logs show continuous `i/o timeout` healthcheck failures cycling through many servers. Fix: `docker compose up -d vpn` after ensuring `UPDATER_PERIOD` is not `0` — but if the updater is already running, suspect a stale `PROTON_WG_PRIVATE_KEY` instead, which produces the same silent no-traffic signature.
- **A failed VPN switch used to be silent — it is not any more. Diagnose it with
  `vpn_switch`.** The old gate checked health *before* rotating and never re-checked,
  so a rotation that didn't finish left the scrape on a still-reconnecting tunnel and
  the run logged `ok: true` / `no_new` — a quiet day, not a failure. Over 626 runs
  (2026-06-01 → 08-12) rotation failed on 22% of them, and 54% of those scraped **zero**
  results from every source.
  That gate is gone. Both engines now call `vpn_client.switch_until_usable()`, which
  re-reads the IP *and* fetches a real URL through the proxy after every restart, and
  raises `VPNUnavailableError` when no exit works — the run fails loudly instead of
  returning empty. `rotate()` was deleted on 2026-08-14 (no caller; its "the IP must
  change" success test was itself a source of false failures).
  The event to read is **`vpn_switch`**, not `vpn_rotated` — that op no longer exists.
  It carries `ok`, `ip`, `changed`, `attempts`, `seconds`, `reason`,
  `tried[]`, and `restarts[]` (seconds per tunnel restart). `VPN_ROTATE_TIMEOUT`
  (default **120 s**) is the knob: raise it if `restarts` clusters near the ceiling,
  and note that a restart landing *exactly* on the ceiling is one that never produced
  an IP at all. Surveying 457 live exits (2026-08-13/14) put clean switches at a 14 s
  median and 46 s p90, with ~4.5% never coming up; every exit that did come up carried
  traffic, at a 0.4 s median. So there is no "still settling" grey zone to wait out —
  the outcome is binary, and `VERIFY_DEADLINE_SECONDS` exists for a measured 36 s tail,
  not for the common case.
  There is **no exit quarantine any more** (deleted 2026-08-14, along with
  `VPN_QUARANTINE_PATH`). It guarded failure modes measured at zero — 0/633 verify
  failures on live exits, 0/148 exits bad every time seen — and the one time it did
  fire in production it condemned three healthy servers inside 66 seconds, all of
  which were later measured working. A failed exit is switched away from, not
  remembered.
- **`SERVER_COUNTRIES` does not bound where traffic actually exits.** ~4% of exits
  (18 of 457 surveyed) egress from countries not in the list at all — Sweden, Germany,
  Norway, Slovenia, Luxembourg, Spain, Austria against a configured pool of
  US/Canada/Switzerland/Netherlands. These are ProtonVPN's **Tor-over-VPN** servers:
  the Proton server sits in an allowed country so gluetun's filter passes it, but
  egress lands on a Tor exit relay elsewhere. Recognise them by operator — DFRI
  (`171.25.193.x`) and Zwiebelfreunde (`185.220.101.x`) are Tor exit ranges. They are
  not broken (all 12 sampled worked) but they were 4.6× slower to first traffic, and
  `vpn_client.usable()`'s docstring records one holding a valid IP while unable to
  reach the target at all — the exact shape of a silently empty scrape. The survey
  flags them as `outside_pool`; if they ever correlate with blocked scrapes, exclude
  Tor servers from gluetun's pool rather than widening timeouts.
- **No LLM is reachable from cortex.** `llm-proxy` exists and is well established —
  six sites under `/srv/docker/websites/` run one as a sidecar (multi-backend, tiers
  `light`/`middle`/`heavy`, `X-Proxy-Secret` auth on `:11434`) — but every instance is
  attached to its own site's stack. Cortex joins only `mailnet` and `eventbus`, so it
  has **no route to any of them**, and no LLM client of its own. Anything here needing
  classification or summarization must first resolve that: give the target site a
  sidecar on a network cortex can reach, or add a cortex-side client. Don't assume the
  capability is available because the service exists.
- **eventbus stack must exist before cortex starts** — cortex's compose declares the
  `eventbus_redis_password` Docker secret with `file: ../eventbus/secrets/eventbus_redis_password`
  and joins the external `eventbus` network. Both are resolved at `docker compose up` time, so if
  the eventbus stack has never been brought up, `up`/`make reload` fails with a compose error
  before any container starts (the worker's in-process graceful-degradation can't help here).
  Bring the bus up once first: `cd /srv/docker/eventbus && docker compose up -d`.
- **Registration approval replies must reach the `Command` folder.** cortex's IMAP listener
  only watches the Proton `Command` folder. For an admin's `APPROVE hs-…` / `DENY hs-…` reply
  to be processed, create a one-time Proton sieve filter: if the subject or body starts with
  `APPROVE ` or `DENY `, move/label the message into `Command`. Also set `approval_allowlist`
  (array of admin emails) in `local/config.json` — only those senders' decisions are honored.
