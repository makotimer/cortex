# tests/test_health_check.py
"""modules.health_check -- one nightly pass over the day's activity log.

Calibrated against eleven days of real logs, where a healthy day is exactly
``start: 10, summary: 10`` with no variance at all. There is no noise floor, so
the check is strict: anything short of that is reported.

The shape it exists to catch is 2026-08-13, which read ``start: 10,
summary: 6`` with ``vpn_health_fail: 4``. Four scrapes fired and bailed
fail-closed on a broken verify. Counting *runs* would have seen ten and called
the day healthy -- the signal is the gap between firing and finishing.

Returns None when everything checks out, so a healthy day is silent (the runner
maps None to no email). Returns HTML when any check fails, which is the 'or'.
"""
import json

import pytest

from modules import health_check

TUESDAY = "2026-08-11"     # an ordinary scrape day
SUNDAY = "2026-08-09"      # career_watch does not run
THURSDAY = "2026-08-13"    # the day four scrapes bailed


def _write_log(tmp_path, date, *, starts=10, summaries=10,
               cycles_ok=10, cycles_failed=0, health_fails=0):
    """Build an activity log with the counts that matter."""
    rows: list[dict] = []
    rows += [{"op": "start", "component": "career_watch.main",
              "module": "modules.career_watch"}] * starts
    rows += [{"op": "summary", "component": "career_watch.engine",
              "module": "modules.career_watch", "found_by_source": {}}] * summaries
    rows += [{"op": "vpn_cycle", "component": "vpn_cycle", "ok": True,
              "module": "modules.vpn_cycle", "ip": "2.2.2.2"}] * cycles_ok
    rows += [{"op": "vpn_cycle", "component": "vpn_cycle", "ok": False,
              "module": "modules.vpn_cycle",
              "reason": "restart returned the same exit 1.1.1.1"}] * cycles_failed
    rows += [{"op": "vpn_health_fail", "component": "career_watch.engine",
              "module": "modules.career_watch",
              "reason": "no usable exit"}] * health_fails
    # Sonos chimes run every day, which is why a missing file means something.
    rows.append({"module": "modules.sonos"})

    path = tmp_path / f"activity-{date}.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    return path


def _run(tmp_path, date, **kw):
    return health_check.run(date=date, log_dir=str(tmp_path), **kw)


# ----------------------------------------------------------------------
# Healthy days are silent
# ----------------------------------------------------------------------
def test_a_normal_day_reports_nothing(tmp_path):
    _write_log(tmp_path, TUESDAY)
    assert _run(tmp_path, TUESDAY) is None


def test_sunday_passes_with_no_scrapes(tmp_path):
    """career_watch is mon-sat; zero runs on Sunday is correct, not a fault."""
    _write_log(tmp_path, SUNDAY, starts=0, summaries=0, cycles_ok=0)
    assert _run(tmp_path, SUNDAY) is None


# ----------------------------------------------------------------------
# The regression this exists for
# ----------------------------------------------------------------------
def test_scrapes_that_fired_but_bailed_are_caught(tmp_path):
    """2026-08-13: ten fired, six finished. Counting runs would see ten."""
    _write_log(tmp_path, THURSDAY, starts=10, summaries=6, health_fails=4)
    report = _run(tmp_path, THURSDAY)
    assert report is not None, "four bailed scrapes must be reported"
    assert "6" in report and "10" in report


def test_a_single_bailed_scrape_is_enough(tmp_path):
    """Strict: eleven days of real logs show no variance, so 9/10 is a fault."""
    _write_log(tmp_path, TUESDAY, starts=10, summaries=9)
    assert _run(tmp_path, TUESDAY) is not None


# ----------------------------------------------------------------------
# Scheduler / rotation
# ----------------------------------------------------------------------
def test_scrapes_not_running_at_all_is_caught(tmp_path):
    """The scheduler died, or the job was disabled by accident."""
    _write_log(tmp_path, TUESDAY, starts=0, summaries=0)
    report = _run(tmp_path, TUESDAY)
    assert report is not None
    assert "0" in report


