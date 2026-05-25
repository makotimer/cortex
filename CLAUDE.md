# CLAUDE.md — cortex

## Project shape

Python 3.12 container. APScheduler runs jobs defined in `local/config.json`. ProtonMail Bridge sidecar (`cortex_bridge`) handles all SMTP/IMAP. An IMAP listener watches `Labels/Command` for ad-hoc run requests. No database — state lives in `local/`.

```
service/   — scheduler, runner, IMAP listener, emailer, MCP server, CLI entrypoint
  imap_commands/  — parses + dispatches IMAP commands (LIST, RUN MODULE=, CAREER REPORT)
    templates.py  — response message templates used by handlers
  config_schema.py — JSON schema definition + validation for local/config.json
  logging_utils.py — shared logging helpers used across service modules
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

See `.env.example` for all keys with descriptions.

## Gotchas

- **Bridge must be logged in** before cortex will send or receive mail. One-time setup: `docker compose up -d cortex_bridge` then `docker exec -it cortex_bridge protonmail-bridge --cli` → `login`.
- **`local/` is a bind-mount** from the host at `/srv/docker/cortex/local/`. It is not inside the image. Never put secrets in the image.
- **Heartbeat** — the scheduler writes `local/state/heartbeat` every 60 s. The Docker `HEALTHCHECK` watches this file (`find ... -mmin -2`). If the scheduler stalls the container goes unhealthy.
- **IMAP command format** — send an email to yourself with subject matching a command (e.g. `LIST`, `RUN MODULE=modules.example_daily`). The listener polls `Labels/Command`.
- **Dry-run** — set `CORTEX_DRY_RUN=1` in `.env` to suppress all outbound email.
- **VPN sidecar** — the `vpn` service (gluetun/ProtonVPN WireGuard) must be running for `career_watch` to scrape. If `CAREER_WATCH_PROXY_URL` is set and gluetun is unreachable, `career_watch` skips the run (fail-closed). Bring it up with `docker compose up -d vpn`.
- **VPN peer keys go stale** — ProtonVPN rotates WireGuard peer public keys periodically. If gluetun's server list is old, the tunnel will appear up (`tun0` gets an IP, `public_ip` returns empty) but pass no traffic. `SERVER_UPDATE_PERIOD=24h` in `docker-compose.yaml` keeps the list fresh. Symptom: gluetun logs show continuous `i/o timeout` healthcheck failures cycling through many servers. Fix: `docker compose up -d vpn` after ensuring `SERVER_UPDATE_PERIOD` is not `0`.
