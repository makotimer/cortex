"""KBTX Community Calendar, via Tockify (calname=kbtx.calendar).

Same public JSON + ICS pair as the library calendar, but this is a community
board: many one-off venues, mixed cities, freeform tags, and months-long
listings that are not attendable dates.

Fetch reuses the Tockify parse helpers. Place, geo and topic rules live here.
Address resolution (when Tockify omitted a usable city) is I/O and happens in
``enrich_places`` during fetch; ``normalize`` only reads what was attached.
"""

from __future__ import annotations

import hashlib
import re
from datetime import date
from typing import Any

from modules._shared.http import HttpClient

from .. import normalize
from . import tockify
from .base import BaseEventScraper, RawEvent, ScraperError

CALNAME = "kbtx.calendar"
NGEVENT_URL = tockify.NGEVENT_URL
ICS_URL = f"https://tockify.com/api/feeds/ics/{CALNAME}"

ORGANIZATION = {
    "slug": "kbtx",
    "name": "KBTX Community Calendar",
    "website_url": "https://www.kbtx.com/community/calendar/",
}

#: All-day listings longer than this are programmes/PSAs, not a date a family
#: attends. Timed multi-day events (a play, an exhibit) are kept.
MAX_ALL_DAY_DAYS = 14

CITY_AREA = {
    "bryan": ("Bryan", "bryan"),
    "college station": ("College Station", "college_station"),
}

#: Bryan + College Station only. Hearne (77859) is in address-kit's default
#: Brazos Valley set and must not sneak in here.
BCS_ZIPS = frozenset({
    "77801",
    "77802",
    "77803",
    "77807",
    "77808",
    "77840",
    "77841",
    "77842",
    "77843",
    "77844",
    "77845",
})

#: Tag -> closed-vocabulary topic. Unknown tags are dropped, never sent.
TAG_TOPICS = {
    "music": "music",
    "live-music": "music",
    "live-music-event": "music",
    "theater": "arts",
    "arts": "arts",
    "performing-arts": "arts",
    "comedy": "arts",
    "artist": "arts",
    "stem": "science",
    "astronomy": "science",
    "history": "history",
    "football": "sports",
    "agriculture": "outdoors",
    "4h": "outdoors",
    "4-h": "outdoors",
    "ai": "technology",
    "community-service": "community",
}

TAG_AUDIENCES = {
    "family-friendly": "all-ages",
    "family": "all-ages",
    "kids": "elementary",
    "youth": "elementary",
}

_TAG_KEY = re.compile(r"[^a-z0-9]+")


class _Drop(Exception):
    """Not an error — the record is out of scope and should vanish quietly."""


class KbtxScraper(BaseEventScraper):
    kind = "kbtx"
    source_slug = "kbtx"
    source_name = ORGANIZATION["name"]
    verify_url = ICS_URL

    def __init__(
        self,
        proxy_url: str | None = None,
        state_dir: str | None = None,
        resolve: Any | None = None,
    ) -> None:
        self._proxy_url = proxy_url
        self._state_dir = state_dir
        self._resolve = resolve
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

        events = _fetch_ngevent(client, window_start, window_end)
        descriptions: dict[str, str] = {}
        try:
            descriptions = tockify.parse_ics_descriptions(client.get_text(ICS_URL))
        except Exception:
            descriptions = {}

        raw = [tockify._to_raw(e, descriptions) for e in events]
        cache: dict[str, dict] = {}
        if self._state_dir:
            from .. import state

            cache = state.load_addresses(self._state_dir, self.source_slug)
        enrich_places(raw, resolve=self._resolve or resolve_address, cache=cache)
        if self._state_dir:
            from .. import state

            state.save_addresses(self._state_dir, self.source_slug, cache)
        return raw

    def normalize(self, raw: list[RawEvent]) -> tuple[list[dict], list[dict]]:
        payloads: list[dict] = []
        rejected: list[dict] = []
        for item in raw:
            if is_long_all_day(item.record):
                continue
            try:
                series = self._series(item)
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
                "source": {"slug": self.source_slug, "name": self.source_name, "kind": "feed"},
                "series": series,
                "occurrence": tockify._occurrence(item, item),
            })
        return payloads, rejected

    def _series(self, item: RawEvent) -> dict:
        content = item.record.get("content") or {}
        title = tockify.strip_registration_suffix((content.get("summary") or {}).get("text") or "")
        description = item.supplement.get("description") or normalize.clean_text(
            (content.get("description") or {}).get("text")
        )
        labels = tockify._labels(item.record)
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "audiences": audiences_from_tags(labels),
            "topics": topics_from_tags(labels),
        }
        if description:
            series["description"] = description
        if url := _detail_url(item.record):
            series["source_url"] = url
        registration = tockify._registration(title, description or "")
        if registration is not None:
            series["registration_required"] = registration
        if any(_tag_key(label) == "free" for label in labels):
            series["is_free"] = True

        place = _place_for(item)
        if place:
            series["place"] = place
        return series


