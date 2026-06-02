# trigger-reading CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `trigger-reading` CLI command that sends today's Bible reading to the configured recipients and suppresses the upcoming scheduled fire (within 6h) via a cross-process skip-token honored by the running scheduler.

**Architecture:** A new pure module `service/skip_tokens.py` writes/consumes JSON skip-token files under the writable `local/state` bind-mount. The scheduler's `_job_wrapper` consumes a matching token at fire time and skips the run. A new `cmd_trigger_reading` in `service/cli.py` selects today's bible job by weekday, runs it via `runner.run_module_once` with config recipients, and writes a skip token when the next fire is within 6h.

**Tech Stack:** Python 3.12, APScheduler 3.x, pytest, argparse. Spec: `docs/superpowers/specs/2026-06-02-trigger-reading-cli-design.md`.

---

## File Structure

- **Create** `service/skip_tokens.py` — write/consume skip-token files. Pure, no deps on scheduler/cli.
- **Modify** `service/scheduler.py` — add `_state_dir()` helper and a skip-token guard at the top of `_job_wrapper`.
- **Modify** `service/cli.py` — add `_day_of_week_indices()`, `_weekday_matches()`, `_select_bible_job()`, `cmd_trigger_reading()`, and the `trigger-reading` subparser.
- **Create** `tests/test_skip_tokens.py` — token lifecycle tests.
- **Create** `tests/test_trigger_reading.py` — weekday selection + within-6h decision (runner mocked).
- **Modify** `Makefile` — `trigger-reading` target.

---

## Task 1: skip_tokens module — write + consume within grace

