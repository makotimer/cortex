from __future__ import annotations

import html
import os
from datetime import datetime, timezone
from typing import Any, Optional

_HTML_ESCAPE_QUOTE = True  # keep quotes escaped for attributes


def esc(s: str | None) -> str:
    """
    Escape text for HTML contexts (titles, URLs). Do NOT wrap or add tags.
    """
    if s is None:
        return ""
    return html.escape(str(s), quote=_HTML_ESCAPE_QUOTE)


def truthy(v: Any) -> bool:
    """
    Normalize common truthy inputs from env/kwargs.
    Accepts bools or strings like: '1', 'true', 'yes', 'on'.
    """
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    if isinstance(v, (int, float)):
        return v != 0
    s = str(v).strip().lower()
    return s in {"1", "true", "yes", "on", "y", "t"}


def now_iso() -> str:
    """
    UTC ISO-8601 timestamp with 'Z' suffix.
    """
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def getenv_str(name: str, default: str | None = None) -> str | None:
    """
    Typed wrapper for environment access.
    """
    val = os.getenv(name)
    return val if val is not None else default
