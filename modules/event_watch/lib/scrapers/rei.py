"""REI Co-op classes and events at the College Station store.

The public list (``/events/p/us-tx-college-station``) is a 100-mile
radius. Course cards and their in-radius sessions are embedded in
``#modelData`` as JSON. Austin and Houston rows are dropped; only
location id 214 (College Station REI) is published.

``session.timeZone`` is ``America/Los_Angeles`` on every captured CS
row. Wall-clock times come from the UTC start/end via
``location.timezone`` (``America/Chicago``).
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError

LIST_URL = "https://www.rei.com/events/p/us-tx-college-station"
SITE = "https://www.rei.com"
TIMEZONE = "America/Chicago"
COLLEGE_STATION_LOCATION_ID = "214"

ORGANIZATION = {
    "slug": "rei",
    "name": "REI Co-op",
    "website_url": "https://www.rei.com/",
}

PLACE = {
    "slug": "rei-college-station",
    "name": "College Station REI",
    "street": "615 University Dr. E #300",
    "city": "College Station",
    "region": "TX",
    "postcode": "77840",
    "area": "college_station",
    "latitude": 30.633181,
    "longitude": -96.330664,
}

PROGRAM_TOPICS: dict[str, list[str]] = {
    "CAMPING_AND_HIKING": ["outdoors"],
    "CYCLING": ["sports"],
    "FITNESS": ["sports"],
    "OUTDOOR_SKILLS": ["outdoors"],
    "STEWARDSHIP": ["nature", "outdoors"],
    "SNOWSPORTS": ["sports", "outdoors"],
    "WATERSPORTS": ["sports", "outdoors"],
    "CLIMBING": ["sports", "outdoors"],
}

INDOOR_TYPES = {
    "OS_INDOOR_CLASS",
    "WORKSHOP",
    "PRESENTATION",
    "INDOOR_CLASS",
    "INDOOR_WORKSHOP",
}

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class _Drop(Exception):
    """Out of scope — vanish quietly."""


class ReiScraper(BaseEventScraper):
    kind = "rei"
    source_slug = "rei"
    source_name = ORGANIZATION["name"]
    verify_url = LIST_URL

    def __init__(self, proxy_url: str | None = None) -> None:
        self._proxy_url = proxy_url
        self._client: HttpClient | None = None

    def fetch(self, window_start: date, window_end: date, *, skip_network: bool) -> list[RawEvent]:
        if skip_network:
            return []
        client = self._client or HttpClient(
            user_agent=BROWSER_UA,
            proxy_url=self._proxy_url,
            proxy_env=None,
            timeout=30.0,
        )
        self._client = client
        html = client.get_text(LIST_URL)
        raw: list[RawEvent] = []
        for session in parse_list(html):
            if not is_college_station(session):
                continue
            item = to_raw(session)
            if in_window(item, window_start, window_end):
                raw.append(item)
        return raw

    def normalize(self, raw: list[RawEvent]) -> tuple[list[dict], list[dict]]:
        payloads: list[dict] = []
        rejected: list[dict] = []
        for item in raw:
            try:
                if not is_college_station(item.record):
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
        rec = item.record
        title = (rec.get("name") or "").strip()
        if not title:
            raise ScraperError("missing title")
        description = normalize.clean_text(
            rec.get("longDescription") or rec.get("briefDescription")
        )
        href = rec.get("uri") or rec.get("courseUri") or ""
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "place": dict(PLACE),
            "topics": topics_from(rec),
            "audiences": ["all-ages"],
            "registration_required": True,
        }
        if href:
            series["source_url"] = href if href.startswith("http") else SITE + href
        if description:
            series["description"] = description
        indoor = indoor_from(rec)
        if indoor is not None:
            series["indoor"] = indoor
        member = _price(rec.get("memberPrice"))
        nonmember = _price(rec.get("nonMemberPrice"))
        if member == 0 and (nonmember is None or nonmember == 0):
            series["is_free"] = True
        elif member is not None and member > 0:
            series["cost_low_cents"] = int(round(member * 100))
            dollars = int(member) if float(member).is_integer() else member
            series["cost_note"] = f"from ${dollars}"
        return series


def parse_list(html_text: str) -> list[dict[str, Any]]:
    """``#modelData`` HTML -> flattened session dicts. Pure."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    tag = soup.find("script", id="modelData")
    if tag is None:
        raise ScraperError("rei: page has no modelData")
    try:
        data = json.loads(tag.get_text() or "")
    except json.JSONDecodeError as exc:
        raise ScraperError("rei: modelData is not JSON") from exc
    if not isinstance(data, dict):
        raise ScraperError("rei: modelData is not an object")
    page = data.get("pageData") or {}
    search = page.get("search") if isinstance(page, dict) else None
    if not isinstance(search, dict):
        raise ScraperError("rei: modelData has no search")
    return parse_search(search)


