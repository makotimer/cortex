# modules/career_watch/lib/scrapers/boeing.py
from __future__ import annotations

import contextlib
import logging
import os
import re
import time
from collections.abc import Sequence
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup  # pip install beautifulsoup4 html5lib

from ..config import ScraperConfig
from ..http_client import HttpClient
from ..models import Posting, ScrapeResult
from .base import BaseScraper
from .registry import register

log = logging.getLogger(__name__)


@register
class BoeingScraper(BaseScraper):
    """
    Scraper for Boeing careers (explicit pre-filtered targets).

    ScraperConfig.params:
      start_urls: list[[list_url, source_label], ...]
                  e.g. [["https://jobs.boeing.com/.../remote-engineering-software...", "boeing:virtual-software"]]
      delay_seconds: float (default 3.0)
      query: optional case-insensitive substring to keep by title (default None)

    Behavior:
      - Emits ONE ScrapeResult PER tenant (per entry in start_urls).
      - Each Posting.source uses the per-tenant label you pass, e.g. "boeing:virtual-software".
    """

    kind = "boeing"
    SOURCE = "boeing:multi-tenant"  # display-only; per-tenant labels go on Posting/ScrapeResult

    def __init__(self) -> None:
        # A slightly more browser-like UA; some Phenom/Workday skins hide for generic UAs.
        self._client = HttpClient(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        # Helpful, but not strictly required:
        with contextlib.suppress(Exception):
            self._client.session.headers.update({"Accept-Language": "en-US,en;q=0.9"})

    def run(
        self,
        person_env: str,
        specs: list[ScraperConfig],
        *,
        skip_network: bool,
    ) -> list[ScrapeResult]:
        if skip_network:
            return []

        results: list[ScrapeResult] = []
        for spec in specs:
            params = dict(spec.params or {})
            start_urls: Sequence[tuple[str, str]] = []

            # Accept [["url","label"], ...] OR [{"url":..., "source":...}, ...]
            raw = params.get("start_urls") or []
            for item in raw:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    start_urls.append((str(item[0]).strip(), str(item[1]).strip()))
                elif isinstance(item, dict):
                    u = str(item.get("url") or item.get("list_url") or "").strip()
                    s = str(item.get("source") or item.get("source_label") or "").strip()
                    if u and s:
                        start_urls.append((u, s))

            if not start_urls:
                # Back-compat: single pair passed as params
                u = str(params.get("list_url") or "").strip()
                s = str(params.get("source_label") or spec.source or "").strip()
                if u and s:
                    start_urls = [(u, s)]

            delay = float(params.get("delay_seconds") or 3.0)
            query = params.get("query") or None
            if isinstance(query, str):
                query = query.strip() or None

            for i, (list_url, label) in enumerate(start_urls):
                if i > 0 and delay > 0:
                    time.sleep(delay)

                items: list[Posting] = []
                errors: list[str] = []
                try:
                    html = self._client.get_text(list_url)
                    base = self._origin_base(list_url)

                    if os.getenv("JOBWATCH_DEBUG") == "1":
                        dump = f"/tmp/boeing_debug_{i}.html"
                        try:
                            with open(dump, "w", encoding="utf-8") as f:
                                f.write(html)
                            log.debug("Boeing: wrote HTML dump to %s (len=%d)", dump, len(html))
                        except Exception as e:
                            log.debug("Boeing: failed to write HTML dump: %s", e)

                    for title, url in self._parse_list_page(html, base, query=query):
                        items.append(Posting(source=label, person_env=person_env, title=title, url=url))
                except Exception as e:
                    errors.append(f"{label}: {e!r}")

                results.append(ScrapeResult(source=label, items=items, errors=errors))
        return results

    # ---- internals ----

    def _origin_base(self, url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"

    def _parse_list_page(
        self,
        html: str,
        base: str,
        *,
        query: str | None,
    ) -> list[tuple[str, str]]:
        """
        Return list[(title, url)] from a Boeing results page (Phenom/Workday skins).
        Liberal selectors + fallback heuristics.
        """
        soup = BeautifulSoup(html, "html.parser")
        out: list[tuple[str, str]] = []

        # Prefer a contained area if present
        containers = [
            soup.select_one("div.search-results__list"),
            soup.select_one('section[data-ph-at-id="job-search-results"]'),
            soup.select_one("#search-results"),
            soup.select_one("main"),  # broad fallback
        ]
        container = next((c for c in containers if c), soup)

        anchors = []
        anchors.extend(container.select('a[data-ph-at-id="job-title-link"]'))
        anchors.extend(container.select("a.search-results__job-link"))
        anchors.extend(container.select("a.job-card__title-link"))

        # Heuristic fallback: any anchor with /job or /jobs in href and non-empty text
        if not anchors:
            anchors = [
                a
                for a in container.find_all("a", href=True)
                if re.search(r"/job[s]?/", a["href"]) and a.get_text(strip=True)
            ]

        seen = set()
        for a in anchors:
            href = (a.get("href") or "").strip()
            title_el = a.select_one("span.search-results__job-title") or a.find(["h2", "h3"]) or a
            title = (title_el.get_text(" ", strip=True) or "").strip()

            key = (href, title)
            if key in seen:
                continue
            seen.add(key)

            if not href or not title:
                continue
            if query and query.lower() not in title.lower():
                continue

            url = href if href.startswith("http") else urljoin(base, href)
            out.append((title, url))

        return out
