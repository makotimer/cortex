# Decisions

Each entry: the claim, the evidence, the date. Evidence is a commit hash in
this repo, a file, or a measurement recorded in the previous `CLAUDE.md` and
re-checked against the code on 2026-09-13.

## Runtime shape

- **Code is baked into the image; only `local/` is bind-mounted.**
  `Dockerfile` `COPY`s the packages and `pip install -e .`; the layer order
  (copy `pyproject.toml`, install, then copy source) exists so a code change
  does not re-resolve dependencies. The cost is that every `.py` edit needs
  `make reload`. Unchanged since the first commit; re-verified 2026-09-13.
- **No cron, no database.** Every job is an entry in `local/config.json`
  under APScheduler with an in-memory jobstore; cross-process coordination is
  by files in `local/state/` (skip tokens, last-UID file, SQLite for
  `career_watch`). `service/scheduler.py`, `service/skip_tokens.py`, 2026-09-13.
- **`cortex` does not carry the `autoheal=true` label; the bridge and vpn do.**
  Commit `a24f836` (2026-06-30) added the label to the bridge because autoheal
  only acts on labelled containers and the bridge had none. `cortex` was left
  out, so a stalled scheduler is flagged (heartbeat healthcheck) but not
  restarted automatically. Still true in `docker-compose.yaml`, 2026-09-13.
- **All mail goes through a ProtonMail Bridge sidecar; credentials are the
  Bridge-local pair in `.env`, never the Proton password.** The only Docker
  secret declared is `eventbus_redis_password`, sourced from the eventbus
  stack (`docker-compose.yaml`, 2026-09-13).

## Bridge health is a real IMAP LOGIN, not a TCP connect

**Commit `8af7970`, 2026-09-04.** Bridge keeps `1143` listening with zero
users loaded. From 2026-08-26 to 08-28 a Proton API outage (`503` on
`auth/v4/refresh`) hit during an autoheal restart; Bridge logged
`Failed to load connected user`, never retried, and the old
`socat - TCP:127.0.0.1:1143` check stayed green for 42 hours while every SMTP
AUTH returned `454 4.7.0`. Scheduler mail was lost outright and eventbus
`email.send` messages dead-lettered. `scripts/bridge-healthcheck.sh` now
requires `a1 OK` from a LOGIN. Tradeoff accepted: `retries: 4` × `interval: 15s`
means ~60 s of failure before autoheal restarts the bridge, and during a long
Proton outage it will restart about every 150 s until the API recovers — a
restart is exactly what fixes the failure, so that churn is wanted.

## VPN: switch until usable, fail loudly

- **A silent empty scrape is the failure everything here is designed
  against.** Before 2026-08-12 the engine checked VPN health *before*
  rotating and never re-checked; over 626 runs (2026-06-01 → 08-12) rotation
  failed on 22 % and 54 % of those scraped zero from every source while
  logging `ok: true` / `no_new`. Commit `01ceb0f` (2026-08-12) replaced that
  with `vpn_client.switch_until_usable()`, which re-reads the IP *and*
  fetches a real URL through the proxy after every restart, raising
  `VPNUnavailableError` when no exit works.
- **`rotate()` was deleted on 2026-08-14 (`f007cdd`).** Its "the IP must
  change" success test manufactured false failures. The activity op is
  `vpn_switch`; `vpn_rotated` no longer exists.
- **The exit quarantine was deleted on 2026-08-14 (`1fac49e`), together with
  `VPN_QUARANTINE_PATH`.** It guarded failure modes measured at zero (0/633
  verify failures on live exits, 0/148 exits bad every time seen) and the one
  time it fired it condemned three healthy servers inside 66 s. A failed exit
  is switched away from, not remembered.
- **`VPN_ROTATE_TIMEOUT` is 120 s — do not raise it.** Survey of 1,518 exits
  (2026-08-12 → 08-16): the share of restarts that never produce an IP is
  12.5 % at 90 s, 7.2 % at 120 s, 3.7 % at 180 s, but with the 3-attempt retry
  loop the *run*-level failure rate is 0.050 % / 0.038 % / 0.018 % with
  worst-case stalls of 4.5 / 6 / 9 min. 120 → 180 buys 0.02 pp for three extra
  minutes of dead stall. `DEFAULT_ROTATE_TIMEOUT = 120.0` in
  `modules/_shared/vpn_client.py`, set by `81da05b` (2026-08-13).
- **The verify budget is calibrated: `VERIFY_SETTLE_SECONDS = 2.0`,
  `VERIFY_DEADLINE_SECONDS = 45.0`, `VERIFY_MAX_PROBES = 6`.** Probe ladder
  over 1,420 exits: 97.3 % carry traffic on the first probe, 99.65 % by +8 s,
  then nothing until +30 s. There is no grey zone to widen for. Constants
  present in `vpn_client.py` on 2026-09-13.
- **A verify URL must be a real target of the scraped class.** Until
  `f3426ab` (2026-08-16) `career_watch` verified against
  `cloudflare.com/cdn-cgi/trace`, which proves the tunnel carries traffic and
  nothing about job boards. Now `DEFAULT_VERIFY_URL` is Lever's postings API
  (`modules/career_watch/lib/engine.py:45`), overridable with
  `CAREER_WATCH_VERIFY_URL`; each `event_watch` scraper sets its own
  `verify_url`. Hardening, not a fix for an observed outage — Lever
  hard-blocked 0 of 989 surveyed exits.
