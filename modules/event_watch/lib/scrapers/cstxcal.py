"""City of College Station public calendar, via the Timely widget API.

The CivicPlus page at ``www.cstx.gov/calendar`` embeds
``events.timely.fun/yl7pe679``. Events are JSON at
``timelyapp.time.ly/api/calendars/54735353/events``. The city's default
embed already pins Type=Public; Internal stays out. Clean List is a
view, not a feed.

The widget API key is public in ``embed.js``. List rows omit venue
addresses; one detail GET per event id fills them in.
"""

from __future__ import annotations

from datetime import date, datetime, time
from typing import Any
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError, UnknownVenueError

CALENDAR_ID = 54735353
CALENDAR_CODE = "yl7pe679"
API_BASE = "https://timelyapp.time.ly/api"
EVENTS_URL = f"{API_BASE}/calendars/{CALENDAR_ID}/events"
SITE = "https://events.timely.fun"
VERIFY_URL = f"{SITE}/{CALENDAR_CODE}"
TIMEZONE = "America/Chicago"
#: Type=Public in filter group "Type". Same id as the city's default embed.
PUBLIC_TYPE_ID = 677656162
TYPE_FILTER_GROUP_ID = 659989886
API_KEY = "c6e5e0363b5925b28552de8805464c66f25ba0ce"
PAGE_SIZE = 100

ORGANIZATION = {
    "slug": "cstx",
    "name": "City of College Station",
    "website_url": "https://www.cstx.gov/",
}

CITY_AREA = {
    "college station": ("College Station", "college_station"),
    "bryan": ("Bryan", "bryan"),
}

TAG_TOPICS = {
    "history": "history",
    "art": "arts",
    "dance": "arts",
    "music": "music",
    "movie": "arts",
    "sports": "sports",
    "fitness": "sports",
    "aquatics": "sports",
    "nature": "nature",
    "parks": "outdoors",
    "fishing": "outdoors",
    "books": "reading",
    "city council": "community",
    "committee": "community",
    "community event": "community",
    "holiday": "community",
    "special event": "community",
    "food/drinks": "community",
    "lecture": "community",
}