def is_long_all_day(record: dict) -> bool:
    when = record.get("when") or {}
    if not when.get("allDay"):
        return False
    start = (when.get("start") or {}).get("millis")
    end = (when.get("end") or {}).get("millis")
    if start is None or end is None:
        return False
    days = (int(end) - int(start)) / (1000 * 60 * 60 * 24)
    return days > MAX_ALL_DAY_DAYS


def _tag_key(label: str) -> str:
    return _TAG_KEY.sub("-", (label or "").strip().lower()).strip("-")


def topics_from_tags(labels: list[str]) -> list[str]:
    out = {TAG_TOPICS[key] for key in (_tag_key(label) for label in labels) if key in TAG_TOPICS}
    return sorted(out)


def audiences_from_tags(labels: list[str]) -> list[str]:
    out = {TAG_AUDIENCES[key] for key in (_tag_key(label) for label in labels) if key in TAG_AUDIENCES}
    return sorted(out)


def city_area(city: str | None) -> tuple[str, str] | None:
    """Canonical (City, area) or None when the city is not Bryan/CS."""
    return CITY_AREA.get((city or "").strip().lower())


def place_decision(record: dict) -> str:
    """How this record gets a place: structured, drop_geo, resolve, or reject."""
    content = record.get("content") or {}
    loc = content.get("location") or {}
    city = (loc.get("c_locality") or "").strip()
    if city:
        return "structured" if city_area(city) else "drop_geo"
    address = (content.get("address") or "").strip()
    place = (content.get("place") or "").strip()
    if address or place:
        return "resolve"
    return "reject"


def venue_cache_key(place_id: str | None, address: str) -> str:
    if place_id:
        return f"place:{place_id}"
    cleaned = re.sub(r"\s+", " ", (address or "").strip().lower())
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:16]
    return f"addr:{digest}"


def _resolve_queries(content: dict) -> list[str]:
    """Address first, then the venue name if the address is unusable."""
    address = (content.get("address") or "").strip()
    place = (content.get("place") or "").strip()
    out: list[str] = []
    for query in (address, place):
        if query and query not in out:
            out.append(query)
    return out


def enrich_places(
    raw: list[RawEvent],
    *,
    resolve: Any,
    cache: dict[str, dict],
) -> dict[str, dict]:
    """Attach ``place_resolution`` for records that need a lookup. Mutates raw.

    ``resolve`` is called at most once per venue identity for this process; the
    caller persists ``cache`` so the next run does not call again. Long all-day
    listings are dropped later and never looked up.
    """
    for item in raw:
        if is_long_all_day(item.record):
            continue
        if place_decision(item.record) != "resolve":
            continue
        content = item.record.get("content") or {}
        loc = content.get("location") or {}
        queries = _resolve_queries(content)
        if not queries:
            continue
        key = venue_cache_key(loc.get("place_id"), queries[0])
        if key not in cache:
            result: dict = {"status": "no_match"}
            for query in queries:
                result = dict(resolve(query) or {"status": "no_match"})
                if result.get("status") in {"matched", "out_of_area"}:
                    break
            cache[key] = result
        item.supplement["place_resolution"] = cache[key]
    return cache


