"""Lake Walk events, via The Events Calendar REST API.

The public /events/ grid is a shop window (one card per series). Recurring
listings lie about their permalink date. The tribe/events/v1 feed expands
the window, but it also repeats each real occurrence many times with dated
slug suffixes. Fetch walks pages; normalize keeps one row per
(series, start).
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from bs4 import BeautifulSoup

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError

EVENTS_URL = "https://lakewalktx.com/wp-json/tribe/events/v1/events"
PER_PAGE = 50
TIMEZONE = "America/Chicago"

ORGANIZATION = {
    "slug": "lakewalk",
    "name": "Lake Walk",
    "website_url": "https://lakewalktx.com/",
}

CITY_AREA = {
    "bryan": ("Bryan", "bryan"),
    "college station": ("College Station", "college_station"),
}

CATEGORY_TOPICS = {
    "athletics": "sports",
    "live-music": "music",
    "community": "community",
    "dining": "community",
    "shopping": "community",
    "special-event": "community",
    "networking": "community",
}

_DATE_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}$")
_FREE_COST = re.compile(r"^(free|\$?0(?:\.0+)?)$", re.I)


class LakeWalkScraper(BaseEventScraper):
    kind = "lakewalk"
    source_slug = "lakewalk"
    source_name = ORGANIZATION["name"]
    verify_url = EVENTS_URL + "?per_page=1"

    def __init__(self, proxy_url: str | None = None) -> None:
        self._proxy_url = proxy_url
        self._client: HttpClient | None = None

    def fetch(
        self, window_start: date, window_end: date, *, skip_network: bool
    ) -> list[RawEvent]:
        if skip_network:
            return []
        client = self._client or HttpClient(
            user_agent="CortexEventWatch/1.0 (+https://discoverbcs.org)",
            proxy_url=self._proxy_url,
            proxy_env=None,
            timeout=30.0,
        )
        self._client = client

        records: list[dict] = []
        page = 1
        total_pages = 1
        while page <= total_pages:
            data = _get_page(client, window_start, window_end, page)
            batch = data.get("events")
            if not isinstance(batch, list) or not batch:
                break
            records.extend(batch)
            try:
                total_pages = max(1, int(data.get("total_pages") or 1))
            except (TypeError, ValueError):
                total_pages = page
            page += 1
        return [to_raw(r) for r in records]

    def normalize(self, raw: list[RawEvent]) -> tuple[list[dict], list[dict]]:
        payloads: list[dict] = []
        rejected: list[dict] = []
        for item in _dedupe(raw):
            try:
                series = self._series(item)
                occurrence = _occurrence(item)
            except ScraperError as exc:
                rejected.append({
                    "series_uid": item.series_uid,
                    "occurrence_tid": item.occurrence_tid,
                    "reason": str(exc),
                })
                continue
            payloads.append({
                "schema_version": "1",
                "source": {
                    "slug": self.source_slug,
                    "name": self.source_name,
                    "kind": "feed",
                },
                "series": series,
                "occurrence": occurrence,
            })
        return payloads, rejected

    def _series(self, item: RawEvent) -> dict:
        rec = item.record
        title = (rec.get("title") or "").strip()
        if not title:
            raise ScraperError("missing title")
        description = strip_html(rec.get("description") or rec.get("excerpt") or "")
        cats = _category_slugs(rec)
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "topics": topics_from_categories(cats),
            "audiences": audiences_from_categories(cats),
            "place": _place(rec),
        }
        if description:
            series["description"] = description
        if rec.get("url"):
            series["source_url"] = rec["url"]
        if is_free(rec.get("cost") or "", rec.get("cost_details") or {}):
            series["is_free"] = True
        return series


def to_raw(record: dict) -> RawEvent:
    start = wall_clock(record.get("start_date"))
    return RawEvent(
        series_uid=series_uid(record.get("slug") or ""),
        occurrence_tid=start or str(record.get("id") or ""),
        record=record,
    )


def series_uid(slug: str) -> str:
    """Strip TEC's per-instance ``-YYYY-MM-DD`` suffix."""
    return _DATE_SUFFIX.sub("", slug or "")


