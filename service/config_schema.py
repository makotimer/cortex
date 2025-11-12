# service/config_schema.py
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover
    yaml = None  # YAML optional


class ConfigError(ValueError):
    """Raised when the config is invalid."""


@dataclass
class _LoadResult:
    """Internal convenience container (not required by callers)."""

    cfg: dict[str, Any]
    source: str


_EMAIL_FIELDS = ("email_to", "email_cc", "email_bcc")
_EMAIL_ENV_FIELDS = {
    "email_to": "email_to_env",
    "email_cc": "email_cc_env",
    "email_bcc": "email_bcc_env",
}
_TRIGGER_FIELDS = ("cron", "interval", "date", "daily_time")
_DAILY_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def load_config(path: str | None = None) -> dict[str, Any]:
    """
    Load the service configuration.

    Resolution order:
      1) Explicit `path` argument (if provided)
      2) os.environ['CONFIG_PATH'] (if set)
      3) Internal default (empty config with empty jobs list)

    Returns:
        dict with at least {"jobs": [...]}. May also include "timezone".
    """
    resolved_path = path or os.environ.get("CONFIG_PATH")
    if not resolved_path:
        logger.info("CONFIG_PATH not provided; using empty default config.")
        cfg: dict[str, Any] = {"jobs": []}
        _apply_top_level_defaults(cfg)
        return cfg

    lr = _read_any(resolved_path)
    cfg = lr.cfg

    # Normalize top-level fields and validate if validate(...) is present.
    _apply_top_level_defaults(cfg)
    return cfg


def validate(cfg: dict[str, Any]) -> None:
    """
    Validate the configuration. Raise ConfigError on any problem.
    No prints, no sys.exit().
    """
    if not isinstance(cfg, dict):
        raise ConfigError("Config must be a dict.")

    # jobs must be present and list
    jobs = cfg.get("jobs")
    if jobs is None:
        raise ConfigError("Missing required top-level 'jobs' list.")
    if not isinstance(jobs, list):
        raise ConfigError("'jobs' must be a list.")

    # timezone is optional; if present must be str
    tz = cfg.get("timezone")
    if tz is not None and not isinstance(tz, str):
        raise ConfigError("'timezone' must be a string if provided.")

    seen_ids: set[str] = set()
    for idx, job in enumerate(jobs):
        if not isinstance(job, dict):
            raise ConfigError(f"Job at index {idx} must be an object/dict.")

        # module (required, str)
        module = job.get("module")
        if not isinstance(module, str) or not module.strip():
            raise ConfigError(f"Job {idx}: 'module' is required and must be a non-empty string.")

        # id | name | module → id
        job_id = _derive_job_id(job, idx)
        if not job_id:
            raise ConfigError(f"Job {idx}: could not derive a valid job id.")
        if job_id in seen_ids:
            raise ConfigError(f"Duplicate job id '{job_id}'.")
        seen_ids.add(job_id)

        # Exactly one trigger among cron | interval | date | daily_time
        # Trigger may be nested under "trigger": {...} or at job top-level.
        trigger_container = job.get("trigger", job)
        if "trigger" in job and not isinstance(trigger_container, dict):
            raise ConfigError(f"Job '{job_id}': 'trigger' must be an object when present.")

        present_triggers = [k for k in _TRIGGER_FIELDS if k in trigger_container]
        # Guard against mixed usage (nested + top-level simultaneously)
        if "trigger" in job:
            also_top_level = [k for k in _TRIGGER_FIELDS if k in job and k != "trigger"]
            if also_top_level:
                raise ConfigError(
                    f"Job '{job_id}': do not mix top-level triggers {also_top_level} with nested 'trigger'."
                )

        if len(present_triggers) != 1:
            raise ConfigError(f"Job '{job_id}': exactly one trigger required among {', '.join(_TRIGGER_FIELDS)}.")

        # Light type checks for common mistakes
        trig_key = present_triggers[0]
        trig_val = trigger_container[trig_key]
        if trig_key == "interval" and not isinstance(trig_val, dict):
            raise ConfigError(f"Job '{job_id}': interval must be an object of time kwargs.")
        if trig_key == "cron" and not (isinstance(trig_val, (str, dict))):
            raise ConfigError(f"Job '{job_id}': cron must be a crontab string or an object.")
        if trig_key == "date" and not isinstance(trig_val, (str,)):
            # APScheduler also accepts datetime, but string is the common case
            raise ConfigError(f"Job '{job_id}': date must be an ISO-8601 string.")
        if trig_key == "daily_time" and not isinstance(trig_val, str):
            raise ConfigError(f"Job '{job_id}': daily_time must be 'HH:MM' string.")

        # Validate trigger content
        if "cron" in job:
            _require_type(job, "cron", dict, f"Job '{job_id}': 'cron' must be an object with cron fields.")
        elif "interval" in job:
            _require_type(
                job, "interval", dict, f"Job '{job_id}': 'interval' must be an object with interval fields."
            )
            # Optional: make sure any provided fields parse as ints >= 0
            _validate_int_map(job["interval"], job_id, allow_zero=True)
        elif "date" in job:
            # Leave to scheduler to parse the date string; ensure it's a non-empty string
            if not isinstance(job["date"], str) or not job["date"].strip():
                raise ConfigError(f"Job '{job_id}': 'date' must be a non-empty ISO-like string.")
        elif "daily_time" in job:
            _validate_daily_time(job["daily_time"], job_id)

        # Optional fields
        _require_optional_bool(job, "coalesce", job_id)
        _require_optional_bool(job, "send_email", job_id)

        # Optional ints (timeout_sec, max_instances, misfire_grace_time)
        _require_optional_int(job, "timeout_sec", job_id, allow_zero=True)
        _require_optional_int(job, "max_instances", job_id, allow_zero=False)
        _require_optional_int(job, "misfire_grace_time", job_id, allow_zero=True)

        # kwargs: dict if present
        if "kwargs" in job and not isinstance(job["kwargs"], dict):
            raise ConfigError(f"Job '{job_id}': 'kwargs' must be a dict if provided.")

        # Email fields normalization + basic validation
        for f in _EMAIL_FIELDS:
            if f in job:
                _normalize_email_field(job, f, job_id)

        # Optional strings
        for opt_str in ("subject", "summary", "description"):
            if opt_str in job and not isinstance(job[opt_str], str):
                raise ConfigError(f"Job '{job_id}': '{opt_str}' must be a string if provided.")


