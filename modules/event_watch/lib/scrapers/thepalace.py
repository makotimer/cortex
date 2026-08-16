"""The Palace live shows in downtown Bryan, via Theater Toolkit JSON-LD.

``/movie-theater/thepalace/movies`` is a concert listing wearing a
movie-house skin. Each show page carries a screeningEvent StartDate
like ``Friday, September 18, 2026 7:00 PM`` and a duration in minutes.
Weekly open-mic / watch-party rows are a different page and are not
fetched.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError

SITE = "https://www.bryantxentertainment.com"
LIST_URL = f"{SITE}/movie-theater/thepalace/movies"
TIMEZONE = "America/Chicago"

ORGANIZATION = {
    "slug": "the-palace",
    "name": "The Palace",
    "website_url": f"{SITE}/movie-theater/thepalace",
}

PLACE = {
    "slug": "the-palace-bryan",
    "name": "The Palace",
    "street": "105 South Main Street",
    "city": "Bryan",
    "region": "TX",
    "postcode": "77803",
    "area": "bryan",
}

_SLUG = re.compile(r"/movie/ThePalace/([A-Za-z0-9-]+)", re.I)
_START = re.compile(
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{1,2}),\s+(\d{4})\s+"
    r"(\d{1,2}):(\d{2})\s*(AM|PM)",
    re.I,
)
_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11,
    "december": 12,
}


class ThePalaceScraper(BaseEventScraper):
    kind = "thepalace"
    source_slug = "the-palace"
    source_name = ORGANIZATION["name"]
    verify_url = LIST_URL

    def __init__(self, proxy_url: str | None = None) -> None:
        self._proxy_url = proxy_url
        self._client: HttpClient | None = None

    def fetch(self, window_start: date, window_end: date, *, skip_network: bool) -> list[RawEvent]:
        if skip_network:
            return []
        client = self._client or HttpClient(
            user_agent="CortexEventWatch/1.0 (+https://discoverbcs.org)",
            proxy_url=self._proxy_url,
            proxy_env=None,
        )
        self._client = client
        slugs = parse_list(client.get_text(LIST_URL))
        raw: list[RawEvent] = []
        for slug in slugs:
            html = client.get_text(f"{SITE}/movie/ThePalace/{slug}")
            rec = parse_detail(html, slug)
            item = to_raw(rec)
            if in_window(item, window_start, window_end):
                raw.append(item)
        return raw

    def normalize(self, raw: list[RawEvent]) -> tuple[list[dict], list[dict]]:
        payloads: list[dict] = []
        rejected: list[dict] = []
        for item in raw:
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
        title = display_title(rec.get("title") or rec.get("slug") or "")
        if not title:
            raise ScraperError("missing title")
        description = normalize.clean_text(rec.get("description"))
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "place": dict(PLACE),
            "topics": ["music"],
            "audiences": ["adult"],
            "indoor": True,
            "registration_required": True,
            "source_url": f"{SITE}/movie/ThePalace/{item.series_uid}",
        }
        if description:
            series["description"] = description
        return series


def parse_list(html_text: str) -> list[str]:
    """Movies page -> unique ThePalace slugs, list order. Pure."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.find_all("a", href=True):
        match = _SLUG.search(a.get("href") or "")
        if not match:
            continue
        slug = match.group(1)
        if slug in seen:
            continue
        seen.add(slug)
        out.append(slug)
    return out


def parse_detail(html_text: str, slug: str) -> dict[str, Any]:
    """Show page HTML -> record. Pure."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    movie: dict[str, Any] | None = None
    itemlist: dict[str, Any] | None = None
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.get_text() or "")
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        kind = str(data.get("@type") or "")
        if kind == "Movie":
            movie = data
        elif kind == "ItemList":
            itemlist = data
    return parse_detail_payload({"slug": slug, "movie": movie, "itemlist": itemlist})


def parse_detail_payload(row: dict[str, Any]) -> dict[str, Any]:
    """Saved movie + itemlist JSON-LD -> record. Pure."""
    movie = row.get("movie") or {}
    itemlist = row.get("itemlist") or {}
    elements = itemlist.get("ItemListElement") or []
    screening = (elements[0].get("Item") if elements else {}) or {}
    work = screening.get("WorkPresented") or {}
    start = parse_start(screening.get("StartDate"))
    minutes = _int(work.get("Duration") or screening.get("Duration"))
    end = None
    if start and minutes:
        end = start + timedelta(minutes=minutes)
    return {
        "slug": row.get("slug") or "",
        "title": movie.get("Name") or screening.get("Name") or row.get("slug") or "",
        "description": movie.get("Description") or work.get("Description"),
        "start": start,
        "end": end,
    }


def parse_start(text: str | None) -> datetime | None:
    match = _START.search(text or "")
    if not match:
        return None
    month = _MONTHS[match.group(1).lower()]
    day = int(match.group(2))
    year = int(match.group(3))
    hour = int(match.group(4))
    minute = int(match.group(5))
    ap = match.group(6).lower()
    if ap == "am":
        if hour == 12:
            hour = 0
    elif hour != 12:
        hour += 12
    return datetime(year, month, day, hour, minute)


def display_title(name: str) -> str:
    text = (name or "").replace("-", " ").strip()
    if text.isupper() or text.islower():
        text = text.title()
    return normalize.clean_text(text) or ""


def to_raw(rec: dict[str, Any]) -> RawEvent:
    start: datetime | None = rec.get("start")
    start_ms = (
        int(start.replace(tzinfo=ZoneInfo(TIMEZONE)).timestamp() * 1000)
        if start
        else None
    )
    return RawEvent(
        series_uid=str(rec.get("slug") or ""),
        occurrence_tid=str(start_ms) if start_ms is not None else "",
        record=dict(rec),
    )


def in_window(item: RawEvent, window_start: date, window_end: date) -> bool:
    start = item.record.get("start")
    if not isinstance(start, datetime):
        return False
    return window_start <= start.date() < window_end


def _occurrence(item: RawEvent) -> dict:
    start = item.record.get("start")
    if not isinstance(start, datetime):
        raise ScraperError("missing start")
    start_ms = int(start.replace(tzinfo=ZoneInfo(TIMEZONE)).timestamp() * 1000)
    occ: dict[str, Any] = {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": normalize.local_iso(start_ms, TIMEZONE),
        "timezone": TIMEZONE,
        "all_day": False,
        "status": "scheduled",
    }
    end = item.record.get("end")
    if isinstance(end, datetime):
        end_ms = int(end.replace(tzinfo=ZoneInfo(TIMEZONE)).timestamp() * 1000)
        occ["end_local"] = normalize.local_iso(end_ms, TIMEZONE)
    return occ


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
