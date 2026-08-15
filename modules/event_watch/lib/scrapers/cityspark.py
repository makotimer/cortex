"""FOX 44 / MyCenTX community calendar, via CitySpark's public widget API.

The listing on fox44news.com is a CitySpark portal (slug ``MyCenTX``) behind
PerimeterX. The widget itself POSTs to portal.cityspark.com with no auth —
that is the feed. Bryan + 15 miles is the filter the station's own URL uses.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError

SLUG = "MyCenTX"
GETEVENTS_URL = f"https://portal.cityspark.com/api/events/GetEvents/{SLUG}"
PAGE_SIZE = 25
PPID = 8299
BRYAN_LAT = 30.6744
BRYAN_LNG = -96.3700
DISTANCE_MILES = 15
TIMEZONE = "America/Chicago"

ORGANIZATION = {
    "slug": "mycentx",
    "name": "FOX 44 Community Calendar",
    "website_url": "https://www.fox44news.com/calendar/",
}

CITY_AREA = {
    "bryan": ("Bryan", "bryan"),
    "college station": ("College Station", "college_station"),
}

#: Tag id -> closed-vocabulary topic. Unknown ids are dropped, never sent.
#: Built from the widget's tag table (parent walk) for ids this feed uses.
TAG_TOPICS = {
    2: "arts",          # Performing Arts
    3: "arts",          # Visual Arts
    16: "arts",         # Arts
    20: "arts",         # Comedy
    4: "reading",       # Literary Arts
    17: "music",        # Music
    100: "music",       # Concerts
    10262: "music",     # Live Music
    6: "sports",        # Sports & Outdoors
    36: "sports",       # Sports
    126: "sports",      # Basketball
    138: "sports",      # Football
    139: "sports",      # Golf
    10049: "sports",    # Cross Country
    46: "science",      # Science
    934: "science",     # STEM
    11: "community",    # Civic Benefit
    12: "community",    # Food & Drink
    74: "community",    # Food
    31: "community",    # Festivals & Fairs
    10051: "community", # Festivals & Street Fairs
    10222: "community", # Comic-Con
}

_ADULT = re.compile(r"18\s*\+|adult show|\b21\s*\+", re.I)
_ESC = re.compile(r"\\(.)")


class _Drop(Exception):
    """Not an error — the record is out of scope and should vanish quietly."""


class CitySparkScraper(BaseEventScraper):
    kind = "cityspark"
    source_slug = "mycentx"
    source_name = ORGANIZATION["name"]
    verify_url = "https://portal.cityspark.com/Content/images/citysparklogoSmall.png"

    def __init__(self, proxy_url: str | None = None) -> None:
        self._proxy_url = proxy_url
        self._client: HttpClient | None = None

    def fetch(
        self, window_start: date, window_end: date, *, skip_network: bool
    ) -> list[RawEvent]:
        if skip_network:
            return []
        client = self._client or HttpClient(
            user_agent="CortexEventWatch/1.0 (+https://discoverbcs.org)",
            proxy_url=self._proxy_url,
            proxy_env=None,
        )
        self._client = client

        records: list[dict] = []
        skip = 0
        while True:
            batch = _get_events(client, window_start, skip)
            if not batch:
                break
            records.extend(batch)
            if len(batch) < PAGE_SIZE:
                break
            skip = len(records)

        return [
            to_raw(record)
            for record in records
            if _in_window(record, window_start, window_end)
        ]

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
        title = (rec.get("Name") or "").strip()
        if not title:
            raise ScraperError("missing title")
        description = unescape_text(rec.get("Description") or "")
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "audiences": audiences_from_text(description or ""),
            "topics": topics_from_tag_ids(rec.get("Tags") or []),
        }
        if description:
            series["description"] = description
        if url := _detail_url(rec):
            series["source_url"] = url
        if rec.get("Free") is True:
            series["is_free"] = True
        series["place"] = _place(rec)
        return series


def to_raw(record: dict) -> RawEvent:
    return RawEvent(
        series_uid=str(record.get("PId") or ""),
        occurrence_tid=str(record.get("Id") or ""),
        record=record,
    )


def topics_from_tag_ids(tag_ids: list[int]) -> list[str]:
    out = {TAG_TOPICS[int(tid)] for tid in tag_ids if int(tid) in TAG_TOPICS}
    return sorted(out)


def audiences_from_text(text: str) -> list[str]:
    return ["adult"] if _ADULT.search(unescape_text(text) or "") else []


def unescape_text(text: str) -> str | None:
    """Undo the widget's backslash escapes (``18\\+``, ``Ms\\.``)."""
    cleaned = normalize.clean_text(_ESC.sub(r"\1", text or ""))
    return cleaned