**Files:**
- Create: `service/skip_tokens.py`
- Test: `tests/test_skip_tokens.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_skip_tokens.py
import datetime as dt
from pathlib import Path

from service import skip_tokens


def _now(h=5, m=25):
    return dt.datetime(2026, 6, 2, h, m, tzinfo=dt.timezone.utc)


def test_write_then_consume_within_grace_returns_true_and_removes(tmp_path: Path):
    slot = _now()
    skip_tokens.write_skip_token(tmp_path, "bible-plan-mon-thu-0455", slot, grace_sec=600)
    token_file = tmp_path / "skip" / "bible-plan-mon-thu-0455.json"
    assert token_file.exists()

    # Fire at exactly the slot time → within grace
    skipped = skip_tokens.consume_skip_token(tmp_path, "bible-plan-mon-thu-0455", slot)
    assert skipped is True
    assert not token_file.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `make test` is container-based; for fast local iteration use
`python -m pytest tests/test_skip_tokens.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'service.skip_tokens'`.

- [ ] **Step 3: Write minimal implementation**

```python
# service/skip_tokens.py
"""Cross-process "skip the next scheduled fire" tokens.

A token is a small JSON file under ``<state_dir>/skip/<job-id>.json`` written by an
out-of-process trigger (e.g. the ``trigger-reading`` CLI command) and consumed by the
running scheduler's job wrapper. It lets a separate process suppress one imminent
scheduled run without sharing the scheduler's in-memory job store.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_SKIP_SUBDIR = "skip"


def _safe(job_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", job_id)


def _token_path(state_dir: Path, job_id: str) -> Path:
    return Path(state_dir) / _SKIP_SUBDIR / f"{_safe(job_id)}.json"


def write_skip_token(state_dir: Path, job_id: str, slot: datetime, grace_sec: int = 600) -> Path:
    """Write a skip token for ``job_id`` targeting the scheduled fire at ``slot``.

    The token is honored by :func:`consume_skip_token` until ``slot + grace_sec``.
    Returns the path written.
    """
    path = _token_path(state_dir, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "slot": slot.isoformat(),
        "expires": (slot + timedelta(seconds=grace_sec)).isoformat(),
    }
    path.write_text(json.dumps(payload))
    return path


def consume_skip_token(state_dir: Path, job_id: str, now: datetime) -> bool:
    """Return True (and delete the token) if a non-stale token exists for ``job_id``.

    - No token        → False.
    - now <= expires  → delete, return True (caller should skip the run).
    - now >  expires  → stale; delete, return False.
    - corrupt token   → delete, return False.
    """
    path = _token_path(state_dir, job_id)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text())
        expires = datetime.fromisoformat(data["expires"])
    except Exception:
        logger.warning("Corrupt skip token %s; removing", path, exc_info=True)
        path.unlink(missing_ok=True)
        return False

    path.unlink(missing_ok=True)
    if now <= expires:
        return True
    logger.info("Stale skip token for %s (expired %s); not skipping", job_id, expires)
    return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_skip_tokens.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

(Per repo preference, work is bundled into one commit at session end — stage but do not commit yet.)

```bash
git add service/skip_tokens.py tests/test_skip_tokens.py
```

---

## Task 2: skip_tokens — absent, stale, and corrupt cases

**Files:**
- Modify: `tests/test_skip_tokens.py`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_skip_tokens.py

def test_consume_absent_returns_false(tmp_path: Path):
    assert skip_tokens.consume_skip_token(tmp_path, "nope", _now()) is False


def test_consume_after_expiry_returns_false_and_removes(tmp_path: Path):
    slot = _now()
    skip_tokens.write_skip_token(tmp_path, "job", slot, grace_sec=600)
    token_file = tmp_path / "skip" / "job.json"

    # 11 minutes after slot → past the 600s grace
    late = slot + dt.timedelta(minutes=11)
    assert skip_tokens.consume_skip_token(tmp_path, "job", late) is False
    assert not token_file.exists()


def test_consume_corrupt_token_returns_false_and_removes(tmp_path: Path):
    token_file = tmp_path / "skip" / "job.json"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text("{not json")
    assert skip_tokens.consume_skip_token(tmp_path, "job", _now()) is False
    assert not token_file.exists()
```

- [ ] **Step 2: Run tests to verify they pass**

These exercise code already written in Task 1.
Run: `python -m pytest tests/test_skip_tokens.py -v`
Expected: PASS (all four tests).

- [ ] **Step 3: Commit**

```bash
git add tests/test_skip_tokens.py
```

---

## Task 3: scheduler honors skip tokens

**Files:**
- Modify: `service/scheduler.py` (add `_state_dir()` near top-level helpers; add guard inside `_job_wrapper` at line ~498)
- Test: `tests/test_scheduler_skip.py` (create)

- [ ] **Step 1: Write the failing test**

The job wrapper is a closure built inside `_add_job`. Test it end-to-end by registering
a job on a real `BackgroundScheduler`, writing a skip token, and asserting the module's
`run` is NOT invoked when the wrapper runs. We invoke the wrapper directly via the
registered job's `func` to avoid timing flakiness.

```python
# tests/test_scheduler_skip.py
import datetime as dt
from pathlib import Path

import pytest

from service import scheduler as sch
from service import skip_tokens


@pytest.fixture
def cfg(tmp_path: Path):
    return {
        "timezone": "UTC",
        "jobs": [
            {
                "id": "demo-job",
                "module": "modules.example_daily",
                "trigger": {"daily_time": {"time": "05:25", "day_of_week": "mon-sun"}},
            }
        ],
    }


def test_job_wrapper_skips_when_token_present(monkeypatch, tmp_path: Path, cfg):
    # Point the scheduler's state dir at tmp_path
    monkeypatch.setattr(sch, "_state_dir", lambda: tmp_path)

    calls = []
    monkeypatch.setattr(
        sch.runner, "run_module_once",
        lambda *a, **k: calls.append((a, k)) or (None, "rid"),
    )

    from zoneinfo import ZoneInfo
    tz = ZoneInfo("UTC")
    spec = sch._make_job_spec(cfg["jobs"][0], default_job_defaults={"coalesce": True, "max_instances": 1}, tz=tz)

    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler(timezone=tz)
    sch._add_job(scheduler, spec)
    wrapper = scheduler.get_job("demo-job").func

    # No token → runs
    wrapper()
    assert len(calls) == 1

    # Token present for "now" → skipped
    now = dt.datetime.now(tz)
    skip_tokens.write_skip_token(tmp_path, "demo-job", now)
    wrapper()
    assert len(calls) == 1  # unchanged → skipped
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_scheduler_skip.py -v`
Expected: FAIL — `_state_dir` does not exist yet (AttributeError) / wrapper does not skip.

- [ ] **Step 3: Add the `_state_dir` helper**

Add near the other module-level helpers in `service/scheduler.py` (after imports; the
module already imports `os` and `Path`). Place it just above `_resolve_timezone`:

```python
def _state_dir() -> Path:
    """Writable state directory (the bind-mounted local/state), matching heartbeat path."""
    cfg_path = os.getenv("CONFIG_PATH") or "local/config.json"
    return Path(cfg_path).parent / "state"
```

- [ ] **Step 4: Add the import and the guard in `_job_wrapper`**

At the top of `service/scheduler.py`, add to the imports:

```python
from datetime import datetime as _datetime

from service import skip_tokens
```

(If `from service import runner` is already imported as `runner`, follow the same style;
`skip_tokens` import sits alongside it.)

Inside `_add_job`, at the very top of the `_job_wrapper` function (before
`started = _time.monotonic()`), insert:

```python
    def _job_wrapper():
        if skip_tokens.consume_skip_token(_state_dir(), spec.id, _datetime.now(scheduler.timezone)):
            LOG.info("Job[%s] skipped: manual-trigger dedup token consumed", spec.id)
            return
        started = _time.monotonic()
        ...
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_scheduler_skip.py -v`
Expected: PASS.

- [ ] **Step 6: Run the existing scheduler tests (no regression)**

Run: `python -m pytest tests/test_scheduler_triggers.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add service/scheduler.py tests/test_scheduler_skip.py
```

---

## Task 4: CLI weekday-selection helpers

**Files:**
- Modify: `service/cli.py` (add helpers above `cmd_run` or near `_extract_jobs_from_config`)
- Test: `tests/test_trigger_reading.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_trigger_reading.py
import datetime as dt

import pytest

from service import cli


@pytest.mark.parametrize(
    "spec,weekday,expected",
    [
        ("mon-thu", 0, True),   # Mon
        ("mon-thu", 3, True),   # Thu
        ("mon-thu", 4, False),  # Fri
        ("fri,sat,sun", 5, True),   # Sat
        ("fri,sat,sun", 0, False),  # Mon
        ("mon-sun", 6, True),
    ],
)
def test_weekday_matches(spec, weekday, expected):
    assert cli._weekday_matches(spec, weekday) is expected


def _bible_cfg():
    return {
        "timezone": "America/Chicago",
        "jobs": [
            {"id": "bible-plan-mon-thu-0455", "module": "modules.bible_plan",
             "trigger": {"daily_time": {"time": "05:25", "day_of_week": "mon-thu"}}},
            {"id": "bible-plan-fri-sun-0555", "module": "modules.bible_plan",
             "trigger": {"daily_time": {"time": "06:55", "day_of_week": "fri,sat,sun"}}},
        ],
    }


def test_select_bible_job_weekday():
    # 2026-06-02 is a Tuesday → mon-thu job
    job = cli._select_bible_job(_bible_cfg(), dt.date(2026, 6, 2))
    assert job["id"] == "bible-plan-mon-thu-0455"


def test_select_bible_job_weekend():
    # 2026-06-06 is a Saturday → fri-sun job
    job = cli._select_bible_job(_bible_cfg(), dt.date(2026, 6, 6))
    assert job["id"] == "bible-plan-fri-sun-0555"


def test_select_bible_job_no_match_raises():
    cfg = {"jobs": [{"id": "x", "module": "modules.other",
                     "trigger": {"daily_time": {"time": "05:25", "day_of_week": "mon-sun"}}}]}
    with pytest.raises(ValueError):
        cli._select_bible_job(cfg, dt.date(2026, 6, 2))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trigger_reading.py -v`
Expected: FAIL — `cli._weekday_matches` / `cli._select_bible_job` not defined.

- [ ] **Step 3: Write the helpers**

Add to `service/cli.py` (e.g. just below `_extract_jobs_from_config`):

```python
_DOW_NAMES = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
_BIBLE_MODULE = "modules.bible_plan"


def _day_of_week_indices(spec: str) -> set[int]:
    """Parse an APScheduler day_of_week string into weekday indices (Mon=0..Sun=6).

    Supports comma lists and hyphen ranges of 3-letter names, e.g. 'mon-thu',
    'fri,sat,sun'. Numeric forms are not used by this codebase and are not supported.
    """
    out: set[int] = set()
    for part in spec.lower().replace(" ", "").split(","):
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            li, hi_i = _DOW_NAMES[lo], _DOW_NAMES[hi]
            rng = range(li, hi_i + 1) if li <= hi_i else list(range(li, 7)) + list(range(0, hi_i + 1))
            out.update(rng)
        else:
            out.add(_DOW_NAMES[part])
    return out


def _weekday_matches(spec: str, weekday: int) -> bool:
    return weekday in _day_of_week_indices(spec)


def _select_bible_job(cfg: dict[str, Any], target_date) -> dict[str, Any]:
    """Pick the bible_plan job whose day_of_week covers target_date's weekday.

    Raises ValueError on zero or multiple matches.
    """
    wd = target_date.weekday()
    matches = []
    for job in cfg.get("jobs", []):
        if job.get("module") != _BIBLE_MODULE:
            continue
        dow = (((job.get("trigger") or {}).get("daily_time") or {}).get("day_of_week")) or "mon-sun"
        if _weekday_matches(dow, wd):
            matches.append(job)
    if not matches:
        raise ValueError(f"No {_BIBLE_MODULE} job covers weekday {wd}")
    if len(matches) > 1:
        ids = ", ".join(j.get("id", "?") for j in matches)
        raise ValueError(f"Ambiguous: multiple {_BIBLE_MODULE} jobs cover weekday {wd}: {ids}")
    return matches[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trigger_reading.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add service/cli.py tests/test_trigger_reading.py
```

---

## Task 5: CLI next-fire / within-6h decision + token write

**Files:**
- Modify: `service/cli.py` (add `_next_fire_within` helper)
- Modify: `tests/test_trigger_reading.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_trigger_reading.py
import datetime as dt
from zoneinfo import ZoneInfo


def test_next_fire_within_true_for_imminent_slot():
    tz = ZoneInfo("America/Chicago")
    job = {"id": "bible-plan-mon-thu-0455", "module": "modules.bible_plan",
           "trigger": {"daily_time": {"time": "05:25", "day_of_week": "mon-thu"}}}
    # now = 05:00 Tue, next fire 05:25 same day → within 6h
    now = dt.datetime(2026, 6, 2, 5, 0, tzinfo=tz)
    fire = cli._next_fire_within(job, tz, now, hours=6)
    assert fire is not None
    assert fire.hour == 5 and fire.minute == 25


def test_next_fire_within_none_when_far():
    tz = ZoneInfo("America/Chicago")
    job = {"id": "bible-plan-mon-thu-0455", "module": "modules.bible_plan",
           "trigger": {"daily_time": {"time": "05:25", "day_of_week": "mon-thu"}}}
    # now = 12:00 Tue, next fire 05:25 Wed → >6h away
    now = dt.datetime(2026, 6, 2, 12, 0, tzinfo=tz)
    assert cli._next_fire_within(job, tz, now, hours=6) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trigger_reading.py -k next_fire -v`
Expected: FAIL — `cli._next_fire_within` not defined.

- [ ] **Step 3: Write the helper**

Add the imports near the top of `service/cli.py` if not present:

```python
from datetime import timedelta

from service import scheduler as _scheduler  # already imported as _scheduler
from service import skip_tokens
```

(`from service import scheduler as _scheduler` already exists at the top of the file.)

Add the helper:

```python
def _next_fire_within(job: dict[str, Any], tz, now, hours: int = 6):
    """Return the job's next scheduled fire time if it is within `hours` of `now`, else None."""
    spec = _scheduler._make_job_spec(
        job, default_job_defaults={"coalesce": True, "max_instances": 1}, tz=tz
    )
    next_fire = spec.trigger.get_next_fire_time(None, now)
    if next_fire is None:
        return None
    return next_fire if next_fire <= now + timedelta(hours=hours) else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trigger_reading.py -k next_fire -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add service/cli.py tests/test_trigger_reading.py
