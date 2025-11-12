# service/scheduler.py
from __future__ import annotations

import logging
import os
import threading
import time as _time
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import time
from typing import Any

# NOTE: We intentionally avoid importing BaseTrigger to keep imports light; we refer
# to it by string for type hints where helpful.
from apscheduler.executors.pool import ProcessPoolExecutor, ThreadPoolExecutor
from apscheduler.jobstores.memory import MemoryJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.combining import OrTrigger
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger

# Project-local interfaces
from . import config_schema, runner
from .logging_utils import write_activity_log  # type: ignore[import-not-found]

LOG = logging.getLogger(__name__)


# ---- Internal structures ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class JobSpec:
    id: str
    trigger: Any  # "apscheduler.triggers.base.BaseTrigger"
    module: str
    kwargs: dict[str, Any]
    send_email: bool | None
    timeout_sec: int | None
    max_instances: int
    coalesce: bool
    misfire_grace_time: int | None
    summary: str | None
    email_to: list[str] | None = None
    email_cc: list[str] | None = None
    email_bcc: list[str] | None = None
    subject: str | None = None


# ---- Public controller ------------------------------------------------------


class SchedulerController:
    """
    A small façade around APScheduler so the CLI can manage lifecycle cleanly.
    """

    def __init__(self, scheduler: BackgroundScheduler) -> None:
        self._scheduler = scheduler
        self._stopped_evt = threading.Event()

    def stop(self) -> None:
        """
        Promptly shut down APScheduler.
        """
        if self._scheduler.running:
            LOG.info("Shutting down scheduler...")
            # wait=False -> stop immediately; jobs in-flight are allowed to finish.
            self._scheduler.shutdown(wait=False)
        self._stopped_evt.set()
        LOG.info("Scheduler shut down complete.")

    def join(self, timeout: float | None = None) -> bool:
        """
        Block until the scheduler is fully stopped (or timeout).
        Returns True if stopped before timeout, else False.
        """
        return self._stopped_evt.wait(timeout=timeout)

    # (Nice) helper for diagnostics, not required by CLI:
    def get_job_ids(self) -> Iterable[str]:
        if not self._scheduler:
            return []
        return (job.id for job in self._scheduler.get_jobs())


# ---- Module API -------------------------------------------------------------


def start(config_path: str | None = None) -> SchedulerController:
    """
    Load configuration, build an APScheduler instance, add jobs, and start.
    Returns a SchedulerController that exposes stop() and join().

    Notes:
      * APScheduler 3.x prefers a pytz scheduler timezone. Individual triggers
        can be constructed with zoneinfo; APS coerces internally. We keep the
        scheduler tz as pytz to avoid surprises.
    """
    cfg = config_schema.load_config(config_path)
    tz = _resolve_timezone(cfg)

    # Reasonable defaults; tweak as your workload evolves.
    job_defaults = {
        "coalesce": True,  # run only the latest if many were missed
        "max_instances": 1,
        # misfire_grace_time left to per-job override, else APScheduler default.
    }
    executors = {
        "default": ThreadPoolExecutor(_int_or(cfg.get("executor_workers"), 10)),
        # Keep process pool off unless you need isolation for CPU-heavy jobs:
        # "processpool": ProcessPoolExecutor(2),
    }
    jobstores = {"default": MemoryJobStore()}

    scheduler = BackgroundScheduler(
        timezone=tz,
        job_defaults=job_defaults,
        executors=executors,
        jobstores=jobstores,
    )

    # Translate config jobs into JobSpec + schedule them
    jobs_cfg = cfg.get("jobs", [])
    if not isinstance(jobs_cfg, list):
        raise ValueError("config.jobs must be a list")

    for raw in jobs_cfg:
        try:
            spec = _make_job_spec(raw, default_job_defaults=job_defaults, tz=tz)
        except Exception:
            LOG.exception("Skipping job due to config error: %r", raw)
            continue

        _add_job(scheduler, spec)

    # Start the engine
    scheduler.start()
    LOG.info("Scheduler started with %d job(s).", len(scheduler.get_jobs()))

    return SchedulerController(scheduler)


