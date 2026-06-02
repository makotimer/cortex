# Design: `trigger-reading` CLI command

**Date:** 2026-06-02
**Status:** Approved (pending spec review)

## Problem

There is no easy way to fire the daily Bible reading on demand from a terminal *the
same way the email `RUN MODULE=…` command does* — i.e. sending the real reading to the
configured recipients **and** cancelling the upcoming scheduled fire so recipients do
not receive a duplicate.

The existing email path (`service/imap_commands/handlers.py::_handle_run`) achieves the
"skip the next run within 6h" behaviour by mutating the **live, in-memory** APScheduler
instance held by the running `serve` process. The scheduler uses
`MemoryJobStore` (`service/scheduler.py:113`), so a *separate* CLI process cannot reach
those jobs — there is no shared jobstore and no IPC. A new cross-process mechanism is
required.

Requirements (from brainstorming):
- Trigger **today's** reading (weekday vs weekend) from the CLI.
- Send the real reading email to the configured recipients (`BIBLE_PLAN_EMAILS`),
  exactly like the scheduled/email path.
- Cancel ("dedup") the upcoming scheduled fire if it is within 6 hours.
- Do **not** rely on the IMAP listener or a command-email round-trip.
- Bible-only scope (no generic "run any job" command).

## Background facts

- Both Bible jobs run the **same module with no distinguishing kwargs**
  (`modules.bible_plan`). The reading is chosen purely by
  `days_since(plan_start, today)` (`modules/bible_plan/main.py:35`). Weekday vs weekend
  changes only the *send time* (05:25 Mon–Thu, 06:55 Fri–Sun), never the content.
- Recipients come from `email_to_env: BIBLE_PLAN_EMAILS`, which
  `config_schema.load_config()` resolves into a real `email_to` list at load time
  (`service/config_schema.py:192`).
- Scheduler timezone is `America/Chicago` (config top-level `timezone`; `TZ` env agrees).
  The bible `daily_time` triggers carry no own timezone, so they inherit the scheduler tz.
- `daily_time` builds an `OrTrigger` of `CronTrigger`s; `trigger.get_next_fire_time(None,
  now)` yields the next scheduled fire.

## Approach

