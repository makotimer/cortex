from __future__ import annotations

from datetime import date, datetime

try:
    from dateutil import tz as _tz
except Exception:
    _tz = None


def resolve_date(for_date: str | None, tz_name: str) -> date:
    if for_date:
        return datetime.strptime(for_date, "%Y-%m-%d").date()
    if _tz:
        return datetime.now(_tz.gettz(tz_name) or _tz.UTC).date()
    return datetime.now().date()


def days_since(start: date, target: date) -> int:
    return (target - start).days