```

---

## Task 6: `cmd_trigger_reading` + subparser (run + send + dedup)

**Files:**
- Modify: `service/cli.py` (add `cmd_trigger_reading`; register subparser in `_build_parser`)
- Modify: `tests/test_trigger_reading.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_trigger_reading.py
from types import SimpleNamespace
from unittest import mock


def test_cmd_trigger_reading_runs_and_writes_token(monkeypatch, tmp_path):
    cfg = {
        "timezone": "America/Chicago",
        "jobs": [
            {"id": "bible-plan-mon-thu-0455", "module": "modules.bible_plan",
             "email_to": ["a@example.com"], "subject": "Daily Reading",
             "trigger": {"daily_time": {"time": "05:25", "day_of_week": "mon-thu"}}},
            {"id": "bible-plan-fri-sun-0555", "module": "modules.bible_plan",
             "email_to": ["a@example.com"],
             "trigger": {"daily_time": {"time": "06:55", "day_of_week": "fri,sat,sun"}}},
        ],
    }
    monkeypatch.setattr(cli, "_load_config_with_optional_path", lambda path: cfg)
    monkeypatch.setattr(cli.skip_tokens, "_token_path",
                        lambda sd, jid: tmp_path / "skip" / f"{jid}.json")
    # Force "now" to a Tuesday 05:00 (within 6h of the 05:25 fire)
    import datetime as dt
    from zoneinfo import ZoneInfo
    fixed_now = dt.datetime(2026, 6, 2, 5, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr(cli, "_now_local", lambda tz: fixed_now)

    run_mock = mock.Mock(return_value=(None, "run-123"))
    monkeypatch.setattr(cli._runner, "run_module_once", run_mock)

    args = SimpleNamespace(config=None, date=None, no_dedup=False)
    rc = cli.cmd_trigger_reading(args)
    assert rc == 0

    # Sent with config recipients, command trigger
    _, kwargs = run_mock.call_args
    assert kwargs["email_to"] == ["a@example.com"]
    assert kwargs["trigger_type"] == "command"
    assert kwargs["send_email"] is True

    # Token written for the selected job
    assert (tmp_path / "skip" / "bible-plan-mon-thu-0455.json").exists()


def test_cmd_trigger_reading_no_dedup_skips_token(monkeypatch, tmp_path):
    cfg = {
        "timezone": "America/Chicago",
        "jobs": [
            {"id": "bible-plan-mon-thu-0455", "module": "modules.bible_plan",
             "email_to": ["a@example.com"],
             "trigger": {"daily_time": {"time": "05:25", "day_of_week": "mon-thu"}}},
        ],
    }
    monkeypatch.setattr(cli, "_load_config_with_optional_path", lambda path: cfg)
    monkeypatch.setattr(cli, "_state_dir", lambda: tmp_path)
    import datetime as dt
    from zoneinfo import ZoneInfo
    monkeypatch.setattr(cli, "_now_local",
                        lambda tz: dt.datetime(2026, 6, 2, 5, 0, tzinfo=ZoneInfo("America/Chicago")))
    monkeypatch.setattr(cli._runner, "run_module_once", mock.Mock(return_value=(None, "r")))

    args = SimpleNamespace(config=None, date=None, no_dedup=True)
    assert cli.cmd_trigger_reading(args) == 0
    assert not (tmp_path / "skip").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_trigger_reading.py -k cmd_trigger -v`
Expected: FAIL — `cmd_trigger_reading` / `_now_local` / `_state_dir` not defined.

- [ ] **Step 3: Implement command + small helpers**

Add to `service/cli.py`:

```python
def _state_dir() -> Path:
    cfg_path = os.getenv("CONFIG_PATH") or "local/config.json"
    return Path(cfg_path).parent / "state"


def _now_local(tz):
    return datetime.now(tz)


def cmd_trigger_reading(args: argparse.Namespace) -> int:
    """Run today's Bible reading (config recipients) and skip the upcoming fire if <6h."""
    try:
        cfg = _load_config_with_optional_path(args.config)
        tz = _scheduler._resolve_timezone(cfg)
        now = _now_local(tz)

        if args.date:
            target_date = datetime.strptime(args.date, "%Y-%m-%d").date()
        else:
            target_date = now.date()

        job = _select_bible_job(cfg, target_date)
        job_id = job.get("id")

        final_kwargs = dict(job.get("kwargs") or {})
        if args.date:
            final_kwargs["for_date"] = args.date

        _, run_id = _runner.run_module_once(
            module=job["module"],
            kwargs=final_kwargs,
            email_to=job.get("email_to") or None,
            cc=job.get("email_cc") or None,
            bcc=job.get("email_bcc") or None,
            subject=job.get("subject"),
            send_email=True,
            trigger_type="command",
            timeout_sec=job.get("timeout_sec"),
        )

        token_note = "no dedup (--no-dedup)"
        if not args.no_dedup:
            next_fire = _next_fire_within(job, tz, now, hours=6)
            if next_fire is not None:
                skip_tokens.write_skip_token(_state_dir(), job_id, next_fire)
                token_note = f"skip token written for {next_fire.isoformat()}"
            else:
                token_note = "next fire >6h away; no token"

        print(f"DONE: ran {job_id} (run-id {run_id}); {token_note}")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"FAILURE: {e}", file=sys.stderr)
        return 1
