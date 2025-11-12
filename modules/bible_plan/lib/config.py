from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    plan_start: str
    tz_name: str
    skip_probe: bool
    enable_llm: bool


def _truthy(s: str | None) -> bool:
    return (s or "").strip().lower() in {"1", "true", "yes", "on"}


def load() -> Settings:
    return Settings(
        plan_start=os.getenv("BIBLE_PLAN_START", "2025-09-06"),
        tz_name=os.getenv("TZ", "UTC"),
        skip_probe=_truthy(os.getenv("BIBLE_PLAN_SKIP_PROBE", "1")),
        enable_llm=_truthy(os.getenv("BIBLE_PLAN_ENABLE_LLM", "")),
    )