def parse_search(search: dict[str, Any]) -> list[dict[str, Any]]:
    """Search-results object -> one dict per session. Pure."""
    results = search.get("results")
    if not isinstance(results, list):
        raise ScraperError("rei: search.results is not a list")
    out: list[dict[str, Any]] = []
    for course in results:
        if not isinstance(course, dict):
            continue
        out.extend(_sessions_from_course(course))
    return out


def is_college_station(session: dict[str, Any]) -> bool:
    loc = session.get("location") or {}
    if not isinstance(loc, dict):
        return False
    return str(loc.get("id") or "") == COLLEGE_STATION_LOCATION_ID


def to_raw(session: dict[str, Any]) -> RawEvent:
    start_ms = iso_to_ms(session.get("start"))
    return RawEvent(
        series_uid=str(session.get("courseId") or ""),
        occurrence_tid=str(start_ms) if start_ms is not None else "",
        record=dict(session),
    )


def in_window(item: RawEvent, window_start: date, window_end: date) -> bool:
    start = _date_from_iso(item.record.get("start"))
    if start is None:
        return False
    return window_start <= start < window_end


def topics_from(session: dict[str, Any]) -> list[str]:
    program = str(session.get("program") or "").upper()
    return list(PROGRAM_TOPICS.get(program, ["outdoors"]))


def indoor_from(session: dict[str, Any]) -> bool | None:
    types = {str(t).upper() for t in (session.get("types") or [])}
    if types & INDOOR_TYPES:
        return True
    return None


def iso_to_ms(value: str | None) -> int | None:
    dt = _parse_iso(value)
    if dt is None:
        return None
    return int(dt.timestamp() * 1000)


def _sessions_from_course(course: dict[str, Any]) -> list[dict[str, Any]]:
    data = course.get("data") if isinstance(course.get("data"), dict) else {}
    sessions = data.get("sortedSessions") if isinstance(data, dict) else None
    if not isinstance(sessions, list):
        return []
    out: list[dict[str, Any]] = []
    for session in sessions:
        if not isinstance(session, dict):
            continue
        row = dict(session)
        row.setdefault("courseId", course.get("courseId") or data.get("courseId"))
        row.setdefault("name", course.get("name") or data.get("name"))
        row.setdefault("courseUri", course.get("courseUri"))
        row.setdefault("courseTypeLabel", course.get("courseTypeLabel"))
        row["briefDescription"] = data.get("briefDescription") or course.get("briefDescription")
        row["longDescription"] = data.get("longDescription") or course.get("longDescription")
        row["program"] = data.get("program") or course.get("program")
        row["types"] = data.get("types") or course.get("types") or []
        if row.get("memberPrice") is None:
            row["memberPrice"] = data.get("baseMemberPrice")
        if row.get("nonMemberPrice") is None:
            row["nonMemberPrice"] = data.get("baseNonMemberPrice")
        out.append(row)
    return out


def _occurrence(item: RawEvent) -> dict:
    start_ms = iso_to_ms(item.record.get("start"))
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
    end_ms = iso_to_ms(item.record.get("end"))
    if end_ms is not None:
        occ["end_local"] = normalize.local_iso(end_ms, tzid)
    return occ


def _timezone(session: dict[str, Any]) -> str:
    loc = session.get("location") or {}
    tzid = (loc.get("timezone") if isinstance(loc, dict) else None) or TIMEZONE
    try:
        ZoneInfo(tzid)
    except Exception:
        return TIMEZONE
    return tzid


def _price(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
