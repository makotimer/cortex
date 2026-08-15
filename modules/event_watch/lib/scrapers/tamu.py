"""Texas A&M University Events Calendar, via LiveWhale JSON.

The public listing is a LiveWhale calendar. There is nothing to scrape in the
HTML sense — ``/live/json/events`` already carries types, campus, audience,
location and HTML description.

The unpaginated endpoint silently caps (~400). Fetch walks the window in week
chunks so a 270-day run does not lose the tail.
"""

from __future__ import annotations

import html
import re
from datetime import date, timedelta
from typing import Any

from modules._shared.http import HttpClient

from .. import normalize
from . import kbtx
from .base import BaseEventScraper, RawEvent, ScraperError

JSON_URL = "https://calendar.tamu.edu/live/json/events"
VERIFY_URL = "https://calendar.tamu.edu/live/json/events/max/1"
CATEGORIES = "Arts & Entertainment|General Interest|Speakers, Forums, Conferences, Training & Workshops"
CAMPUS = "Bryan-College Station"
CHUNK_DAYS = 7
TIMEZONE = "America/Chicago"

ORGANIZATION = {
    "slug": "tamu",
    "name": "Texas A&M University Events Calendar",
    "website_url": "https://calendar.tamu.edu/",
}

MAX_ALL_DAY_DAYS = 14

TITLE_DROP = re.compile(
    r"training|\borientation\b|office hours|\bretreat\b|new student|"
    r"graduate student orientation",
    re.I,
)
GROUP_DROP = {
    "center for teaching excellence",
    "faculty affairs",
    "howdy week",
}
PUBLIC_AUDIENCES = {"visitors", "residents", "youth (k-12)"}
OTHER_CITIES = re.compile(
    r"\b(mcallen|dallas|houston|fort worth|socorro|canyon|temple|"
    r"round rock|galveston|waco|austin|san antonio|cypress)\b",
    re.I,
)
VIRTUAL_LOC = re.compile(
    r"^(virtual|zoom|zoom - virtual|social media|online)(\b|$)",
    re.I,
)
CAMPUS_HINTS = (
    "memorial student center",
    "forsyth",
    "stark galler",
    "rudder",
    "harrington",
    "zachry",
    "mitchell institute",
    "chemistry building",
    "blocker",
    "wehner",
    "student services",
    "student rec",
    "student recreation",
    "heldenfels",
    "throckmorton",
    "aggie park",
    "simpson drill",
    "langford",
    "hullabaloo",
    "emerging technologies",
    "physics teaching observatory",
    "leach teaching",
    "all faiths chapel",
    "residence hall",
    "white creek",
    "gilchrist",
    "commons",
    "ilcb",
    "ilsq",
    "jack e. brown",
    "richardson petroleum",
    "wayne roberts",
    "henderson hall",
    "northside",
    "bush school",
    "allen building",
    "veterinary",
    "center for infrastructure",
    "baptist student",
)
TAG_TOPICS = {
    "arts & entertainment": "arts",
    "sports & athletics": "sports",
}
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


class _Drop(Exception):
    """Not an error — the record is out of scope and should vanish quietly."""


class TamuScraper(BaseEventScraper):
    kind = "tamu"
    source_slug = "tamu"
    source_name = ORGANIZATION["name"]
    verify_url = VERIFY_URL

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

        seen: set[tuple[str, str]] = set()
        raw: list[RawEvent] = []
        chunk_start = window_start
        while chunk_start < window_end:
            chunk_end = min(window_end, chunk_start + timedelta(days=CHUNK_DAYS))
            for record in _fetch_chunk(client, chunk_start, chunk_end):
                item = to_raw(record)
                key = (item.series_uid, item.occurrence_tid)
                if not item.series_uid or not item.occurrence_tid or key in seen:
                    continue
                seen.add(key)
                raw.append(item)
            chunk_start = chunk_end

        cache: dict[str, dict] = {}
        if self._state_dir:
            from .. import state

            cache = state.load_addresses(self._state_dir, self.source_slug)
        enrich_places(raw, resolve=self._resolve or kbtx.resolve_address, cache=cache)
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
        title = _text(rec.get("title"))
        description = _description(rec.get("description"))
        labels = _event_types(rec)
        org_name = _text(rec.get("group_title")) or ORGANIZATION["name"]
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": {
                "slug": normalize.slugify(org_name) or ORGANIZATION["slug"],
                "name": org_name,
                "website_url": ORGANIZATION["website_url"],
            },
            "audiences": audiences_from_labels(_audiences(rec)),
            "topics": topics_from_types(labels),
        }
        if description:
            series["description"] = description
        if rec.get("url"):
            series["source_url"] = rec["url"]
        if rec.get("has_registration"):
            series["registration_required"] = True
        place = _place_for(item)
        if place:
            series["place"] = place
        return series