```

Ensure these imports exist at the top of `service/cli.py` (add what's missing):

```python
from service import skip_tokens
# `from datetime import datetime` and `import os` already present;
# `from service import runner as _runner` and `from service import scheduler as _scheduler` already present.
```

Register the subparser in `_build_parser`, after the `run` parser block:

```python
    # trigger-reading
    sp = sub.add_parser(
        "trigger-reading",
        help="Send today's Bible reading now and skip the upcoming scheduled fire (<6h).",
    )
    sp.add_argument("--date", help="Override target date (YYYY-MM-DD); maps to for_date.")
    sp.add_argument("--no-dedup", action="store_true",
                    help="Send only; do not write a skip token for the upcoming fire.")
    sp.set_defaults(func=cmd_trigger_reading)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_trigger_reading.py -k cmd_trigger -v`
Expected: PASS.

- [ ] **Step 5: Run the full new test file + cli tests**

Run: `python -m pytest tests/test_trigger_reading.py tests/test_cli_run_multi_noemail.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add service/cli.py tests/test_trigger_reading.py
```

---

## Task 7: Makefile target + manual verification

**Files:**
- Modify: `Makefile`

- [ ] **Step 1: Add the target**

Append to `Makefile` (match existing `.PHONY` / tab-indented recipe style):

```makefile
trigger-reading: ## Send today's Bible reading now + dedup the upcoming scheduled fire
	docker compose exec -T cortex python -m service.cli trigger-reading $(ARGS)
