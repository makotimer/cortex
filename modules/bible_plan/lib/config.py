from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    plan_start: str
    tz_name: str
    prayer_count: int


def _int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def load() -> Settings:
    return Settings(
        plan_start=os.getenv("BIBLE_PLAN_START", "2025-09-13"),
        tz_name=os.getenv("TZ", "UTC"),
        prayer_count=max(1, _int(os.getenv("BIBLE_PLAN_PRAYER_COUNT"), 3)),
    )
