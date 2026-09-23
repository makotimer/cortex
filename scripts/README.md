# scripts/

Helper scripts for setup, container management, and Proton Mail inspection.

---

## `first_run.sh`

One-time bootstrap. Copies example files into place if they don't exist yet.

```bash
./scripts/first_run.sh
```

- `local/config.example.json` → `local/config.json` (if missing)
- `.env.example` → `.env` (if missing)
- Touches `local/command_history` so Docker bind-mount creates a file, not a directory

Called automatically by `make bootstrap` (which is part of `make setup`).

---

## `reload.sh`

Rebuilds and recreates containers without a full `down`/`up` cycle.

```bash
./scripts/reload.sh              # rebuild + restart cortex only
./scripts/reload.sh --bridge     # rebuild + restart cortex_bridge then cortex
./scripts/reload.sh --dry-run    # print commands without running them
./scripts/reload.sh --help       # full usage
```

Called by `make reload` and `make reload-bridge`.

---

## `pytest.sh`

Runs the pytest suite inside the running cortex container.

```bash
./scripts/pytest.sh              # standard suite (skips @pytest.mark.live tests)
./scripts/pytest.sh --live       # include live tests (hit real network/email)
./scripts/pytest.sh -k sonos     # pass any pytest args through
```

Called by `make test` and `make live-tests`.

---

## `career_check.py`

Summarizes new career postings from a local SQLite database.

```bash
python scripts/career_check.py
```

Note: the module docstring refers to `summarize_new_postings.py` — that was the original filename before it was renamed.

Called by `make career-report`.

---

## `print_latest_postings.py`

Prints the most recent postings from any `.db` files found under `local/`.

```bash
python scripts/print_latest_postings.py
```

Useful for a quick sanity-check of what the career scraper collected.

---

## `proton_query.py`

Standalone Proton Mail (Bridge) query tool. Connects to `cortex_bridge` via IMAP.

```bash
# Must be run inside the container (needs network access to cortex_bridge)
docker compose exec cortex python scripts/proton_query.py list-folders
docker compose exec cortex python scripts/proton_query.py subjects [folder] [options]
```

### `list-folders`

Lists all mailboxes/labels visible through the bridge.

```bash
python scripts/proton_query.py list-folders
```

### `subjects [folder]`

Prints email subjects from a folder (default: `INBOX`).

```bash
python scripts/proton_query.py subjects
python scripts/proton_query.py subjects "Labels/Command"
python scripts/proton_query.py subjects INBOX --limit 20 --unseen
python scripts/proton_query.py subjects INBOX --search "SINCE 1-May-2026"
```

| Option | Description |
|---|---|
| `folder` | Mailbox path (default: `INBOX`) |
| `--limit N` | Max messages to show |
| `--unseen` | Only unseen messages |
| `--search TEXT` | Raw IMAP search criteria |

---

## `event_probe.py`

Fetch a candidate calendar page through gluetun and print what it actually is.
The fetch lives in `modules/_shared/probe.py` and is also exposed to Claude Code
as the `vpn` MCP (`service/mcp_vpn.py`, see `docs/mcp-tools.md`); this is the CLI.
Use this when standing up a new `event_watch` source: a host-side curl from
this box is not the path a scraper will take, and news-site calendars are
often a widget behind a bot wall.

```bash
# Inside the cortex container (needs the vpn service)
docker compose run --rm cortex python scripts/event_probe.py \
  'https://www.fox44news.com/calendar/'

# Keep the current exit (do this during career_watch's daytime window)
docker compose run --rm cortex python scripts/event_probe.py \
  --no-rotate \
  'https://www.fox44news.com/calendar/'
```

Writes `local/state/event_probe/<host>-<utc>.html` and a sibling `.meta.json`
(status, headers, fingerprint, vpn switch). A 403 is saved, not treated as a
dead tunnel — the exit is verified against a neutral URL so PerimeterX cannot
burn three rotations and leave us with no body.

| Option | Description |
|---|---|
| `--out DIR` | Capture directory (default `/app/local/state/event_probe`) |
| `--no-proxy` | Skip the VPN; fetch from the container's own address |
| `--no-rotate` | Keep the current exit if it already carries traffic |
| `--verify-url URL` | Neutral reachability check (default: Cloudflare trace) |
| `--timeout SEC` | Per-request timeout (default 20) |

---

## `run-jobs.sh` / `run_jobs.py`

Runs **configured jobs** from `local/config.json` now, by id or glob, the way
the scheduler would: the same spec parsing and the same `run_module_once`
call (kwargs, `send_email`, email fields, timeout), with
`trigger_type="manual"`. `service.cli run` is the module-level tool and makes
you retype kwargs; this is the job-level one.

```bash
./scripts/run-jobs.sh --list 'event-watch-*'      # what would run, in order
./scripts/run-jobs.sh 'event-watch-*'             # the whole Tue/Fri batch
./scripts/run-jobs.sh event-watch-destbryan       # one scraper
make run-jobs JOBS="'event-watch-*'" ARGS=--list
```

- Parallel by default, like the scheduler's 10-thread pool: every job runs
  at once (`--parallel N`, default 10) except the **latest time group**,
  which starts after the rest finish — so `event-watch-*` ends with
  `event-watch-done` and its `watch.complete` lands after every scraper has
  published. `--parallel 1` runs serially in schedule order. That message is what makes the
  discoverbcs worker run new-and-notable, fact-review and — **on Tuesday
  and Friday only** — the pick lineups. On any other day, re-run the
  pickers with `websites/discoverbcs/scripts/propose-picks.sh`.
- Disabled jobs print `SKIP` unless `--include-disabled`. `--no-email`
  forces `send_email` off. A failure is printed as `FAIL` and the rest still
  run; exit code 1 if any failed.
- The Python file is piped from the host into the **running** container, so
  it works without a rebuild (`service/` is baked into the image). Do not
  run it while `make reload` / `rebuild` recreates cortex — that kills it.
- In parallel the batch takes about as long as its slowest one or two jobs (~9–13 min:
  `event-watch-tamu`, and `event-watch-destbryan` via the VPN); most others
  take seconds. Serially it is far longer. Detach it with `setsid` so it
  outlives your shell (a plain `&` tied to a session died silently once):
  `setsid nohup ./scripts/run-jobs.sh 'event-watch-*' > /tmp/watch-run.log 2>&1 < /dev/null &`
