"""Brazos Valley Museum Upcoming Events, via the homepage Wix repeater.

The /calendar widget is a dead Wix Events install. The homepage strip is what
they keep as "upcoming": four static cards, yearless month/day. Fetch assigns
the run window's year and drops cards whose span is already past.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError

HOME_URL = "https://www.brazosvalleymuseum.org/"
TIMEZONE = "America/Chicago"
UPCOMING_REPEATER = "#comp-k5cycbh5"

ORGANIZATION = {
    "slug": "brazos-valley-museum",
    "name": "Brazos Valley Museum of Natural History",
    "website_url": HOME_URL,
}

PLACE = {
    "slug": "brazos-valley-museum-of-natural-history-bryan",
    "name": "Brazos Valley Museum of Natural History",
    "street": "3232 Briarcrest Dr",
    "city": "Bryan",
    "region": "TX",
    "postcode": "77802",
    "area": "bryan",
}

_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_MONTH = r"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
_DATE_RANGE = re.compile(
    rf"(?P<sm>{_MONTH})\s+(?P<sd>\d{{1,2}})"
    rf"(?:\s*[-\u2013]\s*(?:(?P<em>{_MONTH})\s+)?(?P<ed>\d{{1,2}}))?",
    re.I,
)
_CLOCK = re.compile(
    r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<ampm>a\.?m\.?|p\.?m\.?)",
    re.I,
)
_ITEM_ID = re.compile(r"__item-(?P<id>.+)$")
_FREE = re.compile(r"admission is free", re.I)


class BvMuseumScraper(BaseEventScraper):
    kind = "bvmuseum"
    source_slug = "brazos-valley-museum"
    source_name = ORGANIZATION["name"]
    verify_url = HOME_URL

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
        html = client.get_text(HOME_URL)
        raw: list[RawEvent] = []
        for card in parse_upcoming_html(html):
            try:
                item = to_raw(card, year=window_start.year)
            except ScraperError:
                continue
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
        title = (rec.get("title") or "").strip()
        if not title:
            raise ScraperError("missing title")
        description = normalize.clean_text(rec.get("description"))
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "topics": [],
            "audiences": [],
            "place": dict(PLACE),
        }
        if description:
            series["description"] = description
        if rec.get("href"):
            series["source_url"] = rec["href"]
        if is_free(description or ""):
            series["is_free"] = True
        return series


def parse_upcoming_html(html: str) -> list[dict]:
    """Homepage HTML -> Upcoming Events cards. Pure. Ignores other repeaters."""
    soup = BeautifulSoup(html or "", "html.parser")
    strip = soup.select_one(UPCOMING_REPEATER)
    if strip is None:
        return []
    out: list[dict] = []
    for item in strip.select(".wixui-repeater__item"):
        card = _parse_card(item)
        if card:
            out.append(card)
    return out


def to_raw(card: dict, *, year: int) -> RawEvent:
    start_local, end_local = parse_when(card, year)
    if start_local is None:
        raise ScraperError("missing date")
    record = dict(card)
    record["year"] = year
    record["start_local"] = start_local
    if end_local:
        record["end_local"] = end_local
    return RawEvent(
        series_uid=series_uid(card),
        occurrence_tid=local_to_tid(start_local),
        record=record,
    )


def series_uid(card: dict) -> str:
    href = card.get("href") or ""
    path = urlparse(href).path.strip("/")
    if path:
        return path.rsplit("/", 1)[-1]
    return str(card.get("item_id") or normalize.slugify(card.get("title") or ""))


def parse_when(card: dict, year: int) -> tuple[str | None, str | None]:
    date_text = card.get("date_text") or ""
    match = _DATE_RANGE.search(date_text)
    if match is None:
        return None, None
    start_d = date(year, _month(match.group("sm")), int(match.group("sd")))
    end_d = start_d
    if match.group("ed"):
        end_month = match.group("em") or match.group("sm")
        end_d = date(year, _month(end_month), int(match.group("ed")))
        if end_d < start_d:
            end_d = date(year + 1, end_d.month, end_d.day)
    start_t, end_t = parse_clocks(date_text)
    if start_t is None:
        start_t, extra_end = parse_clocks(card.get("description") or "")
        if end_t is None:
            end_t = extra_end
    start_local = datetime.combine(start_d, start_t or time(0, 0)).isoformat()
    end_local = None
    if end_t is not None or end_d != start_d:
        end_local = datetime.combine(end_d, end_t or start_t or time(0, 0)).isoformat()
    return start_local, end_local


def parse_clocks(text: str) -> tuple[time | None, time | None]:
    found = [_to_time(m) for m in _CLOCK.finditer(text or "")]
    if not found:
        return None, None
    if len(found) == 1:
        return found[0], None
    return found[0], found[1]


def local_to_tid(start_local: str) -> str:
    dt = datetime.fromisoformat(start_local).replace(tzinfo=ZoneInfo(TIMEZONE))
    return str(int(dt.timestamp() * 1000))


def in_window(item: RawEvent, window_start: date, window_end: date) -> bool:
    start = _as_date(item.record.get("start_local"))
    if start is None:
        return False
    end = _as_date(item.record.get("end_local")) or start
    return not (end < window_start or start > window_end)


def is_free(text: str) -> bool:
    return bool(_FREE.search(text or ""))


def _parse_card(item: Any) -> dict | None:
    title = _comp_text(item, "comp-k5cycbls")
    if not title:
        return None
    href = ""
    link = item.select_one("a.wixui-button[href], a[aria-label='Learn More'][href]")
    if link is not None:
        href = str(link.get("href") or "")
    return {
        "item_id": _item_id(item.get("id") or ""),
        "title": title,
        "date_text": _comp_text(item, "comp-k5cycbn7", joiner="\n"),
        "description": _comp_text(item, "comp-k5cycbix", joiner="\n"),
        "href": href,
    }


def _comp_text(item: Any, class_substr: str, joiner: str = " ") -> str:
    el = item.select_one(f'[class*="{class_substr}"]')
    if el is None:
        return ""
    return normalize.clean_text(el.get_text(joiner, strip=True)) or ""


def _item_id(raw_id: str) -> str:
    match = _ITEM_ID.search(raw_id)
    if match:
        return match.group("id")
    # Wix's first repeater item is ``…__item1``, not ``…__item-…``.
    if "__item" in raw_id:
        return "item" + raw_id.rsplit("__item", 1)[1]
    return raw_id


def _month(name: str) -> int:
    return _MONTHS[name.strip().lower()]


def _to_time(match: re.Match[str]) -> time:
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    ampm = match.group("ampm").lower().replace(".", "")
    if ampm == "am" and hour == 12:
        hour = 0
    elif ampm == "pm" and hour != 12:
        hour += 12
    return time(hour, minute)


def _as_date(value: str | None) -> date | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        return None


def _occurrence(item: RawEvent) -> dict:
    start = item.record.get("start_local")
    if not start:
        raise ScraperError("missing start_local")
    occ: dict[str, Any] = {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": start,
        "timezone": TIMEZONE,
        "all_day": False,
        "status": "scheduled",
    }
    end = item.record.get("end_local")
    if end:
        occ["end_local"] = end
    return occ
