# career_watch/scrapers/__init__.py
# Importing scrapers triggers @register, populating scrapers/registry.py's _REGISTRY.
from __future__ import annotations

from .avature import AvatureScraper
from .bae import BaePhenomAPIScraper
from .boeing import BoeingScraper
from .icims import IcimsScraper
from .lever import LeverScraper
from .workday_cxs import WorkdayCxSScraper

__all__ = [
    "AvatureScraper",
    "BaePhenomAPIScraper",
    "BoeingScraper",
    "IcimsScraper",
    "LeverScraper",
    "WorkdayCxSScraper",
]