def to_raw(record: dict) -> RawEvent:
    eid = record.get("id")
    parent = record.get("parent")
    uid = str(parent or eid or "")
    ts = record.get("date_ts")
    tid = str(int(ts) * 1000) if ts else str(eid or "")
    return RawEvent(series_uid=uid, occurrence_tid=tid, record=record)


def _text(value: Any) -> str:
    return html.unescape(str(value or "")).strip()


def _event_types(record: dict) -> list[str]:
    return [_text(t) for t in (record.get("event_types") or []) if t]


def _audiences(record: dict) -> list[str]:
    return [_text(a) for a in (record.get("event_types_audience") or []) if a]


def _campuses(record: dict) -> list[str]:
    out = []
    for raw in record.get("event_types_campus") or []:
        name = _text(raw).replace("\u2028", "")
        if name:
            out.append(name)
    return out


def _location(record: dict) -> str:
    return _text(record.get("location_title") or record.get("location"))


def _has_bcs(record: dict) -> bool:
    return CAMPUS in _campuses(record)


def _is_virtual(record: dict) -> bool:
    loc = _location(record)
    if record.get("is_online"):
        return True
    return bool(loc) and bool(VIRTUAL_LOC.match(loc))


def _is_long_all_day(record: dict) -> bool:
    if not record.get("is_all_day"):
        return False
    start, end = record.get("date_ts"), record.get("date2_ts")
    if start is None or end is None:
        return False
    days = (int(end) - int(start)) / 86400
    return days > MAX_ALL_DAY_DAYS


def drop_reason(record: dict) -> str | None:
    """Why this record is out of scope, or None to keep going."""
    title = _text(record.get("title"))
    if TITLE_DROP.search(title):
        return "title"
    group = _text(record.get("group_title")).lower()
    if group in GROUP_DROP:
        return "group"
    aud = {a.lower() for a in _audiences(record)}
    if aud == {"students"}:
        return "students"
    campuses = _campuses(record)
    if campuses and CAMPUS not in campuses:
        return "campus"
    loc = _location(record)
    if loc and OTHER_CITIES.search(loc) and not re.search(r"bryan|college station", loc, re.I):
        return "city"
    if _is_virtual(record) and not _has_bcs(record):
        return "virtual"
    if _is_long_all_day(record):
        return "long"
    return None


def topics_from_types(labels: list[str]) -> list[str]:
    out = set()
    for label in labels:
        key = re.sub(r"\s+", " ", label.lower())
        if key in TAG_TOPICS:
            out.add(TAG_TOPICS[key])
    return sorted(out)


def audiences_from_labels(labels: list[str]) -> list[str]:
    out = set()
    for label in labels:
        key = label.lower()
        if key in {"visitors", "residents"}:
            out.add("all-ages")
        elif key == "youth (k-12)":
            out.add("elementary")
        elif key in {"faculty", "staff"}:
            out.add("adult")
    return sorted(out)


def _description(raw: Any) -> str | None:
    text = _text(raw)
    if not text:
        return None
    text = _TAG.sub(" ", text)
    text = _WS.sub(" ", text).strip()
    return normalize.clean_text(text)


def _place_decision(record: dict) -> str:
    """structured_cs, structured_bryan, no_place, resolve, drop_geo, reject."""
    loc = _location(record)
    if _is_virtual(record):
        return "no_place" if _has_bcs(record) else "drop_geo"
    if loc and OTHER_CITIES.search(loc) and not re.search(r"bryan|college station", loc, re.I):
        return "drop_geo"
    if loc and re.search(r"\bbryan\b", loc, re.I):
        return "structured_bryan"
    if loc and re.search(r"college station", loc, re.I):
        return "structured_cs"
    if loc and _has_bcs(record):
        return "structured_cs"
    if loc and _looks_campus(loc):
        return "structured_cs"
    if not loc:
        # BCS + no venue is a campus-wide notice. No campus and no venue
        # cannot be placed; dropping is quieter than emailing forever.
        return "no_place" if _has_bcs(record) else "drop_geo"
    return "resolve"


