# service/logging_utils.py
from __future__ import annotations

import contextlib
import datetime as _dt
import json
import os
import socket
from collections.abc import Iterable
from typing import Any

# ---- Configuration (env-driven, with sensible defaults) ---------------------

# Base directory for logs (mounted volume recommended, e.g., ./local/logs)
_LOG_DIR = os.getenv("LOG_DIR", "/app/local/logs")

# Prefixes for activity and error logs
_ACTIVITY_PREFIX = os.getenv("ACTIVITY_LOG_PREFIX", "activity")
_ERROR_PREFIX = os.getenv("ERROR_LOG_PREFIX", "error")

# Optional size-based rotation (bytes). If <=0, size-based rotation is disabled.
# Date-based rotation is always enabled via YYYY-MM-DD filenames.
_MAX_BYTES = int(os.getenv("ACTIVITY_LOG_MAX_BYTES", "0"))

# A minimal set of keys/substrings to redact (case-insensitive, substring match)
_DEFAULT_REDACT_KEYS = {
    "password",
    "token",
    "apikey",
    "api_key",
    "secret",
    "smtp_",
    "bridge_password",
    "authorization",
    "cookie",
    "set-cookie",
}

# Host + process metadata (fixed per-process)
_HOSTNAME = socket.gethostname()
_PID = os.getpid()


# ---- Public API --------------------------------------------------------------


def write_activity_log(record: dict[str, Any]) -> None:
    """
    Persist a single structured activity record (JSON-safe).
    Must be fast, non-blocking (within reason), and robust.

    May raise on unrecoverable I/O/serialization errors.
    Should never mutate the passed-in dict.
    """
    _write_jsonl(_log_path_for_today(_ACTIVITY_PREFIX), record)


# Nice-to-have helpers


def write_error_log(record: dict[str, Any]) -> None:
    """Persist a single structured error record (JSON-safe), parallel to activity log."""
    _write_jsonl(_log_path_for_today(_ERROR_PREFIX), record)


def get_activity_log_path() -> str:
    """Return the current day's activity log path (YYYY-MM-DD.jsonl)."""
    return _log_path_for_today(_ACTIVITY_PREFIX)


def redact(record: dict[str, Any], keys: set[str] | None = None) -> dict[str, Any]:
    """
    Produce a redacted deep copy of `record` by scrubbing values whose KEYS
    contain any of the substrings in `keys` (case-insensitive). Does not mutate input.
    """
    return _redact_deep(record, keys or _DEFAULT_REDACT_KEYS)


# ---- Internal helpers --------------------------------------------------------


def _log_path_for_today(prefix: str) -> str:
    today = _dt.date.today().isoformat()  # YYYY-MM-DD
    log_dir = os.path.join(_LOG_DIR)
    return os.path.join(log_dir, f"{prefix}-{today}.jsonl")


def _ensure_dir(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)


def _should_rotate_size(path: str) -> bool:
    if _MAX_BYTES <= 0:
        return False
    try:
        return os.path.getsize(path) >= _MAX_BYTES
    except FileNotFoundError:
        return False


def _rotate_file_if_needed(path: str) -> None:
    """
    Rotate current file if size exceeds _MAX_BYTES. Date rotation is inherent
    via filename per day; this only handles size-based rotation without losing data.
    """
    if not _should_rotate_size(path):
        return
    # Suffix with timestamp to avoid collisions.
    ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    rotated = f"{path}.{ts}"
    with contextlib.suppress(FileNotFoundError):
        # Atomic rename on POSIX
        os.replace(path, rotated)


def _json_dumps(obj: Any) -> str:
    # Compact, UTF-8, JSON-safe (assumes upstream sanitized values are JSON-serializable)
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _safe_bearer_scrub(value: str) -> str:
    """
    If a string looks like an Authorization header ("Bearer <token>") or similar,
    scrub the token part. Keeps the scheme for usefulness.
    """
    lower = value.lower()
    if "bearer " in lower:
        # Preserve scheme, replace the rest.
        try:
            scheme, _ = value.split(" ", 1)
        except ValueError:
            return "***REDACTED***"
        return f"{scheme} ***REDACTED***"
    return value


def _key_matches(name: str, patterns: Iterable[str]) -> bool:
    n = name.lower()
    return any(pat in n for pat in patterns)


def _redact_deep(value: Any, patterns: Iterable[str]) -> Any:
    """
    Deep-copy and redact dict/list structures. Keys that match patterns have their
    values replaced with "***REDACTED***". Strings that look like bearer tokens are scrubbed.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and _key_matches(k, patterns):
                out[k] = "***REDACTED***"
            else:
                out[k] = _redact_deep(v, patterns)
        return out
    if isinstance(value, list):
        return [_redact_deep(v, patterns) for v in value]
    if isinstance(value, tuple):
        return tuple(_redact_deep(v, patterns) for v in value)
    if isinstance(value, str):
        return _safe_bearer_scrub(value)
    # Primitive (int/float/bool/None) or other JSON-safe types pass through.
    return value


def _with_metadata(copy_of_record: dict[str, Any]) -> dict[str, Any]:
    """
    Add minimal immutable metadata without mutating the passed-in structure.
    """
    # Shallow copy already made by caller; we add a tiny dict to avoid clobbering.
    meta_host = copy_of_record.get("_meta", {})
    if not isinstance(meta_host, dict):
        meta_host = {}
    meta_host = {
        **meta_host,
        "host": _HOSTNAME,
        "pid": _PID,
    }
    out = dict(copy_of_record)
    out["_meta"] = meta_host
    return out


def _write_jsonl(path: str, record: dict[str, Any]) -> None:
    """
    Core writer:
      - makes a deep redacted copy
      - enriches with host/pid
      - rotates by size (optional)
      - appends a single line atomically (POSIX O_APPEND)
      - retries once on transient OSError
    """
    _ensure_dir(path)

    # Rotate by size if configured (date-based rotation happens automatically via filename).
    _rotate_file_if_needed(path)

    # Redact and add metadata; do not mutate caller's dict.
    redacted = _redact_deep(record, _DEFAULT_REDACT_KEYS)
    payload = _with_metadata(redacted)

    # Serialize first so any serialization errors happen before file ops.
    try:
        line = _json_dumps(payload) + "\n"
        data = line.encode("utf-8")
    except Exception:  # JSON errors etc.
        # Bubble up: caller (runner.py) has a fallback path.
        raise

    # Fast, atomic append using low-level os.open with O_APPEND.
    flags = os.O_CREAT | os.O_APPEND | os.O_WRONLY

    # 0o644 typical; umask may reduce this further
    def _append_once() -> None:
        fd = os.open(path, flags, 0o644)
        try:
            os.write(fd, data)  # Single write; O_APPEND ensures atomicity on POSIX.
        finally:
            os.close(fd)

    try:
        _append_once()
    except OSError:
        # Retry once (transient issues): re-check dir and try again.
        _ensure_dir(path)
        _rotate_file_if_needed(path)
        _append_once()