def test_a_failed_rotation_is_caught(tmp_path):
    _write_log(tmp_path, TUESDAY, cycles_ok=9, cycles_failed=1)
    report = _run(tmp_path, TUESDAY)
    assert report is not None
    assert "rotation" in report.lower() or "cycle" in report.lower()


def test_rotation_not_running_at_all_is_caught(tmp_path):
    """Scrapes fine, but nothing is cycling -- exits go stale silently."""
    _write_log(tmp_path, TUESDAY, cycles_ok=0)
    assert _run(tmp_path, TUESDAY) is not None


def test_health_fail_alone_is_caught(tmp_path):
    """A fail-closed bail with the counts still matching must not slip through."""
    _write_log(tmp_path, TUESDAY, health_fails=1)
    assert _run(tmp_path, TUESDAY) is not None


# ----------------------------------------------------------------------
# The check must not fail open
# ----------------------------------------------------------------------
def test_a_missing_log_is_a_failure_not_a_pass(tmp_path):
    """No file means nothing logged all day, including the daily sonos chimes.

    Treating absence as 'no failures found' is how a dead container reports
    itself healthy.
    """
    report = _run(tmp_path, TUESDAY)
    assert report is not None
    assert "log" in report.lower()


def test_expectations_are_configurable(tmp_path):
    """Thresholds live in config, not code."""
    _write_log(tmp_path, TUESDAY, starts=4, summaries=4, cycles_ok=4)
    assert _run(tmp_path, TUESDAY, scrapes_expected=4) is None
    assert _run(tmp_path, TUESDAY, scrapes_expected=10) is not None


def test_the_report_names_what_failed(tmp_path):
    """It is an alert, so it has to say which check tripped."""
    _write_log(tmp_path, TUESDAY, starts=10, summaries=7, cycles_failed=2)
    report = _run(tmp_path, TUESDAY)
    assert report is not None
    assert THURSDAY not in report
    assert TUESDAY in report


@pytest.mark.parametrize("bad", [
    {"summaries": 9},
    {"cycles_failed": 1},
    {"health_fails": 1},
    {"starts": 9, "summaries": 9},
])
def test_any_single_fault_trips_it(tmp_path, bad):
    """The 'or': independent checks, any one of which is enough."""
    _write_log(tmp_path, TUESDAY, **bad)
    assert _run(tmp_path, TUESDAY) is not None


# ----------------------------------------------------------------------
# evaluate() and the JSON CLI -- how ops consumes this
# ----------------------------------------------------------------------
def test_evaluate_returns_a_machine_readable_verdict(tmp_path):
    """ops renders its own section, so it needs the verdict, not HTML."""
    _write_log(tmp_path, TUESDAY, starts=10, summaries=6, health_fails=4)
    v = health_check.evaluate(date=TUESDAY, log_dir=str(tmp_path))

    assert v["ok"] is False
    assert v["date"] == TUESDAY
    assert len(v["failures"]) == 2
    assert v["tally"]["starts"] == 10
    assert v["tally"]["summaries"] == 6


def test_evaluate_says_ok_on_a_clean_day(tmp_path):
    _write_log(tmp_path, TUESDAY)
    v = health_check.evaluate(date=TUESDAY, log_dir=str(tmp_path))
    assert v["ok"] is True
    assert v["failures"] == []


def test_cli_prints_json(tmp_path, capsys):
    _write_log(tmp_path, TUESDAY, summaries=9)
    rc = health_check.main_cli(["--json", "--date", TUESDAY,
                                "--log-dir", str(tmp_path)])
    payload = json.loads(capsys.readouterr().out)

    assert rc == 0, "reporting a fault is a successful check, not a crashed one"
    assert payload["ok"] is False
    assert payload["date"] == TUESDAY


def test_cli_understands_yesterday(tmp_path, capsys):
    """ops runs at 02:00, so the day it wants is the one that just finished."""
    from datetime import date, timedelta
    y = (date.today() - timedelta(days=1)).isoformat()
    _write_log(tmp_path, y)

    health_check.main_cli(["--json", "--date", "yesterday", "--log-dir", str(tmp_path)])
    assert json.loads(capsys.readouterr().out)["date"] == y
