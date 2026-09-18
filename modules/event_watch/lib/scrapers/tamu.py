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
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from itertools import pairwise
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
UNPLACEABLE_LOC = re.compile(
    r"^(multiple locations|various locations|tbd|tba|n/?a)$",
    re.I,
)
# Named local venues that should still go through address-kit when the campus
# field is empty. Country names, "US", and "Texas University" have none of
# these, which is what keeps Traveling Aggies from matching a restaurant.
PLACEABLE_HINTS = (
    "building",
    "hall",
    "museum",
    "club",
    "garden",
    "library",
    "chapel",
    "auditorium",
    "arena",
    "theatre",
    "theater",
    "stadium",
    "pavilion",
    "observatory",
    "gallery",
    "laboratory",
    "coliseum",
    "ballroom",
    "complex",
    "plaza",
    "center",
    "fieldhouse",
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
                    "title": _text(item.record.get("title")),
                    "location": _location(item.record),
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
        return collapse_intra_source(payloads), rejected

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
    if loc and re.search(r"\bbrazos county\b", loc, re.I):
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
    if not _looks_placeable(loc):
        # Empty-campus travel listings ("Italy", "US") and statewide AgriLife
        # offices. Geocoding them emails forever on no_match, or worse, pins
        # "Peru" to Fusion Peru on Texas Avenue.
        return "drop_geo"
    return "resolve"


def _looks_placeable(location: str) -> bool:
    if UNPLACEABLE_LOC.match(location.strip()):
        return False
    if _looks_campus(location):
        return True
    low = location.lower()
    if any(hint in low for hint in PLACEABLE_HINTS):
        return True
    return bool(re.search(r"\d{2,}\s+\w+", location))


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


_HOUSE_NUMBER = re.compile(r"\d{2,}\s+[\w.&'-]+")


def _split_location_name_street(location: str) -> tuple[str, str]:
    """Split a comma-less LiveWhale location blob into (name, street).

    ``Made Well House 600 E. 27th Bryan`` used to land whole in both the place
    name and the street, which is why the site's venue signal could not match
    ``600 East 27th Street``. When a house number appears, the name is the
    stripped prefix and the street runs from the house number with the trailing
    city/state tokens removed. Without a house number, the name is the first
    comma clause and the street falls back to ``_street_from_location``.
    """
    if not location:
        return "", ""
    if "," in location:
        name = location.split(",")[0].strip()
        street = _street_from_location(location)
        # A comma blob that starts with a house number carries the street in
        # the same clause as the name; take the clause from the number on.
        match = _HOUSE_NUMBER.search(location.split(",")[0])
        if match:
            street = location.split(",")[0][match.start():].strip()
        return name, street
    match = _HOUSE_NUMBER.search(location)
    if not match:
        return location.strip(), ""
    name = location[: match.start()].strip()
    street = location[match.start():].strip()
    # Trailing city/state after the house number: "600 E. 27th Bryan",
    # "600 E. 27th Bryan, TX", "600 E. 27th Bryan and College Station".
    street = re.sub(
        r"\s*(,|\band\b)?\s*(bryan|college station|tx|texas)\b"
        r"(\s*,\s*(tx|texas))?\s*$",
        "",
        street,
        flags=re.I,
    ).strip()
    return name or location.strip(), street


def _place_dict(record: dict, city: str, area: str, reso: dict | None) -> dict:
    name, street = _split_location_name_street(_location(record))
    name = name or city
    place: dict[str, Any] = {
        "slug": normalize.slugify(f"{name}-{city}"),
        "name": name,
        "city": city,
        "region": "TX",
        "area": area,
    }
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


_AT_VENUE = re.compile(r"\s+at\s+[^a-z]{0,2}[a-z0-9&'-]*(\s+[a-z0-9&'-]+)*$", re.I)


def _normalized_title(title: str) -> str:
    """Lowercase, one-spaced, with a trailing ``at <venue>`` clause removed —
    the same shape the site's ``normalize_title`` compares on."""
    text = " ".join((title or "").lower().split())
    return _AT_VENUE.sub("", text).strip() or text


def _titles_match(a: str, b: str) -> bool:
    """The site's title rule, cheaply: equal once normalized, containment with
    a real prefix, or a high sequence ratio. Two genuinely different shows at
    one slot must not be collapsed, so a one-token overlap is never enough."""
    short = _normalized_title(a)
    long = _normalized_title(b)
    if short and long and short == long:
        # A trailing ``at <venue>`` clause is the only difference — the raw
        # ratio would reject a short title carrying a long venue name.
        return True
    if short and long and short != long:
        if len(short) < len(long):
            shorter, longer = short, long
        else:
            shorter, longer = long, short
        if shorter in longer and (len(shorter.split()) >= 2 or len(shorter) >= 12):
            return True
    return SequenceMatcher(None, a or "", b or "").ratio() >= 0.6


def _collapse_winner(group: list[dict]) -> dict:
    """calendar.tamu.edu over agriliferegister, then the longer description,
    then the lexicographically smaller series uid."""
    return min(group, key=_collapse_winner_rank)


def _parse_local(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _interval(payload: dict) -> tuple[datetime, datetime] | None:
    """Half-open [start, end). All-day with no end covers that calendar day.
    A timed row with no end is a point and only overlaps an identical start.
    """
    occ = payload.get("occurrence") or {}
    start = _parse_local(occ.get("start_local"))
    if start is None:
        return None
    end = _parse_local(occ.get("end_local"))
    if end is None:
        if occ.get("all_day"):
            end = start + timedelta(days=1)
        else:
            return start, start
    if end <= start:
        end = start + timedelta(seconds=1)
    return start, end


def _program_key(payload: dict) -> tuple[str, str, str] | None:
    place_slug = (payload.get("series", {}).get("place") or {}).get("slug") or ""
    if not place_slug:
        return None
    occ = payload.get("occurrence") or {}
    title = _normalized_title((payload.get("series") or {}).get("title") or "")
    return (title, place_slug, occ.get("status") or "")


def _intervals_overlap(a: tuple[datetime, datetime],
                       b: tuple[datetime, datetime]) -> bool:
    return a[0] < b[1] and b[0] < a[1]


def _range_winner(group: list[dict]) -> dict:
    """Prefer a timed span over an all-day blob, then the earliest start,
    then the same ranking as a same-slot collapse."""
    def rank(payload: dict) -> tuple:
        occ = payload.get("occurrence") or {}
        start = occ.get("start_local") or ""
        return (bool(occ.get("all_day")), start, *_collapse_winner_rank(payload))
    return min(group, key=rank)


def _collapse_winner_rank(payload: dict) -> tuple:
    url = payload["series"].get("source_url") or ""
    description = payload["series"].get("description") or ""
    return ("calendar.tamu.edu" not in url, -len(description),
            payload["series"]["source_series_uid"])


def _drop_all_day_inside_timed_spans(payloads: list[dict]) -> list[dict]:
    """LiveWhale repeats a multi-day listing as one timed span plus an
    all-day row for every calendar day in the window. Keep the span."""
    timed_by_program: dict[tuple, list[tuple[datetime, datetime]]] = {}
    for payload in payloads:
        key = _program_key(payload)
        occ = payload.get("occurrence") or {}
        if not key or occ.get("all_day"):
            continue
        interval = _interval(payload)
        if interval and interval[0] < interval[1]:
            timed_by_program.setdefault(key, []).append(interval)

    kept: list[dict] = []
    for payload in payloads:
        occ = payload.get("occurrence") or {}
        if not occ.get("all_day"):
            kept.append(payload)
            continue
        key = _program_key(payload)
        interval = _interval(payload)
        if not key or interval is None:
            kept.append(payload)
            continue
        if any(_intervals_overlap(interval, span)
               for span in timed_by_program.get(key, ())):
            continue
        kept.append(payload)
    return kept


def _collapse_overlapping_ranges(payloads: list[dict]) -> list[dict]:
    """Several LiveWhale ids for one program, each a window shifted by a
    day, are one happening. Weekly sessions on different days do not overlap
    and survive.
    """
    groups: dict[tuple, list[dict]] = {}
    leftover: list[dict] = []
    for payload in payloads:
        key = _program_key(payload)
        if key is None or _interval(payload) is None:
            leftover.append(payload)
            continue
        groups.setdefault(key, []).append(payload)

    kept: list[dict] = list(leftover)
    for group in groups.values():
        remaining = list(group)
        while remaining:
            winner = _range_winner(remaining)
            win_iv = _interval(winner)
            kept.append(winner)
            nxt = []
            for payload in remaining:
                if payload is winner:
                    continue
                other = _interval(payload)
                if win_iv and other and _intervals_overlap(win_iv, other) \
                        and _titles_match(
                            winner["series"].get("title", ""),
                            payload["series"].get("title", "")):
                    continue
                nxt.append(payload)
            remaining = nxt
    return kept


def collapse_intra_source(payloads: list[dict]) -> list[dict]:
    """Collapse same-run double listings.

    Same start, status and place with titles that read as one program
    (Garden Club on the calendar *and* the AgriLife register) collapse
    first. Then all-day continuation days inside a timed span of the same
    program are dropped, then overlapping multi-day windows of the same
    title and place collapse to one payload. Distinct starts, distinct
    places and disjoint titles all survive. Payloads without a place slug
    never collapse against each other.
    """
    groups: dict[tuple, list[dict]] = {}
    ungrouped: list[dict] = []
    for payload in payloads:
        place_slug = (payload.get("series", {}).get("place") or {}).get("slug") or ""
        occurrence = payload.get("occurrence", {})
        if not place_slug:
            ungrouped.append(payload)
            continue
        key = (
            occurrence.get("start_local") or "",
            occurrence.get("status") or "",
            place_slug,
        )
        groups.setdefault(key, []).append(payload)

    kept: list[dict] = list(ungrouped)
    for group in groups.values():
        if len(group) == 1:
            kept.append(group[0])
            continue
        winner = _collapse_winner(group)
        kept.append(winner)
        for loser in group:
            # A loser stays when its title does not read as the winner's
            # program — two different shows at one slot are not a clone.
            if loser is not winner and not _titles_match(
                winner["series"].get("title", ""), loser["series"].get("title", "")
            ):
                kept.append(loser)
    return _unify_daily_runs(
        _collapse_overlapping_ranges(_drop_all_day_inside_timed_spans(kept)))


def _program_uid(key: tuple[str, str, str]) -> str:
    """A series uid that is the program, not the day: stable across runs, so
    every day of a show lands in the same site row. The title part is the
    normalized title (venue clause stripped) slugified."""
    title, place_slug, _status = key
    return f"program:{place_slug}:{normalize.slugify(title)}"


def _unify_daily_runs(payloads: list[dict]) -> list[dict]:
    """LiveWhale gives a gallery show — and some lecture series — a fresh
    event id for every day, each a timed row. Left alone the site gets one
    series per day (172 for one exhibition in September 2026), which every
    per-series count and the home page's "new this week" then eat.

    A program (normalized title + place) seen under two or more distinct
    ids on a *dense run* of days — at least DAILY_RUN_MIN_DAYS calendar
    days with no gap over DAILY_RUN_MAX_GAP days, so a gallery closed
    Saturday to Monday still counts — is one series: every payload takes the
    program uid, keeps its own occurrence tid (already per day) and its
    own series fields, which the site pins to that occurrence. Two concerts
    of one ensemble five weeks apart, a weekly seminar under a fresh id
    each week, a program that already arrives under one parent id, and a
    single listing are all left alone.
    """
    groups: dict[tuple, list[dict]] = {}
    for payload in payloads:
        key = _program_key(payload)
        if key is not None:
            groups.setdefault(key, []).append(payload)
    for key, group in groups.items():
        uids = {p["series"]["source_series_uid"] for p in group}
        if len(uids) < 2 or not _is_dense_run(group):
            continue
        uid = _program_uid(key)
        for payload in group:
            payload["series"]["source_series_uid"] = uid
    return payloads


DAILY_RUN_MIN_DAYS = 3
DAILY_RUN_MAX_GAP = 4


def _is_dense_run(group: list[dict]) -> bool:
    days = sorted({
        d for d in (
            _parse_local((p.get("occurrence") or {}).get("start_local"))
            for p in group)
        if d is not None
    })
    dates = sorted({d.date() for d in days})
    if len(dates) < DAILY_RUN_MIN_DAYS:
        return False
    gaps = [(b - a).days for a, b in pairwise(dates)]
    return max(gaps) <= DAILY_RUN_MAX_GAP


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
