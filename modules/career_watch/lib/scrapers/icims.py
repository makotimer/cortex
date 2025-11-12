# career_watch/scrapers/icims.py
"""
Multi-tenant iCIMS scraper (Peraton, others).

Supports:
  - Multiple companies via `start_urls`
  - Title include/exclude filters
  - Pagination (pr=0, pr=50, ...)

Example groups entry:
{
  "kind": "icims",
  "source": "peraton:remote",
  "params": {
    "start_urls": [
      ["https://careers-peraton.icims.com/jobs/search?ss=1&searchKeyword=Remote+work+allowed+100%25",
      "peraton:remote"]
    ],
    "filters": ["engineer", "developer", "architect"],
    "excludes": ["intern", "entry level"]
  }
}
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

from bs4 import BeautifulSoup

from ..config import ScraperConfig
from ..http_client import HttpClient
from ..models import Posting, ScrapeResult
from .base import BaseScraper
from .registry import register

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Target:
    search_url: str
    source_label: str


def _normalize_targets(raw: object) -> list[_Target]:
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
                url = str(item.get("url") or item.get("search_url") or item.get("list_url") or "").strip()
                label = str(item.get("source") or item.get("source_label") or "").strip()
                if url and label:
                    out.append(_Target(url, label))
    return out


def _normalize_list(raw: object) -> list[str]:
    """Convert config list to lowercase strings."""
    out: list[str] = []
    if isinstance(raw, list):
        for x in raw:
            s = str(x).strip().lower()
            if s:
                out.append(s)
    elif isinstance(raw, str):
        out = [s.strip().lower() for s in raw.split(",") if s.strip()]
    return out


@register
class IcimsScraper(BaseScraper):
    """
    Multi-tenant iCIMS scraper with title filtering.
    """

    kind = "icims"
    SOURCE = "icims:multi-tenant"

    def __init__(self) -> None:
        ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        self._client = HttpClient(user_agent=ua, timeout=10.0)

        with contextlib.suppress(Exception):
            self._client.session.headers.update({
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            })

    def run(
        self,
        person_env: str,
        specs: list[ScraperConfig],
        *,
        skip_network: bool = False,
    ) -> list[ScrapeResult]:
        if skip_network:
            return []

        results: list[ScrapeResult] = []
        debug = True  # os.getenv("JOBWATCH_DEBUG") == "1"

        for spec in specs:
            params = dict(spec.params or {})

            targets = _normalize_targets(params.get("start_urls"))
            if not targets:
                u = str(params.get("search_url") or params.get("list_url") or "").strip()
                s = str(params.get("source") or params.get("source_label") or spec.source or "").strip()
                if u and s:
                    targets = [_Target(u, s)]

            if not targets:
                results.append(ScrapeResult(source=spec.source, items=[], errors=["No valid targets"]))
                continue

            filters = _normalize_list(params.get("filters"))
            excludes = _normalize_list(params.get("excludes"))
            delay = float(params.get("delay_seconds") or 0.5)

            for idx, tgt in enumerate(targets):
                if idx > 0 and delay > 0:
                    time.sleep(delay)

                items, errors = self._scrape_company(
                    person_env=person_env,
                    source_label=tgt.source_label,
                    search_url=tgt.search_url,
                    filters=filters,
                    excludes=excludes,
                    debug=debug and idx == 0,
                )
                results.append(ScrapeResult(source=tgt.source_label, items=items, errors=errors))

        return results

    # --------------------------------------------------------------------- #
    #  Scrape one company (all pages)
    # --------------------------------------------------------------------- #
    def _scrape_company(
        self,
        *,
        person_env: str,
        source_label: str,
        search_url: str,
        filters: list[str],
        excludes: list[str],
        debug: bool,
    ) -> tuple[list[Posting], list[str]]:
        items: list[Posting] = []
        errors: list[str] = []

        if "in_iframe=1" not in search_url:
            separator = "&" if "?" in search_url else "?"
            search_url = f"{search_url}{separator}in_iframe=1"

        parsed = urlparse(search_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        query_dict = parse_qs(parsed.query, keep_blank_values=True)

        page_key = "pr"
        if page_key not in query_dict:
            query_dict[page_key] = ["0"]

        page = 0
        max_pages = 10

        while page < max_pages:
            q = query_dict.copy()
            q[page_key] = [str(page)]
            url = f"{base}{parsed.path}?{urlencode(q, doseq=True)}"

            try:
                html = self._client.get_text(url)
                if not html.strip() or "<title>Access Denied</title>" in html:
                    errors.append(f"Blocked or empty on page {page}")
                    break

                if debug and page == 0:
                    dump = f"/tmp/icims_{source_label.replace(':', '_')}_page{page}.html"
                    try:
                        with open(dump, "w", encoding="utf-8") as f:
                            f.write(html)
                        log.debug("iCIMS debug dump: %s", dump)
                    except Exception:
                        pass

                soup = BeautifulSoup(html, "html.parser")

                # Check if we're in the iframe (has job rows)
                if not soup.select(".iCIMS_JobsTable"):
                    errors.append("Not in iCIMS iframe - wrong URL")
                    break

                page_items = self._parse_jobs(
                    soup,
                    base=base,
                    person_env=person_env,
                    source_label=source_label,
                    filters=filters,
                    excludes=excludes,
                )

                if not page_items:
                    break

                items.extend(page_items)
                log.debug("iCIMS %s - page %d: %d jobs", source_label, page, len(page_items))
                page += 1

            except Exception as exc:
                errors.append(f"page {page}: {exc!r}")
                break

        log.info("iCIMS %s - collected %d postings", source_label, len(items))
        return items, errors

    # --------------------------------------------------------------------- #
    @staticmethod
    def _parse_jobs(
        soup: BeautifulSoup,
        *,
        base: str,
        person_env: str,
        source_label: str,
        filters: Sequence[str],
        excludes: Sequence[str],
    ) -> list[Posting]:
        postings: list[Posting] = []
        seen = set()

        for row in soup.select(".iCIMS_JobsTable .row"):
            a = row.select_one("a[href*='/job/'], .title a, h3 a")
            if not a:
                continue

            # Get raw text
            raw_title = a.get_text(strip=True)

            # CLEAN TITLE: Remove "External Job Posting Title" prefix
            title = raw_title
            prefix = "External Job Posting Title"
            if title.startswith(prefix):
                title = title[len(prefix) :].lstrip()  # remove prefix + any whitespace

            href = a.get("href") or ""
            if not title or not href:
                continue

            t_lower = title.lower()

            if excludes and any(ex in t_lower for ex in excludes):
                continue
            if filters and not any(f in t_lower for f in filters):
                continue

            key = (href, title)
            if key in seen:
                continue
            seen.add(key)

            url = href if href.startswith("http") else urljoin(base, href)
            postings.append(
                Posting(
                    source=source_label,
                    person_env=person_env,
                    title=title,
                    url=url,
                )
            )

        return postings
