"""Bryan-College Station Chamber events, via the GrowthZone XML catalog.

The public search page is a 10-card paint plus scroll. ``GET /api/events``
already returns every upcoming ``EventDisplay``. Query params are ignored;
fetch filters to the run window. ``MapCity`` is often a dummy pin — venue
comes from ``LocationDesc``, then the description.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError

EVENTS_URL = "https://business.bcschamber.org/api/events"
DETAIL_URL = "https://business.bcschamber.org/events/details/{slug}-{event_id}"
TIMEZONE = "America/Chicago"

ORGANIZATION = {
    "slug": "bcs-chamber",
    "name": "Bryan-College Station Chamber of Commerce",
    "website_url": "https://www.bcschamber.org/",
}

CATEGORY_TOPICS = {
    2: "community",  # Business After Hours
    4: "community",  # Government
    6: "community",  # Ribbon Cuttings
}

CITY_AREA = {
    "bryan": ("Bryan", "bryan"),
    "college station": ("College Station", "college_station"),
}

ZIP_CITY = {
    "77801": "Bryan",
    "77802": "Bryan",
    "77803": "Bryan",
    "77807": "Bryan",
    "77808": "Bryan",
    "77840": "College Station",
    "77841": "College Station",
    "77842": "College Station",
    "77843": "College Station",
    "77844": "College Station",
    "77845": "College Station",
}

_CITY_LINE = re.compile(
    r"\b(?P<city>Bryan|College Station),\s*TX(?:as)?(?:\s+(?P<zip>\d{5}))?\b",
    re.I,
)
_IN_CITY = re.compile(r"\bin\s+(?P<city>Bryan|College Station)\b", re.I)
_ZIP = re.compile(r"\b(7780[12378]|7784[0-5])\b")
_NAMED_CITY = re.compile(r"\b(College Station|Bryan)\b", re.I)
_FREE = re.compile(r"\bfree\b", re.I)
_TIMEISH = re.compile(
    r"(\d{1,2}:\d{2}|\d{1,2}\s*(a\.?m\.?|p\.?m\.?)|"
    r"monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"january|february|march|april|may|june|july|august|september|"
    r"october|november|december)",
    re.I,
)
_STREETISH = re.compile(r"^\d+\s")


class _Drop(Exception):
    """Not an error — the record is out of scope and should vanish quietly."""


class BcsChamberScraper(BaseEventScraper):
    kind = "bcschamber"
    source_slug = "bcs-chamber"
    source_name = ORGANIZATION["name"]
    verify_url = EVENTS_URL

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
        xml = client.get_text(EVENTS_URL)
        raw = [to_raw(rec) for rec in parse_events_xml(xml)]
        return [item for item in raw if in_window(item, window_start, window_end)]

    def normalize(self, raw: list[RawEvent]) -> tuple[list[dict], list[dict]]:
        payloads: list[dict] = []
        rejected: list[dict] = []
        for item in raw:
            try:
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
        title = (rec.get("title") or "").strip()
        if not title:
            raise ScraperError("missing title")
        description = strip_html(rec.get("description") or "")
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "topics": topics_from_categories(rec.get("categories") or []),
            "audiences": [],
            "place": parse_place(rec),
        }
        if description:
            series["description"] = description
        slug = rec.get("slug") or ""
        if slug and item.series_uid:
            series["source_url"] = DETAIL_URL.format(slug=slug, event_id=item.series_uid)
        if is_free(rec.get("admission") or ""):
            series["is_free"] = True
        return series


def parse_events_xml(xml_text: str) -> list[dict]:
    """GrowthZone ``ArrayOfEventDisplay`` -> record dicts. Pure."""
    root = ET.fromstring(xml_text or "<ArrayOfEventDisplay/>")
    out: list[dict] = []
    for ev in root.findall("EventDisplay"):
        status = (_child_text(ev, "Status") or "").strip().upper()
        if status and status != "APPROVED":
            continue
        start = _local_iso(_child_text(ev, "StartDate"))
        if not start:
            continue
        cats: list[int] = []
        for node in ev.findall("Categories/int"):
            try:
                cats.append(int(node.text or ""))
            except ValueError:
                continue
        out.append({
            "event_id": _child_text(ev, "EventID") or "",
            "title": _child_text(ev, "Name") or "",
            "description": _child_text(ev, "Description") or "",
            "location_html": _child_text(ev, "LocationDesc") or "",
            "admission": _child_text(ev, "AdmissionDesc") or "",
            "slug": _child_text(ev, "Slug") or "",
            "start_local": start,
            "end_local": _local_iso(_child_text(ev, "EndDate")),
            "all_day": (_child_text(ev, "IsAllDayEvent") or "").lower() == "true",
            "categories": cats,
        })
    return out


def to_raw(record: dict) -> RawEvent:
    start = record.get("start_local") or ""
    return RawEvent(
        series_uid=str(record.get("event_id") or ""),
        occurrence_tid=local_to_tid(start) if start else "",
        record=record,
    )


def local_to_tid(start_local: str) -> str:
    dt = datetime.fromisoformat(start_local).replace(tzinfo=ZoneInfo(TIMEZONE))
    return str(int(dt.timestamp() * 1000))


def in_window(item: RawEvent, window_start: date, window_end: date) -> bool:
    start = _as_date(item.record.get("start_local"))
    if start is None:
        return False
    return window_start <= start <= window_end


def topics_from_categories(ids: list[int]) -> list[str]:
    return sorted({CATEGORY_TOPICS[i] for i in ids if i in CATEGORY_TOPICS})


def is_free(text: str) -> bool:
    return bool(_FREE.search(strip_html(text) or ""))


def strip_html(html: str) -> str | None:
    text = BeautifulSoup(html or "", "html.parser").get_text("\n", strip=True)
    return normalize.clean_text(text)


def parse_place(record: dict) -> dict:
    location = strip_html(record.get("location_html") or "") or ""
    description = strip_html(record.get("description") or "") or ""
    parsed = _place_from_text(location, allow_bare_name=True) or _place_from_text(
        description, allow_bare_name=False
    )
    if parsed is None:
        raise ScraperError("missing city")
    city, area = parsed["city_area"]
    if city is None:
        raise _Drop()
    name = parsed.get("name") or city
    place: dict[str, Any] = {
        "slug": normalize.slugify(f"{name}-{city}"),
        "name": name,
        "city": city,
        "region": "TX",
        "area": area,
    }
    if parsed.get("street"):
        place["street"] = parsed["street"]
    if parsed.get("postcode"):
        place["postcode"] = parsed["postcode"]
    return place


def _place_from_text(text: str, *, allow_bare_name: bool = True) -> dict[str, Any] | None:
    raw = text or ""
    if not raw.strip():
        return None
    city: str | None = None
    postcode: str | None = None
    match = _CITY_LINE.search(raw)
    if match:
        mapped = CITY_AREA.get(match.group("city").strip().lower())
        if mapped:
            city = mapped[0]
        else:
            return {"city_area": (None, None)}
        postcode = match.group("zip")
    if city is None:
        zip_match = _ZIP.search(raw)
        if zip_match:
            city = ZIP_CITY[zip_match.group(1)]
            postcode = zip_match.group(1)
    if city is None:
        in_city = _IN_CITY.search(raw)
        if in_city:
            city = CITY_AREA[in_city.group("city").strip().lower()][0]
    if city is None:
        end_city = re.search(r"(\d\S.*)\s+(Bryan|College Station)\s*$", raw, re.I | re.S)
        if end_city:
            city = CITY_AREA[end_city.group(2).strip().lower()][0]
    if city is None and allow_bare_name:
        named = _NAMED_CITY.search(raw)
        if named:
            city = CITY_AREA[named.group(1).strip().lower()][0]
    if city is None:
        other = re.search(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*),\s*TX(?:as)?\b", raw)
        if other and other.group(1).lower() not in CITY_AREA:
            return {"city_area": (None, None)}
        return None
    area = CITY_AREA[city.lower()][1]
    lines = [ln.strip(" |") for ln in raw.splitlines() if ln.strip()]
    useful = [ln for ln in lines if not _TIMEISH.search(ln) or _STREETISH.match(ln)]
    name = None
    street = None
    for ln in useful:
        if _CITY_LINE.search(ln):
            continue
        if _STREETISH.match(ln):
            street = street or ln.split(",")[0].strip()
            if name is None:
                name = street
        elif name is None:
            name = ln
    if street is None:
        for ln in useful:
            m = re.search(r"(\d+\s+[^,\n]+)", ln)
            if m and "in " not in m.group(1).lower():
                street = m.group(1).strip()
                break
    return {
        "city_area": (city, area),
        "name": name,
        "street": street,
        "postcode": postcode,
    }


def _child_text(el: ET.Element, tag: str) -> str | None:
    child = el.find(tag)
    if child is None or child.text is None:
        return None
    return child.text


def _local_iso(value: str | None) -> str | None:
    raw = (value or "").strip()
    if not raw:
        return None
    raw = raw.replace("Z", "")
    if raw.endswith("+00:00"):
        raw = raw[:-6]
    return raw.replace(" ", "T", 1) if "T" not in raw else raw


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
        "all_day": bool(item.record.get("all_day")),
        "status": "scheduled",
    }
    end = item.record.get("end_local")
    if end:
        occ["end_local"] = end
    return occ
