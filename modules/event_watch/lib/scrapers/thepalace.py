"""The Palace live shows in downtown Bryan, via Theater Toolkit JSON-LD.

``/movie-theater/thepalace/movies`` is a concert listing wearing a
movie-house skin. Each show page carries a screeningEvent StartDate
like ``Friday, September 18, 2026 7:00 PM`` and a duration in minutes.

``/weekly-events`` is standing copy, not a dated grid. Open Mic,
First Friday, Maroon & White, and Gameday on Main are expanded from
that page's headings across the run window.
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
WEEKLY_URL = f"{SITE}/movie-theater/thepalace/weekly-events"
TIMEZONE = "America/Chicago"

WEEKLY = {
    "first-friday": {
        "title": "First Fridays at The Palace",
        "topics": ["music"],
        "audiences": ["all-ages"],
        "is_free": True,
        "hour": 18,
        "minute": 0,
    },
    "open-mic": {
        "title": "Open Mic Night",
        "topics": ["music"],
        "audiences": ["adult"],
        "is_free": True,
        "hour": 19,
        "minute": 0,
        "end_hour": 22,
        "end_minute": 0,
    },
    "gameday": {
        "title": "Gameday on Main",
        "topics": ["sports"],
        "audiences": ["adult"],
        "hour": 11,
        "minute": 0,
    },
    "maroon-white": {
        "title": "Maroon & White Night",
        "topics": ["community"],
        "audiences": ["adult"],
        "hour": 18,
        "minute": 0,
    },
}

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
        listing: list[RawEvent] = []
        for slug in slugs:
            html = client.get_text(f"{SITE}/movie/ThePalace/{slug}")
            rec = parse_detail(html, slug)
            item = to_raw(rec)
            if in_window(item, window_start, window_end):
                listing.append(item)
        try:
            weekly_html = client.get_text(WEEKLY_URL)
        except Exception:
            weekly_html = ""
        weekly = expand_weekly(weekly_html, window_start, window_end) if weekly_html else []
        return combine(listing, weekly)

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
        weekly = WEEKLY.get(item.series_uid)
        if weekly:
            series: dict[str, Any] = {
                "source_series_uid": item.series_uid,
                "title": weekly["title"],
                "organization": dict(ORGANIZATION),
                "place": dict(PLACE),
                "topics": list(weekly["topics"]),
                "audiences": list(weekly["audiences"]),
                "indoor": True,
                "source_url": WEEKLY_URL,
            }
            if weekly.get("is_free"):
                series["is_free"] = True
            return series
        title = display_title(rec.get("title") or rec.get("slug") or "")
        if not title:
            raise ScraperError("missing title")
        description = normalize.clean_text(rec.get("description"))
        series = {
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


def weekly_kinds(html_text: str) -> set[str]:
    """Which standing series the weekly-events page still advertises. Pure."""
    text = (html_text or "").lower()
    out: set[str] = set()
    if "first friday" in text:
        out.add("first-friday")
    if "open mic" in text:
        out.add("open-mic")
    if "gameday" in text or "watch party" in text:
        out.add("gameday")
    if "maroon" in text and "white" in text:
        out.add("maroon-white")
    return out


def listing_kind(slug: str) -> str | None:
    """Show-page slug -> the weekly series it restates, if exactly one. Pure."""
    kinds = weekly_kinds((slug or "").replace("-", " "))
    return next(iter(kinds)) if len(kinds) == 1 else None


def combine(listing: list[RawEvent], weekly: list[RawEvent]) -> list[RawEvent]:
    """Fold listing pages that restate a weekly series into that series. Pure.

    The listing started carrying GAMEDAY-ON-MAIN, NFL-GAMEDAY-ON-MAIN and
    OPEN-MIC-NIGHT as show pages (2026-09-22). Under their own slugs each
    became a second series from this source beside the weekly one, and the
    site only matches duplicates across sources. So a restating page takes
    the weekly uid, and on any day it covers it replaces the projected
    weekly row: the page has the real start and end.
    """
    covered: set[tuple[str, date]] = set()
    for item in listing:
        kind = listing_kind(item.series_uid)
        if kind:
            item.series_uid = kind
            covered.add((kind, item.record["start"].date()))
    kept = [
        item for item in weekly
        if (item.series_uid, item.record["start"].date()) not in covered
    ]
    return listing + kept


def expand_weekly(html_text: str, window_start: date, window_end: date) -> list[RawEvent]:
    """Standing weekly copy -> one RawEvent per dated occurrence. Pure."""
    kinds = weekly_kinds(html_text)
    raw: list[RawEvent] = []
    if "first-friday" in kinds:
        raw.extend(_weekly_days("first-friday", _first_fridays(window_start, window_end)))
    if "maroon-white" in kinds:
        raw.extend(_weekly_days("maroon-white", _nth_fridays(window_start, window_end, 3)))
    if "open-mic" in kinds:
        raw.extend(_weekly_days("open-mic", _weekdays(window_start, window_end, 2)))
    if "gameday" in kinds:
        days = _weekdays(window_start, window_end, 5)  # Saturday
        days += _weekdays(window_start, window_end, 6)  # Sunday
        days = [d for d in days if _is_gameday(d)]
        raw.extend(_weekly_days("gameday", days))
    return raw


def _weekly_days(kind: str, days: list[date]) -> list[RawEvent]:
    meta = WEEKLY[kind]
    out: list[RawEvent] = []
    for day in days:
        start = datetime(day.year, day.month, day.day, meta["hour"], meta["minute"])
        rec: dict[str, Any] = {"title": meta["title"], "start": start, "weekly": True}
        if "end_hour" in meta:
            rec["end"] = datetime(day.year, day.month, day.day, meta["end_hour"], meta["end_minute"])
        item = to_raw(rec)
        item.series_uid = kind
        out.append(item)
    return out


def _weekdays(window_start: date, window_end: date, weekday: int) -> list[date]:
    day = window_start
    out: list[date] = []
    while day < window_end:
        if day.weekday() == weekday:
            out.append(day)
        day += timedelta(days=1)
    return out


def _first_fridays(window_start: date, window_end: date) -> list[date]:
    return _nth_fridays(window_start, window_end, 1)


def _nth_fridays(window_start: date, window_end: date, n: int) -> list[date]:
    out: list[date] = []
    year, month = window_start.year, window_start.month
    while date(year, month, 1) < window_end:
        first = date(year, month, 1)
        friday = first + timedelta(days=(4 - first.weekday()) % 7)
        day = friday + timedelta(days=7 * (n - 1))
        if day.month == month and window_start <= day < window_end:
            out.append(day)
        if month == 12:
            year, month = year + 1, 1
        else:
            month += 1
    return out


def _is_gameday(day: date) -> bool:
    """College Saturdays from last Saturday of August through the second
    Saturday of January; NFL Sundays from the Sunday after Labor Day
    through the second Sunday of February.
    """
    if day.weekday() == 5:
        season = day.year if day.month >= 8 else day.year - 1
        start = _last_saturday_of_august(season)
        end = _nth_weekday(season + 1, 1, 5, 2)
        return start <= day <= end
    if day.weekday() == 6:
        season_year = day.year if day.month >= 9 else day.year - 1
        start = _labor_day(season_year) + timedelta(days=6)
        end = _nth_weekday(season_year + 1, 2, 6, 2)
        return start <= day <= end
    return False


def _labor_day(year: int) -> date:
    first = date(year, 9, 1)
    return first + timedelta(days=(0 - first.weekday()) % 7)


def _last_saturday_of_august(year: int) -> date:
    day = date(year, 8, 31)
    return day - timedelta(days=(day.weekday() - 5) % 7)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    first_hit = first + timedelta(days=(weekday - first.weekday()) % 7)
    return first_hit + timedelta(days=7 * (n - 1))


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
