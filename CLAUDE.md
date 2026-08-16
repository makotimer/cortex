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
- **VPN peer keys go stale** — ProtonVPN rotates WireGuard peer public keys periodically. If gluetun's server list is old, the tunnel will appear up (`tun0` gets an IP, `public_ip` returns empty) but pass no traffic. Symptom: gluetun logs show continuous `i/o timeout` healthcheck failures cycling through many servers.
- **`UPDATER_PERIOD` alone never updated anything, and the server list is the
  image's baked-in snapshot.** Found 2026-08-16. `UPDATER_PERIOD=6h` is set and the
  ticker does fire, but for ProtonVPN gluetun refuses the job without account
  credentials and says so at WARN, once, between restarts:
  `getting protonvpn servers: credentials missing: email is empty - skipping update
  for protonvpn`. Reproduce it in isolation without touching the live stack:
  `docker run --rm -v <scratch>:/gluetun qmcgaw/gluetun:v3 update -enduser -providers protonvpn`.
  Two ways to see it in place: the `protonvpn` block of `/gluetun/servers.json` carries
  `"timestamp": 1763472933` (**2025-11-18** — gluetun v3.41.1's build snapshot, not
  anything fetched), and every start logs `merging by most recent 20901 hardcoded
  servers and 20901 servers read from /gluetun/servers.json` — identical counts on
  both sides means the file *is* the hardcoded list.
  Fix: set `PROTON_API_EMAIL` / `PROTON_API_PASSWORD` in `.env` (the Proton **account**
  login, not `PROTON_WG_PRIVATE_KEY`); compose passes them as
  `UPDATER_PROTONVPN_EMAIL` / `UPDATER_PROTONVPN_PASSWORD`. Then
  `docker compose up -d vpn` and confirm with
  `docker compose logs vpn | grep -i "updating Protonvpn"`. Left empty, behaviour is
  exactly as before — it fails open, and silently, which is why it went unnoticed.
  A 9-month-old list is the leading suspect for the residual 3.7% of switches that
  never produce an IP. Note this is *also* the signature of a stale
  `PROTON_WG_PRIVATE_KEY`; rule the list out first now that it is cheap to check.
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
  `tried[]`, and `restarts[]` (seconds per tunnel restart). A restart landing
  *exactly* on `VPN_ROTATE_TIMEOUT` is one that never produced an IP at all.
- **`VPN_ROTATE_TIMEOUT` is settled at 120 s. Do not raise it.** The overnight
  survey (1,518 exits, 2026-08-12 → 08-16) was built to decide this and the answer
  is counter-intuitive, so it is written down here rather than re-derived. The
  ceiling *does* manufacture its own failure rate — the share of switches that never
  produce an IP is 12.5% at 90 s (n=24), 7.2% at 120 s (n=542) and 3.7% at 180 s
  (n=952), and at 180 s a real 1.6% of *successful* switches took longer than 120 s.
  But `switch_until_usable` retries 3×, and that swamps the ceiling completely.
  Simulating the production retry loop against the empirical distribution:

  | ceiling | mean time to usable | run fails | worst case |
  |---|---|---|---|
  | 90 s | 26.5 s | 0.050% | 4.5 min |
  | **120 s** | **28.0 s** | **0.038%** | **6 min** |
  | 180 s | 30.5 s | 0.018% | 9 min |

  120 → 180 buys 0.02 pp of run reliability — one saved run every few years at 14
  switches/day — and pays three extra minutes of worst-case dead stall. Judge this
  knob on *run*-level failure, never on the single-switch no-IP rate, which is what
  makes a bigger ceiling look free.
- **The verify budget is calibrated; leave it alone.** A probe ladder across 1,420
  exits: 97.3% carry traffic on the first probe, 98.4% by +2 s, 99.3% by +4 s,
  99.65% by +8 s, then nothing until +30 s (4 exits) and one that never did.
  `VERIFY_SETTLE_SECONDS=2.0` / `VERIFY_DEADLINE_SECONDS=45.0` / `VERIFY_MAX_PROBES=6`
  cover that with room to spare. There is no "still settling" grey zone to widen for.
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
  flags them as `outside_pool`.
  **They now do correlate with blocked scrapes — and the right response is still to
  do nothing.** Over the full 1,441 live exits, `outside_pool` ones passed every
  target only 80.9% of the time against 99.1% for in-pool, and **7 of the 9 hard 403s
  in the whole corpus came from that 3.3% of exits** (15% of Tor exits were 403'd by
  Tockify, versus 0.2% in-pool). Excluding them is nevertheless not on the table:
  gluetun's `/gluetun/servers.json` ProtonVPN records carry only
  `city, country, hostname, ips, port_forward, server_name` — **no Tor flag** — and
  gluetun offers allowlists (`SERVER_NAMES`) but no exclusion filter. Only 3 of the
  286 servers in the 4-country pool are even named `*-TOR` (`CH#18-TOR`,
  `US-CO#21-TOR`, `US-GA#29-TOR`), and one Tor entry server yields many egress IPs.
  So the measured cost of ignoring this is 3.3% × 15% ≈ **0.5% of switches wasted**,
  and `switch_until_usable` already detects the 403 and switches away by itself. A
  283-name allowlist that goes stale is the worse trade.
- **A verify URL must be a real target of the same class you are scraping.** Until
  2026-08-16 `career_watch` verified exits against `cloudflare.com/cdn-cgi/trace`,
  which proves the tunnel carries traffic and nothing about whether a job board will
  answer — so an exit blocked by board infrastructure verified clean, scraped zero and
  logged `ok: true`. That is the silent-empty-scrape failure `switch_until_usable`
  exists to kill, displaced one layer outward. The default is now
  `career_watch.lib.engine.DEFAULT_VERIFY_URL` (Lever's postings API, `limit=1`),
  overridable with `CAREER_WATCH_VERIFY_URL`. `event_watch` never had the gap: each
  scraper sets `verify_url` to its own real target. The survey says this was still
  theoretical — Lever hard-blocked 0 of 989 exits, its 6 failures all transient
  `ProxyError` — so treat it as hardening, not a fix for an observed outage.
- **The overnight VPN exit survey is finished and its cron is stopped**
  (`scripts/vpn_survey_cron.sh`, commented out of the crontab 2026-08-16; backup at
  `local/state/crontab.bak.before-vpn-survey-stop-20260816`). Everything it measured
  is written into the bullets above — read those before re-running it. Its own header
  claimed that leaving the crontab line in place "costs nothing" because of a
  1,500-record self-limit; that was wrong. Each night stopped on the 03:20 clock at
  ~425 records, so the limit was never reached and the job restarted the tunnel
  ~100×/night indefinitely. That churn is also why the (broken anyway) 6 h server-list
  updater tick could never fire on a survey night. Raw records:
  `local/state/vpn_survey/survey-*.jsonl`.
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
