from __future__ import annotations

import copy
import logging
from typing import Any

# Try the preferred location first, then a fallback; default to stdlib logging.
# No prints; this module should be silent on import.
_logging_backend = None
try:
    # Preferred: service package
    from service import logging_utils as _svc_logging  # type: ignore

    _logging_backend = _svc_logging
except Exception:
    try:
        # Fallback: project-root logging_utils.py
        import logging_utils as _root_logging  # type: ignore

        _logging_backend = _root_logging
    except Exception:
        _logging_backend = None

# Keys that should be redacted from structured logs
_REDACT_KEYS = {
    "password",
    "token",
    "apikey",
    "api_key",
    "secret",
    "smtp_password",
    "smtp_token",
    "bridge_password",
    "authorization",
    "auth",
    "bearer",
}


def _redact_record(record: dict[str, Any]) -> dict[str, Any]:
    """
    Shallow-copy record and redact obvious secret-like fields at top level.
    If nested redaction is needed later, we can extend this recursively.
    """
    redacted = copy.copy(record)
    for k in list(redacted.keys()):
        lk = str(k).lower()
        if lk in _REDACT_KEYS or lk.startswith("smtp_") or lk.endswith("_secret"):
            redacted[k] = "***REDACTED***"
    return redacted


def activity(record: dict[str, Any]) -> None:
    """
    Write an activity record to the project's logging utility if available.
    Falls back to stdlib logging as structured info.
    """
    payload = _redact_record(record)
    if _logging_backend and hasattr(_logging_backend, "write_activity_log"):
        try:
            _logging_backend.write_activity_log(payload)  # type: ignore[attr-defined]
            return
        except Exception:
            # Fall through to std logging
            pass
    logging.getLogger("career_watch.activity").info(payload)


def error(record: dict[str, Any]) -> None:
    """
    Write an error record to the project's logging utility if available.
    Falls back to stdlib logging as structured error.
    """
    payload = _redact_record(record)
    if _logging_backend and hasattr(_logging_backend, "write_error_log"):
        try:
            _logging_backend.write_error_log(payload)  # type: ignore[attr-defined]
            return
        except Exception:
            # Fall through to std logging
            pass
    logging.getLogger("career_watch.error").error(payload)