def _looks_campus(location: str) -> bool:
    low = location.lower()
    return any(hint in low for hint in CAMPUS_HINTS)


def enrich_places(
    raw: list[RawEvent],
    *,
    resolve: Any,
    cache: dict[str, dict],
) -> dict[str, dict]:
    for item in raw:
        if drop_reason(item.record):
            continue
        if _place_decision(item.record) != "resolve":
            continue
        loc = _location(item.record)
        if not loc:
            continue
        key = kbtx.venue_cache_key(None, loc)
        if key not in cache:
            cache[key] = dict(resolve(loc) or {"status": "no_match"})
        item.supplement["place_resolution"] = cache[key]
    return cache


def _place_for(item: RawEvent) -> dict | None:
    rec = item.record
    decision = _place_decision(rec)
    if decision == "drop_geo":
        raise _Drop()
    if decision == "no_place":
        return None
    if decision == "reject":
        raise ScraperError("no usable address or city")
    if decision == "resolve":
        reso = item.supplement.get("place_resolution")
        if not reso:
            raise ScraperError("address needs resolution; no city from source")
        if reso.get("status") == "out_of_area":
            raise _Drop()
        if reso.get("status") != "matched":
            raise ScraperError(f"address could not be resolved ({reso.get('status')})")
        mapped = kbtx.city_area(reso.get("city"))
        if not mapped:
            raise _Drop()
        city, area = mapped
        return _place_dict(rec, city, area, reso)
    city, area = (
        ("Bryan", "bryan")
        if decision == "structured_bryan"
        else (
            "College Station",
            "college_station",
        )
    )
    return _place_dict(rec, city, area, None)


def _place_dict(record: dict, city: str, area: str, reso: dict | None) -> dict:
    name = _location(record) or city
    # Keep the short building name, not a 200-char room+address blob.
    name = name.split(",")[0].strip() or city
    place: dict[str, Any] = {
        "slug": normalize.slugify(f"{name}-{city}"),
        "name": name,
        "city": city,
        "region": "TX",
        "area": area,
    }
    loc = _location(record)
    street = _street_from_location(loc)
    if reso and reso.get("matched_address"):
        street = street or kbtx._street_from_address(reso["matched_address"])
        if reso.get("zip_code"):
            place["postcode"] = str(reso["zip_code"]).split("-")[0]
        if isinstance(reso.get("lat"), (int, float)):
            place["latitude"] = float(reso["lat"])
        if isinstance(reso.get("lng"), (int, float)):
            place["longitude"] = float(reso["lng"])
    if street:
        place["street"] = street
    lat, lng = record.get("location_latitude"), record.get("location_longitude")
    if "latitude" not in place and isinstance(lat, (int, float)):
        place["latitude"] = float(lat)
    if "longitude" not in place:
        try:
            if lng not in (None, ""):
                place["longitude"] = float(lng)
        except (TypeError, ValueError):
            pass
    return place


def _street_from_location(location: str) -> str:
    """Best-effort street from a LiveWhale location blob."""
    if not location:
        return ""
    if re.search(r"\d{2,}\s+\w+", location):
        # Prefer the clause that looks like a street.
        for part in location.split(","):
            part = part.strip()
            if re.search(r"\d{2,}\s+\w+", part):
                return part
    return ""


def _occurrence(item: RawEvent) -> dict:
    rec = item.record
    ts = rec.get("date_ts")
    if ts is None:
        raise ScraperError("missing date_ts")
    tzid = rec.get("timezone") or TIMEZONE
    occ: dict[str, Any] = {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": normalize.local_iso(int(ts) * 1000, tzid),
        "timezone": tzid,
        "all_day": bool(rec.get("is_all_day")),
        "status": "cancelled" if rec.get("is_canceled") else "scheduled",
    }
    end = rec.get("date2_ts")
    if end:
        occ["end_local"] = normalize.local_iso(int(end) * 1000, rec.get("timezone") or tzid)
    return occ


def _fetch_chunk(client: HttpClient, start: date, end: date) -> list[dict]:
    from urllib.parse import quote

    url = (
        f"{JSON_URL}/campus/{quote(CAMPUS)}/category/{quote(CATEGORIES)}"
        f"/start_date/{start.isoformat()}/end_date/{end.isoformat()}/max/400"
    )
    data = client.get_json(url)
    if not isinstance(data, list):
        raise ScraperError(f"tamu: expected a JSON array from {url}")
    return data