# ---- Helpers ----------------------------------------------------------------


def _preview_trigger(trigger, tz, count: int = 6, start=None):
    """
    Return next `count` fire times for visibility in logs/prints.
    Deterministic: we seed previous_fire_time = now = `start` (or "now" in tz),
    then advance `now` by 1µs after each hit so the next lookup moves forward.
    Works for CronTrigger, OrTrigger, IntervalTrigger, etc. (APScheduler 3.x).
    """
    from datetime import datetime, timedelta

    now = start or datetime.now(tz=tz)
    prev = now
    times = []
    for _ in range(count):
        nxt = trigger.get_next_fire_time(prev, now)
        if nxt is None:
            break
        times.append(nxt)
        prev = nxt
        now = nxt + timedelta(microseconds=1)
    return times


def _resolve_timezone(cfg: dict[str, Any]):
    """
    APScheduler 3.x expects a pytz timezone. We accept either:
    - config['timezone'] (e.g., 'America/Indiana/Indianapolis')
    - env TZ
    - default to UTC
    """
    tz_name = cfg.get("timezone") or os.getenv("TZ") or "UTC"
    try:
        # Prefer pytz for APScheduler 3.x
        import pytz  # type: ignore

        return pytz.timezone(tz_name)
    except Exception:
        import pytz  # type: ignore

        LOG.warning("Falling back to UTC timezone (invalid or missing tz '%s')", tz_name)
        return pytz.UTC


def _make_job_spec(raw: dict[str, Any], default_job_defaults: dict[str, Any], tz) -> JobSpec:
    """
    Convert a raw config job dict into a normalized JobSpec + APScheduler trigger.

    Accepted trigger forms:
      - {"cron": string crontab or dict of cron fields}
      - {"interval": dict with seconds/minutes/hours/etc.}
      - {"date": ISO string or epoch seconds}
      - {"daily_time": {"time": "HH:MM[:SS]" | ["..."], ...}}  # sugar over Cron
    """
    # Identity
    jid = str(raw.get("id") or raw.get("name") or _require(raw, "module"))
    module = _require(raw, "module")

    # Optional arguments
    kwargs = dict(raw.get("kwargs") or {})
    send_email = raw.get("send_email")
    email_to = raw.get("email_to")
    email_cc = raw.get("email_cc")
    email_bcc = raw.get("email_bcc")
    subject = raw.get("subject")
    timeout_sec = _int_or(raw.get("timeout_sec"), None)

    # Per-job policy overrides
    max_instances = _int_or(raw.get("max_instances"), default_job_defaults.get("max_instances", 1))
    coalesce = bool(raw.get("coalesce", default_job_defaults.get("coalesce", True)))
    misfire_grace_time = _int_or(raw.get("misfire_grace_time"), None)

    # Summary for list/diagnostics
    summary = raw.get("summary") or raw.get("description")

    # Trigger translation
    if os.getenv("SCHEDULER_PREVIEW", None) == "1":
        print("RAW:  ", raw)
    trigger = _build_trigger(raw["trigger"], tz)
    if os.getenv("SCHEDULER_PREVIEW", None) == "1":
        print(f"PARSED[{jid}]:", trigger)

    return JobSpec(
        id=jid,
        trigger=trigger,
        module=module,
        kwargs=kwargs,
        send_email=send_email,
        timeout_sec=timeout_sec,
        max_instances=max_instances,
        coalesce=coalesce,
        misfire_grace_time=misfire_grace_time,
        summary=summary,
        email_to=email_to,
        email_cc=email_cc,
        email_bcc=email_bcc,
        subject=subject,
    )


