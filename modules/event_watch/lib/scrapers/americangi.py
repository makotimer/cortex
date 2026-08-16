"""Museum of the American G.I. events, via The Events Calendar REST API.

The public ``/event/`` page is a TEC Pro photo grid. Dates, times and
descriptions live on ``/wp-json/tribe/events/v1/events``. Every current
listing is at the museum's own venue (19124 Highway 6 South).
"""
from __future__ import annotations

from datetime import date
from typing import Any

from bs4 import BeautifulSoup

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError

EVENTS_URL = "https://americangimuseum.org/wp-json/tribe/events/v1/events"
SITE = "https://americangimuseum.org/"
PER_PAGE = 50
TIMEZONE = "America/Chicago"

ORGANIZATION = {
    "slug": "museum-of-the-american-gi",
    "name": "Museum of the American G.I.",
    "website_url": SITE,
}

PLACE = {
    "slug": "museum-of-the-american-gi",
    "name": "Museum of the American G.I.",
    "street": "19124 Highway 6 South",
    "city": "College Station",
    "region": "TX",
    "postcode": "77845",
    "area": "college_station",
}


class AmericanGiScraper(BaseEventScraper):
    kind = "americangi"
    source_slug = "american-gi-museum"
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
        raw: list[RawEvent] = []
        page = 1
        total_pages = 1
        while page <= total_pages:
            data = _get_page(client, window_start, window_end, page)
            for event in parse_events(data):
                item = to_raw(event)
                if in_window(item, window_start, window_end):
                    raw.append(item)
            try:
                total_pages = max(1, int(data.get("total_pages") or 1))
            except (TypeError, ValueError):
                total_pages = page
            page += 1
        return raw

    def normalize(self, raw: list[RawEvent]) -> tuple[list[dict], list[dict]]:
        payloads: list[dict] = []
        rejected: list[dict] = []
        for item in raw:
            try:
                series = _series(item)
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


def parse_events(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        raise ScraperError("americangi: tribe events response is not an object")
    rows = payload.get("events")
    if not isinstance(rows, list):
        raise ScraperError("americangi: tribe events response missing 'events'")
    return [row for row in rows if isinstance(row, dict)]


def to_raw(record: dict[str, Any]) -> RawEvent:
    start = wall_clock(record.get("start_date"))
    return RawEvent(
        series_uid=str(record.get("slug") or ""),
        occurrence_tid=start or str(record.get("id") or ""),
        record=record,
    )


def wall_clock(value: Any) -> str | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    return raw.replace(" ", "T", 1)


def in_window(item: RawEvent, window_start: date, window_end: date) -> bool:
    start = wall_clock(item.record.get("start_date"))
    if not start:
        return False
    try:
        day = date.fromisoformat(start[:10])
    except ValueError:
        return False
    return window_start <= day < window_end


def topics_from(record: dict[str, Any]) -> list[str]:
    title = (record.get("title") or "").lower()
    topics = ["history"]
    if "craft" in title:
        topics.insert(0, "crafts")
    if "treat" in title or "paws" in title:
        topics.insert(0, "community")
    return topics


def _series(item: RawEvent) -> dict[str, Any]:
    rec = item.record
    title = (rec.get("title") or "").strip()
    if not title:
        raise ScraperError("missing title")
    description = strip_html(rec.get("description") or rec.get("excerpt") or "")
    series: dict[str, Any] = {
        "source_series_uid": item.series_uid,
        "title": title,
        "organization": dict(ORGANIZATION),
        "place": dict(PLACE),
        "topics": topics_from(rec),
        "audiences": ["all-ages"],
    }
    if description:
        series["description"] = description
    if rec.get("url"):
        series["source_url"] = rec["url"]
    if "school day" in title.lower():
        series["field_trip"] = True
    return series


def _occurrence(item: RawEvent) -> dict[str, Any]:
    start = wall_clock(item.record.get("start_date"))
    if not start:
        raise ScraperError("missing start_date")
    occ: dict[str, Any] = {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": start,
        "timezone": item.record.get("timezone") or TIMEZONE,
        "all_day": bool(item.record.get("all_day")),
        "status": "scheduled",
    }
    end = wall_clock(item.record.get("end_date"))
    if end:
        occ["end_local"] = end
    return occ


def strip_html(html: str) -> str | None:
    text = BeautifulSoup(html or "", "html.parser").get_text("\n", strip=True)
    return normalize.clean_text(text)


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
        raise ScraperError("americangi: tribe events response is not an object")
    if "events" not in data:
        raise ScraperError("americangi: tribe events response missing 'events'")
    return data