- **Tor-over-VPN exits are tolerated, not filtered.** ~3.3 % of exits egress
  from countries outside `SERVER_COUNTRIES` via Tor relays (DFRI `171.25.193.x`,
  Zwiebelfreunde `185.220.101.x`); they produced 7 of the 9 hard 403s in the
  survey. gluetun's ProtonVPN records carry no Tor flag and it has no
  exclusion filter, so the cost (≈0.5 % of switches wasted, auto-recovered by
  `switch_until_usable`) is cheaper than a 283-name allowlist that goes stale.
  Recorded 2026-08-16.
- **The proxy setting is authoritative over the environment (`6f60798`,
  2026-08-12).** `HttpClient` used to re-read `EVENT_WATCH_PROXY_URL` itself,
  so `proxy_url=None` was silently overridden — and since the VPN gate only
  runs when the setting is present, traffic could bypass fail-closed.
  `HttpClient(proxy_env=None)` disables the fallback.
- **Exit rotation is its own job (`vpn_cycle`), 15 min before each scrape,
  with `DEFAULT_ATTEMPTS = 5`.** Nothing waits on it, one rotation costs
  ~28 s expected, and each extra attempt dodges the 6.36 % of restarts that
  produce no IP (`modules/vpn_cycle/main.py`, schedule in `local/config.json`
  2026-09-13).
- **The overnight exit survey is stopped (`f3426ab`, 2026-08-16).** Its
  crontab line is commented out (backup
  `local/state/crontab.bak.before-vpn-survey-stop-20260816`). It restarted the
  tunnel ~100×/night indefinitely because it stopped on the 03:20 clock, never
  on its 1,500-record limit. Everything it measured is in the bullets above;
  raw records stay in `local/state/vpn_survey/survey-*.jsonl`.
- **gluetun's ProtonVPN server list is frozen at 2025-11-18 until
  `PROTON_API_EMAIL` / `PROTON_API_PASSWORD` are set in `.env`.** Found
  2026-08-16: `UPDATER_PERIOD=6h` fires but gluetun skips ProtonVPN without
  account credentials (`credentials missing: email is empty`). Compose passes
  them as `UPDATER_PROTONVPN_EMAIL` / `_PASSWORD` and is inert while blank;
  they were still absent from `.env` on 2026-09-13.

## event_watch

- **All kinds run Tue/Fri, America/Chicago, 03:40–10:35, one kind per job,
  15 min apart, with `vpn-cycle-events` at 03:25 ahead of the grid**
  (`0338787`, 2026-08-30; `local/config.json` 2026-09-15 has 29 such jobs,
  `rei` disabled).
- **De-duplication is the scraper's job, not the display's.** TAMU's
  LiveWhale feed emits one multi-day package as several series ids with
  windows shifted a day apart plus an all-day row for every day inside each
  window; `tamu.collapse_intra_source` folds all three shapes, grouping on
  `_normalized_title` (which strips a trailing `at <venue>`) — `55251b5`,
  2026-09-05.
- **Unplaceable TAMU addresses are dropped quietly** — country names,
  `multiple locations`, other-county offices are not geocoded; only
  local-looking venues are, and a `no_match` on those still emails with title
  and location (`0338787`, 2026-08-30).
- **BCS Chamber city-less streets resolve through a ladder** ending in
  `address_kit` (`4140c93` … `cb7c1be`, all 2026-09-05), which is why
  `../websites/address-kit` is mounted into the container.

## IMAP commands

- **Commands are read from the reply body, not the subject** (`9744364`,
  2026-06-11). A reply's subject is rewritten (`Re: …`, or Proton's filter
  relabels it `COMMAND`) and the quoted draft below the reply also contains
  the `APPROVE`/`DENY` instruction lines, which must never match. So
  `handlers.py` isolates the user's own text above the quoted original and
  scans that; the subject is the fallback only when the body names no
  command, which keeps the "edit the subject" workflow working. The parser
  also strips `Re:`/`Fwd:` prefixes (`parser.py:40`).
- **Only `APPROVE`/`DENY` are sender-gated, by `approval_allowlist` in
  `local/config.json`.** `LIST` / `RUN` / `CAREER REPORT` rely on the Proton
  sieve that routes mail into `Labels/Command`. A domain gate on
  `COMMAND_ALLOWED_DOMAINS` is specified and filed
  (`docs/superpowers/handoffs/2026-08-30-imap-domain-gate-and-display-verse-command.md`)
  but not implemented as of 2026-09-13.

## bible_plan

- **No LLM.** `35a3d2d` (2026-06-07) swapped the OpenAI commentary for a link
  to `https://study.coviecraft.dev` plus a rotating prayer focus; `652739d`
  (2026-07-11) trimmed the body to the verses and the link. `OPENAI_API_KEY`
  therefore powers nothing scheduled.

## MCP server

- **`service/mcp_server.py` uses `mcp.server.MCPServer` (mcp 2.x), invoked
  by `docker exec`, never by `serve`** (`3eeafa9`, 2026-09-05). The port
  happened because mcp 2.x dropped `mcp.server.fastmcp` and the server died
  at import, which is why the tools silently never appeared in Claude Code.
