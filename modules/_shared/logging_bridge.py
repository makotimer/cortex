"""Structured activity/error logging for modules, with secret redaction.

The implementation was duplicated per module; ``event_watch`` made that a second
copy, so it lives here parameterized by module name. Bind it once per module:

    from modules._shared.logging_bridge import make

    activity, error = make("modules.event_watch", "event_watch")

``career_watch`` still carries its own copy — migrating it is a separate change,
deliberately not bundled with live-scraper edits.
"""
from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from typing import Any

_logging_backend: Any = None
try:
    from service import logging_utils as _svc_logging

    _logging_backend = _svc_logging
except Exception:
    try:
        import logging_utils as _root_logging

        _logging_backend = _root_logging
    except Exception:
        _logging_backend = None

_REDACT_KEYS = {
    "password", "token", "apikey", "api_key", "secret", "smtp_password",
    "smtp_token", "bridge_password", "authorization", "auth", "bearer",
}


def _redact_record(record: dict[str, Any]) -> dict[str, Any]:
    """Shallow-copy and redact obvious secret-like fields at top level."""
    redacted = copy.copy(record)
    for k in list(redacted.keys()):
        lk = str(k).lower()
        if lk in _REDACT_KEYS or lk.startswith("smtp_") or lk.endswith("_secret"):
            redacted[k] = "***REDACTED***"
    return redacted


def make(
    module_name: str, logger_prefix: str
) -> tuple[Callable[[dict[str, Any]], None], Callable[[dict[str, Any]], None]]:
    """Return (activity, error) writers bound to one module's names."""

    def activity(record: dict[str, Any]) -> None:
        payload = _redact_record(record)
        payload.setdefault("module", module_name)
        if _logging_backend and hasattr(_logging_backend, "write_activity_log"):
            try:
                _logging_backend.write_activity_log(payload)
                return
            except Exception:
                pass
        logging.getLogger(f"{logger_prefix}.activity").info(payload)

    def error(record: dict[str, Any]) -> None:
        payload = _redact_record(record)
        if _logging_backend and hasattr(_logging_backend, "write_error_log"):
            try:
                _logging_backend.write_error_log(payload)
                return
            except Exception:
                pass
        logging.getLogger(f"{logger_prefix}.error").error(payload)

    return activity, error
