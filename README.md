---
repo: cortex
what: Python scheduler + ProtonMail Bridge + VPN sidecar — scheduled jobs, IMAP commands, fleet outbound mail
containers: [cortex, cortex_bridge, vpn, autoheal]
ports: []               # nothing published; internal 25/143 (bridge), 8888/8000 (vpn)
networks: [mailnet, eventbus]
deploy: mount           # LINTER EVIDENCE ONLY — ops/ops-worker bind-mount this tree for audits and run_tests.sh. cortex's OWN code is baked into its image: a .py edit needs `make reload`, never `restart`. See Deploy.
verified: 2026-09-13
---

# cortex

## Orientation

**cortex is the automation brain: one Python 3.12 container running APScheduler
jobs from `local/config.json`, plus a ProtonMail Bridge sidecar and a gluetun
VPN sidecar.** Every stack's `email.send` event is delivered here through
Proton (sites' own transactional mail goes via Resend, not here), and the
Proton `Labels/Command` folder is read only here.

Things that get confused, checked 2026-09-13:

- **`cortex_bridge` is a compose service name, not a container.** The container
  is `cortex-cortex_bridge-1`; the Bridge CLI inside it is `bridge --cli`, not
  `protonmail-bridge`. `docker exec -it cortex_bridge …` fails.
- **Code is baked into the image.** `service/`, `modules/`, `scripts/`, `tests/`
  are `COPY`d by the `Dockerfile`; only `local/` and two read-only kits are
  mounted. Unlike the website apps, editing a `.py` on the host changes nothing
  until `make reload`. The frontmatter's `deploy: mount` is about how `ops`
  mounts this tree for audits, not about how cortex runs.
- **`make test` tests the image, `./run_tests.sh` tests the working tree.**
  Different counts are expected (626+39 skipped vs 654 on 2026-09-13).
- **No LLM is reachable from here.** cortex joins `mailnet` and `eventbus` only;
  every `llm-proxy` sits on its own site's networks. `OPENAI_API_KEY` powers
  nothing scheduled.
- **This is not `eventbus`.** cortex consumes `email.send` and publishes
  `events:<site>` / `registration.decision`; the bus itself, and the
  `eventbus_redis_password` secret, live in `/srv/docker/eventbus`.
- **This is not `ops`.** `websites/ops` (read-only) and `websites/ops-worker`
  (read-write) bind-mount this whole directory for dep audits and
  `run_tests.sh`; nothing here imports from them.

## Quickstart

```bash
cd /srv/docker/eventbus && docker compose up -d   # once: external network + secret
cd /srv/docker/cortex
make up                                            # start (no build)
./run_tests.sh                                     # host, working tree, ~50 s — 654 passed 2026-09-13
make reload && make test                           # rebuild image, then test the image
make logs-f                                        # follow cortex-cortex-1
docker compose run --rm cortex python -m service.cli list-jobs
```

Config: `local/config.json` (jobs) + `.env` (Bridge creds, proxy URLs, switches).
`.env.example` documents every key. `make setup` creates `.venv` and copies both
examples into place.

## Deploy

| Changed | Run |
|---|---|
| `service/`, `modules/`, `scripts/`, `tests/`, `pyproject.toml`, `Dockerfile` | `make reload` |
| `docker-compose.yaml`, `.env` | `docker compose up -d --force-recreate <service>` |
| `local/config.json`, `../eventbus/eventbus-kit`, `../websites/address-kit` | `docker compose restart cortex` |
| `scripts/bridge-healthcheck.sh` | `make reload-bridge` |

`make up` never builds. `make rebuild` recreates all four containers, including
the bridge (90 s `start_period`, mail is down meanwhile).

## Gotchas

- **Only `cortex_bridge` and `vpn` carry `autoheal=true`.** An unhealthy
  `cortex` (stale `local/state/heartbeat`) is never auto-restarted.
- **`make clean` deletes the Bridge vault volume** (`cortex_cortex_bridge-data`)
  → interactive Bridge login again. Use `make down`.
- **`make lint` is red on a pre-existing backlog** (31 ruff errors, 2026-09-13)
  and never reaches mypy. Lint the files you touched.
- **`LIST`/`RUN`/`CAREER REPORT` are not sender-gated.** `COMMAND_ALLOWED_DOMAINS`
  is in `.env` but read by no code; only `APPROVE`/`DENY` check
  `approval_allowlist` in `local/config.json`.
- **A VPN failure is loud now.** Scrapers raise `VPNUnavailableError`; read the
  `vpn_switch` records in `local/logs/activity-<date>.jsonl` first. The op
  `vpn_rotated` and `VPN_QUARANTINE_PATH` no longer exist.
- **gluetun's ProtonVPN server list is frozen at 2025-11-18** until
  `PROTON_API_EMAIL` / `PROTON_API_PASSWORD` are set in `.env` (still empty).
- **Command mail must land in `Labels/Command`** (`COMMANDS_FOLDER` in `.env`);
  the listener reads no other folder and resumes from the UID in
  `local/state/command_last_uid_Labels_Command.txt`.
- **`docker compose restart` replays the original container spec.** After a
  compose change use `up -d --force-recreate`.

## Do not touch

- `VPN_ROTATE_TIMEOUT` (120), `VERIFY_SETTLE_SECONDS` (2.0),
  `VERIFY_DEADLINE_SECONDS` (45.0), `VERIFY_MAX_PROBES` (6) — calibrated on a
  1,518-exit survey; the reasoning is in `docs/decisions.md`.
- `scripts/bridge-healthcheck.sh` — the IMAP `LOGIN` check is what ended a
  42-hour silent mail outage (2026-08-26 → 28). Do not revert to a TCP connect.
- `scripts/vpn_survey_cron.sh` — stays commented out of the crontab; it
  restarted the tunnel ~100×/night.
- `local/` — runtime state, the only writable bind mount, gitignored.
- `docs/superpowers/` — specs, plans and handoffs; ticket inputs, not docs.

## Documentation

- [docs/architecture.md](docs/architecture.md) — containers, `service/` and `modules/` tables, flows, networks
- [docs/development.md](docs/development.md) — both test runners, local loop, restart-vs-rebuild, traps
- [docs/operations.md](docs/operations.md) — healthchecks, logs, the outage playbooks, Bridge recovery
- [docs/decisions.md](docs/decisions.md) — dated reasoning: VPN survey numbers, bridge healthcheck, event_watch
- [docs/config-schema.md](docs/config-schema.md) — every `local/config.json` field and trigger
- [docs/mcp-tools.md](docs/mcp-tools.md) — the 7 ProtonMail MCP tools (`service.mcp_server`) and the 2 `vpn` tools (`service.mcp_vpn`: fetch a page through gluetun), both `docker exec -i cortex-cortex-1 python -m …`
- [modules/README.md](modules/README.md) — Module Author's Guide; `modules/event_watch/README.md` for scrapers
- `docs/vpn-instability.md`, `docs/external-sonos-streamer.md` — historical notes (May / Aug 2026)
