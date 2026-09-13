# Operations

Everything below was checked against the running stack on 2026-09-13
(`docker ps`, `docker inspect`, `docker compose logs`, files under `local/`).

## Start and stop

```bash
cd /srv/docker/eventbus && docker compose up -d     # once — cortex's compose needs the external `eventbus` network and secret
cd /srv/docker/cortex
make up            # docker compose up -d   (no build, no pull)
make down          # docker compose down
make reload        # rebuild + force-recreate cortex only (scripts/reload.sh)
make reload-bridge # same, for cortex_bridge then cortex
make rebuild       # build --pull every service, force-recreate all four
make clean         # docker compose down -v --remove-orphans — DELETES the Bridge vault volume
```

**Start order is enforced by compose, not by you.** `cortex` has
`depends_on: cortex_bridge: condition: service_healthy`, and the bridge's
healthcheck is a real IMAP `LOGIN`, so `cortex` waits until the Bridge has
actually loaded the account (`docker-compose.yaml`, 2026-09-13). The bridge has
`start_period: 90s`; expect that long before `cortex` starts after a cold boot.

**`make clean` destroys `cortex_cortex_bridge-data`**, the only named volume,
which holds the Bridge account and tokens. After it the one-time Bridge login
must be repeated (see *Recovery*).

**The `vpn` container is independent.** Nothing `depends_on` it; if it is
down, `career_watch`, `event_watch` and `vpn_cycle` runs fail with
`VPNUnavailableError` and the rest of the stack carries on.

## Health

Three healthchecks, all green on 2026-09-13
(`docker inspect <c> --format '{{.State.Health.Status}}'`):

| Container | Check | Cadence |
|---|---|---|
| `cortex-cortex-1` | `find /app/local/state/heartbeat -mmin -2` — the scheduler touches that file every 60 s | interval 60 s, start 90 s, 3 retries (`Dockerfile`) |
| `cortex-cortex_bridge-1` | `scripts/bridge-healthcheck.sh`: IMAP `LOGIN` on `127.0.0.1:1143` with `HEALTH_IMAP_USER`/`HEALTH_IMAP_PASS` (= `BRIDGE_USERNAME`/`BRIDGE_PASSWORD` from `.env`), must see `a1 OK` | interval 15 s, timeout 10 s, 4 retries, start 90 s |
| `cortex-vpn-1` | `wget http://127.0.0.1:8000/v1/publicip/ip` must return a numeric `public_ip` | interval 60 s, 3 retries, start 30 s |

**Only the bridge and vpn are auto-restarted on red.** `autoheal` acts on the
`autoheal=true` label; `cortex` does not carry it. A `cortex` that goes
unhealthy stays unhealthy until someone runs `make reload` or
`docker compose restart cortex`.

**The nightly fleet report is the only scheduled external check of cortex.**
`/srv/docker/websites/ops/scripts/nightly_fleet.sh` (cron `0 2 * * *`) runs
`docker exec cortex-cortex-1 python -m modules.health_check --json --date yesterday`
and injects the JSON as `OPS_NIGHTLY_CORTEX_HEALTH`; an empty result renders as
"cortex did not report", not as healthy (`websites/ops/app/nightly.py:355`).
`health_check` expects exactly `DEFAULT_SCRAPES_EXPECTED = 10` `career_watch`
runs on a working day (`modules/health_check/main.py`).

Quick manual probes:

```bash
docker inspect cortex-cortex_bridge-1 --format '{{json .State.Health}}' | python3 -m json.tool
docker exec cortex-vpn-1 wget -qO- http://127.0.0.1:8000/v1/publicip/ip      # current exit
docker compose run --rm cortex python -m service.cli validate-config
docker exec cortex-cortex-1 python -m modules.health_check --json --date yesterday
```

## Logs

**Two log streams; the JSONL one is the one that matters.**

- **Container stdout** — `make logs-f` / `docker compose logs -f cortex`. At
  INFO this is dominated by APScheduler's per-minute heartbeat lines; `LOG_LEVEL`
  (default `INFO`, not set in `.env` on 2026-09-13) controls it (`service/cli.py:64`).
- **Structured JSONL under `local/logs/`** (host path
  `/srv/docker/cortex/local/logs/`, `LOG_DIR` in the container):
  `activity-YYYY-MM-DD.jsonl` every run, `error-YYYY-MM-DD.jsonl` only on
  days with errors (354 files on 2026-09-13, error files for 5 of the last 13
  days). Rotation is by date; `ACTIVITY_LOG_MAX_BYTES` (default `0`) adds size
  rotation. Keys named `password`, `token`, `secret`, `smtp_`, `cookie`, … are
  redacted (`service/logging_utils.py`).
- `local/logs/reload.log` — appended by `scripts/reload.sh` on every `make reload`.
- `vpn` and `cortex_bridge` write only to container stdout
  (`docker compose logs vpn`, `docker compose logs cortex_bridge`); all four
  services use json-file logging capped at 3 × 10 MB.

Records carry `component` and `op`. From `activity-2026-09-12.jsonl`, a normal
Saturday: 10 × `career_watch.main/start`, 10 × `career_watch.engine/vpn_switch`,
10 × `summary`, 10 × `no_new`, 10 × `vpn_cycle/vpn_cycle`. Ops worth grepping:

| `op` | Meaning |
|---|---|
| `vpn_switch` | one exit switch; `ok`, `ip`, `changed`, `attempts`, `seconds`, `tried[]`, `restarts[]` |
| `vpn_health_fail` | control API unreachable / no IP — the run bails before scraping |
| `summary` / `no_new` / `rendered` | a `career_watch` run finished (with or without new postings) |
| `rejected_records` | `event_watch` dropped events (e.g. `"missing city"`), with the ids |

