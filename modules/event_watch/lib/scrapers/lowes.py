"""Lowe's Kids Club workshops, via the public workshopdata JSON.

The Kids Club landing page is a marketing SPA. Named workshops, dates and
registration slugs come from ``/workshopdata``. ``start``/``end`` are Eastern
wall-clock wearing a ``Z`` — the same class of bug as CitySpark. The calendar
date is taken from America/New_York; the published window is 10:00–13:00
America/Chicago, which is what Lowe's own registration page states.

The feed is national. Only the College Station store is published.
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError

WORKSHOPDATA_URL = (
    "https://www.lowes.com/workshopdata"
    "?template=REGISTRATION&types=WORKSHOP&closed=false"
)
REGISTER_URL = "https://www.lowes.com/events/register/{slug}"
#: workshopdata 403s the VPN probe's default UA (status >= 400 is
#: treated as a dead exit). The engine default is a known-good probe.
VERIFY_URL = "https://tockify.com/"
TIMEZONE = "America/Chicago"
EASTERN = ZoneInfo("America/New_York")
START_CLOCK = time(10, 0)
END_CLOCK = time(13, 0)

ORGANIZATION = {
    "slug": "lowes",
    "name": "Lowe's",
    "website_url": "https://www.lowes.com/",
}

STORES: tuple[dict[str, Any], ...] = (
    {
        "id": "3032",
        "slug": "lowes-college-station",
        "name": "College Station Lowe's",
        "street": "4451 State Highway 6 S",
        "city": "College Station",
        "region": "TX",
        "postcode": "77845",
        "area": "college_station",
    },
)

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class LowesScraper(BaseEventScraper):
    kind = "lowes"
    source_slug = "lowes"
    source_name = ORGANIZATION["name"]
    verify_url = VERIFY_URL

    def __init__(self, proxy_url: str | None = None) -> None:
        self._proxy_url = proxy_url
        self._client: HttpClient | None = None

    def fetch(
        self, window_start: date, window_end: date, *, skip_network: bool
    ) -> list[RawEvent]:
        if skip_network:
            return []
        client = self._client or HttpClient(
            user_agent=BROWSER_UA,
            proxy_url=self._proxy_url,
            proxy_env=None,
            timeout=30.0,
        )
        self._client = client
        payload = client.get_json(WORKSHOPDATA_URL)
        raw: list[RawEvent] = []
        for event in parse_workshopdata(payload):
            if not keep_kids_workshop(event):
                continue
            for item in to_raw_events(event):
                if in_window(item, window_start, window_end):
                    raw.append(item)
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


def parse_workshopdata(payload: Any) -> list[dict[str, Any]]:
    """API object -> event dicts. Pure."""
    if not isinstance(payload, dict):
        raise ScraperError("lowes: workshopdata is not an object")
    rows = payload.get("data")
    if not isinstance(rows, list):
        raise ScraperError("lowes: workshopdata.data is not a list")
    return [row for row in rows if isinstance(row, dict)]


def keep_kids_workshop(event: dict[str, Any]) -> bool:
    if str(event.get("subType") or "") != "KIDS":
        return False
    if event.get("isPaid") is True:
        return False
    tags = {str(t) for t in (event.get("tags") or [])}
    if "NO_LOCATION" in tags:
        return False
    slug = str(event.get("url") or "")
    if "senior-builder" in slug:
        return False
    return True


def to_raw_events(event: dict[str, Any]) -> list[RawEvent]:
    """One national workshop -> one RawEvent per local store. Pure."""
    day = workshop_date(event.get("start"))
    if day is None:
        return []
    event_id = str(event.get("id") or "")
    out: list[RawEvent] = []
    for store in STORES:
        record = dict(event)
        record["day"] = day
        record["store"] = dict(store)
        out.append(RawEvent(
            series_uid=event_id,
            occurrence_tid=f"{event_id}:{store['id']}",
            record=record,
        ))
    return out


def in_window(item: RawEvent, window_start: date, window_end: date) -> bool:
    day = item.record.get("day")
    if not isinstance(day, date):
        return False
    return window_start <= day < window_end


def workshop_date(value: Any) -> date | None:
    """Eastern wall-clock stored as UTC -> the calendar date Lowe's means."""
    dt = _parse_iso(value)
    if dt is None:
        return None
    return dt.astimezone(EASTERN).date()


def _series(item: RawEvent) -> dict[str, Any]:
    rec = item.record
    name = (rec.get("name") or "").strip()
    if not name:
        raise ScraperError("missing title")
    slug = (rec.get("url") or "").strip()
    if not slug:
        raise ScraperError("missing registration slug")
    store = rec.get("store") or {}
    if not store.get("name"):
        raise ScraperError("missing store")
    url = REGISTER_URL.format(slug=slug)
    note = _description(rec)
    series: dict[str, Any] = {
        "source_series_uid": item.series_uid,
        "title": f"Kids Workshop: {name}",
        "source_url": url,
        "registration_url": url,
        "organization": dict(ORGANIZATION),
        "place": _place(store),
        "topics": ["crafts"],
        "audiences": ["elementary"],
        "is_free": True,
        "registration_required": True,
        "indoor": True,
    }
    if note:
        series["description"] = note
    return series


def _occurrence(item: RawEvent) -> dict[str, Any]:
    day = item.record.get("day")
    if not isinstance(day, date):
        raise ScraperError("missing start")
    return {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": datetime.combine(day, START_CLOCK).isoformat(),
        "end_local": datetime.combine(day, END_CLOCK).isoformat(),
        "timezone": TIMEZONE,
        "all_day": False,
        "status": "scheduled",
    }


def _place(store: dict[str, Any]) -> dict[str, Any]:
    place = {
        "slug": store["slug"],
        "name": store["name"],
        "street": store["street"],
        "city": store["city"],
        "region": store.get("region") or "TX",
        "postcode": store["postcode"],
        "area": store["area"],
    }
    if store.get("latitude") is not None:
        place["latitude"] = store["latitude"]
        place["longitude"] = store["longitude"]
    return place


def _description(event: dict[str, Any]) -> str | None:
    meta = event.get("meta") or {}
    en = meta.get("en-US") if isinstance(meta, dict) else None
    tile = (en or {}).get("tileContent") if isinstance(en, dict) else None
    page = (tile or {}).get("registrationPage") if isinstance(tile, dict) else None
    note = (page or {}).get("note") if isinstance(page, dict) else None
    return normalize.clean_text(note)


def _parse_iso(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None
