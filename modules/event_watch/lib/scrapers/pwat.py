"""Painting with a Twist College Station calendar.

The public calendar is server-rendered HTML. ``time.event-datetime``
stores a 12-hour clock in the ``T`` field (``T03:00`` with
``3:00 pm`` in the text is 15:00, not 03:00). Times come from
``.event-time``; the date comes from the attribute's calendar day.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any

from bs4 import BeautifulSoup

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError

LIST_URL = "https://www.paintingwithatwist.com/studio/college-station/calendar/"
SITE = "https://www.paintingwithatwist.com"
TIMEZONE = "America/Chicago"

ORGANIZATION = {
    "slug": "painting-with-a-twist",
    "name": "Painting with a Twist",
    "website_url": "https://www.paintingwithatwist.com/studio/college-station/",
}

PLACE = {
    "slug": "painting-with-a-twist-college-station",
    "name": "Painting with a Twist",
    "street": "1643 Texas Ave S",
    "city": "College Station",
    "region": "TX",
    "postcode": "77840",
    "area": "college_station",
}

_EVENT_ID = re.compile(r"/event/(\d+)")
_PRICE = re.compile(r"\$(\d+(?:\.\d+)?)")
_FAMILY = re.compile(r"\bfamily day\b|\ball ages\b", re.I)
_TIME_RANGE = re.compile(
    r"(?P<sh>\d{1,2}):(?P<sm>\d{2})\s*(?P<sap>a\.?m\.?|p\.?m\.?)"
    r"\s*[-–]\s*"
    r"(?P<eh>\d{1,2}):(?P<em>\d{2})\s*(?P<eap>a\.?m\.?|p\.?m\.?)",
    re.I,
)


class PwatScraper(BaseEventScraper):
    kind = "pwat"
    source_slug = "painting-with-a-twist"
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
        cards = parse_calendar(client.get_text(LIST_URL))
        raw: list[RawEvent] = []
        for card in cards:
            item = to_raw(card)
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
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "place": dict(PLACE),
            "topics": ["arts"],
            "audiences": audiences_from_title(title),
            "indoor": True,
            "registration_required": True,
            "source_url": f"{SITE}/studio/college-station/event/{item.series_uid}/",
        }
        cents = rec.get("cost_cents")
        if cents == 0:
            series["is_free"] = True
        elif isinstance(cents, int) and cents > 0:
            series["cost_low_cents"] = cents
            dollars = cents // 100
            series["cost_note"] = f"${dollars}"
        return series


def parse_calendar(html_text: str) -> list[dict[str, Any]]:
    """Studio calendar HTML -> card dicts. Pure."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for article in soup.select("div.event-article"):
        link = article.select_one('a[href*="/event/"]')
        if not link:
            continue
        match = _EVENT_ID.search(link.get("href") or "")
        if not match:
            continue
        eid = match.group(1)
        if eid in seen:
            continue
        title_el = article.select_one(".event-title")
        title = title_el.get_text(" ", strip=True) if title_el else link.get_text(" ", strip=True)
        dt_el = article.select_one("time.event-datetime")
        range_el = article.select_one("time.event-time")
        price_el = article.select_one(".event-price")
        datetime_attr = dt_el.get("datetime") if dt_el else None
        day = _date_from_attr(datetime_attr)
        start, end = parse_time_range(range_el.get_text(" ", strip=True) if range_el else "")
        if day is None or start is None:
            continue
        seen.add(eid)
        out.append({
            "event_id": eid,
            "title": title,
            "datetime_attr": datetime_attr,
            "start": datetime.combine(day, start),
            "end": datetime.combine(day, end) if end else None,
            "cost_cents": parse_price(price_el.get_text() if price_el else ""),
        })
    return out


def to_raw(card: dict[str, Any]) -> RawEvent:
    start: datetime | None = card.get("start")
    start_ms = int(start.replace(tzinfo=_chicago()).timestamp() * 1000) if start else None
    return RawEvent(
        series_uid=str(card.get("event_id") or ""),
        occurrence_tid=str(start_ms) if start_ms is not None else "",
        record=dict(card),
    )


def parse_time_range(text: str) -> tuple[time | None, time | None]:
    match = _TIME_RANGE.search(text or "")
    if not match:
        return None, None
    return (
        _clock(match.group("sh"), match.group("sm"), match.group("sap")),
        _clock(match.group("eh"), match.group("em"), match.group("eap")),
    )


def parse_price(text: str) -> int | None:
    match = _PRICE.search(text or "")
    if not match:
        return None
    return int(round(float(match.group(1)) * 100))


def audiences_from_title(title: str) -> list[str]:
    if _FAMILY.search(title or ""):
        return ["all-ages"]
    return ["adult"]


def in_window(item: RawEvent, window_start: date, window_end: date) -> bool:
    start = item.record.get("start")
    if not isinstance(start, datetime):
        return False
    return window_start <= start.date() < window_end


def _occurrence(item: RawEvent) -> dict:
    start = item.record.get("start")
    if not isinstance(start, datetime):
        raise ScraperError("missing start")
    start_ms = int(start.replace(tzinfo=_chicago()).timestamp() * 1000)
    occ: dict[str, Any] = {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": normalize.local_iso(start_ms, TIMEZONE),
        "timezone": TIMEZONE,
        "all_day": False,
        "status": "scheduled",
    }
    end = item.record.get("end")
    if isinstance(end, datetime):
        end_ms = int(end.replace(tzinfo=_chicago()).timestamp() * 1000)
        occ["end_local"] = normalize.local_iso(end_ms, TIMEZONE)
    return occ


def _date_from_attr(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _clock(hour: str, minute: str, ampm: str) -> time:
    h = int(hour)
    m = int(minute)
    ap = ampm.lower().replace(".", "")
    if ap == "am":
        if h == 12:
            h = 0
    elif h != 12:
        h += 12
    return time(h, m)


def _chicago():
    from zoneinfo import ZoneInfo

    return ZoneInfo(TIMEZONE)