```bash
grep -h '"op":"vpn_switch"' local/logs/activity-2026-09-1*.jsonl | python3 -c 'import sys,json;[print(r["ts"],r.get("ok"),r.get("attempts"),r.get("seconds")) for r in map(json.loads,sys.stdin)]'
```

## Diagnosing failures

**No mail is going out, everything looks healthy.** This is the 2026-08-26 →
08-28 shape: Proton's API 503'd, autoheal restarted the bridge mid-outage,
Bridge loaded zero users and never retried, and the *old* TCP-connect
healthcheck stayed green for 42 h while every SMTP AUTH returned
`454 4.7.0 invalid username or password` and every IMAP login `no such user`.
The IMAP-LOGIN healthcheck (commit `8af7970`) now turns the bridge red within
~60 s of that state so autoheal restarts it. If it recurs anyway:

```bash
docker inspect cortex-cortex_bridge-1 --format '{{json .State.Health}}' | python3 -m json.tool
docker compose logs --tail=200 cortex_bridge | grep -i "failed to load\|503\|refresh"
cd /srv/docker/cortex && docker compose exec -T cortex python -c "
import os, smtplib
s = smtplib.SMTP(os.getenv('BRIDGE_HOST'), int(os.getenv('BRIDGE_SMTP_PORT')), timeout=15)
s.ehlo(); s.login(os.getenv('BRIDGE_USERNAME'), os.getenv('BRIDGE_PASSWORD')); print('SMTP AUTH OK'); s.quit()"
```

**Scrapers returning nothing / `VPNUnavailableError`.** Read the day's
`vpn_switch` records first. A `restarts[]` entry landing exactly on
`VPN_ROTATE_TIMEOUT` (120) is a restart that never produced an IP. Then:

```bash
docker compose logs --tail=100 vpn | grep -i "timeout\|healthcheck\|updating Protonvpn"
docker exec cortex-vpn-1 wget -qO- http://127.0.0.1:8000/v1/publicip/ip
make event-probe URL='https://www.cloudflare.com/cdn-cgi/trace' NO_ROTATE=1
```

Continuous `i/o timeout` healthcheck failures cycling through many servers is
the stale-peer-key signature: gluetun's ProtonVPN server list is its baked-in
snapshot (`"timestamp": 1763472933` = 2025-11-18 in `/gluetun/servers.json`)
because `UPDATER_PROTONVPN_EMAIL` / `_PASSWORD` are empty — `PROTON_API_EMAIL`
and `PROTON_API_PASSWORD` were still absent from `.env` on 2026-09-13.
`docker compose logs vpn | grep -i "updating Protonvpn"` prints nothing until
they are set.

**A command email got no reply.** `imap_listener` only reads the folder named
by `COMMANDS_FOLDER` (`Labels/Command` in `.env`), resumes from the UID in
`local/state/command_last_uid_Labels_Command.txt`, and replies to the envelope
sender. `APPROVE`/`DENY` from an address not in `approval_allowlist`
(`local/config.json`, currently one address) get a "Not authorized" reply and
a WARNING in the container log (`service/imap_commands/handlers.py:176`).

**`make up` fails before any container starts** with a compose error about the
`eventbus` network or `eventbus_redis_password` secret: the eventbus stack has
never been brought up. `cd /srv/docker/eventbus && docker compose up -d`.

**`cortex` unhealthy, process still running.** The scheduler thread stalled
(heartbeat older than 2 min). `docker compose logs --tail=200 cortex`, then
`docker compose restart cortex` — nothing restarts it for you.

**A restarted container crash-loops on a missing module or secret.** The
compose spec changed (new mount, renamed secret) after the container was
created; `restart` replays the *original* spec. Use
`docker compose up -d --force-recreate <service>`.

## Recovery

**Bridge account lost (fresh volume, `make clean`, or a re-login demanded).**
The Bridge vault persists in the `cortex_cortex_bridge-data` volume at `/root`;
`docker compose restart cortex_bridge` after a Proton outage reloads the account
without a new login (2026-08-28). Only when the volume is gone is the
interactive login needed. The binary is `/usr/bin/bridge` and the container is
`cortex-cortex_bridge-1` (not `cortex_bridge`), so:

```bash
docker compose up -d cortex_bridge
docker exec -it cortex-cortex_bridge-1 bridge --cli
#   inside: `login` (Proton account, 2FA), then `info` for the Bridge-local
#   username/password → BRIDGE_USERNAME / BRIDGE_PASSWORD in .env
#   (the `login`/`info` steps are carried over from the previous README and were
#   not re-run this session; the binary and container name were)
make reload-bridge
```

**Dead-lettered outbound mail.** `email.send.dead` (19 entries on 2026-09-13) keeps the full
envelope in their `json` field; a replay is an `XADD` of that field back onto
`email.send`. Originals stay in the dead stream as the audit trail — dedupe
before replaying. The `{"ping": 1}` entry in that stream is historical, not a
failed delivery.

**Missed a scheduled Bible reading.** `make trigger-reading` sends today's
reading now and writes a skip token so the next fire within 6 h is suppressed;
`ARGS='--no-dedup'` sends without the token, `ARGS='--date YYYY-MM-DD'` targets
another day (`service/cli.py`, `service/skip_tokens.py`).

**Re-run any job by hand.**
`docker compose run --rm cortex python -m service.cli run <module> [--no-email]`
or email `RUN MODULE=<job-id>` to the `Command` folder; `LIST` in the same
folder replies with every job id.

**`.venv` broken on the host.** `./run_tests.sh` recreates it when
`.venv/bin/python -c ''` fails; nothing else needs doing.
