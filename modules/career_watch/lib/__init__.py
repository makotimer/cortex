# modules/career_watch/lib/__init__.py
from __future__ import annotations

# Re-export commonly-used types for convenience
from .config import ConfigError, ScraperConfig, Settings
from .engine import run_once
from .models import Posting, ScrapeResult

# Ensure built-in scrapers register themselves, but don't fail package import
# if an optional scraper can't import for any reason.
try:
    from .scrapers import stub as _stub
except Exception:  # pragma: no cover
    _stub = None

try:
    from .scrapers import bae as _bae
    from .scrapers import boeing as _boeing
    from .scrapers import lever as _lever
    from .scrapers import workday_cxs as _workday_cxs
except Exception:  # pragma: no cover
    _lever = None
    _boeing = None
    _bae = None
    _workday_cxs = None

__all__ = [
    "ConfigError",
    "Posting",
    "ScrapeResult",
    "ScraperConfig",
    "Settings",
    "run_once",
]