#: Fallback when a list row has a venue title but no address. Keys are
#: whitespace-normalized lowercase titles.
VENUES: dict[str, dict[str, Any]] = {
    "larry j. ringer library": {
        "slug": "larry-j-ringer-library",
        "name": "Larry J. Ringer Library",
        "street": "1818 Harvey Mitchell Parkway South",
        "city": "College Station",
        "region": "TX",
        "postcode": "77845",
        "area": "college_station",
        "indoor": True,
    },
    "stephen c. beachy central park": {
        "slug": "stephen-c-beachy-central-park",
        "name": "Stephen C. Beachy Central Park",
        "street": "1000 Krenek Tap Road",
        "city": "College Station",
        "region": "TX",
        "postcode": "77840",
        "area": "college_station",
        "indoor": False,
    },
    "college station city hall": {
        "slug": "college-station-city-hall",
        "name": "College Station City Hall",
        "street": "1101 Texas Avenue",
        "city": "College Station",
        "region": "TX",
        "postcode": "77840",
        "area": "college_station",
        "indoor": True,
    },
    "college station municipal court": {
        "slug": "college-station-municipal-court",
        "name": "College Station Municipal Court",
        "street": "300 Krenek Tap Road",
        "city": "College Station",
        "region": "TX",
        "postcode": "77840",
        "area": "college_station",
        "indoor": True,
    },
    "wolf pen creek park": {
        "slug": "wolf-pen-creek-park",
        "name": "Wolf Pen Creek Park",
        "street": "1015 Colgate Drive",
        "city": "College Station",
        "region": "TX",
        "postcode": "77840",
        "area": "college_station",
        "indoor": False,
    },
    "meyer senior & community center": {
        "slug": "bob-and-wanda-meyer-senior-and-community-center",
        "name": "Bob and Wanda Meyer Senior and Community Center",
        "street": "2275 Dartmouth Street",
        "city": "College Station",
        "region": "TX",
        "postcode": "77840",
        "area": "college_station",
        "indoor": True,
    },
    "southwood community center": {
        "slug": "southwood-community-center",
        "name": "Southwood Community Center",
        "street": "1520 Rock Prairie Road",
        "city": "College Station",
        "region": "TX",
        "postcode": "77845",
        "area": "college_station",
        "indoor": True,
    },
    "gary halter nature center": {
        "slug": "gary-halter-nature-center",
        "name": "Gary Halter Nature Center",
        "street": "13600 Rock Prairie Road",
        "city": "College Station",
        "region": "TX",
        "postcode": "77845",
        "area": "college_station",
        "indoor": True,
    },
    "lick creek park": {
        "slug": "lick-creek-park",
        "name": "Lick Creek Park",
        "street": "13600 Rock Prairie Road",
        "city": "College Station",
        "region": "TX",
        "postcode": "77845",
        "area": "college_station",
        "indoor": False,
    },
    "lincoln recreation center": {
        "slug": "lincoln-recreation-center",
        "name": "Lincoln Recreation Center",
        "street": "1809 Deacon Drive",
        "city": "College Station",
        "region": "TX",
        "postcode": "77840",
        "area": "college_station",
        "indoor": True,
    },
    "veterans park & athletic complex": {
        "slug": "veterans-park-and-athletic-complex",
        "name": "Veterans Park & Athletic Complex",
        "street": "3101 Harvey Road",
        "city": "College Station",
        "region": "TX",
        "postcode": "77845",
        "area": "college_station",
        "indoor": False,
    },
    "w.a. tarrow pavilion": {
        "slug": "wa-tarrow-pavilion",
        "name": "W.A. Tarrow Pavilion",
        "street": "600 Tarrow Street",
        "city": "College Station",
        "region": "TX",
        "postcode": "77840",
        "area": "college_station",
        "indoor": False,
    },
    "post oak mall": {
        "slug": "post-oak-mall",
        "name": "Post Oak Mall",
        "street": "1500 Harvey Road",
        "city": "College Station",
        "region": "TX",
        "postcode": "77840",
        "area": "college_station",
        "indoor": True,
    },
    "aggie field of honor & memorial cemetery": {
        "slug": "aggie-field-of-honor",
        "name": "Aggie Field of Honor & Memorial Cemetery",
        "street": "3101 Harvey Road",
        "city": "College Station",
        "region": "TX",
        "postcode": "77845",
        "area": "college_station",
        "indoor": False,
    },
    "visit college station": {
        "slug": "college-station-visitors-center",
        "name": "Visit College Station",
        "street": "1207 Texas Avenue",
        "city": "College Station",
        "region": "TX",
        "postcode": "77840",
        "area": "college_station",
        "indoor": True,
    },
}

_API_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Origin": SITE,
    "Referer": f"{SITE}/{CALENDAR_CODE}/clean-list",
    "X-Api-Key": API_KEY,
}


class _Drop(Exception):
    """Not an error — the record is out of scope and should vanish quietly."""


class CstxCalScraper(BaseEventScraper):
    kind = "cstxcal"
    source_slug = "cstxcal"
    source_name = "City of College Station Calendar"
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

        items = _fetch_list(client, window_start, window_end)
        details = _fetch_details(client, {item.get("id") for item in items})
        raw: list[RawEvent] = []
        for item in items:
            merged = merge_detail(item, details.get(item.get("id")))
            row = to_raw(merged)
            if not in_window(row, window_start, window_end):
                continue
            raw.append(row)
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

    def _series(self, item: RawEvent) -> dict[str, Any]:
        rec = item.record
        title = (rec.get("title") or "").strip()
        if not title:
            raise ScraperError("missing title")
        place = place_from(rec)
        indoor = place.pop("indoor", None)
        description = html_text(rec.get("description"))
        tags = titles_of(rec, "taxonomy_tag")
        cats = titles_of(rec, "taxonomy_category")
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "place": place,
            "topics": topics_from(tags, cats),
            "audiences": audiences_from(tags),
        }
        if description:
            series["description"] = description
        url = series_url(rec)
        if url:
            series["source_url"] = url
        if indoor is not None:
            series["indoor"] = indoor
        if "registration required" in {t.lower() for t in tags}:
            series["registration_required"] = True
        if is_free(tags):
            series["is_free"] = True
        ext = (rec.get("cost_external_url") or "").strip()
        if ext:
            series["registration_url"] = ext
        return series


