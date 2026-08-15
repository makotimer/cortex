"""Visit College Station events, via the public Algolia InstantSearch index.

The /events/ landing page is not a directory. The Upcoming Events widget
queries ``prod-visit-college-station-listings`` with ``sectionName:Events``.
The search-only key is the one InstantSearch already ships in the page HTML.
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

from modules._shared.http import HttpClient

from .. import normalize
from . import kbtx
from .base import BaseEventScraper, RawEvent, ScraperError

ALGOLIA_APP = "EYQHJ2IY2M"
ALGOLIA_KEY = "c6d5977cb5cd80c09abfd2a7e5d9e88b"
ALGOLIA_INDEX = "prod-visit-college-station-listings"
ALGOLIA_URL = f"https://{ALGOLIA_APP}-dsn.algolia.net/1/indexes/{ALGOLIA_INDEX}/query"
SITE = "https://visit.cstx.gov"
TIMEZONE = "America/Chicago"
MAX_SPAN_DAYS = 14
PAGE_SIZE = 100

ORGANIZATION = {
    "slug": "visitcstx",
    "name": "Visit College Station",
    "website_url": "https://visit.cstx.gov/events/",
}

CITY_AREA = {
    "bryan": ("Bryan", "bryan"),
    "college station": ("College Station", "college_station"),
}
NEARBY_CITIES = {
    "wellborn": ("Wellborn", "nearby"),
    "kurten": ("Kurten", "nearby"),
    "wixon valley": ("Wixon Valley", "nearby"),
    "millican": ("Millican", "nearby"),
}

CATEGORY_TOPICS = {
    "arts & culture": "arts",
    "performing arts": "arts",
    "exhibits": "arts",
    "live music": "music",
    "sports": "sports",
    "texas a&m sports": "sports",
    "recreation": "sports",
    "festivals": "community",
    "markets": "community",
    "food & drink": "community",
    "parades": "community",
}

_CITY_LINE = re.compile(
    r"^(?P<city>.+?)\s*,\s*(?:Texas|Tx)\s*,?\s*(?P<zip>\d{5})?\s*$",
    re.I,
)


class _Drop(Exception):
    """Not an error — the record is out of scope and should vanish quietly."""


class VisitCstxScraper(BaseEventScraper):
    kind = "visitcstx"
    source_slug = "visitcstx"
    source_name = ORGANIZATION["name"]
    verify_url = SITE + "/events/"

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

        cache: dict[str, dict] = {}
        if self._state_dir:
            from .. import state
            cache = state.load_addresses(self._state_dir, self.source_slug)

        raw: list[RawEvent] = []
        resolve = self._resolve or kbtx.resolve_address
        for hit in _fetch_hits(client):
            item = to_raw(hit)
            if not _in_window(item, window_start, window_end):
                continue
            _maybe_resolve_place(item, resolve, cache)
            raw.append(item)

        if self._state_dir:
            from .. import state
            state.save_addresses(self._state_dir, self.source_slug, cache)
        return raw

    def normalize(self, raw: list[RawEvent]) -> tuple[list[dict], list[dict]]:
        payloads: list[dict] = []
        rejected: list[dict] = []
        for item in raw:
            try:
                if drop_reason(item.record):
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
        title = (rec.get("title") or "").strip()
        if not title:
            raise ScraperError("missing title")
        cats = list(rec.get("eventCategories") or [])
        description = normalize.clean_text(rec.get("content"))
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "topics": topics_from_categories(cats),
            "audiences": audiences_from_categories(cats),
        }
        if description:
            series["description"] = description
        uri = rec.get("uri") or ""
        if uri:
            series["source_url"] = SITE + (uri if uri.startswith("/") else "/" + uri)
        if any(_cat_key(c) == "free events" for c in cats):
            series["is_free"] = True
        place = _place_for(item)
        if place:
            series["place"] = place
        return series


def to_raw(hit: dict) -> RawEvent:
    eid = hit.get("id") or hit.get("distinctField") or hit.get("objectID")
    start = hit.get("startDate")
    tid = str(int(start) * 1000) if start else str(eid or "")
    return RawEvent(series_uid=str(eid or ""), occurrence_tid=tid, record=hit)


def wall_clock_from_unix(ts: int | float | None) -> datetime | None:
    """Algolia stores Central wall-clock as a UTC unix timestamp."""
    if ts is None:
        return None
    return datetime.fromtimestamp(int(ts), tz=UTC).replace(tzinfo=None)


def city_from_address(address: list | None) -> str | None:
    if not address:
        return None
    last = re.sub(r"\s+", " ", str(address[-1] or "")).strip()
    match = _CITY_LINE.match(last)
    if not match:
        return None
    return match.group("city").strip()


def area_from_city(city: str | None) -> tuple[str, str] | None:
    key = re.sub(r"\s+", " ", (city or "").strip().lower())
    return CITY_AREA.get(key) or NEARBY_CITIES.get(key)


def _cat_key(label: str) -> str:
    return re.sub(r"\s+", " ", (label or "").strip().lower())


def topics_from_categories(labels: list[str]) -> list[str]:
    out = {
        CATEGORY_TOPICS[key]
        for key in (_cat_key(c) for c in labels)
        if key in CATEGORY_TOPICS
    }
    return sorted(out)


def audiences_from_categories(labels: list[str]) -> list[str]:
    keys = {_cat_key(c) for c in labels}
    return ["all-ages"] if "family friendly" in keys else []


def drop_reason(record: dict) -> str | None:
    start, end = record.get("startDate"), record.get("endDate")
    if start is not None and end is not None and (int(end) - int(start)) > MAX_SPAN_DAYS * 86400:
        return "span"
    city = city_from_address(record.get("address"))
    if city and area_from_city(city) is None:
        return "city"
    return None


def _in_window(item: RawEvent, window_start: date, window_end: date) -> bool:
    start = wall_clock_from_unix(item.record.get("startDate"))
    if start is None:
        return False
    return window_start <= start.date() < window_end


def _occurrence(item: RawEvent) -> dict:
    start = wall_clock_from_unix(item.record.get("startDate"))
    if start is None:
        raise ScraperError("missing startDate")
    occ: dict[str, Any] = {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": start.isoformat(),
        "timezone": TIMEZONE,
        "all_day": bool(item.record.get("isAllDay")),
        "status": "scheduled",
    }
    end = wall_clock_from_unix(item.record.get("endDate"))
    if end:
        occ["end_local"] = end.isoformat()
    return occ


def _place_for(item: RawEvent) -> dict:
    rec = item.record
    addr_lines = list(rec.get("address") or [])
    city = city_from_address(addr_lines)
    reso = item.supplement.get("place_resolution")
    if reso and reso.get("status") == "matched" and not city:
        city = reso.get("city")
    if reso and reso.get("status") == "out_of_area":
        city = reso.get("city") or city
    mapped = area_from_city(city)
    if mapped is None:
        if reso and reso.get("status") == "no_match":
            raise ScraperError("address could not be resolved (no_match)")
        if not city:
            raise ScraperError("no usable city")
        raise _Drop()
    display_city, area = mapped
    name = (addr_lines[0] if addr_lines else "") or display_city
    street = addr_lines[1] if len(addr_lines) >= 3 else ""
    place: dict[str, Any] = {
        "slug": normalize.slugify(f"{name}-{display_city}"),
        "name": name,
        "city": display_city,
        "region": "TX",
        "area": area,
    }
    if street:
        place["street"] = street
    last = addr_lines[-1] if addr_lines else ""
    zip_m = re.search(r"\b(\d{5})\b", last)
    if zip_m:
        place["postcode"] = zip_m.group(1)
    geo = rec.get("_geoloc") or {}
    if isinstance(geo.get("lat"), (int, float)):
        place["latitude"] = float(geo["lat"])
    if isinstance(geo.get("lng"), (int, float)):
        place["longitude"] = float(geo["lng"])
    return place


def _maybe_resolve_place(item: RawEvent, resolve: Any, cache: dict) -> None:
    if city_from_address(item.record.get("address")):
        return
    query = ", ".join(str(x) for x in (item.record.get("address") or []) if x)
    if not query:
        return
    key = kbtx.venue_cache_key(None, query)
    if key not in cache:
        cache[key] = dict(resolve(query) or {"status": "no_match"})
    item.supplement["place_resolution"] = cache[key]


def _fetch_hits(client: HttpClient) -> list[dict]:
    hits: list[dict] = []
    page = 0
    while True:
        batch, nb_pages = _query_page(client, page)
        hits.extend(batch)
        page += 1
        if page >= max(nb_pages, 1) or not batch:
            break
        if page > 20:
            break
    return hits


def _query_page(client: HttpClient, page: int) -> tuple[list[dict], int]:
    resp = client.session.post(
        ALGOLIA_URL,
        json={"params": f"hitsPerPage={PAGE_SIZE}&page={page}&filters=sectionName:Events"},
        headers={
            "X-Algolia-Application-Id": ALGOLIA_APP,
            "X-Algolia-API-Key": ALGOLIA_KEY,
            "Content-Type": "application/json",
        },
        timeout=client.timeout,
    )
    resp.raise_for_status()
    try:
        data = resp.json()
    except ValueError as exc:
        raise ScraperError("visitcstx: Algolia returned non-JSON") from exc
    batch = data.get("hits")
    if not isinstance(batch, list):
        raise ScraperError("visitcstx: Algolia hits is not a list")
    clean = [{k: v for k, v in hit.items() if not str(k).startswith("_")} for hit in batch]
    # keep _geoloc for coordinates
    for src, dst in zip(batch, clean, strict=True):
        if isinstance(src.get("_geoloc"), dict):
            dst["_geoloc"] = src["_geoloc"]
    return clean, int(data.get("nbPages") or 1)
