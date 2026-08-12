"""event_watch — scrape event sources and publish them onto events:<site>.

The first source is the Bryan + College Station Public Library System's Tockify
calendar. It is a family, not a one-off: source-specific parts sit behind the
scraper interface in ``lib/scrapers/``.
"""
from __future__ import annotations

from typing import Any

from .lib import logging_bridge
from .lib.config import Settings
from .lib.engine import run_once


def run(**kwargs: Any) -> str | tuple[str, dict] | None:
    """Cortex module entry point.

    Returns None on a clean run (no email) and a summary when something wants a
    human — an unmappable venue, a failed fetch, or a tripped disappearance
    guard.
    """
    settings = Settings.from_env_and_kwargs(kwargs)
    logging_bridge.activity({
        "component": "event_watch.main", "op": "start",
        "kinds": settings.kinds, "site": settings.site,
        "window_days": settings.window_days,
        "flags": {"dry_run": settings.dry_run, "skip_network": settings.skip_network},
    })
    return run_once(settings)
