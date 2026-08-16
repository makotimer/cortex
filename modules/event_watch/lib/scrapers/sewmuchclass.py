"""Sew Much Class studio calendar, via Rain POS month grids.

The public calendar is ``/module/events.htm?pageComponentId=5886934``.
PHD open-sew, 4-packs, studio-closed notices and training are dropped.
Named classes and trips stay.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError

SITE = "https://www.sewmuchclass.com"
LIST_URL = f"{SITE}/module/events.htm?pageComponentId=5886934"
TIMEZONE = "America/Chicago"
COMPONENT_ID = "5886934"

ORGANIZATION = {
    "slug": "sewmuchclass",
    "name": "Sew Much Class",
    "website_url": SITE + "/",
}

PLACE = {
    "slug": "sew-much-class",
    "name": "Sew Much Class",
    "street": "2700 Earl Rudder Freeway S Suite 1100",
    "city": "College Station",
    "region": "TX",
    "postcode": "77845",
    "area": "college_station",
}

_MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
    "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6,
    "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "september": 9,
    "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
_CLOCK = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2})\s*(?P<ap>a\.?m\.?|p\.?m\.?)",
    re.I,
)
_DATE_PREFIX = re.compile(r"^\d{1,2}/\d{1,2}/\d{4}\s+")
_DROP = re.compile(
    r"\bphd\b|4-pack|4 pack|studio closed|\btraining\b",
    re.I,
)
_TRIP = re.compile(r"\bbus trip\b|\bfield trip\b", re.I)


class SewMuchClassScraper(BaseEventScraper):
    kind = "sewmuchclass"
    source_slug = "sewmuchclass"
    source_name = ORGANIZATION["name"]
    verify_url = LIST_URL + "&month=current"

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
        raw: list[RawEvent] = []
        seen: set[str] = set()
        year, month = window_start.year, window_start.month
        while date(year, month, 1) < window_end:
            html = client.get_text(_month_url(year, month))
            for card in parse_calendar(html):
                if is_dropped(card["title"]):
                    continue
                item = to_raw(card)
                if item.series_uid in seen:
                    continue
                if not in_window(item, window_start, window_end):
                    continue
                seen.add(item.series_uid)
                raw.append(item)
            if month == 12:
                year, month = year + 1, 1
            else:
                month += 1
        return raw

    def normalize(self, raw: list[RawEvent]) -> tuple[list[dict], list[dict]]:
        payloads: list[dict] = []
        rejected: list[dict] = []
        for item in raw:
            try:
                if is_dropped(item.record.get("title") or ""):
                    continue
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
        title = clean_title(item.record.get("title") or "")
        if not title:
            raise ScraperError("missing title")
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "place": dict(PLACE),
            "topics": ["crafts"],
            "audiences": ["adult"],
            "indoor": True,
            "registration_required": True,
            "source_url": item.record.get("url") or LIST_URL,
        }
        if _TRIP.search(title):
            series["field_trip"] = True
        return series


def parse_calendar(html_text: str) -> list[dict[str, Any]]:
    """Rain POS month grid -> card dicts. Pure."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    out: list[dict[str, Any]] = []
    for ev in soup.select("div.calEvent"):
        onclick = ev.get("onclick") or ""
        url = _url_from_onclick(onclick)
        params = parse_qs(urlparse(url).query) if url else {}
        event_id = (params.get("eventId") or [""])[0]
        day = _int(params.get("day", [None])[0])
        year = _int(params.get("year", [None])[0])
        month = _month(params.get("month", [None])[0])
        title = clean_title(ev.get_text("\n", strip=True).split("\n")[-1] if ev.get_text() else "")
        # title is after the bold time; prefer last text node
        bold = ev.find("b")
        clock = bold.get_text(" ", strip=True) if bold else ""
        # rebuild title without the time line
        full = ev.get_text("\n", strip=True)
        if bold:
            title = clean_title(full.replace(bold.get_text(), "", 1))
        start_t, end_t = parse_clock(clock)
        start = None
        end = None
        if year and month and day and start_t:
            start = datetime(year, month, day, start_t.hour, start_t.minute)
            if end_t:
                end = datetime(year, month, day, end_t.hour, end_t.minute)
        out.append({
            "event_id": event_id,
            "title": title,
            "url": url,
            "start": start,
            "end": end,
        })
    return out


def is_dropped(title: str) -> bool:
    return bool(_DROP.search(title or ""))


def clean_title(title: str) -> str:
    text = _DATE_PREFIX.sub("", (title or "").strip())
    return normalize.clean_text(text) or ""


def parse_clock(text: str) -> tuple[time | None, time | None]:
    found = list(_CLOCK.finditer(text or ""))
    if not found:
        return None, None
    start = _clock_match(found[0])
    end = _clock_match(found[1]) if len(found) > 1 else None
    return start, end


def to_raw(card: dict[str, Any]) -> RawEvent:
    start: datetime | None = card.get("start")
    start_ms = (
        int(start.replace(tzinfo=ZoneInfo(TIMEZONE)).timestamp() * 1000)
        if start
        else None
    )
    return RawEvent(
        series_uid=str(card.get("event_id") or ""),
        occurrence_tid=str(start_ms) if start_ms is not None else "",
        record=dict(card),
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
    if isinstance(end, datetime) and end > start:
        end_ms = int(end.replace(tzinfo=ZoneInfo(TIMEZONE)).timestamp() * 1000)
        occ["end_local"] = normalize.local_iso(end_ms, TIMEZONE)
    return occ


def _month_url(year: int, month: int) -> str:
    return f"{LIST_URL}&month={month}&year={year}"


def _url_from_onclick(onclick: str) -> str:
    match = re.search(r"location='([^']+)'", onclick or "")
    return match.group(1) if match else ""


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _month(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, int) or str(value).isdigit():
        n = int(value)
        return n if 1 <= n <= 12 else None
    return _MONTHS.get(str(value).strip().lower())


def _clock_match(match: re.Match[str]) -> time:
    h = int(match.group("h"))
    m = int(match.group("m"))
    ap = match.group("ap").lower().replace(".", "")
    if ap == "am":
        if h == 12:
            h = 0
    elif h != 12:
        h += 12
    return time(h, m)