Chosen: **skip-token file** honored by the running scheduler. (Alternative considered:
switching to a persistent SQLAlchemy jobstore so the CLI could remove/re-add jobs like
the email path. Rejected — APScheduler 3.x does not reliably honor *external* jobstore
edits in an already-running scheduler, and it carries a large blast radius across every
job's storage and restart-recovery.)

### Invocation

```bash
docker compose exec -T cortex python -m service.cli trigger-reading [--date YYYY-MM-DD] [--no-dedup]
```

- `--date YYYY-MM-DD` — optional; mapped to the module's existing `for_date` kwarg, for
  re-sending a specific day's reading. Also used as "today" for weekday job selection.
- `--no-dedup` — optional; run + send but do **not** write a skip token.
- A `make trigger-reading` convenience target wraps the `docker compose exec` form.

### Component 1 — `service/skip_tokens.py` (new)

Small, pure, testable module shared by the CLI (writer) and scheduler (consumer).

- `write_skip_token(state_dir: Path, job_id: str, slot: datetime, grace_sec: int = 600) -> Path`
  - Writes `<state_dir>/skip/<safe-job-id>.json` containing
    `{"slot": slot.isoformat(), "expires": (slot + grace_sec).isoformat()}`.
  - Creates the `skip/` directory as needed. `job_id` is sanitized for the filename the
    same way the listener sanitizes mailbox names.
- `consume_skip_token(state_dir: Path, job_id: str, now: datetime) -> bool`
  - No token file → return `False`.
  - Token present and `now <= expires` → delete the file, return `True` (caller skips).
  - Token present and `now > expires` (stale) → delete the file, return `False`.
  - Unreadable/corrupt token → delete the file, return `False`.

`state_dir` is resolved the same way the scheduler resolves the heartbeat directory:
`Path(CONFIG_PATH).parent / "state"` (default `local/state`), which is the writable
bind-mount used by the listener and heartbeat.

### Component 2 — scheduler guard (`service/scheduler.py`)

At the top of `_job_wrapper` (currently `service/scheduler.py:498`), before running the
module:

```python
if skip_tokens.consume_skip_token(_state_dir(), spec.id, datetime.now(tz)):
    LOG.info("Job[%s] skipped: manual-trigger dedup token consumed", spec.id)
    return
```

`tz` is the scheduler timezone. The heartbeat job is added outside `_add_job`, so it is
never affected. The existing email-path dedup (remove + re-add) is left untouched; the
token check is additive and harmless to it.

### Component 3 — CLI command (`service/cli.py`)

`cmd_trigger_reading(args)`:

1. `cfg = load_config()`; resolve scheduler tz via `_resolve_timezone(cfg)`.
2. Determine target date: `--date` if given, else "today" in scheduler tz.
3. **Select the bible job**: among `cfg["jobs"]` with `module == "modules.bible_plan"`,
   pick the one whose `trigger.daily_time.day_of_week` covers the target date's weekday.
   - Zero matches → error and exit non-zero.
   - More than one match → error (ambiguous config) and exit non-zero.
4. Build kwargs: job's configured kwargs, plus `for_date` if `--date` was passed.
5. **Run + send**:
   ```python
   runner.run_module_once(
       module=job["module"], kwargs=final_kwargs,
       email_to=job.get("email_to") or None,
       cc=job.get("email_cc") or None,
       bcc=job.get("email_bcc") or None,
       subject=job.get("subject"),
       send_email=True, trigger_type="command",
       timeout_sec=job.get("timeout_sec"),
   )
   ```
6. **Dedup** (unless `--no-dedup`): build the job's trigger via `_make_job_spec` /
   `_build_trigger`, compute `next_fire = trigger.get_next_fire_time(None, now)`. If
   `next_fire` is within 6 hours of now, `write_skip_token(state_dir, job["id"],
   next_fire)`. Otherwise do nothing (no double-send risk; matches email-path semantics).
7. Print a concise summary (job id, run id, whether a skip token was written and for
   which slot).

A `weekday matches day_of_week spec` helper parses APScheduler-style `day_of_week`
strings (`"mon-thu"`, `"fri,sat,sun"`) into the set of weekday indices and tests
membership. This is pure and unit-tested.

## Data flow

```
CLI (separate process)                         serve process (scheduler)
─────────────────────                          ─────────────────────────
load_config → select today's bible job
runner.run_module_once(send_email=True) ──────► reading email to BIBLE_PLAN_EMAILS
compute next_fire; if ≤6h:
  write skip/<job-id>.json  ───────────────┐
                                           │  (shared local/state bind-mount)
                                           ▼
                          at next_fire: _job_wrapper
                            consume_skip_token() → True → skip + delete token
```

## Edge cases

- **Next fire > 6h away** → no token; scheduled run proceeds normally (no duplicate).
- **Stale token** (scheduled fire never happened, or clock skew) → self-cleans on next
  fire; never indefinitely suppresses future runs.
- **Container restart between trigger and slot** → token persists in the bind-mount and
  is still honored — strictly better than the email path's in-memory re-add.
- **`--no-dedup`** → send only, no token.
- **Weekday/weekend tokens** are keyed by job id and never collide.
- **`coalesce=True`** on the bible jobs means a single fire consumes a single token.

## Testing (TDD)

- `tests/test_skip_tokens.py`
  - write then consume within grace → `True`, file removed.
  - consume when absent → `False`.
  - consume after expiry → `False`, file removed.
  - corrupt token file → `False`, file removed.
- `tests/test_trigger_reading.py`
  - `day_of_week` parsing/membership: `"mon-thu"` covers Tue, not Fri; `"fri,sat,sun"`
    covers Sat, not Mon.
  - weekday selection picks the correct job id for representative dates.
  - within-6h decision writes a token; >6h does not (with `runner.run_module_once`
    mocked).
  - run is invoked with the resolved `email_to` and `trigger_type="command"`
    (mocked runner).

Network send is never exercised in tests; `runner.run_module_once` is mocked.

## Files touched

- `service/skip_tokens.py` — new (~30 lines).
- `service/scheduler.py` — import + one guard at the top of `_job_wrapper`; small
  `_state_dir()` helper (or reuse heartbeat path logic).
- `service/cli.py` — `cmd_trigger_reading` + argparse subparser + weekday/selection
  helpers.
- `tests/test_skip_tokens.py`, `tests/test_trigger_reading.py` — new.
- `Makefile` — `trigger-reading` target.

## Out of scope

- Generic "run any job by id" CLI command.
- Changing the email/listener path.
- Persisting scheduler jobs across restarts (jobstore change).