def wall_clock_local(value: str | None) -> str | None:
    """CitySpark stamps wall-clock Central with a fake ``Z``. Strip it."""
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z"):
        raw = raw[:-1]
    elif "+" in raw[10:]:
        raw = raw.split("+", 1)[0]
    raw = raw.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}", raw):
        raw = raw + ":00"
    return raw or None


def city_area(city: str | None) -> tuple[str, str] | None:
    return CITY_AREA.get((city or "").strip().lower())


def parse_city_state(city_state: str | None) -> tuple[str, str] | None:
    head = (city_state or "").split(",", 1)[0].strip()
    return city_area(head) if head else None


def _place(record: dict) -> dict:
    mapped = parse_city_state(record.get("CityState"))
    if mapped is None:
        city_state = (record.get("CityState") or "").strip()
        if not city_state:
            raise ScraperError("no usable city")
        raise _Drop()
    city, area = mapped
    name = (record.get("Venue") or "").strip() or city
    place: dict[str, Any] = {
        "slug": normalize.slugify(f"{name}-{city}"),
        "name": name,
        "city": city,
        "region": "TX",
        "area": area,
    }
    street = (record.get("Address") or "").strip()
    if street:
        place["street"] = street
    zip_code = record.get("Zip")
    if zip_code:
        place["postcode"] = str(zip_code).strip()
    if isinstance(record.get("latitude"), (int, float)):
        place["latitude"] = float(record["latitude"])
    if isinstance(record.get("longitude"), (int, float)):
        place["longitude"] = float(record["longitude"])
    return place


def _occurrence(item: RawEvent) -> dict:
    rec = item.record
    start = wall_clock_local(rec.get("DateStart"))
    if not start:
        raise ScraperError("missing DateStart")
    occ: dict[str, Any] = {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": start,
        "timezone": TIMEZONE,
        "all_day": bool(rec.get("AllDay")),
        "status": "scheduled",
    }
    end = wall_clock_local(rec.get("DateEnd"))
    if end:
        occ["end_local"] = end
    return occ


def _detail_url(record: dict) -> str | None:
    name = (record.get("Name") or "").strip()
    pid = record.get("PId")
    start = wall_clock_local(record.get("DateStart"))
    if not (name and pid and start):
        return record.get("PrimaryUrl") or None
    stamp = start[:13]  # 2026-08-28T20
    return (
        f"https://www.fox44news.com/calendar/#/details/"
        f"{normalize.slugify(name)}/{pid}/{stamp}"
    )


def _in_window(record: dict, window_start: date, window_end: date) -> bool:
    start = wall_clock_local(record.get("DateStart"))
    if not start:
        return False
    try:
        day = date.fromisoformat(start[:10])
    except ValueError:
        return False
    return window_start <= day < window_end


def _get_events(client: HttpClient, window_start: date, skip: int) -> list[dict]:
    body = {
        "ppid": PPID,
        "start": f"{window_start.isoformat()}T00:00",
        "end": None,
        "labels": [],
        "pick": False,
        "tps": None,
        "sparks": False,
        "sort": "Popularity",
        "category": [],
        "distance": DISTANCE_MILES,
        "lat": BRYAN_LAT,
        "lng": BRYAN_LNG,
        "search": "",
        "skip": skip,
        "defFilter": "all",
    }
    resp = client.session.post(
        GETEVENTS_URL,
        json=body,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
        timeout=client.timeout,
    )
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError as exc:
        raise ScraperError(f"cityspark: non-JSON GetEvents response for {SLUG}") from exc
    if not isinstance(data, dict) or not data.get("Success"):
        err = data.get("ErrorMessage") if isinstance(data, dict) else data
        raise ScraperError(f"cityspark: GetEvents failed for {SLUG}: {err!r}")
    events = data.get("Value")
    if events is None:
        return []
    if not isinstance(events, list):
        raise ScraperError(f"cityspark: GetEvents Value is not a list for {SLUG}")
    return events
