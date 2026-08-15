"""The Theater Company of Bryan / College Station, via Squarespace calendar JSON.

The /calendar HTML is a 979KB shell. ``GET /calendar?format=json`` already
lists every upcoming night. Location pins are Squarespace's empty NYC
default — venue is the address they publish on the homepage.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError

CALENDAR_URL = "https://www.theatrecompany.com/calendar"
CALENDAR_JSON = CALENDAR_URL + "?format=json"
SITE = "https://www.theatrecompany.com"
TIMEZONE = "America/Chicago"

ORGANIZATION = {
    "slug": "theatre-company",
    "name": "The Theater Company of Bryan / College Station",
    "website_url": SITE + "/",
}

PLACE = {
    "slug": "the-theater-company-bryan",
    "name": "The Theater Company of Bryan / College Station",
    "street": "3125 S Texas Ave, Ste 500",
    "city": "Bryan",
    "region": "TX",
    "postcode": "77802",
    "area": "bryan",
}

_COPY = re.compile(r"\s*\(copy\)\s*$", re.I)
_WORK_WEEK = re.compile(r"\bwork week\b", re.I)


class _Drop(Exception):
    """Not an error — the record is out of scope and should vanish quietly."""


class TtcScraper(BaseEventScraper):
    kind = "ttc"
    source_slug = "theatre-company"
    source_name = ORGANIZATION["name"]
    verify_url = CALENDAR_JSON

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
        data = client.get_json(CALENDAR_JSON)
        if not isinstance(data, dict):
            raise ScraperError("ttc: calendar JSON is not an object")
        raw = [to_raw(rec) for rec in parse_calendar_json(data)]
        return [item for item in raw if in_window(item, window_start, window_end)]

    def normalize(self, raw: list[RawEvent]) -> tuple[list[dict], list[dict]]:
        payloads: list[dict] = []
        rejected: list[dict] = []
        for item in raw:
            try:
                if _WORK_WEEK.search(item.record.get("title") or ""):
                    raise _Drop()
                series = self._series(item)
                occurrence = _occurrence(item)
            except _Drop:
                continue
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
            "topics": [],
            "audiences": [],
            "place": dict(PLACE),
        }
        path = item.record.get("full_url") or ""
        if path:
            series["source_url"] = SITE + path if path.startswith("/") else path
        return series


def parse_calendar_json(data: dict) -> list[dict]:
    """Squarespace calendar payload -> upcoming records. Pure."""
    out: list[dict] = []
    for ev in data.get("upcoming") or []:
        if not isinstance(ev, dict):
            continue
        sc = ev.get("structuredContent") if isinstance(ev.get("structuredContent"), dict) else {}
        start_ms = _as_ms(sc.get("startDate") if sc else None) or _as_ms(ev.get("startDate"))
        if start_ms is None:
            continue
        end_ms = _as_ms(sc.get("endDate") if sc else None) or _as_ms(ev.get("endDate"))
        out.append({
            "id": str(ev.get("id") or ""),
            "title": ev.get("title") or "",
            "url_id": ev.get("urlId") or "",
            "full_url": ev.get("fullUrl") or "",
            "start_ms": start_ms,
            "end_ms": end_ms,
        })
    return out


def to_raw(record: dict) -> RawEvent:
    start_ms = int(record["start_ms"])
    title = clean_title(record.get("title") or "")
    return RawEvent(
        series_uid=normalize.slugify(title),
        occurrence_tid=str(start_ms),
        record=record,
    )


def clean_title(title: str) -> str:
    return _COPY.sub("", (title or "").strip()).strip()


def in_window(item: RawEvent, window_start: date, window_end: date) -> bool:
    start = _date_from_ms(item.record.get("start_ms"))
    if start is None:
        return False
    return window_start <= start <= window_end


def _occurrence(item: RawEvent) -> dict:
    start_ms = item.record.get("start_ms")
    if not start_ms:
        raise ScraperError("missing startDate")
    start_ms = _floor_seconds(int(start_ms))
    occ: dict[str, Any] = {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": normalize.local_iso(start_ms, TIMEZONE),
        "timezone": TIMEZONE,
        "all_day": False,
        "status": "scheduled",
    }
    end_ms = item.record.get("end_ms")
    if end_ms:
        occ["end_local"] = normalize.local_iso(_floor_seconds(int(end_ms)), TIMEZONE)
    return occ


def _as_ms(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _floor_seconds(millis: int) -> int:
    return (millis // 1000) * 1000


def _date_from_ms(value: Any) -> date | None:
    ms = _as_ms(value)
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=ZoneInfo(TIMEZONE)).date()
