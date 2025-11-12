from __future__ import annotations

from typing import Any

from .lib.config import Settings
from .lib.engine import run_once as _run_engine
from .lib.logging_bridge import activity as log_activity


def run(**kwargs: Any) -> tuple[str, dict] | None:
    """
    Entry point for the 'career_watch' module.

    Accepts kwargs (from scheduler/runner), including:
      person_env: str = "SCRAPER_USER_1"
      sqlite_path: str = "/app/local/state/careerwatch.db"
      max_threads: int = 8
      skip_network: bool = False

      # Selection (file-based)
      groups_path: Optional[str]  # override default file path if needed

      # Special-run flags:
      email_all_even_if_seen: bool = False
      ingest_only_no_email: bool = False

    Returns:
      - None (no email should be sent), or
      - (html: str, meta: dict) - runner will coerce and send if configured.
    """
    # Build validated settings from env + kwargs
    settings = Settings.from_env_and_kwargs(kwargs)

    # Log a small start record (structured; no prints)
    log_activity({
        "component": "career_watch.main",
        "op": "start",
        "person": settings.person_env,
        "kinds": sorted(settings.group_by_kind().keys()),
        "flags": {
            "email_all_even_if_seen": settings.email_all_even_if_seen,
            "ingest_only_no_email": settings.ingest_only_no_email,
            "skip_network": settings.skip_network,
        },
    })

    # Delegate to engine: may return None or (html, meta)
    result = _run_engine(settings)
    return result
