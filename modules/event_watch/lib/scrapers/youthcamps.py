"""Texas A&M Youth Camps, via the Localist public API.

``youthcamps.tamu.edu`` is Localist (Aggie Camp Finder). The listing HTML
is a theme shell; dates live on ``GET /api/2/events``. One list row is
one instance — recurring programmes (Garden Club) repeat the event
wrapper with a different ``event_instance``.

Bryan and College Station only. Galveston, Glen Rose, and any other
geo.city are a quiet drop. Named campus buildings without a city are
pinned. An unmapped location rejects loudly.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from modules._shared.http import HttpClient

from .. import classify, normalize
from .base import BaseEventScraper, RawEvent, ScraperError, UnknownVenueError

EVENTS_URL = "https://youthcamps.tamu.edu/api/2/events"
VERIFY_URL = "https://youthcamps.tamu.edu/api/2/events?pp=1"
TIMEZONE = "America/Chicago"
PAGE_SIZE = 100
TZ = ZoneInfo(TIMEZONE)

ORGANIZATION = {
    "slug": "youthcamps",
    "name": "Texas A&M Youth Camps",
    "website_url": "https://youthcamps.tamu.edu/",
}

BCS_CITIES = {"college station": "college_station", "bryan": "bryan"}

VENUES: dict[str, dict[str, Any]] = {
    "texas a&m university - college station": {
        "slug": "texas-am-university-college-station",
        "name": "Texas A&M University",
        "street": "400 Bizzell Street",
        "city": "College Station",
        "region": "TX",
        "postcode": "77843",
        "area": "college_station",
    },
    "jack k. williams administration building": {
        "slug": "jack-k-williams-administration-building",
        "name": "Jack K. Williams Administration Building",
        "street": "400 Bizzell Street",
        "city": "College Station",
        "region": "TX",
        "postcode": "77843",
        "area": "college_station",
        "indoor": True,
    },
    "kyle field": {
        "slug": "kyle-field",
        "name": "Kyle Field",
        "street": "756 Houston Street",
        "city": "College Station",
        "region": "TX",
        "postcode": "77843",
        "area": "college_station",
        "indoor": False,
    },
}

THEME_TOPICS = {
    "stem": "science",
    "agriculture": "nature",
    "sports & recreation": "sports",
    "arts & culture": "arts",
    "leadership": "community",
}

MUSIC_TITLE = re.compile(
    r"\b(concert|choir|jazz|orchestra|opas|ensemble|singers)\b",
    re.I,
)
OTHER_CITIES = re.compile(
    r"\b(galveston|glen rose|houston|dallas|austin|waco|mcallen|"
    r"fort worth|san antonio)\b",
    re.I,
)


class _Drop(Exception):
    """Not an error — the record is out of scope and should vanish quietly."""


class YouthCampsScraper(BaseEventScraper):
    kind = "youthcamps"
    source_slug = "youthcamps"
    source_name = "TAMU Youth Camps"
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
            user_agent="CortexEventWatch/1.0 (+https://discoverbcs.org)",
            proxy_url=self._proxy_url,
            proxy_env=None,
        )
        self._client = client

        raw: list[RawEvent] = []
        seen: set[tuple[str, str]] = set()
        for record in _fetch_list(client, window_start, window_end):
            item = to_raw(record)
            key = (item.series_uid, item.occurrence_tid)
            if not item.series_uid or not item.occurrence_tid or key in seen:
                continue
            if not in_window(item, window_start, window_end):
                continue
            seen.add(key)
            raw.append(item)
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
                    "title": (item.record.get("title") or "").strip(),
                    "location": location_of(item.record),
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

    def _series(self, item: RawEvent) -> dict[str, Any]:
        rec = item.record
        title = (rec.get("title") or "").strip()
        if not title:
            raise ScraperError("missing title")
        place = place_from(rec)
        indoor = place.pop("indoor", None)
        description = normalize.clean_text(rec.get("description_text") or "")
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "place": place,
            "topics": topics_from(rec),
            "audiences": ["all-ages"],
        }
        if description:
            series["description"] = description
        url = (rec.get("localist_url") or "").strip()
        if url:
            series["source_url"] = url
        if indoor is not None:
            series["indoor"] = indoor
        ticket = (rec.get("ticket_url") or "").strip()
        if ticket:
            series["registration_url"] = ticket
        if rec.get("has_register") or ticket:
            series["registration_required"] = True
        if is_free(rec):
            series["is_free"] = True
        return series


def to_raw(record: dict) -> RawEvent:
    event = _event_body(record)
    inst = instance_of(event)
    uid = str(event.get("id") or "")
    start = parse_when(inst.get("start"))
    tid = (
        str(int(start.timestamp() * 1000))
        if start is not None
        else str(inst.get("id") or uid)
    )
    return RawEvent(series_uid=uid, occurrence_tid=tid, record=event)


def _event_body(record: dict) -> dict:
    inner = record.get("event")
    if isinstance(inner, dict):
        return inner
    return record


def instance_of(record: dict) -> dict:
    rows = record.get("event_instances") or []
    if not rows:
        return {}
    first = rows[0]
    if isinstance(first, dict) and isinstance(first.get("event_instance"), dict):
        return first["event_instance"]
    return first if isinstance(first, dict) else {}


def parse_when(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt


def in_window(item: RawEvent, window_start: date, window_end: date) -> bool:
    start = parse_when(instance_of(item.record).get("start"))
    if start is None:
        return False
    day = start.astimezone(TZ).date()
    return window_start <= day < window_end


def location_of(record: dict) -> str:
    return (record.get("location") or record.get("location_name") or "").strip()


def venue_key(title: str) -> str:
    return " ".join((title or "").strip().lower().split())


def filter_names(record: dict, key: str) -> list[str]:
    filters = record.get("filters") or {}
    out: list[str] = []
    for item in filters.get(key) or []:
        name = (item.get("name") or "").strip()
        if name:
            out.append(name)
    return out


def drop_reason(record: dict) -> str | None:
    status = (record.get("status") or "").strip().lower()
    if status and status != "live":
        return "cancelled"
    loc = location_of(record)
    geo = record.get("geo") or {}
    city = (geo.get("city") or "").strip()
    blob = f"{loc} {city}"
    if OTHER_CITIES.search(blob) and not re.search(r"college station|\bbryan\b", loc, re.I):
        return "city"
    if city and city.lower() not in BCS_CITIES:
        return "city"
    return None


def place_from(record: dict) -> dict[str, Any]:
    loc = location_of(record)
    if not loc:
        raise UnknownVenueError("unknown venue: (none)")
    geo = record.get("geo") or {}
    city_raw = (geo.get("city") or "").strip()
    street = (geo.get("street") or "").strip() or (record.get("address") or "").strip()
    mapped = BCS_CITIES.get(city_raw.lower()) if city_raw else None
    if mapped and street:
        city = "Bryan" if mapped == "bryan" else "College Station"
        place: dict[str, Any] = {
            "slug": normalize.slugify(f"{loc}-{city}"),
            "name": loc,
            "street": street,
            "city": city,
            "region": "TX",
            "area": mapped,
        }
        postcode = (geo.get("zip") or "").strip()
        if postcode:
            place["postcode"] = postcode.split("-", 1)[0]
        try:
            if geo.get("latitude") not in (None, ""):
                place["latitude"] = float(geo["latitude"])
            if geo.get("longitude") not in (None, ""):
                place["longitude"] = float(geo["longitude"])
        except (TypeError, ValueError):
            pass
        return place
    fallback = VENUES.get(venue_key(loc))
    if fallback:
        return dict(fallback)
    if re.search(r"college station|\bbryan\b", loc, re.I) and not OTHER_CITIES.search(loc):
        bryan_only = (
            re.search(r"\bbryan\b", loc, re.I)
            and "college station" not in loc.lower()
        )
        area = "bryan" if bryan_only else "college_station"
        city = "Bryan" if area == "bryan" else "College Station"
        return {
            "slug": normalize.slugify(f"{loc}-{city}"),
            "name": loc,
            "city": city,
            "region": "TX",
            "area": area,
        }
    raise UnknownVenueError(f"unknown venue: {loc}")


def topics_from(record: dict) -> list[str]:
    out: set[str] = set()
    for theme in filter_names(record, "event_themes"):
        topic = THEME_TOPICS.get(theme.strip().lower())
        if topic:
            out.add(topic)
    types = {t.lower() for t in filter_names(record, "event_types")}
    title = (record.get("title") or "")
    if "day camp" in types or re.search(r"\bcamp\b", title, re.I):
        out.add("camp")
    if MUSIC_TITLE.search(title):
        out.add("music")
    return classify.validate(sorted(out))


def is_free(record: dict) -> bool:
    costs = {c.strip().lower() for c in filter_names(record, "event_cost")}
    return "no cost" in costs


def _occurrence(item: RawEvent) -> dict[str, Any]:
    inst = instance_of(item.record)
    start = parse_when(inst.get("start"))
    if start is None:
        raise ScraperError("missing start")
    local = start.astimezone(TZ).replace(tzinfo=None)
    occ: dict[str, Any] = {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": local.isoformat(),
        "timezone": TIMEZONE,
        "all_day": bool(inst.get("all_day")),
        "status": "scheduled",
    }
    end = parse_when(inst.get("end"))
    if end is not None:
        occ["end_local"] = end.astimezone(TZ).replace(tzinfo=None).isoformat()
    return occ


def _fetch_list(client: HttpClient, window_start: date, window_end: date) -> list[dict]:
    items: list[dict] = []
    page = 1
    days = max(1, (window_end - window_start).days)
    while True:
        payload = client.get_json(
            EVENTS_URL,
            params={
                "start": window_start.isoformat(),
                "days": days,
                "pp": PAGE_SIZE,
                "page": page,
            },
        )
        if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
            raise ScraperError("youthcamps: events list was not a Localist envelope")
        batch = payload["events"]
        items.extend(batch)
        pager = payload.get("page") or {}
        next_page = pager.get("next_page")
        if not next_page or not batch:
            break
        page = int(next_page)
        if page > 20:
            break
    return items
