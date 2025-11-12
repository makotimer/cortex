from __future__ import annotations

from typing import Any

from ..config import ScraperConfig
from ..models import Posting, ScrapeResult
from .base import BaseScraper
from .registry import register


@register
class StubScraper(BaseScraper):
    """
    A zero-network scraper used for tests and dry-runs.

    Each spec.params may contain:
      - items: list[{title:str, url:str}]  # REQUIRED for producing postings
      - errors: list[str]                  # OPTIONAL, propagate to ScrapeResult
      - delay_ms: int                      # OPTIONAL, simulated per-spec latency (ignored here)

    Behavior:
      - Iterates specs sequentially (per module contract).
      - Emits one ScrapeResult PER spec (source) so the engine can group by source.
      - Respects 'skip_network' (but we perform no I/O anyway).
    """

    kind = "stub"

    def run(
        self,
        person_env: str,
        specs: list[ScraperConfig],
        *,
        skip_network: bool,
    ) -> list[ScrapeResult]:
        results: list[ScrapeResult] = []

        for spec in specs:
            params: dict[str, Any] = dict(spec.params or {})
            raw_items = params.get("items") or []
            if not isinstance(raw_items, list):
                raw_items = []

            postings: list[Posting] = []
            for item in raw_items:
                if not isinstance(item, dict):
                    continue
                title = str(item.get("title") or "").strip()
                url = str(item.get("url") or "").strip()
                if not url:
                    continue  # URL is required to be meaningful
                postings.append(
                    Posting(
                        source=spec.source,
                        person_env=person_env,
                        title=title or "(no title)",
                        url=url,
                    )
                )

            errors = params.get("errors") or []
            if not isinstance(errors, list):
                errors = [str(errors)]

            results.append(
                ScrapeResult(
                    source=spec.source,
                    items=postings,
                    errors=[str(e) for e in errors],
                )
            )

        return results