def _build_trigger(trig_def: dict[str, Any], tz: str | None) -> Any:
    """
    Build an APScheduler trigger from a dict.

    Supported shapes:
      {"interval": {weeks|days|hours|minutes|seconds, jitter?, start_date?, end_date?, timezone?}}
      {"cron":     {second?, minute?, hour?, day?, day_of_week?, month?, jitter?, start_date?, end_date?, tz?}}
      {"cron":     "*/15 * * * *"}  # crontab, scheduler tz used by default
      {"date":     {"run_at": ISO|epoch|datetime, timezone?}}
      {"date":     ISO|epoch|datetime}
      {"daily_time": {"time": "HH:MM[:SS]" | ["..."],
                      "day_of_week"?: "...",
                      "timezone"?: "..."}}  # produces OrTrigger of CronTriggers

    Timezone rules:
      - If a trigger block has its own 'timezone', use it.
      - Else, fall back to the scheduler tz (`tz` argument).
      - 'date.run_at' without tz is interpreted in the scheduler tz.
    """
    from collections.abc import Iterable
    from datetime import datetime
    from datetime import tzinfo as _dt_tzinfo
    from zoneinfo import ZoneInfo

    if not isinstance(trig_def, dict):
        raise ValueError("trigger spec must be a dict")

    def _tz(z):
        if not z:
            return None
        if isinstance(z, str):
            return ZoneInfo(z)
        if isinstance(z, _dt_tzinfo):
            return z
        return ZoneInfo(str(z))

    # Compute a default timezone from the scheduler argument
    default_tz = _tz(tz)

    present = [k for k in ("interval", "cron", "date", "daily_time") if k in trig_def and trig_def[k] is not None]
    if len(present) != 1:
        raise ValueError("exactly one of {'interval','cron','date','daily_time'} must be provided")
    kind = present[0]

    # ---------- INTERVAL ----------
    if kind == "interval":
        spec = trig_def["interval"]
        if not isinstance(spec, dict):
            raise ValueError("interval must be an object with time fields")

        allowed = {"weeks", "days", "hours", "minutes", "seconds", "jitter", "timezone", "start_date", "end_date"}
        unknown = set(spec.keys()) - allowed
        if unknown:
            raise ValueError(f"interval has unknown field(s): {sorted(unknown)}")

        def _as_int_ge0(name: str) -> int:
            if name not in spec:
                return 0
            try:
                v = int(spec[name])
            except ValueError as err:
                raise ValueError(f"interval.{name} must be an integer") from err
            if v < 0:
                raise ValueError(f"interval.{name} must be >= 0")
            return v

        iv = {
            "weeks": _as_int_ge0("weeks"),
            "days": _as_int_ge0("days"),
            "hours": _as_int_ge0("hours"),
            "minutes": _as_int_ge0("minutes"),
            "seconds": _as_int_ge0("seconds"),
        }
        if sum(iv.values()) == 0:
            raise ValueError("interval must be greater than 0 (provide at least one nonzero time field)")

        kwargs = {k: v for k, v in iv.items() if v}
        if "jitter" in spec:
            j = _as_int_ge0("jitter")
            if j:
                kwargs["jitter"] = j
        if "start_date" in spec:
            kwargs["start_date"] = spec["start_date"]
        if "end_date" in spec:
            kwargs["end_date"] = spec["end_date"]

        return IntervalTrigger(timezone=_tz(spec.get("timezone")) or default_tz, **kwargs)

    # ---------- CRON ----------
    if kind == "cron":
        cron_spec = trig_def["cron"]
        if isinstance(cron_spec, str):
            fields = cron_spec.strip().split()
            if len(fields) not in (5, 6):
                raise ValueError(f"cron string must have 5 or 6 fields (got {len(fields)}): {cron_spec!r}")
            return CronTrigger.from_crontab(cron_spec, timezone=default_tz)
        if isinstance(cron_spec, dict):
            # Allow a handful of common extras; unknowns get flagged.
            allowed = {
                "second",
                "minute",
                "hour",
                "day",
                "day_of_week",
                "month",
                "timezone",
                "start_date",
                "end_date",
                "jitter",
            }
            unknown = set(cron_spec.keys()) - allowed
            if unknown:
                raise ValueError(f"cron has unknown field(s): {sorted(unknown)}")

            return CronTrigger(
                second=cron_spec.get("second", 0),
                minute=cron_spec.get("minute", 0),
                hour=cron_spec.get("hour", 0),
                day=cron_spec.get("day"),
                day_of_week=cron_spec.get("day_of_week"),
                month=cron_spec.get("month"),
                start_date=cron_spec.get("start_date"),
                end_date=cron_spec.get("end_date"),
                jitter=cron_spec.get("jitter"),
                timezone=_tz(cron_spec.get("timezone")) or default_tz,
            )
        raise ValueError("cron must be a crontab string or an object")

    # ---------- DATE ----------
    if kind == "date":
        dspec = trig_def["date"]
        if isinstance(dspec, dict):
            run_at = dspec.get("run_at")
            tzinfo = _tz(dspec.get("timezone")) or default_tz
        else:
            run_at = dspec
            tzinfo = default_tz

        if run_at is None:
            raise ValueError("date trigger requires 'run_at' (or non-empty scalar value)")

        from datetime import datetime as _dt

        if isinstance(run_at, (int, float)):
            dt = _dt.fromtimestamp(run_at, tz=tzinfo)
        elif isinstance(run_at, _dt):
            dt = run_at if run_at.tzinfo else run_at.replace(tzinfo=tzinfo)
        else:
            try:
                dt = _dt.fromisoformat(str(run_at))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=tzinfo)
            except Exception as e:
                raise ValueError(f"Invalid date.run_at: {run_at!r}") from e

        return DateTrigger(run_date=dt, timezone=dt.tzinfo or tzinfo)

    # ---------- DAILY TIME ----------
    if kind == "daily_time":
        dtdef = trig_def["daily_time"]
        if not isinstance(dtdef, dict):
            raise ValueError("daily_time must be an object")

        # Validate keys early to catch typos
        allowed = {"time", "day_of_week", "timezone"}
        unknown = set(dtdef.keys()) - allowed
        if unknown:
            raise ValueError(f"daily_time has unknown field(s): {sorted(unknown)}")

        tzinfo = _tz(dtdef.get("timezone")) or default_tz

        def _parse_time(s: str) -> tuple[int, int, int]:
            parts = s.split(":")
            if len(parts) not in (2, 3):
                raise ValueError(f"daily_time.time must be 'HH:MM' or 'HH:MM:SS', got {s!r}")
            try:
                hh = int(parts[0])
                mm = int(parts[1])
                ss = int(parts[2]) if len(parts) == 3 else 0
            except ValueError as err:
                raise ValueError(f"daily_time.time must contain integers: {s!r}") from err
            time(hh, mm, ss)  # validates ranges
            return hh, mm, ss

        times = dtdef.get("time")
        if times is None:
            raise ValueError("daily_time requires 'time'")
        if isinstance(times, str):
            times = [times]
        if not isinstance(times, Iterable):
            raise ValueError("daily_time.time must be a string or list of strings")

        # Normalize: dedup + sort by (hour, minute, second)
        uniq: set[tuple[int, int, int]] = set(_parse_time(str(t)) for t in times)
        sorted_times = sorted(uniq)

        per_time_triggers = []
        for h, m, s in sorted_times:
            per_time_triggers.append(
                CronTrigger(
                    second=s,
                    minute=m,
                    hour=h,
                    day_of_week=dtdef.get("day_of_week"),
                    timezone=tzinfo,  # defaults to scheduler tz if not provided
                )
            )

        return per_time_triggers[0] if len(per_time_triggers) == 1 else OrTrigger(per_time_triggers)

    # Unreachable
    raise ValueError("unsupported trigger kind")


