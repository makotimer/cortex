"""Wonderful Words Bookshoppe events, via Wix Events.

The public ``/event-list`` page is an events-viewer shell. Dates live on
``GET /_api/wix-events-web/v1/events`` authenticated with the Events app
instance from ``GET /_api/v2/dynamicmodel``. Each Wix row is one
occurrence; the series uid is the slug with the ``-YYYY-MM-DD-HH-MM``
suffix stripped.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError

SITE = "https://www.wonderfulwordsbookshoppe.com"
LIST_URL = SITE + "/event-list"
DYNAMICMODEL = SITE + "/_api/v2/dynamicmodel"
EVENTS_API = SITE + "/_api/wix-events-web/v1/events"
EVENTS_APP_ID = "140603ad-af8d-84a5-2c80-a0f60cb47351"
TIMEZONE = "America/Chicago"
PAGE_SIZE = 50

ORGANIZATION = {
    "slug": "wonderful-words",
    "name": "Wonderful Words Bookshoppe",
    "website_url": SITE + "/",
}

PLACE = {
    "slug": "wonderful-words-bookshoppe",
    "name": "Wonderful Words Bookshoppe",
    "street": "210 W 26th St",
    "city": "Bryan",
    "region": "TX",
    "postcode": "77803",
    "area": "bryan",
    "latitude": 30.6741162,
    "longitude": -96.3744862,
}

_OCCURRENCE_SUFFIX = re.compile(r"-\d{4}-\d{2}-\d{2}-\d{2}-\d{2}$")
_STORYTIME = re.compile(r"\bstory\s*time\b", re.I)
_BOOKCLUB = re.compile(r"\bbookclub\b|\bbook club\b", re.I)


class WonderfulWordsScraper(BaseEventScraper):
    kind = "wonderfulwords"
    source_slug = "wonderful-words"
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
        instance = _events_instance(client)
        raw: list[RawEvent] = []
        offset = 0
        while True:
            page = _get_page(client, instance, offset)
            events = parse_events(page)
            if not events:
                break
            for event in events:
                if not is_scheduled(event):
                    continue
                item = to_raw(event)
                if in_window(item, window_start, window_end):
                    raw.append(item)
            last_start = _start_iso(events[-1])
            if last_start and _date_from_iso(last_start) is not None:
                if _date_from_iso(last_start) < window_start:
                    break
            if len(events) < PAGE_SIZE:
                break
            offset += PAGE_SIZE
        return raw

    def normalize(self, raw: list[RawEvent]) -> tuple[list[dict], list[dict]]:
        payloads: list[dict] = []
        rejected: list[dict] = []
        for item in raw:
            try:
                if not is_scheduled(item.record):
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
        rec = item.record
        title = (rec.get("title") or "").strip()
        if not title:
            raise ScraperError("missing title")
        description = normalize.clean_text(rec.get("description") or rec.get("about"))
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "place": dict(PLACE),
            "topics": topics_from_title(title),
            "audiences": audiences_from_title(title),
            "indoor": True,
        }
        slug = rec.get("slug")
        if slug:
            series["source_url"] = f"{SITE}/event-details/{slug}"
        if description:
            series["description"] = description
        return series


def parse_events(payload: str | dict) -> list[dict]:
    """Wix Events list body -> event dicts. Pure."""
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ScraperError("wonderfulwords: events returned non-JSON") from exc
    else:
        data = payload
    if not isinstance(data, dict):
        raise ScraperError("wonderfulwords: events is not an object")
    events = data.get("events")
    if not isinstance(events, list):
        raise ScraperError("wonderfulwords: events is not a list")
    return [e for e in events if isinstance(e, dict)]


def is_scheduled(event: dict[str, Any]) -> bool:
    return str(event.get("status") or "").upper() == "SCHEDULED"


def to_raw(event: dict[str, Any]) -> RawEvent:
    start_ms = iso_to_ms(_start_iso(event))
    slug = str(event.get("slug") or "")
    return RawEvent(
        series_uid=series_uid_from_slug(slug, event.get("title") or ""),
        occurrence_tid=str(start_ms) if start_ms is not None else "",
        record=dict(event),
    )


def series_uid_from_slug(slug: str, title: str) -> str:
    base = _OCCURRENCE_SUFFIX.sub("", slug or "")
    return base or normalize.slugify(title)


def topics_from_title(title: str) -> list[str]:
    if _STORYTIME.search(title or "") or _BOOKCLUB.search(title or ""):
        return ["reading"]
    return ["community"]


def audiences_from_title(title: str) -> list[str]:
    if _BOOKCLUB.search(title or ""):
        return ["adult"]
    return ["all-ages"]


def in_window(item: RawEvent, window_start: date, window_end: date) -> bool:
    start = _date_from_iso(_start_iso(item.record))
    if start is None:
        return False
    return window_start <= start < window_end


def iso_to_ms(value: str | None) -> int | None:
    dt = _parse_iso(value)
    if dt is None:
        return None
    return int(dt.timestamp() * 1000)


def _events_instance(client: HttpClient) -> str:
    data = client.get_json(DYNAMICMODEL)
    if not isinstance(data, dict):
        raise ScraperError("wonderfulwords: dynamicmodel is not an object")
    apps = data.get("apps")
    if not isinstance(apps, dict):
        raise ScraperError("wonderfulwords: dynamicmodel has no apps")
    app = apps.get(EVENTS_APP_ID)
    instance = (app or {}).get("instance") if isinstance(app, dict) else None
    if not instance:
        raise ScraperError("wonderfulwords: missing Wix Events instance")
    return str(instance)


def _get_page(client: HttpClient, instance: str, offset: int) -> dict:
    data = client.get_json(
        EVENTS_API,
        params={"limit": PAGE_SIZE, "offset": offset},
        headers={"Authorization": instance, "Accept": "application/json"},
    )
    if not isinstance(data, dict):
        raise ScraperError("wonderfulwords: events page is not an object")
    return data


def _start_iso(event: dict[str, Any]) -> str | None:
    cfg = ((event.get("scheduling") or {}).get("config") or {})
    start = cfg.get("startDate")
    return str(start) if start else None


def _end_iso(event: dict[str, Any]) -> str | None:
    cfg = ((event.get("scheduling") or {}).get("config") or {})
    end = cfg.get("endDate")
    return str(end) if end else None


def _timezone(event: dict[str, Any]) -> str:
    cfg = ((event.get("scheduling") or {}).get("config") or {})
    tzid = cfg.get("timeZoneId") or TIMEZONE
    try:
        ZoneInfo(str(tzid))
    except Exception:
        return TIMEZONE
    return str(tzid)


def _occurrence(item: RawEvent) -> dict:
    start_ms = iso_to_ms(_start_iso(item.record))
    if start_ms is None:
        raise ScraperError("missing start")
    tzid = _timezone(item.record)
    occ: dict[str, Any] = {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": normalize.local_iso(start_ms, tzid),
        "timezone": tzid,
        "all_day": False,
        "status": "scheduled",
    }
    end_ms = iso_to_ms(_end_iso(item.record))
    if end_ms is not None:
        occ["end_local"] = normalize.local_iso(end_ms, tzid)
    return occ


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _date_from_iso(value: str | None) -> date | None:
    dt = _parse_iso(value)
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(ZoneInfo(TIMEZONE)).date()
    return dt.date()
