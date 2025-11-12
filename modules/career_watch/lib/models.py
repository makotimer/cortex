from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Posting:
    """
    A single job posting as returned by scrapers (pre-dedupe).
    Dedupe is performed externally against SQLite on (source, person_env, title, url).
    """

    source: str  # stable label like "lever:tenant" or "workday:org"
    person_env: str  # e.g., "The Archivest"
    title: str
    url: str


@dataclass
class ScrapeResult:
    """
    Result bundle produced by a single scraper (one per 'kind').
    - items: all postings found by that scraper (NOT filtered for 'new').
    - errors: any non-fatal issues the scraper decided to surface.
    """

    source: str
    items: list[Posting] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
