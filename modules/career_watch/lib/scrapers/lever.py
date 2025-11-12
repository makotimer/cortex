# modules/career_watch/lib/scrapers/lever.py
from __future__ import annotations

import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from urllib.parse import urljoin

from bs4 import BeautifulSoup  # pip install beautifulsoup4 html5lib

from ..config import ScraperConfig
from ..http_client import HttpClient
from ..models import Posting, ScrapeResult
from .base import BaseScraper
from .registry import register


@dataclass(frozen=True)
class _Target:
    list_url: str
    source_label: str  # e.g., "lever:palantir"


def _normalize_targets(raw: object) -> list[_Target]:
    """
    Accept either:
      - [["https://jobs.lever.co/palantir?team=Dev", "lever:palantir"], ...]
      - [{"url": "...", "source": "lever:palantir"}, ...]
    and return a normalized list of _Target.
    """
    out: list[_Target] = []
    if not raw:
        return out
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                url, label = str(item[0]).strip(), str(item[1]).strip()
                if url and label:
                    out.append(_Target(url, label))
            elif isinstance(item, dict):
                url = str(item.get("url") or item.get("list_url") or "").strip()
                label = str(item.get("source") or item.get("source_label") or "").strip()
                if url and label:
                    out.append(_Target(url, label))
    return out


@register
class LeverScraper(BaseScraper):
    """
    General Lever.co scraper.

    Each ScraperConfig.params may include:
      start_urls: list of pairs or objects describing tenants, e.g.
          [
            ["https://jobs.lever.co/palantir?team=Dev", "lever:palantir"],
            ["https://jobs.lever.co/acme?team=Eng",   "lever:acme"]
          ]
          or [{"url":"...","source":"lever:palantir"}, ...]

      delay_seconds: float   # polite pause between tenants (default 3.0)
      query: str | null      # optional substring filter on title (case-insensitive)
      exclude: list[str]     # substrings to filter out of classification (default:
                             # ["on-site", "onsite", "internship"])

    Behavior:
      - Produces ONE ScrapeResult PER tenant (so your email tables group per company).
      - Each Posting.source is the per-tenant label (e.g., "lever:palantir").
    """

    kind = "lever"
    _LEVER_BASE = "https://jobs.lever.co"

    def __init__(self) -> None:
        self._client = HttpClient()

    def run(
        self,
        person_env: str,
        specs: list[ScraperConfig],
        *,
        skip_network: bool,
    ) -> list[ScrapeResult]:
        results: list[ScrapeResult] = []
        if skip_network:
            return results

        for spec in specs:
            params = dict(spec.params or {})

            targets = _normalize_targets(params.get("start_urls"))
            if not targets:
                # If a single list_url was passed, accept that too:
                lu = str(params.get("list_url") or "").strip()
                if lu:
                    targets = [_Target(lu, spec.source)]

            delay = float(params.get("delay_seconds") or 3.0)
            query = str(params.get("query") or "").strip() or None
            exclude_raw = params.get("exclude") or ["on-site", "onsite", "internship"]
            exclude = [str(x).lower() for x in exclude_raw]

            # Iterate *tenants* inside this ScraperConfig, producing per-tenant results.
            for idx, tgt in enumerate(targets):
                if idx > 0 and delay > 0:
                    time.sleep(delay)

                items: list[Posting] = []
                errs: list[str] = []
                try:
                    html = self._client.get_text(tgt.list_url)
                    for title, url in self._parse_list_page(html, query=query, exclude=exclude):
                        items.append(
                            Posting(
                                source=tgt.source_label,  # per-tenant label
                                person_env=person_env,
                                title=title,
                                url=url,
                            )
                        )
                except Exception as e:
                    errs.append(f"{tgt.source_label}: {e!r}")

                # ONE result per tenant so tables group nicely
                results.append(
                    ScrapeResult(
                        source=tgt.source_label,
                        items=items,
                        errors=errs,
                    )
                )
        return results

    # ---- internals ----

    def _parse_list_page(
        self,
        html: str,
        *,
        query: str | None,
        exclude: Sequence[str],
    ) -> list[tuple[str, str]]:
        """
        Return list[(title, url)] from a Lever 'list' page, applying filters.
        """
        soup = BeautifulSoup(html, "html5lib")
        out: list[tuple[str, str]] = []

        for group in soup.select("div.postings-group"):
            for a in group.select("a.posting-title"):
                href = (a.get("href") or "").strip()
                url = urljoin(self._LEVER_BASE, href) if href else ""

                title_el = a.select_one('h5[data-qa="posting-name"]')
                title = (title_el.get_text(strip=True) if title_el else "").strip()

                if not title or not url:
                    continue
                if query and query.lower() not in title.lower():
                    continue

                # Classification text — used only for filtering
                cat_el = a.select_one("div.posting-categories")
                classification = (cat_el.get_text(" ", strip=True) if cat_el else "").strip().lower()
                if classification and any(ex in classification for ex in exclude):
                    continue

                out.append((title, url))
        return out