def _apply_top_level_defaults(cfg: dict[str, Any]) -> None:
    # Ensure jobs key exists
    if "jobs" not in cfg or not isinstance(cfg["jobs"], list):
        cfg["jobs"] = []

    # Resolve timezone now so scheduler can use cfg['timezone']
    tz = cfg.get("timezone")
    if not isinstance(tz, str) or not tz.strip():
        cfg["timezone"] = os.environ.get("TZ", "UTC")

    # Normalize/prepare each job (do not mutate caller's objects outside expected normalization)
    normalized_jobs: list[dict[str, Any]] = []
    for idx, job in enumerate(cfg["jobs"]):
        if not isinstance(job, dict):
            raise ConfigError(f"Job at index {idx} must be an object/dict.")

        job_copy = dict(job)  # shallow copy

        # Derive id and set it (so scheduler can rely on it)
        job_copy["id"] = _derive_job_id(job_copy, idx)

        # ──────────────────────────────────────────────────────────────
        #  Resolve email_*_env → email_*  AND hide the secret name
        # ──────────────────────────────────────────────────────────────
        for target, env_key in _EMAIL_ENV_FIELDS.items():
            if env_key in job_copy:
                raw = job_copy[env_key]
                if isinstance(raw, str):
                    value = os.getenv(raw.strip(), "")
                    emails = [e.strip() for e in value.split(",") if e.strip()]
                    job_copy[target] = emails

                del job_copy[env_key]
        # ──────────────────────────────────────────────────────────────

        # Normalize booleans/ints if provided (best-effort)
        for b in ("coalesce", "send_email"):
            if b in job_copy:
                job_copy[b] = _to_bool(job_copy[b], field=b, job_id=job_copy["id"])

        for n, allow_zero in (("timeout_sec", True), ("max_instances", False), ("misfire_grace_time", True)):
            if n in job_copy:
                job_copy[n] = _to_int(job_copy[n], field=n, job_id=job_copy["id"], allow_zero=allow_zero)

        # Email fields to list[str]
        for f in _EMAIL_FIELDS:
            if f in job_copy:
                job_copy[f] = _as_str_list(job_copy[f], field=f, job_id=job_copy["id"])

        # daily_time: keep as original validated "HH:MM" string for scheduler
        # Triggers: leave as-is; scheduler will interpret

        normalized_jobs.append(job_copy)

    cfg["jobs"] = normalized_jobs


