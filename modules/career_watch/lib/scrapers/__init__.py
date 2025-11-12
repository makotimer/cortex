# career_watch/scrapers/__init__.py
from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from .avature import AvatureScraper
from .bae import BaePhenomAPIScraper
from .boeing import BoeingScraper
from .icims import IcimsScraper
from .lever import LeverScraper
from .workday_cxs import WorkdayCxSScraper

REGISTRY = {
    "avature": AvatureScraper,
    "bae": BaePhenomAPIScraper,
    "boeing": BoeingScraper,
    "icims": IcimsScraper,
    "lever": LeverScraper,
    "workday_cxs": WorkdayCxSScraper,
}


def run_source(source_cfg: dict[str, Any]) -> Iterable:
    """
    Minimal dispatcher used by modules/career_watch to execute a single source.
    Expects a dict with at least {"type": "..."} and optional {"list_url": "..."}.
    """
    typ = (source_cfg.get("type") or "").lower()
    cls = REGISTRY.get(typ)
    if not cls:
        raise ValueError(f"Unknown source type: {typ!r}")
    scraper = cls(list_url=source_cfg.get("list_url"))
    return scraper.fetch(query=source_cfg.get("query"))
