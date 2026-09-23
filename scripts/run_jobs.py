#!/usr/bin/env python3
"""Run configured jobs now, by id or glob, exactly as the scheduler would.

``service.cli run`` runs a *module* and makes you retype its kwargs. This
runs *jobs* from ``local/config.json``: the same spec parsing
(``scheduler._make_job_spec``) and the same ``runner.run_module_once`` call
the scheduler's wrapper makes — kwargs, send_email, email fields, timeout —
with ``trigger_type="manual"``. No skip token is written or consumed.

Matched jobs run the way the scheduler's 10-thread pool runs them on a
Tue/Fri morning: every job concurrently (``--parallel N``, default 10)
except the latest time group, which starts only after the rest finish.
``--parallel 1`` runs them one at a time in schedule order. So
``event-watch-*`` ends with ``event-watch-done`` (02:00) either way and its
``watch.complete`` goes out after every scraper has published. A failing
job is reported and the rest still run; the exit code is 1 if any failed.

It runs INSIDE the cortex container but is read from the host, so it works
without a rebuild (``service/`` is baked into the image). Use the wrapper::

    ./scripts/run-jobs.sh 'event-watch-*'
    ./scripts/run-jobs.sh --list 'event-watch-*'
    ./scripts/run-jobs.sh event-watch-destbryan event-watch-done
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, "/app")

from service import runner, scheduler  # noqa: E402
from service.config_schema import load_config  # noqa: E402


def _first_time(raw: dict) -> str:
    """The job's earliest daily_time, "" for any other trigger."""
    dt = (raw.get("trigger") or {}).get("daily_time") or {}
    t = dt.get("time", "")
    if isinstance(t, list):
        t = min(t) if t else ""
    return str(t)


def _job_id(raw: dict) -> str:
    return str(raw.get("id") or raw.get("name") or raw.get("module"))


def select(jobs: list[dict], patterns: list[str],
           include_disabled: bool) -> tuple[list[dict], list[dict]]:
    """(to run, skipped-as-disabled), in schedule order."""
    hits = [j for j in jobs
            if any(fnmatch.fnmatchcase(_job_id(j), p) for p in patterns)]
    hits.sort(key=lambda j: (_first_time(j), _job_id(j)))
    run = [j for j in hits if include_disabled or j.get("enabled", True)]
    skipped = [j for j in hits if j not in run]
    return run, skipped


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="run-jobs", description=__doc__.split("\n\n")[0])
    ap.add_argument("patterns", nargs="+", metavar="JOB",
                    help="job id or shell glob (quote it), e.g. 'event-watch-*'")
    ap.add_argument("--list", action="store_true",
                    help="print what would run, in order, and exit")
    ap.add_argument("--include-disabled", action="store_true",
                    help="also run jobs with enabled: false")
    ap.add_argument("--no-email", action="store_true",
                    help="force send_email off for every job")
    ap.add_argument("--parallel", type=int, default=10, metavar="N",
                    help="run up to N jobs at once, the latest time group"
                         " last (default 10, as the scheduler; 1 = serial)")
    args = ap.parse_args(argv)

    cfg = load_config(os.getenv("CONFIG_PATH") or "local/config.json")
    run, skipped = select(list(cfg.get("jobs") or []), args.patterns,
                          args.include_disabled)
    for j in skipped:
        print(f"SKIP {_job_id(j)} (disabled)", flush=True)
    if not run:
        print("no enabled job matches " + " ".join(args.patterns), file=sys.stderr)
        return 2
    if args.list:
        for j in run:
            print(f"{_first_time(j) or '-':>8}  {_job_id(j)}  ({j['module']})")
        return 0

    tz = scheduler._resolve_timezone(cfg)

    def one(raw: dict) -> bool:
        spec = scheduler._make_job_spec(raw, default_job_defaults={}, tz=tz)
        send = False if args.no_email else (
            spec.send_email if spec.send_email is not None else True)
        started = time.monotonic()
        print(f"RUN  {spec.id}", flush=True)
        try:
            runner.run_module_once(
                spec.module,
                kwargs=dict(spec.kwargs or {}),
                send_email=send,
                timeout_sec=spec.timeout_sec,
                trigger_type="manual",
                job_context=scheduler._build_job_context(spec),
                email_to=spec.email_to,
                cc=spec.email_cc,
                bcc=spec.email_bcc,
                subject=spec.subject,
            )
            print(f"OK   {spec.id} {time.monotonic() - started:.0f}s", flush=True)
            return True
        except Exception as e:  # noqa: BLE001 — report and keep going
            print(f"FAIL {spec.id} {time.monotonic() - started:.0f}s {e!r}",
                  flush=True)
            return False

    if args.parallel > 1:
        last = _first_time(run[-1])
        first, final = ([j for j in run if _first_time(j) != last],
                        [j for j in run if _first_time(j) == last])
        results: list[bool] = []
        for wave in (first, final):
            with ThreadPoolExecutor(args.parallel) as pool:
                results += list(pool.map(one, wave))
    else:
        results = [one(raw) for raw in run]
    failed = results.count(False)
    print(f"ALL DONE: {len(run) - failed} ok, {failed} failed", flush=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
