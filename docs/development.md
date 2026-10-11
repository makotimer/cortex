# Development

## Tests

**Two runners exist and they test different copies of the code.** Both were
run on 2026-09-13:

| Command | Runs where | Tests what | Result 2026-09-13 |
|---|---|---|---|
| `./run_tests.sh` | host `.venv` | the working tree | `654 passed in 50.20s` |
| `make test` (`scripts/pytest.sh`) | `docker compose run --rm cortex pytest -q` | **the image's copy** of `service/`, `modules/`, `tests/` | `626 passed, 39 skipped in 50.41s` |

The counts differ because `run_tests.sh` passes `--ignore` for
`tests/career_live`, `tests/assorted_live` and `tests/manual_live_runs`, while
the container run collects them and skips each one. The container run also
scores whatever image is current — on 2026-09-13 that image was built
2026-09-05 against a tree whose HEAD was 2026-09-12 — so `make test` after a
host edit, without `make reload`, reports a stale pass count.

**`./run_tests.sh` is the ops-worker contract** (`websites/kits/ops-kit/opskit/fleet.py`
runs every catalog project's `run_tests.sh`). It rebuilds `.venv` when the
interpreter is missing or broken (it did so on 2026-09-13), reinstalls
`cortex[dev]` when `pyproject.toml` is newer than `.venv/.deps-installed`, and
prepends `eventbus-kit` and `address-kit` to `PYTHONPATH` from
`/srv/docker/eventbus/eventbus-kit` and `/srv/docker/websites/kits/address-kit`.
`./run_tests.sh -m smoke` runs the four-file hermetic subset
(`test_config_schema`, `test_logging_utils`, `test_skip_tokens`, `test_bible_plan`).

Live tests need `--live` or `RUN_LIVE_TESTS=1`:

```bash
make live-tests            # everything, in the container
make live-test bae         # one file matching *bae*.py under tests/career_live or tests/assorted_live
```

**`make lint` is red on a pre-existing backlog.** `ruff check .` reported
`Found 31 errors` on 2026-09-13 and stops the `&& mypy .` half from running.
Do not treat a red `make lint` as evidence about your change; run
`.venv/bin/ruff check <files you touched>`.

## Local loop

```bash
cd /srv/docker/cortex
make setup                              # .venv + local/config.json + .env from the examples (scripts/first_run.sh)
.venv/bin/ruff format . && .venv/bin/ruff check <paths>
./run_tests.sh                          # host, working tree, ~50 s
make reload && make test                # authoritative: rebuild the image, test the image
make logs-f                             # follow cortex-cortex-1
```

Run any CLI subcommand against the live stack:

```bash
docker compose run --rm cortex python -m service.cli list-jobs
docker compose run --rm cortex python -m service.cli validate-config
docker compose run --rm cortex python -m service.cli run bible_plan --no-email --print-html
make trigger-reading                    # today's reading now + skip token for the next scheduled fire
make event-probe URL='https://example.com/calendar/'   # fetch through the VPN; NO_ROTATE=1 keeps the exit
```

`SCHEDULER_PREVIEW=1` on `serve` prints the next `SCHEDULER_PREVIEW_COUNT`
(default 6) fires per job (`service/scheduler.py:249`).

Adding a job module: copy `modules/example_daily/`, follow `modules/README.md`
(folder name = module name, `__init__.py` re-exports `run`, helpers in `lib/`),
add a job to `local/config.json`. The schema is in `docs/config-schema.md`.

## Deploy: restart vs rebuild

**Code is baked into the image; a `.py` edit needs `make reload`, not
`restart`.** `Dockerfile` `COPY`s `modules/`, `service/`, `scripts/`, `tests/`
and `pip install -e .`; `docker-compose.yaml` bind-mounts only `./local`,
`./local/command_history`, `../eventbus/eventbus-kit:ro` and
`../websites/kits/address-kit:ro`. Verified 2026-09-13.

| You changed | Do |
|---|---|
| anything under `service/`, `modules/`, `scripts/`, `tests/` | `make reload` (= `scripts/reload.sh`: `build --pull` + `up -d --force-recreate --build cortex`) |
| `pyproject.toml` or `Dockerfile` | `make reload` too — same path; the deps layer re-runs because `pyproject.toml` is copied before `pip install` |
| `docker-compose.yaml` or `.env` | `docker compose up -d --force-recreate <service>` — `restart` replays the old spec |
| `local/config.json` | `make reload` or `docker compose restart cortex`; config is read once at `serve` start (`service/scheduler.py`) |
| `../eventbus/eventbus-kit` or `../websites/kits/address-kit` | `docker compose restart cortex` — mounted, but only re-imported on process start |
| `scripts/bridge-healthcheck.sh` | `make reload-bridge` (also recreates `cortex_bridge`) |

**`make up` does not build** (`docker compose up -d`). `make rebuild` builds
every service with `--pull` and force-recreates all four containers, including
the bridge — which drops IMAP/SMTP for its 90 s `start_period`.

## Conventions

- **One directory per job module, `run(**kwargs) -> str | tuple[str, dict] | None`**
  (`modules/README.md`). `None` = no email, a string = HTML body,
  `(html, {"subject": …})` = body plus metadata.
- **Job definitions live only in `local/config.json`; there is no cron.**
  `email_to_env` / `email_cc_env` / `email_bcc_env` name an environment variable
  holding a comma-separated list (`service/config_schema.py`).
- **Ruff, line length 115, `py312`, rule set `F E W I UP B SIM PERF RUF C4 PTH DTZ S ARG N`**
  with per-directory relaxations for `scripts/`, `modules/`, `tests/`
  (`pyproject.toml`). `mypy` is `strict = false` with `check_untyped_defs`; the
  `bible_plan`, `_shared`, `sonos`, `scripts` and `tests` trees are
  `ignore_errors = true`.
- **Structured logs, not print.** Modules use `lib/logging_bridge.py` →
  `service.logging_utils.write_activity_log`; records carry `component` and
  `op` keys (e.g. `career_watch.engine` / `vpn_switch`).
- **Dependencies are pinned exactly** in `pyproject.toml` and bumped by the
  ops-worker (`chore(deps): …` commits, four between 2026-09-09 and 2026-09-12).
- **Specs, plans and handoffs go under `docs/superpowers/`** and are filed via
  `/srv/docker/tickets/ticket file` (see `/srv/docker/CLAUDE.md`).

## Traps

- **`docker exec -it cortex_bridge …` does not work.** `cortex_bridge` is the
  compose *service* (and DNS name on `mailnet`); the container is
  `cortex-cortex_bridge-1`. The Bridge CLI inside it is `/usr/bin/bridge --cli`,
  not `protonmail-bridge` (`/app/entrypoint.sh`, checked 2026-09-13).
- **`COMMAND_ALLOWED_DOMAINS` is set in `.env` and `.env.example` but read
  nowhere** — `grep` over `service/` and `modules/` finds no reader
  (2026-09-13). `LIST`, `RUN` and `CAREER REPORT` are accepted from any sender
  whose mail reaches the `Command` folder. Only `APPROVE`/`DENY` are gated, by
  `approval_allowlist` in `local/config.json`
  (`service/imap_commands/handlers.py:176`). The domain gate is a filed
  handoff, `docs/superpowers/handoffs/2026-08-30-imap-domain-gate-and-display-verse-command.md`,
  not shipped.
- **The host `.venv` cannot import `eventbus` unless `PYTHONPATH` carries the
  kit.** `run_tests.sh` sets it; a bare `.venv/bin/pytest` does not.
- **`make test` scores the image, not your edit.** See *Tests*.
- **`.venv` goes stale/broken between sessions** — it was rebuilt from scratch
  by `run_tests.sh` on 2026-09-13. Let the script do
  it; do not `pip install` into it by hand.
- **`modules/_shared/html.py` is a 0-byte file** (2026-09-13). Nothing to
  import there; `service/runner.py:_html_email_fallback` is the live email template.
- **`rotate()` and the exit quarantine are gone** (2026-08-14). The VPN
  activity op is `vpn_switch`, not `vpn_rotated`; `VPN_QUARANTINE_PATH` is
  dead. See `decisions.md` for the survey numbers behind
  `VPN_ROTATE_TIMEOUT = 120` and the verify budget.
- **`grep --include=` on this host silently skips nested directories** (ugrep
  7.8.4). Use `find … -exec grep -Hn … {} \;` — `/srv/docker/DOCS-CONVENTION.md`.