def wall_clock(value: str | None) -> str | None:
    """``2026-08-15 08:00:00`` -> ``2026-08-15T08:00:00``."""
    raw = (value or "").strip()
    if not raw:
        return None
    return raw.replace(" ", "T", 1)


def topics_from_categories(slugs: list[str]) -> list[str]:
    out = {CATEGORY_TOPICS[s] for s in slugs if s in CATEGORY_TOPICS}
    return sorted(out)


def audiences_from_categories(slugs: list[str]) -> list[str]:
    return ["all-ages"] if "family-friendly" in slugs else []


def is_free(cost: str, cost_details: dict) -> bool:
    if _FREE_COST.match((cost or "").strip()):
        return True
    values = cost_details.get("values") if isinstance(cost_details, dict) else None
    return bool(values) and all(str(v).strip() in {"", "0", "0.0", "0.00"} for v in values)


def strip_html(html: str) -> str | None:
    text = BeautifulSoup(html or "", "html.parser").get_text("\n", strip=True)
    return normalize.clean_text(text)


def _category_slugs(record: dict) -> list[str]:
    out: list[str] = []
    for cat in record.get("categories") or []:
        slug = (cat.get("slug") or "").strip() if isinstance(cat, dict) else str(cat).strip()
        if slug:
            out.append(slug)
    return out


def _venue(record: dict) -> dict:
    venue = record.get("venue")
    if isinstance(venue, list):
        venue = venue[0] if venue else {}
    return venue if isinstance(venue, dict) else {}


def _place(record: dict) -> dict:
    venue = _venue(record)
    city_raw = (venue.get("city") or "").strip()
    mapped = CITY_AREA.get(city_raw.lower())
    if mapped:
        city, area = mapped
    else:
        city, area = "Bryan", "bryan"
    name = (venue.get("venue") or "").strip() or city
    place: dict[str, Any] = {
        "slug": normalize.slugify(f"{name}-{city}"),
        "name": name,
        "city": city,
        "region": "TX",
        "area": area,
    }
    street = (venue.get("address") or "").strip()
    if street:
        place["street"] = street
    zip_code = (venue.get("zip") or "").strip()
    if zip_code:
        place["postcode"] = zip_code
    return place


def _occurrence(item: RawEvent) -> dict:
    rec = item.record
    start = wall_clock(rec.get("start_date"))
    if not start:
        raise ScraperError("missing start_date")
    occ: dict[str, Any] = {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": start,
        "timezone": rec.get("timezone") or TIMEZONE,
        "all_day": bool(rec.get("all_day")),
        "status": "scheduled",
    }
    end = wall_clock(rec.get("end_date"))
    if end:
        occ["end_local"] = end
    return occ


def _dedupe(raw: list[RawEvent]) -> list[RawEvent]:
    """Keep one record per (series, start). Prefer a slug without a date suffix."""
    best: dict[tuple[str, str], RawEvent] = {}
    order: list[tuple[str, str]] = []
    for item in raw:
        key = (item.series_uid, item.occurrence_tid)
        if key not in best:
            best[key] = item
            order.append(key)
            continue
        if _is_canonical(item) and not _is_canonical(best[key]):
            best[key] = item
    return [best[k] for k in order]


def _is_canonical(item: RawEvent) -> bool:
    slug = item.record.get("slug") or ""
    return not _DATE_SUFFIX.search(slug)


def _get_page(
    client: HttpClient, window_start: date, window_end: date, page: int
) -> dict:
    params = {
        "start_date": f"{window_start.isoformat()} 00:00:00",
        "end_date": f"{window_end.isoformat()} 23:59:59",
        "per_page": PER_PAGE,
        "page": page,
        "status": "publish",
    }
    data = client.get_json(EVENTS_URL, params=params)
    if not isinstance(data, dict):
        raise ScraperError("lakewalk: tribe events response is not an object")
    if "events" not in data:
        raise ScraperError("lakewalk: tribe events response missing 'events'")
    return data
