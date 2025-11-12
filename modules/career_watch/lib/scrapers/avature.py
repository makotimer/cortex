# career_watch/scrapers/avature.py
"""
Avature scraper - robust, multi-tenant, pagination-aware.

Works with ManTech, BAE, or any Avature instance.

Example groups entry:
{
  "kind": "avature",
  "source": "mantech:fully_remote",
  "params": {
    "search_url": "https://mantech.avature.net/en_US/careers/SearchJobs/?1328=%5B7006%5D&1328_format=1841&listFilterMode=1&jobRecordsPerPage=500"
  }
}
"""

from __future__ import annotations

import logging
import os
import re
import time
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


@register
class AvatureScraper(BaseScraper):
    """
    General Avature scraper with full pagination (jobOffset) and multi-tenant support.
    """

    kind = "avature"
    SOURCE = "avature:multi-tenant"  # display-only; per-tenant labels go on Posting/ScrapeResult

    def __init__(self) -> None:
        ua = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
        self._client = HttpClient(user_agent=ua)

        # Add browser-like headers (critical for Avature anti-bot)
        try:
            self._client.session.headers.update({
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Cache-Control": "max-age=0",
            })
        except Exception as e:
            log.debug("Failed to set headers: %s", e)

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
        debug = os.getenv("JOBWATCH_DEBUG") == "1"

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

            delay = float(params.get("delay_seconds") or 2.0)
            query = str(params.get("query") or "").strip() or None
            per_page_override = int(params.get("per_page") or 0) or None

            for idx, tgt in enumerate(targets):
                if idx > 0 and delay > 0:
                    time.sleep(delay)

                items, errors = self._scrape_one_target(
                    person_env=person_env,
                    source_label=tgt.source_label,
                    search_url=tgt.search_url,
                    query=query,
                    delay=delay,
                    per_page_override=per_page_override,
                    debug=debug and idx == 0,
                )
                results.append(ScrapeResult(source=tgt.source_label, items=items, errors=errors))

        return results

    # --------------------------------------------------------------------- #
    #  Scrape one filtered search
    # --------------------------------------------------------------------- #
    def _scrape_one_target(
        self,
        *,
        person_env: str,
        source_label: str,
        search_url: str,
        query: str | None,
        delay: float,
        per_page_override: int | None,
        debug: bool,
    ) -> tuple[list[Posting], list[str]]:
        items: list[Posting] = []
        errors: list[str] = []

        parsed = urlparse(search_url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        query_dict = parse_qs(parsed.query, keep_blank_values=True)

        query_dict.setdefault("listFilterMode", ["1"])
        per_page = per_page_override or int(query_dict.get("jobRecordsPerPage", ["0"])[0]) or 500
        query_dict["jobRecordsPerPage"] = [str(per_page)]

        def _fetch(offset: int) -> BeautifulSoup | None:
            q = query_dict.copy()
            if offset:
                q["jobOffset"] = [str(offset)]
            url = f"{base}{parsed.path}?{urlencode(q, doseq=True)}"
            try:
                html = self._client.get_text(url)
                if debug and offset == 0:
                    dump = f"/tmp/avature_{source_label.replace(':', '_')}_page0.html"
                    try:
                        with open(dump, "w", encoding="utf-8") as f:
                            f.write(html)
                        log.debug("Avature debug dump: %s (len=%d)", dump, len(html))
                    except Exception as e:
                        log.debug("Avature dump failed: %s", e)
                return BeautifulSoup(html, "html.parser")
            except Exception as exc:
                errors.append(f"offset {offset}: {exc!r}")
                return None

        soup = _fetch(0)
        if not soup:
            return [], errors

        # Debug: log what we see
        total = self._extract_total(soup) or 999999
        log.info("Avature %s - declared total %d (per_page=%d)", source_label, total, per_page)

        items.extend(
            self._parse_cards(soup, base=base, person_env=person_env, source_label=source_label, query=query)
        )

        offset = per_page
        while offset < total:
            if delay > 0:
                time.sleep(delay)

            page_soup = _fetch(offset)
            if not page_soup:
                break

            page_items = self._parse_cards(
                page_soup, base=base, person_env=person_env, source_label=source_label, query=query
            )
            if not page_items:
                break
            if len(page_items) < per_page:
                items.extend(page_items)
                break

            items.extend(page_items)
            offset += per_page

        log.info("Avature %s - collected %d postings", source_label, len(items))
        return items, errors

    # --------------------------------------------------------------------- #
    @staticmethod
    def _extract_total(soup: BeautifulSoup) -> int | None:
        legend = soup.select_one(".list-controls__text__legend")
        if not legend:
            return None
        m = re.search(r"(\d+)", legend.get_text(strip=True))
        return int(m.group(1)) if m else None

    @staticmethod
    def _parse_cards(
        soup: BeautifulSoup,
        *,
        base: str,
        person_env: str,
        source_label: str,
        query: str | None,
    ) -> list[Posting]:
        # Try multiple containers (like Boeing)
        containers = [
            soup.select_one(".results--grided"),
            soup.select_one(".section__content__results"),
            soup.select_one("main"),
            soup,
        ]
        container = next((c for c in containers if c), soup)

        # Try multiple link selectors
        anchors = []
        anchors.extend(container.select("h3 a.link"))
        anchors.extend(container.select(".article__header__text__title a"))
        anchors.extend(container.select("a[href*='/JobDetail/']"))
        if not anchors:
            anchors = [
                a
                for a in container.find_all("a", href=True)
                if "/JobDetail/" in a["href"] and a.get_text(strip=True)
            ]

        postings: list[Posting] = []
        seen = set()
        for a in anchors:
            href = a.get("href") or ""
            title = a.get_text(strip=True).strip()
            if not title or not href:
                continue
            if query and query.lower() not in title.lower():
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