def _add_job(scheduler: BackgroundScheduler, spec: JobSpec) -> None:
    """
    Register the APScheduler job with a wrapper that:

      - Logs start/finish + duration
      - Writes structured activity log
      - Executes the module via ``runner.run_module_once()`` with:
            • ``kwargs`` (module-specific args)
            • ``send_email`` (default: True)
            • ``timeout_sec`` (optional)
            • ``email_to``, ``email_cc``, ``email_bcc``, ``subject`` (top-level, from config)
            • ``trigger_type="scheduled"``
            • ``job_context`` (job metadata)
      - All email fields are sourced from ``JobSpec`` (resolved at config load time)
      - No fallback to ``kwargs`` for email fields — keeps module args clean
    """

    def _job_wrapper():
        started = _time.monotonic()
        LOG.info("Job[%s] starting (module=%s)", spec.id, spec.module)

        call_kwargs_rich = {
            "kwargs": dict(spec.kwargs or {}),
            "send_email": spec.send_email if spec.send_email is not None else True,
            "timeout_sec": spec.timeout_sec,
            "trigger_type": "scheduled",
            "job_context": _build_job_context(spec),
            "email_to": spec.email_to,
            "cc": spec.email_cc,
            "bcc": spec.email_bcc,
            "subject": spec.subject,
        }

        try:
            result = runner.run_module_once(spec.module, **call_kwargs_rich)
        except Exception:
            LOG.exception("Job[%s] raised an exception.", spec.id)
            _write_activity(spec, status="error", duration_s=_time.monotonic() - started)
            return

        duration = _time.monotonic() - started
        LOG.info("Job[%s] finished in %.3fs", spec.id, duration)
        _write_activity(spec, status="ok", duration_s=duration, result=result)

    # Optional human-friendly preview (toggle with env var)
    try:
        if os.getenv("SCHEDULER_PREVIEW", None) == "1":
            preview = _preview_trigger(
                spec.trigger, scheduler.timezone, count=int(os.getenv("SCHEDULER_PREVIEW_COUNT", "6"))
            )
            print(f"PREVIEW[{spec.id}]:", ", ".join(t.isoformat() for t in preview) if preview else "(none)")
    except Exception as e:
        print(f"PREVIEW[{spec.id}] failed: {e!r}")

    # Add job with per-job policies
    scheduler.add_job(
        func=_job_wrapper,
        trigger=spec.trigger,
        id=spec.id,
        max_instances=spec.max_instances,
        coalesce=spec.coalesce,
        misfire_grace_time=spec.misfire_grace_time,
        replace_existing=True,
    )

    # Log parsed trigger and the engine's computed next_run_time
    job = scheduler.get_job(spec.id)
    try:
        nrt = getattr(job, "next_run_time", None) if job else None
        if nrt:
            LOG.info(
                "Registered job[%s] (module=%s) next_run_time=%s",
                spec.id,
                spec.module,
                getattr(nrt, "isoformat", lambda: str(nrt))(),
            )
    except Exception:
        # Some APScheduler variants don't expose next_run_time; don't fail on logging.
        LOG.debug("Could not read next_run_time for job[%s]", spec.id, exc_info=True)
    LOG.debug(
        "Registered job[%s] (module=%s, summary=%r, trigger=%s, "
        "max_instances=%s, coalesce=%s, misfire_grace_time=%s)",
        spec.id,
        spec.module,
        spec.summary,
        spec.trigger,
        spec.max_instances,
        spec.coalesce,
        spec.misfire_grace_time,
    )


