from __future__ import annotations

import argparse
import html
import json
import os
from collections import Counter
from datetime import date as _date
from datetime import datetime, timedelta
from pathlib import Path

from .lib import logging_bridge

#: Scrapes career_watch is scheduled to run on a working day.
#:
#: Eleven consecutive days of real logs (2026-08-01 .. 08-12) read exactly
#: ``start: 10, summary: 10``, with no variance whatsoever. That absence of a
#: noise floor is what licenses a strict check: 9 is not a slow day, it is a
#: fault.
DEFAULT_SCRAPES_EXPECTED = 10

#: Weekdays career_watch runs (Mon=0 .. Sun=6). Sunday is legitimately empty.
DEFAULT_SCRAPE_WEEKDAYS = (0, 1, 2, 3, 4, 5)

DEFAULT_LOG_DIR = "/app/local/logs"
SCRAPE_MODULE = "modules.career_watch"


def _read_log(path: Path) -> list[dict] | None:
    """Return the day's records, or None if the file is not there."""
    if not path.is_file():
        return None
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                # A torn final line is normal if the file is being written.
                continue
            if isinstance(rec, dict):
                rows.append(rec)
    return rows


def _tally(rows: list[dict]) -> Counter:
    """Count the handful of signals the checks are built from.

    Keyed on ``module`` rather than ``component``: ``start`` comes from
    ``career_watch.main`` and ``summary`` from ``career_watch.engine``, but both
    carry the same module, so the module survives refactors of either.
    """
    t: Counter = Counter()
    for r in rows:
        op, module = r.get("op"), r.get("module")
        if module == SCRAPE_MODULE and op == "start":
            t["starts"] += 1
        elif module == SCRAPE_MODULE and op == "summary":
            t["summaries"] += 1
        elif op == "vpn_cycle":
            t["cycles_ok" if r.get("ok") else "cycles_failed"] += 1
        elif op == "vpn_health_fail":
            t["health_fails"] += 1
    return t


def _int_kwarg(kwargs: dict, name: str, env: str, default: int) -> int:
    raw = kwargs.get(name)
    if raw is None or str(raw).strip() == "":
        raw = os.getenv(env) or default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _resolve_date(value: str | None) -> str:
    """Accept a date, 'today', 'yesterday', or nothing.

    ops runs its nightly at 02:00, so the day it actually wants to judge is the
    one that has just finished — hence 'yesterday' being spelled out rather
    than left to the caller's shell.
    """
    raw = str(value or "").strip().lower()
    if not raw or raw == "today":
        return _date.today().isoformat()
    if raw == "yesterday":
        return (_date.today() - timedelta(days=1)).isoformat()
    return str(value).strip()


def evaluate(*, date: str | None = None, log_dir: str | None = None,
             scrapes_expected: int | None = None,
             expect_rotation: bool = True) -> dict:
    """Judge one day and return the verdict as data.

    Separate from run() because the ops nightly email renders its own section
    and wants the counts, not this module's HTML.

    Returns {"date", "ok", "failures": [str, ...], "tally": {...}}.
    """
    day = _resolve_date(date)
    log_dir_path = Path(str(log_dir or os.getenv("LOG_DIR") or DEFAULT_LOG_DIR))
    scrapes_expected = _int_kwarg({"scrapes_expected": scrapes_expected},
                                  "scrapes_expected",
                                  "HEALTH_SCRAPES_EXPECTED",
                                  DEFAULT_SCRAPES_EXPECTED)

    weekday = datetime.strptime(day, "%Y-%m-%d").weekday()
    is_scrape_day = weekday in DEFAULT_SCRAPE_WEEKDAYS
    if not is_scrape_day:
        scrapes_expected = 0

    failures: list[str] = []
    rows = _read_log(log_dir_path / f"activity-{day}.jsonl")

    if rows is None:
        # Sonos chimes run every day, so an absent file is not an idle day --
        # it means nothing logged at all. Failing open here is how a dead
        # container would report itself healthy.
        failures.append(
            f"No activity log for {day}. Nothing was written all day, which "
            f"means the scheduler or the container is down.")
        tally: Counter = Counter()
    else:
        tally = _tally(rows)

        if tally["starts"] < scrapes_expected:
            failures.append(
                f"Scrapes did not run: {tally['starts']} of {scrapes_expected} "
                f"expected career_watch runs fired.")

        # The gap between firing and finishing is the fail-closed bail-out, and
        # it is invisible to a check that only counts runs. This is the shape
        # 2026-08-13 had: 10 fired, 6 finished.
        if tally["summaries"] < tally["starts"]:
            failures.append(
                f"Scrapes bailed out: {tally['starts']} runs fired but only "
                f"{tally['summaries']} completed a scrape "
                f"({tally['starts'] - tally['summaries']} gave up).")

        if tally["health_fails"]:
            failures.append(
                f"VPN fail-closed {tally['health_fails']} time(s): a run found "
                f"no usable exit and abandoned the scrape.")

        if tally["cycles_failed"]:
            failures.append(
                f"VPN rotation failed {tally['cycles_failed']} time(s): the "
                f"cycle could not move to a different exit, so the next scrape "
                f"reused one.")

        if expect_rotation and is_scrape_day and not tally["cycles_ok"]:
            failures.append(
                "VPN rotation never ran: no successful vpn_cycle all day, so "
                "exits are going stale without anything reporting it.")

    return {"date": day, "ok": not failures, "failures": failures,
            "tally": dict(tally)}


