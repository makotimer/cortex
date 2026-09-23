# Architecture

Sources for every claim here: `docker-compose.yaml`, `Dockerfile`,
`docker inspect` on the four running containers, `local/config.json`, and the
`service/` and `modules/` packages — all read on 2026-09-13.

## Components

**Four containers, one compose project (`cortex`).** Service name → container
name, from `docker compose config` and `docker ps` on 2026-09-13:

| Service | Container | Image | Role |
|---|---|---|---|
| `cortex` | `cortex-cortex-1` | built from `./Dockerfile` (`python:3.12-slim`, digest-pinned) | scheduler + runner + IMAP listener + `email.send` consumer; also hosts the on-demand MCP server |
| `cortex_bridge` | `cortex-cortex_bridge-1` | `ghcr.io/videocurio/proton-mail-bridge` (digest-pinned) | ProtonMail Bridge; every SMTP and IMAP byte goes through it |
| `vpn` | `cortex-vpn-1` | `qmcgaw/gluetun:v3` (ProtonVPN WireGuard) | HTTP proxy on `:8888` and control API on `:8000` for the scrapers |
| `autoheal` | `cortex-autoheal-1` | `willfarrell/autoheal:latest` | restarts any container labelled `autoheal=true` whose healthcheck goes red |

**Only `cortex_bridge` and `vpn` carry the `autoheal=true` label.** `cortex`
itself does not, so an unhealthy `cortex` (stalled heartbeat) is *not*
auto-restarted — `docker-compose.yaml`, 2026-09-13.

**`cortex` runs `python -m service.cli serve` under `tini`, as user `app`,
capped at `mem_limit: 512m`** (`Dockerfile`, `docker-compose.yaml`). `serve`
starts three things in one process (`service/cli.py`):

1. APScheduler, jobs from `local/config.json` (`service/scheduler.py`).
2. The IMAP listener thread (`service/imap_listener.py`).
3. The outbound-email worker thread (`service/email_outbound.py`).

### The `service/` package

| Module | Does |
|---|---|
| `cli.py` | argparse entry point: `serve`, `run <module> [--kwargs k=v …] [--no-email] [--print-html]`, `trigger-reading [--date] [--no-dedup]`, `list-jobs`, `validate-config`; global `--config` |
| `scheduler.py` | builds APScheduler jobs from config; triggers `cron`, `interval`, `date`, `daily_time`; touches `local/state/heartbeat` every 60 s; `SCHEDULER_PREVIEW=1` prints upcoming fires |
| `runner.py` | `run_module_once`: imports the module, calls `run(**kwargs)`, emails the returned HTML; honours `CORTEX_DRY_RUN` / `SEND_EMAIL` |
| `imap_listener.py` | polls `COMMANDS_FOLDER` on `PROTON_IMAP_HOST:PROTON_IMAP_PORT` (defaults `cortex_bridge:143`); persists the last handled UID in `local/state/command_last_uid_<folder>.txt` |
| `imap_commands/` | `parser.py` recognises `LIST`, `CAREER REPORT`, `RUN MODULE=<id> [KWARGS={…}] [NO_EMAIL=true] [PRINT_HTML=true]`, `APPROVE <token>`, `DENY <token>`; `handlers.py` dispatches; `templates.py` renders replies |
| `emailer.py` | SMTP send via `BRIDGE_HOST:BRIDGE_SMTP_PORT` (`.env`: `cortex_bridge:25`) |
| `email_outbound.py` | consumer group `cortex-emailer` on the `email.send` stream; `MAX_ATTEMPTS = 3`, then dead-letters to `email.send.dead` |
| `site_events.py` | publishes `registration.decision` onto `events:<site>`, site taken from the token prefix (`hs-…` → `events:hs`) |
| `skip_tokens.py` | `local/state/skip/<job-id>.json`; lets a CLI run cancel the next scheduled fire (600 s grace) |
| `config_schema.py` | loads and validates `local/config.json`; resolves `*_env` fields from the environment |
| `logging_utils.py` | JSONL `activity-YYYY-MM-DD.jsonl` / `error-YYYY-MM-DD.jsonl` under `LOG_DIR` (default `/app/local/logs`), with key-based redaction |
| `mcp_server.py` | `MCPServer` (mcp 2.x) exposing the Bridge account as tools; started on demand by `docker exec`, never by `serve` |
| `mcp_vpn.py` | `MCPServer` `vpn`: `vpn_fetch` / `vpn_status`, a page GET through gluetun (`modules/_shared/probe.py`, shared with `scripts/event_probe.py`); same on-demand start |

### The `modules/` package

Each job module is a directory exposing `run(**kwargs)`; `modules/README.md` is
the author's guide and `modules/example_daily/` the template.

| Module | Does | Scheduled (`local/config.json`, 2026-09-22) |
|---|---|---|
| `bible_plan` | daily reading email linking `https://study.coviecraft.dev`; no LLM call (`modules/bible_plan/main.py`) | Mon–Thu 05:25, Fri–Sun 06:55 |
| `career_watch` | job-board scrapers through the VPN proxy; SQLite state `local/state/careerwatch.db` | 10×/day Mon–Sat, 05:00–18:30 every 90 min; a second user's job is `enabled: false` |
| `event_watch` | 31 scraper files under `lib/scrapers/`, one `kind` per job, publishing onto `events:<site>` | 29 jobs Tue+Fri 01:10–01:36, at most six at once; `rei` disabled; `event-watch-done` at 02:00 |
| `vpn_cycle` | moves the VPN exit while nothing is scraping (`DEFAULT_ATTEMPTS = 5`) | 10×/day Mon–Sat at :45/:15, 15 min before each `career_watch` run; Tue+Fri 01:00 before the event grid |
| `sonos` | hourly chime via SoCo; `sonos_volume` kwarg 25 (quiet) / 40 (loud) | four jobs splitting Sun vs Mon–Sat, quiet vs loud hours |
| `health_check` | CLI `python -m modules.health_check --json --date yesterday`; reads the JSONL logs and judges the day | not scheduled — the ops nightly `docker exec`s it (`websites/ops/scripts/nightly_fleet.sh:75`) |
| `example_daily` | reference implementation | — |
| `_shared/` | `vpn_client` (used by both scrapers and `vpn_cycle`), `probe` (verified-exit page fetch behind `event_probe.py` and the `vpn` MCP), `http`, `cache`, `dates`, `email_ctx`, `utils` (`OpenAIChat`), `logging_bridge`; `html.py` is an empty stub | — |