```

(If the Makefile declares a `.PHONY` list, add `trigger-reading` to it.)

- [ ] **Step 2: Verify the command wires up (dry, no real send)**

Run (in container, dry-run so no email leaves):
```bash
docker compose exec -T -e CORTEX_DRY_RUN=1 cortex python -m service.cli trigger-reading --date 2026-06-02
```
Expected: prints `DONE: ran bible-plan-mon-thu-0455 (run-id …); …`, exit 0. Because
`CORTEX_DRY_RUN=1`, the runner suppresses the actual send. A skip token may be written
under `local/state/skip/` — remove it after this check so it does not suppress a real run:
```bash
rm -f local/state/skip/bible-plan-*.json
```

- [ ] **Step 3: Run the whole suite in-container**

Run: `make test`
Expected: PASS (existing + new tests; live tests skipped).

- [ ] **Step 4: Lint**

Run: `make lint`
Expected: clean (ruff + mypy). Fix any type/style findings inline.

- [ ] **Step 5: Stage remaining files**

```bash
git add Makefile
```

---

## Task 8: Final bundled commit

Per the repo preference (one bundled commit per session), commit everything together now,
including the spec and plan docs.

- [ ] **Step 1: Review staged changes**

Run: `git status && git diff --cached --stat`
Expected: `service/skip_tokens.py`, `service/scheduler.py`, `service/cli.py`,
`tests/test_skip_tokens.py`, `tests/test_scheduler_skip.py`,
`tests/test_trigger_reading.py`, `Makefile`, and the two `docs/superpowers/` files.

- [ ] **Step 2: Commit**

```bash
git add docs/superpowers/specs/2026-06-02-trigger-reading-cli-design.md \
        docs/superpowers/plans/2026-06-02-trigger-reading-cli.md
