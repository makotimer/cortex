# modules/career_watch/lib/scrapers/workday_cxs.py
from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import time
from typing import Any
from urllib.parse import parse_qs, urlsplit

from ..config import ScraperConfig
from ..http_client import HttpClient
from ..models import Posting, ScrapeResult
from .base import BaseScraper
from .registry import register

log = logging.getLogger(__name__)

_LOCALE_RE = re.compile(r"^[a-z]{2}-[A-Z]{2}$")


def _infer_cxs_from_list_url(list_url: str) -> str | None:
    """
    https://<tenant>.wdX.myworkdayjobs.com/<Site>[/...] ->
    https://<tenant>.wdX.myworkdayjobs.com/wday/cxs/<tenant>/<Site>/jobs

    If the first path segment is a locale (e.g., en-US), use the *next* segment as the site.
    """
    try:
        p = urlsplit(list_url)
        host = p.netloc  # e.g., radiancetech.wd12.myworkdayjobs.com
        segs = [s for s in p.path.strip("/").split("/") if s]
        if not segs:
            return None
        # Skip locale segment if present (en-US, fr-FR, etc.)
        if segs and _LOCALE_RE.match(segs[0]):
            segs = segs[1:]
        site = segs[0] if segs else ""
        tenant = host.split(".")[0]  # e.g., radiancetech
        if not (host and site and tenant):
            return None
        return f"https://{host}/wday/cxs/{tenant}/{site}/jobs"
    except Exception:
        return None


def _deepcopy_jsonable(obj: Any) -> Any:
    return json.loads(json.dumps(obj))


def _merge_query_into_payload(list_or_cxs_url: str, payload: dict) -> dict:
    """
    Map common query parameters found on 'pretty' Workday pages into CxS payload fields.
    - ?q=foo                      -> searchText
    - ?searchText=foo             -> searchText
    - ?locations=UUID[,UUID...]   -> appliedFacets.locations
    - ?jobFamilyGroup=UUID[...]   -> appliedFacets.jobFamilyGroup
    - ?remoteType=UUID[...]       -> appliedFacets.remoteType
    """
    try:
        p = urlsplit(list_or_cxs_url)
        q = parse_qs(p.query or "")
        out = json.loads(json.dumps(payload or {}))  # deepcopy
        applied = out.setdefault("appliedFacets", {})

        # search text
        for key in ("q", "searchText"):
            if q.get(key):
                out["searchText"] = q[key][0]
                break

        # facets (accept comma-delimited or repeated params)
        def _vals(name: str) -> list[str]:
            if name not in q:
                return []
            vals = []
            for v in q[name]:
                vals.extend([x for x in (v or "").split(",") if x])
            return [v.strip() for v in vals if v.strip()]

        for facet_key in ("locations", "jobFamilyGroup", "remoteType"):
            vals = _vals(facet_key)
            if vals:
                applied[facet_key] = vals

        return out
    except Exception:
        return payload