def _write_activity(spec: JobSpec, status: str, duration_s: float, result: Any = None) -> None:
    """Best-effort JSONL-ish activity logging; non-fatal on errors."""
    try:
        write_activity_log({
            "ts": __import__("datetime").datetime.now().__str__(),
            "source": "scheduler",
            "event": "job_run",
            "fields": {
                "job_id": spec.id,
                "module": spec.module,
                "status": status,
                "duration_ms": int(duration_s * 1000),
                "summary": spec.summary,
                "result_type": type(result).__name__ if result is not None else None,
            },
        })
    except Exception:
        LOG.debug("write_activity_log failed for job[%s]", spec.id, exc_info=True)


def _require(d: dict[str, Any], key: str) -> Any:
    if key not in d or d[key] in (None, ""):
        raise ValueError(f"Missing required key: {key}")
    return d[key]


def _int_or(v: Any, default: int | None) -> int | None:
    """Return int(v) or default if v is None/invalid (lenient for config)."""
    try:
        return int(v) if v is not None else default
    except Exception:
        return default


def _build_job_context(spec: JobSpec) -> dict:
    # Keep it small and predictable; runner can enrich as needed.
    # You can add more fields later (e.g., next_run_time) without breaking callers.
    from datetime import datetime, timezone

    return {
        "job_id": spec.id,
        "module": spec.module,
        "now_iso": datetime.now(timezone.utc).isoformat(),
    }
