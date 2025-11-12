# modules/career_watch/lib/scrapers/bae.py
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import time
from typing import Any
from urllib.parse import urljoin, urlsplit

from ..config import ScraperConfig
from ..http_client import HttpClient
from ..models import Posting, ScrapeResult
from .base import BaseScraper
from .registry import register

log = logging.getLogger(__name__)


def _origin(url: str) -> str:
    p = urlsplit(url)
    return f"{p.scheme}://{p.netloc}"


def _slugify(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


@register
class BaePhenomAPIScraper(BaseScraper):
    """
    BAE Systems (Phenom) — API-backed scraper using POST https://jobs.baesystems.com/widgets

    ScraperConfig.params supported:
      start_targets: list of [api_url, source_label, payload_dict]
        - api_url: usually "https://jobs.baesystems.com/widgets"
        - source_label: e.g., "bae:remote-engtech"
        - payload_dict: base payload for the refineSearch POST (see example below)

      delay_seconds: float   (default 4.0)
      page_size:     int     (default 50)  -> forces payload["size"]
      max_pages:     int     (default 6)

    Behavior:
      - Emits ONE ScrapeResult PER target in start_targets (per-tenant grouping like your Lever/Boeing).
      - Each Posting.source is the per-target source_label you pass (e.g., "bae:remote-engtech").
    """

    kind = "phenom-api"
    SOURCE = "bae:phenom-api"

    def __init__(self) -> None:
        self._client = HttpClient(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        # Helpful headers for Phenom widgets
        with contextlib.suppress(Exception):
            self._client.session.headers.update({
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json;charset=UTF-8",
                "Accept-Language": "en-US,en;q=0.9",
                "Origin": "https://jobs.baesystems.com",
                "Referer": "https://jobs.baesystems.com/global/en/search-results",
                "X-Requested-With": "XMLHttpRequest",
            })

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
        debug = os.getenv("JOBWATCH_DEBUG") == "1"

        for spec in specs:
            params = dict(spec.params or {})
            delay = float(params.get("delay_seconds") or 4.0)
            page_size = int(params.get("page_size") or 50)
            max_pages = int(params.get("max_pages") or 6)

            # Normalize start_targets to [(api_url, label, payload_dict), ...]
            start_targets: list[tuple[str, str, dict[str, Any]]] = []
            raw = params.get("start_targets") or []
            for item in raw:
                if isinstance(item, (list, tuple)) and len(item) >= 3:
                    api_url = str(item[0]).strip()
                    source_label = str(item[1]).strip()
                    payload = dict(item[2] or {})
                    if api_url and source_label:
                        start_targets.append((api_url, source_label, payload))
                elif isinstance(item, dict):
                    api_url = str(item.get("url") or item.get("api_url") or "").strip()
                    source_label = str(item.get("source") or item.get("source_label") or "").strip()
                    payload = dict(item.get("payload") or {})
                    if api_url and source_label:
                        start_targets.append((api_url, source_label, payload))

            for ti, (api_url, source_label, base_payload) in enumerate(start_targets):
                if ti and delay:
                    time.sleep(delay)

                # Ensure required/default fields; force size to page_size
                payload_base = dict(base_payload or {})
                payload_base.setdefault("ddoKey", "refineSearch")
                payload_base.setdefault("pageName", "search-results")
                payload_base.setdefault("pageId", "page1-migration-ds")
                payload_base.setdefault("counts", True)
                payload_base.setdefault("jobs", True)
                payload_base.setdefault("global", True)
                payload_base.setdefault("jsdsource", "facets")
                payload_base.setdefault("lang", "en_global")
                payload_base.setdefault("country", "global")
                payload_base.setdefault("deviceType", "desktop")
                payload_base.setdefault("siteType", "external")
                payload_base.setdefault("locationData", {})
                payload_base.setdefault("isSliderEnable", False)
                payload_base.setdefault("clearAll", False)
                payload_base["size"] = page_size

                items: list[Posting] = []
                errors: list[str] = []

                total = 0
                for page_idx in range(max_pages):
                    # deep copy so we can mutate safely
                    payload = json.loads(json.dumps(payload_base))
                    payload["from"] = total

                    try:
                        if debug and page_idx == 0:
                            try:
                                with open("/tmp/bae_widgets_req.json", "w", encoding="utf-8") as f:
                                    json.dump(payload, f, ensure_ascii=False, indent=2)
                                log.debug("BAE widgets: wrote request payload to /tmp/bae_widgets_req.json")
                            except Exception:
                                pass

                        r = self._client.session.post(api_url, json=payload, timeout=self._client.timeout)
                        r.raise_for_status()
                        data = r.json()

                        if debug:
                            dump = f"/tmp/bae_widgets_resp_{page_idx}.json"
                            try:
                                with open(dump, "w", encoding="utf-8") as f:
                                    json.dump(data, f, ensure_ascii=False, indent=2)
                                log.debug("BAE widgets: saved %s", dump)
                            except Exception:
                                pass

                        raw_items = self._extract_items(data)
                        if not raw_items:
                            break

                        for title, url in self._normalize_items(raw_items, api_url):
                            items.append(Posting(source=source_label, person_env=person_env, title=title, url=url))

                        total += len(raw_items)
                        if len(raw_items) < page_size:
                            break  # exhausted results

                        if delay:
                            time.sleep(delay)
                    except Exception as e:
                        errors.append(f"{source_label}: page {page_idx + 1}: {e!r}")
                        break  # bail on this tenant/target

                results.append(ScrapeResult(source=source_label, items=items, errors=errors))

        return results

    # ---- internals ----

    def _extract_items(self, data: Any) -> list[dict[str, Any]]:
        """
        Handles shapes:
          { refineSearch: { data: { jobs: [...] } } }
          { data: { jobs: [...] } }
          { jobs: [...] }
          { data: { searchResults: { items: [...] } } }
        """
        if not isinstance(data, dict):
            return []

        rs = data.get("refineSearch")
        if isinstance(rs, dict):
            d = rs.get("data")
            if isinstance(d, dict) and isinstance(d.get("jobs"), list):
                return [x for x in d["jobs"] if isinstance(x, dict)]

        d = data.get("data")
        if isinstance(d, dict):
            if isinstance(d.get("jobs"), list):
                return [x for x in d["jobs"] if isinstance(x, dict)]
            sr = d.get("searchResults")
            if isinstance(sr, dict) and isinstance(sr.get("items"), list):
                return [x for x in sr["items"] if isinstance(x, dict)]

        if isinstance(data.get("jobs"), list):
            return [x for x in data["jobs"] if isinstance(x, dict)]

        return []

    def _normalize_items(self, items: list[dict[str, Any]], api_url: str) -> list[tuple[str, str]]:
        base = _origin(api_url)
        out: list[tuple[str, str]] = []

        for it in items:
            title = (it.get("title") or it.get("name") or it.get("jobTitle") or "").strip()
            if not title:
                continue

            url = it.get("url") or it.get("canonicalUrl") or it.get("jobUrl") or it.get("applyUrl") or ""
            if url and not url.startswith("http"):
                url = urljoin(base, url)

            if not url:
                job_id = str(it.get("jobId") or it.get("id") or "").strip()
                slug = it.get("slug") or it.get("jobSlug") or _slugify(title)
                if job_id:
                    url = urljoin(base, f"/global/en/job/{slug}/{job_id}")

            if not url:
                continue

            out.append((title, url))

        return out
