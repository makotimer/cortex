"""Pure helpers shared by more than one source.

Nothing here may perform I/O, read the clock, or touch state. Per-source rules
live next to their fetch code in ``scrapers/<source>.py``; only genuinely common
helpers belong here.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")
_WS = re.compile(r"[ \t\r\f\v]+")


def slugify(text: str) -> str:
    """Lowercase ASCII slug. Stable across runs — it is part of place identity."""
    norm = unicodedata.normalize("NFKD", text or "")
    ascii_only = norm.encode("ascii", "ignore").decode("ascii")
    return _SLUG_STRIP.sub("-", ascii_only.lower()).strip("-")


def clean_text(text: str | None) -> str | None:
    """Collapse runs of spaces and trim, preserving paragraph breaks."""
    if text is None:
        return None
    lines = [_WS.sub(" ", line).strip() for line in str(text).splitlines()]
    out = "\n".join(lines).strip()
    return out or None


def local_iso(millis: int, tzid: str) -> str:
    """Epoch milliseconds -> wall-clock local ISO 8601, no offset suffix.

    The contract wants wall-clock local plus a separate IANA ``timezone``; the
    worker converts to UTC on write. Pure: ``ZoneInfo`` is a table lookup, not a
    clock read.
    """
    dt = datetime.fromtimestamp(millis / 1000, tz=ZoneInfo(tzid))
    return dt.replace(tzinfo=None).isoformat()
