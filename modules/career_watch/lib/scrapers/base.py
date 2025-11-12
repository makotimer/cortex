from __future__ import annotations

from abc import ABC, abstractmethod

from ..config import ScraperConfig
from ..models import ScrapeResult


class ScraperError(Exception):
    """Base exception for scraper failures."""


class BaseScraper(ABC):
    """
    Abstract scraper interface.

    One instance processes a list of specs (companies/tenants/etc.) for a given 'kind'
    SEQUENTIALLY, but the engine may run multiple scrapers (different kinds) in parallel.

    Contract:
      - run(person_env, specs, skip_network) returns a LIST of ScrapeResult,
        typically one ScrapeResult PER spec/source.
      - Do NOT send email, print, or mutate global state.
      - Return *all* postings found (dedupe happens upstream in the DB layer).
    """

    # Concrete subclasses MUST set this to a stable string, e.g. "workday", "lever", "greenhouse", "stub"
    kind: str = ""

    @abstractmethod
    def run(
        self,
        person_env: str,
        specs: list[ScraperConfig],
        *,
        skip_network: bool,
    ) -> list[ScrapeResult]:
        """
        Execute the scraper for all provided specs SEQUENTIALLY.

        Args:
            person_env: Resolved person name for this run (e.g., "The Archivist")
            specs:  ScraperConfig objects of the SAME kind (self.kind)
            skip_network: If True, avoid real HTTP calls (helpful for tests)

        Returns:
            List[ScrapeResult] - one result per spec/source is typical.
        """
        raise NotImplementedError