def resolve_address(address: str) -> dict[str, Any]:
    """Census → Geoapify → Google, BCS ZIPs only. Direct, not through the VPN."""
    try:
        from address_kit import resolve
        from address_kit.footprints import BCS_BBOX, BCS_PROXIMITY
        from address_kit.models import AddressConfig
        from address_kit.secrets import load_api_keys
    except ImportError:
        return {"status": "no_match"}

    config = AddressConfig(
        strategy="economy",
        footprint_zips=BCS_ZIPS,
        bbox=BCS_BBOX,
        proximity_bias=BCS_PROXIMITY,
        city_retries=("College Station, TX", "Bryan, TX"),
    )
    google_key, geoapify_key = load_api_keys()
    result = resolve(
        address,
        config=config,
        google_key=google_key,
        geoapify_key=geoapify_key,
    )
    return {
        "status": result.status,
        "city": result.city,
        "zip_code": result.zip_code,
        "matched_address": result.matched_address,
        "lat": result.lat,
        "lng": result.lng,
        "provider": result.provider,
    }


def _place_for(item: RawEvent) -> dict:
    decision = place_decision(item.record)
    if decision == "drop_geo":
        raise _Drop()
    if decision == "structured":
        return _place_from_structured(item.record)
    if decision == "resolve":
        reso = item.supplement.get("place_resolution")
        if not reso:
            raise ScraperError("address needs resolution; no city from source")
        status = reso.get("status")
        if status == "out_of_area":
            raise _Drop()
        if status != "matched":
            raise ScraperError(f"address could not be resolved ({status})")
        mapped = city_area(reso.get("city"))
        if not mapped:
            raise _Drop()
        return _place_from_resolution(item.record, reso, mapped)
    raise ScraperError("no usable address or city")


def _place_from_structured(record: dict) -> dict:
    content = record.get("content") or {}
    loc = content.get("location") or {}
    mapped = city_area(loc.get("c_locality"))
    if not mapped:
        raise _Drop()
    city, area = mapped
    name = (content.get("place") or "").strip() or (loc.get("name") or "").strip() or city
    place: dict[str, Any] = {
        "slug": normalize.slugify(f"{name}-{city}"),
        "name": name,
        "city": city,
        "region": "TX",
        "area": area,
    }
    street = (loc.get("c_street") or "").strip() or _street_from_address(content.get("address") or "")
    if street:
        place["street"] = street
    if loc.get("c_postcode"):
        place["postcode"] = str(loc["c_postcode"])
    if loc.get("place_id"):
        place["external_place_id"] = loc["place_id"]
    if isinstance(loc.get("latitude"), (int, float)):
        place["latitude"] = float(loc["latitude"])
    if isinstance(loc.get("longitude"), (int, float)):
        place["longitude"] = float(loc["longitude"])
    return place


def _place_from_resolution(record: dict, reso: dict, mapped: tuple[str, str]) -> dict:
    content = record.get("content") or {}
    loc = content.get("location") or {}
    city, area = mapped
    name = (content.get("place") or "").strip() or city
    place: dict[str, Any] = {
        "slug": normalize.slugify(f"{name}-{city}"),
        "name": name,
        "city": city,
        "region": "TX",
        "area": area,
    }
    street = _street_from_address(reso.get("matched_address") or content.get("address") or "")
    if street:
        place["street"] = street
    if reso.get("zip_code"):
        place["postcode"] = str(reso["zip_code"])
    if loc.get("place_id"):
        place["external_place_id"] = loc["place_id"]
    if isinstance(reso.get("lat"), (int, float)):
        place["latitude"] = float(reso["lat"])
    if isinstance(reso.get("lng"), (int, float)):
        place["longitude"] = float(reso["lng"])
    return place


def _street_from_address(address: str) -> str:
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    return parts[0] if parts else ""


def _detail_url(record: dict) -> str | None:
    eid = record.get("eid") or {}
    uid, tid = eid.get("uid"), eid.get("tid")
    if uid and tid:
        return f"https://tockify.com/{CALNAME}/detail/{uid}/{tid}"
    return None


def _fetch_ngevent(client: HttpClient, window_start: date, window_end: date) -> list[dict]:
    events: list[dict] = []
    start = 0
    page_size = 200
    while True:
        params = {
            "calname": CALNAME,
            "startms": tockify._to_millis(window_start),
            "endms": tockify._to_millis(window_end),
            "start": start,
            "max": page_size,
        }
        data = client.get_json(NGEVENT_URL, params=params)
        batch = (data or {}).get("events")
        if not isinstance(batch, list):
            raise ScraperError(f"tockify: no 'events' array in response for {CALNAME!r}")
        events.extend(batch)
        meta = (data or {}).get("metaData") or {}
        if not meta.get("hasNext") or not batch:
            break
        start += len(batch)
    return events
