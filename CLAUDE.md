# CLAUDE.md — cortex

## Project shape

Python 3.12 container. APScheduler runs jobs defined in `local/config.json`. ProtonMail Bridge sidecar (`cortex_bridge`) handles all SMTP/IMAP. An IMAP listener watches `Labels/Command` for ad-hoc run requests. No database — state lives in `local/`.

```
service/   — scheduler, runner, IMAP listener, emailer, CLI entrypoint
modules/   — one subdirectory per job module (each has a run() entry point)
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
make setup          # create .venv, install deps, bootstrap local/config.json
make test           # run pytest in container (skips live tests)
make live-test bae  # run a single live test by keyword
make lint           # ruff check + mypy
make format         # ruff format
make rebuild        # pull base images, rebuild, force-recreate
make logs-f         # follow cortex container logs
```

Run anything inside the container:
```bash
docker compose run --rm cortex python -m service.cli <args>
docker compose exec cortex python scripts/proton_query.py list-folders
```

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