def run(**kwargs) -> str | None:
    """Module entrypoint, kept for an ad-hoc `RUN MODULE=modules.health_check`.

    The scheduled path is the ops nightly email, which calls the CLI below and
    renders its own section — cortex deliberately does not send a second
    nightly mail. This wrapper stays because a one-off check over IMAP is
    genuinely useful, and it costs nothing.

    Returns None when every check passes, an HTML report otherwise.
    """
    verdict = evaluate(
        date=kwargs.get("date"),
        log_dir=kwargs.get("log_dir"),
        scrapes_expected=kwargs.get("scrapes_expected"),
        expect_rotation=bool(kwargs.get("expect_rotation", True)),
    )
    logging_bridge.activity({
        "component": "health_check", "op": "health_check", **verdict,
    })
    if verdict["ok"]:
        return None
    return _report(verdict["date"], verdict["failures"],
                   Counter(verdict["tally"]))


def main_cli(argv: list[str] | None = None) -> int:
    """Print the verdict as JSON. Consumed by ops/scripts/nightly_fleet.sh.

    Exit code is 0 whether or not checks passed: a reported fault is a
    successful check. A non-zero exit would make the wrapper's `|| true` swallow
    the payload and the email would silently lose the section.
    """
    ap = argparse.ArgumentParser(prog="python -m modules.health_check",
                                 description=__doc__)
    ap.add_argument("--json", action="store_true",
                    help="print the verdict as JSON (currently the only format)")
    ap.add_argument("--date", default="today",
                    help="YYYY-MM-DD, 'today', or 'yesterday' (default today)")
    ap.add_argument("--log-dir", default=None)
    ap.add_argument("--scrapes-expected", type=int, default=None)
    ap.add_argument("--no-rotation-check", action="store_true",
                    help="skip the 'rotation never ran' check, for days before "
                         "modules.vpn_cycle was scheduled")
    args = ap.parse_args(argv)

    verdict = evaluate(date=args.date, log_dir=args.log_dir,
                       scrapes_expected=args.scrapes_expected,
                       expect_rotation=not args.no_rotation_check)
    print(json.dumps(verdict, sort_keys=True))
    return 0


def _report(day: str, failures: list[str], tally: Counter) -> str:
    items = "\n".join(f"    <li>{html.escape(f)}</li>" for f in failures)
    counts = "\n".join(
        f"    <tr><td>{html.escape(k)}</td><td align=\"right\">{v}</td></tr>"
        for k, v in sorted(tally.items()))
    return f"""
<h2>cortex health check &mdash; {html.escape(day)}</h2>
<p>{len(failures)} check(s) failed.</p>
<ul>
{items}
</ul>
<h3>Counts for the day</h3>
<table cellpadding="4" border="1" style="border-collapse:collapse;">
{counts or '    <tr><td colspan="2">nothing logged</td></tr>'}
</table>
<p style="color:#666;font-size:0.9em;">A healthy day is 10 starts, 10
summaries, 10 successful rotations and no failures. This check is strict
because eleven days of logs showed no variance at all.</p>
""".strip()