def merge_detail(item: dict, detail: dict | None) -> dict:
    """Overlay description and addressed venues from the per-id payload. Pure."""
    if not detail:
        return item
    out = dict(item)
    if detail.get("description"):
        out["description"] = detail["description"]
    if detail.get("timezone") and not out.get("timezone"):
        out["timezone"] = detail["timezone"]
    if detail.get("cost_external_url") and not out.get("cost_external_url"):
        out["cost_external_url"] = detail["cost_external_url"]
    dtax = detail.get("taxonomies") or {}
    if dtax.get("taxonomy_venue"):
        tax = dict(out.get("taxonomies") or {})
        tax["taxonomy_venue"] = dtax["taxonomy_venue"]
        out["taxonomies"] = tax
    return out


def to_raw(item: dict) -> RawEvent:
    eid = item.get("id")
    return RawEvent(
        series_uid=str(eid or ""),
        occurrence_tid=occurrence_tid(item),
        record=item,
    )


def occurrence_tid(item: dict) -> str:
    start = wall_clock(item)
    if start is None:
        return str(item.get("id") or "")
    aware = start.replace(tzinfo=ZoneInfo(TIMEZONE))
    return str(int(aware.timestamp() * 1000))


def wall_clock(item: dict) -> datetime | None:
    inst = str(item.get("instance") or "")
    if len(inst) == 14 and inst.isdigit():
        return datetime.strptime(inst, "%Y%m%d%H%M%S")
    raw = item.get("start_datetime") or ""
    text = str(raw).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def in_window(item: RawEvent, window_start: date, window_end: date) -> bool:
    start = wall_clock(item.record)
    if start is None:
        return False
    return window_start <= start.date() < window_end


def drop_reason(record: dict) -> str | None:
    if (record.get("event_status") or "").strip().lower() == "cancelled":
        return "cancelled"
    cats = {t.lower() for t in titles_of(record, "taxonomy_category")}
    if "notice" in cats:
        return "notice"
    venue = first_venue(record)
    title = ((venue or {}).get("title") or "").strip()
    if title.lower().startswith("teams meeting"):
        return "virtual"
    return None


def titles_of(record: dict, key: str) -> list[str]:
    tax = record.get("taxonomies") or {}
    out: list[str] = []
    for item in tax.get(key) or []:
        title = (item.get("title") or "").strip()
        if title:
            out.append(title)
    return out


def first_venue(record: dict) -> dict | None:
    tax = record.get("taxonomies") or {}
    items = tax.get("taxonomy_venue") or []
    return items[0] if items else None


def venue_key(title: str) -> str:
    return " ".join((title or "").strip().lower().split())


def place_from(record: dict) -> dict[str, Any]:
    venue = first_venue(record)
    if not venue:
        raise UnknownVenueError("unknown venue: (none)")
    title = (venue.get("title") or "").strip()
    if not title:
        raise UnknownVenueError("unknown venue: (none)")
    fallback = VENUES.get(venue_key(title))
    city_raw = (venue.get("city") or "").strip()
    mapped = CITY_AREA.get(city_raw.lower()) if city_raw else None
    street = (venue.get("address") or "").strip()
    if mapped and street:
        city, area = mapped
        place: dict[str, Any] = {
            "slug": normalize.slugify(f"{title}-{city}"),
            "name": title,
            "street": street,
            "city": city,
            "region": "TX",
            "area": area,
        }
        postcode = (venue.get("postal_code") or "").strip()
        if postcode:
            place["postcode"] = postcode.split("-", 1)[0]
        extra = venue.get("address2")
        if extra:
            place["street"] = f"{street}, {extra}".strip(", ")
        lat, lng = parse_geo(venue.get("geo_location"))
        if lat is not None:
            place["latitude"] = lat
            place["longitude"] = lng
        indoor = indoor_from_title(title)
        if indoor is None and fallback:
            indoor = fallback.get("indoor")
        if indoor is not None:
            place["indoor"] = indoor
        return place
    if fallback:
        return dict(fallback)
    raise UnknownVenueError(f"unknown venue: {title}")