git commit -m "$(cat <<'EOF'
feat(cli): add trigger-reading command with cross-process scheduler dedup

Sends today's Bible reading to configured recipients on demand and suppresses
the upcoming scheduled fire (within 6h) via a skip-token file honored by the
running scheduler's job wrapper. No IMAP listener / command-email round-trip.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review notes

- **Spec coverage:** invocation (Task 6/7), weekday job selection (Task 4), real send via
  runner with config recipients (Task 6), skip-token write within 6h (Tasks 1/5/6),
  scheduler consume guard (Task 3), stale/corrupt/absent handling (Task 2),
  `--no-dedup`/`--date` (Task 6), Makefile (Task 7). All covered.
- **Type/name consistency:** `write_skip_token`/`consume_skip_token`/`_token_path`,
  `_state_dir`, `_select_bible_job`, `_weekday_matches`, `_day_of_week_indices`,
  `_next_fire_within`, `_now_local`, `cmd_trigger_reading` are referenced consistently
  across tasks and tests. `run_module_once` is called with the exact keyword names from
  its signature (`module`, `kwargs`, `email_to`, `cc`, `bcc`, `subject`, `send_email`,
  `trigger_type`, `timeout_sec`).
- **State-dir agreement:** both `service/cli.py::_state_dir` and
  `service/scheduler.py::_state_dir` resolve `Path(CONFIG_PATH).parent/"state"`, so writer
  and consumer point at the same directory in-container.
```
