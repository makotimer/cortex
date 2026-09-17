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

# Age phrasings as sources actually write them. Each pattern needs an age
# cue ("ages", "years", "and under", "birth to") so a bare "10-5" — opening
# hours, a score, a price — never reads as a range. The dash class covers the
# hyphen, en dash and em dash real descriptions use interchangeably.
_DASH = r"\s*(?:[-\u2013\u2014]|to|through)\s*"
_NOT_MONEY = r"(?<![$\d.])"
_AGE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"\bages?\s*(\d{{1,2}}){_DASH}(\d{{1,2}})\b", re.I), "range"),
    (re.compile(rf"{_NOT_MONEY}(\d{{1,2}}){_DASH}(\d{{1,2}})\s*(?:years?|yrs?)\b", re.I), "range"),
    (re.compile(rf"\bbirth{_DASH}(\d{{1,2}})\b", re.I), "to"),
    (re.compile(rf"{_NOT_MONEY}(\d{{1,2}})\s*(?:and|&)\s*(?:under|younger)\b", re.I), "to"),
    (re.compile(r"\bages?\s*under\s*(\d{1,2})\b", re.I), "below"),
    (re.compile(r"\bages?\s*(\d{1,2})\s*(?:\+|and\s*(?:up|older|above)\b)", re.I), "from"),
]


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


def ages_from(text: str | None) -> tuple[int, int | None] | None:
    """A stated age range, or None when the text does not state one.

    The contract allows ``age_min``/``age_max`` **only when the source says
    so**; this reads the prose forms a source uses ("ages 0-5", "5 and under",
    "birth to 5", "ages 3 and up"). An open-ended phrase yields ``(min, None)``.
    Nothing is inferred from an audience band — a band is not an age range.
    """
    if not text:
        return None
    for pattern, kind in _AGE_PATTERNS:
        match = pattern.search(text)
        if not match:
            continue
        if kind == "range":
            lo, hi = int(match.group(1)), int(match.group(2))
            return (lo, hi) if lo <= hi else None
        n = int(match.group(1))
        if kind == "to":
            return (0, n)
        if kind == "below":
            return (0, n - 1) if n > 0 else None
        return (n, None)
    return None