def parse_geo(value: Any) -> tuple[float | None, float | None]:
    text = str(value or "").strip()
    if "," not in text:
        return None, None
    left, right = text.split(",", 1)
    try:
        return float(left.strip()), float(right.strip())
    except ValueError:
        return None, None


def indoor_from_title(title: str) -> bool | None:
    key = venue_key(title)
    if "park" in key and "center" not in key and "pavilion" not in key:
        return False
    if any(bit in key for bit in (
        "library", "city hall", "court", "community center",
        "recreation center", "nature center", "mall",
    )):
        return True
    if "pavilion" in key or "pool" in key or "lagoon" in key or "cemetery" in key:
        return False
    return None


def topics_from(tags: list[str], cats: list[str]) -> list[str]:
    out: set[str] = set()
    for tag in tags:
        topic = TAG_TOPICS.get(tag.strip().lower())
        if topic:
            out.add(topic)
    if any(c.strip().lower() == "meeting" for c in cats):
        out.add("community")
    return sorted(out)


def audiences_from(tags: list[str]) -> list[str]:
    keys = {t.strip().lower() for t in tags}
    out: set[str] = set()
    if "family friendly" in keys:
        out.add("all-ages")
    if "adult (18+)" in keys or "senior (55+)" in keys:
        out.add("adult")
    if "youth" in keys and "all-ages" not in out:
        out.add("all-ages")
    return sorted(out)


def is_free(tags: list[str]) -> bool:
    keys = {t.strip().lower() for t in tags}
    return "free" in keys and "paid" not in keys


def html_text(value: Any) -> str | None:
    if not value:
        return None
    text = BeautifulSoup(str(value), "html.parser").get_text("\n", strip=True)
    return normalize.clean_text(text)


def series_url(record: dict) -> str | None:
    raw = (record.get("canonical_url") or record.get("url") or "").strip()
    if not raw:
        custom = (record.get("custom_url") or "").strip()
        if not custom:
            return None
        raw = f"{SITE}/{CALENDAR_CODE}/event/{custom}"
    parsed = urlparse(raw)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _occurrence(item: RawEvent) -> dict[str, Any]:
    start = wall_clock(item.record)
    if start is None:
        raise ScraperError("missing start_datetime")
    occ: dict[str, Any] = {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": start.isoformat(),
        "timezone": TIMEZONE,
        "all_day": bool(item.record.get("allday")),
        "status": "scheduled",
    }
    end_raw = item.record.get("end_datetime")
    if end_raw:
        end = wall_clock({"start_datetime": end_raw, "instance": ""})
        if end is not None:
            occ["end_local"] = end.isoformat()
    return occ


def _window_utc(day: date) -> int:
    local = datetime.combine(day, time.min, tzinfo=ZoneInfo(TIMEZONE))
    return int(local.timestamp())


def _fetch_list(client: HttpClient, window_start: date, window_end: date) -> list[dict]:
    items: list[dict] = []
    page = 1
    while True:
        payload = client.get_json(
            EVENTS_URL,
            params={
                "range": "days",
                "group_by_date": 0,
                "timezone": TIMEZONE,
                "view": "clean_list",
                "start_date_utc": _window_utc(window_start),
                "end_date_utc": _window_utc(window_end),
                "per_page": PAGE_SIZE,
                "page": page,
                f"filter_groups_{TYPE_FILTER_GROUP_ID}": PUBLIC_TYPE_ID,
            },
            headers=_API_HEADERS,
        )
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, dict):
            raise ScraperError("cstxcal: events list was not a Timely envelope")
        batch = data.get("items") or []
        items.extend(batch)
        if not data.get("has_next") or not batch:
            break
        page += 1
        if page > 20:
            break
    return items


def _fetch_details(client: HttpClient, ids: set[Any]) -> dict[Any, dict]:
    out: dict[Any, dict] = {}
    for eid in ids:
        if eid is None:
            continue
        payload = client.get_json(f"{EVENTS_URL}/{eid}", headers=_API_HEADERS)
        detail = payload.get("data") if isinstance(payload, dict) else None
        if isinstance(detail, dict):
            out[eid] = detail
    return out