## Request flow

**Scheduled job.** APScheduler fires → `runner.run_module_once` imports
`modules.<name>` and calls `run(**kwargs)` → returned HTML is sent by
`service/emailer.py` over SMTP to `cortex_bridge:25` → Bridge relays to Proton.
Every step writes a JSONL record to `local/logs/activity-<date>.jsonl`.

**Scraper job (`career_watch`, `event_watch`).** Before fetching, the engine
calls `vpn_client.switch_until_usable()` against the gluetun control API
`http://vpn:8000`, which restarts the tunnel and re-verifies that a real target
URL answers through `http://vpn:8888`. Failure raises `VPNUnavailableError`
and the run fails loudly (`modules/career_watch/lib/engine.py:48`,
`modules/event_watch/lib/engine.py:30`). Successful `event_watch` runs publish
each event onto `events:<site>` on the eventbus (`modules/event_watch/lib/publish.py`).

**Inbound command.** A mail lands in the Proton `Labels/Command` folder →
`imap_listener` sees it → `imap_commands.parser.parse_command_line` →
`handlers` runs it → a reply is sent to the sender over SMTP. `APPROVE`/`DENY`
additionally publish `registration.decision` onto `events:<site>`
(`service/site_events.py`).

**Outbound mail from other stacks.** Any fleet service publishes onto
`email.send` → `email_outbound` (group `cortex-emailer`) reads it → sends via
Bridge → acks; after 3 failed attempts the message moves to `email.send.dead`.

## Boundaries

- **Cortex never talks to Proton directly.** SMTP `:25` and IMAP `:143` on
  `cortex_bridge` are `socat` forwards to Bridge's loopback `:1025`/`:1143`
  (`/app/entrypoint.sh` inside the bridge image, checked 2026-09-13).
- **Scrapers never egress on the host IP when a proxy is configured.**
  `CAREER_WATCH_PROXY_URL` and `EVENT_WATCH_PROXY_URL` are both
  `http://vpn:8888` in `.env`; the engines fail closed when gluetun is down.
- **No LLM is reachable from cortex.** Cortex joins only `mailnet` and
  `eventbus`; every `llm-proxy` sidecar lives on its own site's networks.
  `OpenAIChat` in `modules/_shared/utils.py` is the only client and no
  scheduled module calls it (`bible_plan` dropped its LLM commentary in commit `35a3d2d`, 2026-06-07).
- **No shared database.** State is files under `local/`, bind-mounted from
  `/srv/docker/cortex/local`: `config.json`, `logs/`, `state/` (heartbeat,
  `careerwatch.db`, `skip/`, `event_watch/`, `llm/`, `vpn_survey/`).
- **Code is baked into the image.** `service/`, `modules/`, `scripts/`,
  `tests/` are `COPY`d in the `Dockerfile`; only `local/` and the two kits are
  mounted. See `development.md` → *Deploy: restart vs rebuild*.
- **Two shared kits are mounted read-only** and put on `PYTHONPATH`:
  `../eventbus/eventbus-kit` → `/eventbus-kit` and
  `../websites/address-kit` → `/address-kit`. Editing either changes cortex
  without a rebuild — but only after the process restarts.
- **The MCP servers are out-of-band.** `~/.claude.json` registers `protonmail`
  as `docker exec -i cortex-cortex-1 python -m service.mcp_server` and `vpn`
  as `… python -m service.mcp_vpn`; they share the container (and so the
  `vpn:8888` proxy) but not the `serve` process.

## Ports and networks

**Nothing is published to the host** (`docker compose config` shows no
`ports:` on any service, 2026-09-13). Internal listeners:

| Container | Port | What |
|---|---|---|
| `cortex_bridge` | 25 | SMTP (→ Bridge 1025) |
| `cortex_bridge` | 143 | IMAP (→ Bridge 1143) |
| `vpn` | 8888 | gluetun HTTP proxy (`HTTPPROXY=on`) |
| `vpn` | 8000 | gluetun control API (`/v1/publicip/ip`, `PUT /v1/vpn/status` — `modules/_shared/vpn_client.py`) |

Networks, from `docker inspect … .NetworkSettings.Networks` on 2026-09-13:

| Network | Declared by | Members |
|---|---|---|
| `mailnet` (Docker name `cortex_mailnet`) | this stack | `cortex`, `cortex_bridge`, `vpn` |
| `eventbus` | **external**, `/srv/docker/eventbus` | `cortex` only |
| `default` (`cortex_default`) | implicit | `autoheal` only |

The `eventbus_redis_password` Docker secret is likewise external
(`file: ../eventbus/secrets/eventbus_redis_password`). Network and secret both
resolve at `docker compose up` time, so the eventbus stack must exist first.

Volumes: `cortex_cortex_bridge-data` (Bridge vault and tokens, mounted at
`/root`) is the only named volume; everything else is a bind mount.