@register
class WorkdayCxSScraper(BaseScraper):
    """
    Workday CxS API scraper (stable vs. brittle HTML).

    ScraperConfig.params:
      start_targets: list of:
        - ["<cxs_jobs_url>", "workday:<label>", {payload}]
        - or {"url": "...", "source": "...", "payload": { ... }}
        - or ["<normal_list_url>", "workday:<label>"] and we will infer cxs url

      delay_seconds: float (default 4.0)
      limit: int (default 20)       -> payload["limit"]
      max_pages: int (default 3)    -> paginate via offset
      base_payload: dict (optional) -> merged into each payload before paging

    Payload shape (typical):
      {
        "searchText": "embedded",
        "appliedFacets": {
          "locations": ["<uuid>"],
          "jobFamilyGroup": ["<uuid>"],
          "remoteType": ["<uuid>"]
        }
      }
    """

    kind = "workday-cxs"
    SOURCE = "workday:cxs"

    def __init__(self) -> None:
        self._client = HttpClient(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            )
        )
        # Headers that tend to keep CxS happy
        with contextlib.suppress(Exception):
            self._client.session.headers.update({
                "Accept": "application/json, text/plain, */*",
                "Content-Type": "application/json;charset=UTF-8",
                "Accept-Language": "en-US,en;q=0.9",
                # Referer/Origin don't have to match exactly, but setting Origin helps sometimes.
            })
        self._debug = os.getenv("JOBWATCH_DEBUG") == "1"

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
            delay = float(params.get("delay_seconds") or 4.0)
            limit = int(params.get("limit") or 20)
            max_pages = int(params.get("max_pages") or 3)
            base_payload = dict(params.get("base_payload") or {})

            raw_targets = params.get("start_targets") or params.get("start_urls") or []
            targets: list[tuple[str, str, dict[str, Any]]] = []

            for item in raw_targets:
                if isinstance(item, (list, tuple)):
                    # ["list or cxs url", "source", payload?]
                    url = str(item[0]).strip()
                    src = str(item[1]).strip() if len(item) >= 2 else (spec.source or self.SOURCE)
                    payload = dict(item[2]) if len(item) >= 3 and isinstance(item[2], dict) else {}
                elif isinstance(item, dict):
                    url = str(item.get("url") or item.get("cxs_url") or item.get("list_url") or "").strip()
                    src = str(item.get("source") or item.get("source_label") or spec.source or self.SOURCE).strip()
                    payload = dict(item.get("payload") or {})
                else:
                    continue

                # If they gave a normal page URL, infer the CxS endpoint
                if url and "/wday/cxs/" not in url:
                    inferred = _infer_cxs_from_list_url(url)
                    if inferred:
                        url = inferred
                if not url:
                    continue

                payload = _merge_query_into_payload(
                    (
                        item[0]
                        if isinstance(item, (list, tuple))
                        else (item.get("url") or item.get("list_url") or url)
                    ),
                    payload,
                )
                targets.append((url, src, payload))

            for ti, (cxs_url, source_label, payload_in) in enumerate(targets):
                if ti and delay:
                    time.sleep(delay)

                items: list[Posting] = []
                errors: list[str] = []

                offset = 0
                for page in range(max_pages):
                    try:
                        payload = _deepcopy_jsonable(base_payload)
                        payload.update(payload_in or {})
                        payload.setdefault("limit", limit)
                        payload["offset"] = offset

                        if self._debug and page == 0:
                            try:
                                with open("/tmp/workday_cxs_req.json", "w", encoding="utf-8") as f:
                                    json.dump({"url": cxs_url, "payload": payload}, f, indent=2)
                            except Exception:
                                pass

                        r = self._client.session.post(cxs_url, json=payload, timeout=self._client.timeout)
                        r.raise_for_status()
                        data = r.json()

                        jobs = self._extract_jobs(data)
                        if self._debug:
                            log.debug("Workday CxS: %s page=%d got %d jobs", source_label, page + 1, len(jobs))

                        if not jobs:
                            break

                        for j in jobs:
                            title = (j.get("title") or "").strip()
                            url = (j.get("externalPath") or j.get("canonicalPositionUrl") or "").strip()
                            # externalPath is usually like "/en-US/Site/job/ReqId/..." -> need scheme/host
                            if url.startswith("/"):
                                # Build base from cxs_url
                                p = urlsplit(cxs_url)
                                url = f"{p.scheme}://{p.netloc}{url}"
                            if title and url:
                                items.append(
                                    Posting(source=source_label, person_env=person_env, title=title, url=url)
                                )

                        # paging
                        if len(jobs) < limit:
                            break
                        offset += len(jobs)
                        if delay:
                            time.sleep(delay)
                    except Exception as e:
                        errors.append(f"{source_label}: page {page + 1}: {e!r}")
                        break

                results.append(ScrapeResult(source=source_label, items=items, errors=errors))

        return results

    # ---- internals ----
    from urllib.parse import parse_qs, urlsplit

    def _extract_jobs(self, data: Any) -> list[dict[str, Any]]:
        """
        Common shapes seen from /wday/cxs/.../jobs:
          { jobPostings: [ {...}, ... ], total: N }
          { body: { jobPostings: [...] } }
        """
        if isinstance(data, dict):
            if isinstance(data.get("jobPostings"), list):
                return [x for x in data["jobPostings"] if isinstance(x, dict)]
            b = data.get("body")
            if isinstance(b, dict) and isinstance(b.get("jobPostings"), list):
                return [x for x in b["jobPostings"] if isinstance(x, dict)]
        return []