def _derive_job_id(job: dict[str, Any], idx: int) -> str:
    # id | name | module → id
    for key in ("id", "name", "module"):
        v = job.get(key)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return f"job_{idx}"


def _validate_daily_time(dt: Any, job_id: str) -> None:
    if not isinstance(dt, str):
        raise ConfigError(f"Job '{job_id}': 'daily_time' must be a string like 'HH:MM'.")
    m = _DAILY_TIME_RE.match(dt.strip())
    if not m:
        raise ConfigError(f"Job '{job_id}': 'daily_time' must match HH:MM (24h).")
    hour, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ConfigError(f"Job '{job_id}': 'daily_time' hour/minute out of range (00:00..23:59).")


def _require_type(obj: dict[str, Any], field: str, t: type, msg: str) -> None:
    if not isinstance(obj.get(field), t):
        raise ConfigError(msg)


def _require_optional_bool(job: dict[str, Any], field: str, job_id: str) -> None:
    if field in job:
        _to_bool(job[field], field=field, job_id=job_id)  # will raise if invalid


def _require_optional_int(job: dict[str, Any], field: str, job_id: str, *, allow_zero: bool) -> None:
    if field in job:
        _to_int(job[field], field=field, job_id=job_id, allow_zero=allow_zero)  # will raise if invalid


def _validate_int_map(m: dict[str, Any], job_id: str, *, allow_zero: bool) -> None:
    for k, v in m.items():
        _to_int(v, field=f"interval.{k}", job_id=job_id, allow_zero=allow_zero)


def _normalize_email_field(job: dict[str, Any], field: str, job_id: str) -> None:
    job[field] = _as_str_list(job[field], field=field, job_id=job_id)


def _as_str_list(value: Any, *, field: str, job_id: str) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        s = value.strip()
        return [s] if s else []
    if isinstance(value, list):
        out: list[str] = []
        for i, item in enumerate(value):
            if not isinstance(item, str) or not item.strip():
                raise ConfigError(f"Job '{job_id}': {field}[{i}] must be a non-empty string.")
            out.append(item.strip())
        return out
    raise ConfigError(f"Job '{job_id}': '{field}' must be a string or list of strings.")


def _to_bool(value: Any, *, field: str, job_id: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v in {"1", "true", "yes", "on"}:
            return True
        if v in {"0", "false", "no", "off"}:
            return False
    raise ConfigError(f"Job '{job_id}': '{field}' must be a boolean (or boolean-like string).")


def _to_int(value: Any, *, field: str, job_id: str, allow_zero: bool) -> int:
    try:
        iv = int(value)
    except ValueError as err:
        raise ConfigError(f"Job '{job_id}': '{field}' must be an integer.") from err
    if iv < 0 or (iv == 0 and not allow_zero):
        raise ConfigError(f"Job '{job_id}': '{field}' must be >= {'0' if allow_zero else '1'} (got {iv}).")
    return iv


def _read_any(path: str) -> _LoadResult:
    lower = path.lower()
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError as e:
        raise ConfigError(f"Config file not found: {path}") from e
    except OSError as e:
        raise ConfigError(f"Failed to read config file: {path}: {e}") from e

    if lower.endswith(".json"):
        try:
            return _LoadResult(cfg=json.loads(text), source=path)
        except json.JSONDecodeError as e:
            raise ConfigError(f"Invalid JSON in {path}: {e}") from e

    if lower.endswith(".yml") or lower.endswith(".yaml"):
        if yaml is None:
            raise ConfigError("YAML config requested but PyYAML is not installed.")
        try:
            data = yaml.safe_load(text)
            if data is None:
                data = {}
            if not isinstance(data, dict):
                raise ConfigError("Top-level YAML must be a mapping/object.")
            return _LoadResult(cfg=data, source=path)
        except Exception as e:
            raise ConfigError(f"Invalid YAML in {path}: {e}") from e

    # Try JSON as a fallback if extension is unknown
    try:
        return _LoadResult(cfg=json.loads(text), source=path)
    except json.JSONDecodeError:
        pass

    raise ConfigError(f"Unsupported config format for {path}. Use .json or .yml/.yaml (with PyYAML installed).")
