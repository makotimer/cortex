"""George H.W. Bush Presidential Library upcoming events.

Drupal Views HTML on ``/events/upcoming-events``. Date is month/day/year on
the listing; clock time is only in the body when they write it. Past events
are a different page and are not fetched.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError

LIST_URL = "https://www.bush41library.gov/events/upcoming-events"
SITE = "https://www.bush41library.gov"
TIMEZONE = "America/Chicago"

ORGANIZATION = {
    "slug": "bush41",
    "name": "George H.W. Bush Presidential Library and Museum",
    "website_url": "https://www.bush41library.gov/events/upcoming-events",
}

VENUE = {
    "slug": "george-hw-bush-presidential-library-and-museum",
    "name": "George H.W. Bush Presidential Library and Museum",
    "street": "1000 George Bush Dr W",
    "city": "College Station",
    "region": "TX",
    "postcode": "77845",
    "area": "college_station",
    "latitude": 30.5965561,
    "longitude": -96.3532701,
}

_MONTHS = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}
_CLOCK = re.compile(
    r"\b(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(a\.m\.|p\.m\.|am|pm)(?!\w)",
    re.I,
)
_FAMILY = re.compile(r"veterans and their families", re.I)


class Bush41Scraper(BaseEventScraper):
    kind = "bush41"
    source_slug = "bush41"
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
        cards = parse_upcoming(client.get_text(LIST_URL))
        raw: list[RawEvent] = []
        for card in cards:
            item = to_raw(card)
            day = card.get("date")
            if isinstance(day, date) and not (window_start <= day < window_end):
                continue
            href = card.get("href")
            if href:
                item.supplement["detail"] = parse_detail(client.get_text(href))
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
        title = (rec.get("title") or "").strip()
        if not title:
            raise ScraperError("missing title")
        detail = item.supplement.get("detail") or {}
        description = normalize.clean_text(detail.get("body") or rec.get("excerpt"))
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "place": dict(VENUE),
            "topics": ["history"],
            "audiences": audiences_from_text(description or ""),
        }
        if description:
            series["description"] = description
        if rec.get("href"):
            series["source_url"] = rec["href"]
        if detail.get("registration"):
            series["registration_required"] = True
            if detail["registration"].startswith("http"):
                series["registration_url"] = detail["registration"]
        return series


def parse_upcoming(html_text: str) -> list[dict]:
    """Upcoming-events Views rows -> card dicts. Pure."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    out: list[dict] = []
    for row in soup.select(".views-row"):
        title_el = row.select_one(".views-field-title a")
        if not title_el:
            continue
        href = title_el.get("href") or ""
        if href.startswith("/"):
            href = urljoin(SITE, href)
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        month = row.select_one(".month")
        day = row.select_one(".day")
        year = row.select_one(".year")
        loc_el = row.select_one(".views-field-field-event-location .field-content")
        body_el = row.select_one(".views-field-field-event-body .field-content")
        event_date = parse_listing_date(
            month.get_text(strip=True) if month else "",
            day.get_text(strip=True) if day else "",
            year.get_text(strip=True) if year else "",
        )
        out.append({
            "slug": slug,
            "title": title_el.get_text(" ", strip=True),
            "href": href,
            "date": event_date,
            "location": loc_el.get_text(" ", strip=True) if loc_el else "",
            "excerpt": body_el.get_text(" ", strip=True) if body_el else "",
        })
    return out


def parse_detail(html_text: str) -> dict:
    """Event node fields. Pure."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    body_el = soup.select_one(".field--name-field-event-body")
    loc_el = soup.select_one(".field--name-field-event-location")
    city_el = soup.select_one(".field--name-field-event-city")
    reg_el = soup.select_one(".field--name-field-register-link a")
    href = (reg_el.get("href") or "").strip() if reg_el else ""
    return {
        "body": body_el.get_text(" ", strip=True) if body_el else "",
        "location": loc_el.get_text(" ", strip=True) if loc_el else "",
        "city": city_el.get_text(" ", strip=True) if city_el else "",
        "registration": href,
    }


def parse_listing_date(month: str, day: str, year: str) -> date | None:
    mon = _MONTHS.get((month or "").strip().lower())
    try:
        d = int(day)
        y = int(year)
    except (TypeError, ValueError):
        return None
    if not mon:
        return None
    try:
        return date(y, mon, d)
    except ValueError:
        return None


def parse_clock(text: str) -> tuple[int, int] | None:
    """First ``at 10 a.m.`` / ``6:30 p.m.`` in the body. None if none."""
    match = _CLOCK.search(text or "")
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    mer = match.group(3).lower().replace(".", "")
    if not (1 <= hour <= 12) or minute > 59:
        return None
    hour %= 12
    if mer == "pm":
        hour += 12
    return hour, minute


def audiences_from_text(text: str) -> list[str]:
    return ["all-ages"] if _FAMILY.search(text or "") else []


def to_raw(card: dict) -> RawEvent:
    day: date | None = card.get("date")
    clock = parse_clock(card.get("excerpt") or "")
    if day and clock:
        start = datetime(day.year, day.month, day.day, clock[0], clock[1], tzinfo=ZoneInfo(TIMEZONE))
    elif day:
        start = datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(TIMEZONE))
    else:
        start = None
    tid = str(int(start.timestamp() * 1000)) if start else card.get("slug") or ""
    return RawEvent(series_uid=str(card.get("slug") or ""), occurrence_tid=tid, record=card)


def _occurrence(item: RawEvent) -> dict:
    rec = item.record
    day: date | None = rec.get("date")
    if day is None:
        raise ScraperError("missing date")
    detail = item.supplement.get("detail") or {}
    clock = parse_clock(detail.get("body") or rec.get("excerpt") or "")
    if clock:
        start = datetime(day.year, day.month, day.day, clock[0], clock[1])
        all_day = False
    else:
        start = datetime(day.year, day.month, day.day)
        all_day = True
    aware = start.replace(tzinfo=ZoneInfo(TIMEZONE))
    tid = str(int(aware.timestamp() * 1000))
    item.occurrence_tid = tid
    return {
        "source_occurrence_tid": tid,
        "start_local": start.isoformat(),
        "timezone": TIMEZONE,
        "all_day": all_day,
        "status": "scheduled",
    }
