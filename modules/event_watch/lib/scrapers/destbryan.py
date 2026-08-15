"""Destination Bryan events directory, via Craft CMS listing HTML + Event JSON-LD.

The public ``/events/`` page is a Craft directory. Date filters work as ISO
query params; other date formats 500. There is no JSON list — 12 cards per
page, paginated with ``page=N``. Clock times and a full postal address live on
each detail page as schema.org Event JSON-LD.
"""

from __future__ import annotations

import contextlib
import html
import json
import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from modules._shared.http import HttpClient

from .. import normalize
from . import kbtx
from .base import BaseEventScraper, RawEvent, ScraperError

LIST_URL = "https://www.destinationbryan.com/events/"
TIMEZONE = "America/Chicago"
PAGE_SIZE = 12
MAX_SPAN_DAYS = 14

ORGANIZATION = {
    "slug": "destinationbryan",
    "name": "Destination Bryan",
    "website_url": "https://www.destinationbryan.com/events/",
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
    "live music": "music",
    "sports": "sports",
    "aggie sports": "sports",
    "fitness": "sports",
    "outdoors": "outdoors",
    "rodeos & ag events": "outdoors",
    "fairs & festivals": "community",
    "market": "community",
    "culinary": "community",
}

NIGHTLIFE_ID = "408042"
FAMILY_ID = "408038"

_DATE_RANGE = re.compile(
    r"^(?P<sm>January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(?P<sd>\d{1,2})"
    r"(?:\s+to\s+(?P<em>January|February|March|April|May|June|July|August|"
    r"September|October|November|December)\s+(?P<ed>\d{1,2}))?"
    r"(?:,\s*(?P<year>\d{4}))?$",
    re.I,
)
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


class _Drop(Exception):
    """Not an error — the record is out of scope and should vanish quietly."""


class DestBryanScraper(BaseEventScraper):
    kind = "destbryan"
    source_slug = "destinationbryan"
    source_name = ORGANIZATION["name"]
    verify_url = LIST_URL

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

        cards = _fetch_listing(client, window_start, window_end)
        extra_cats = _category_index(client, window_start, window_end)
        for card in cards:
            extras = extra_cats.get(card.get("entry_id") or "", set())
            if extras:
                card["categories"] = sorted(set(card.get("categories") or []) | extras)

        cache: dict[str, dict] = {}
        if self._state_dir:
            from .. import state

            cache = state.load_addresses(self._state_dir, self.source_slug)

        raw: list[RawEvent] = []
        resolve = self._resolve or kbtx.resolve_address
        for card in cards:
            item = to_raw(card)
            if drop_reason(card, window_start.year):
                continue
            href = card.get("href")
            if href:
                with contextlib.suppress(Exception):
                    item.supplement["jsonld"] = _fetch_jsonld(client, href)
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
                if drop_reason(item.record, _window_year(item)):
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
        jsonld = item.supplement.get("jsonld") or {}
        description = normalize.clean_text(jsonld.get("description") or rec.get("description"))
        cats = rec.get("categories") or ([rec["category"]] if rec.get("category") else [])
        topics = topics_from_categories(cats)
        if title.lower().startswith("live music"):
            topics = sorted(set(topics) | {"music"})
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "topics": topics,
            "audiences": audiences_from_categories(cats),
        }
        if description:
            series["description"] = description
        if rec.get("href"):
            series["source_url"] = rec["href"]
        if any(_cat_key(c) == "free" for c in cats):
            series["is_free"] = True
        place = _place_for(item)
        if place:
            series["place"] = place
        return series


def parse_list_html(html_text: str) -> list[dict]:
    """One directory page -> card dicts. Pure."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    out: list[dict] = []
    seen: set[str] = set()
    for art in soup.select("article.card"):
        entry_id = art.get("data-entry-id") or ""
        title_el = art.find(attrs={"data-dms-partner-name": True})
        title = html.unescape(title_el.get("data-dms-partner-name") or "") if title_el else ""
        cat_el = art.find(attrs={"data-dms-category-name": True})
        category = html.unescape(cat_el.get("data-dms-category-name") or "") if cat_el else ""
        href_el = art.select_one("a.card__heading, a.card__image, a.card__link")
        href = (href_el.get("href") or "").strip() if href_el else ""
        if href.startswith("/"):
            href = "https://www.destinationbryan.com" + href
        addr_el = art.select_one(".card__address")
        address = addr_el.get_text(" ", strip=True) if addr_el else ""
        date_el = art.select_one(".card__date-heading")
        date_text = date_el.get_text(" ", strip=True) if date_el else ""
        marker = art.find(attrs={"data-marker-lat": True}) or art
        lat = marker.get("data-marker-lat") if marker else None
        lng = marker.get("data-marker-lng") if marker else None
        maps = ""
        maps_el = art.find("a", href=re.compile(r"maps\.google\.com"))
        if maps_el and maps_el.get("href"):
            from urllib.parse import parse_qs, unquote, urlparse

            q = parse_qs(urlparse(maps_el["href"]).query).get("q", [""])[0]
            maps = unquote(q).replace("\n", " | ")
        key = (entry_id or href, date_text)
        if not title or key in seen:
            continue
        seen.add(key)
        out.append({
            "entry_id": entry_id,
            "title": title,
            "category": category,
            "categories": [category] if category else [],
            "href": href,
            "address": address,
            "date_text": date_text,
            "lat": lat,
            "lng": lng,
            "maps": maps,
        })
    return out


def to_raw(card: dict) -> RawEvent:
    start, _end = parse_date_range(card.get("date_text") or "", 2026)
    tid = ""
    if start:
        dt = datetime(start.year, start.month, start.day, tzinfo=ZoneInfo(TIMEZONE))
        tid = str(int(dt.timestamp() * 1000))
    return RawEvent(
        series_uid=str(card.get("entry_id") or ""),
        occurrence_tid=tid,
        record=card,
    )


def parse_date_range(text: str, default_year: int) -> tuple[date | None, date | None]:
    raw = html.unescape(text or "").strip()
    raw = re.sub(r"\s+", " ", raw)
    match = _DATE_RANGE.match(raw)
    if not match:
        return None, None
    sm = _MONTHS[match.group("sm").lower()]
    sd = int(match.group("sd"))
    year = int(match.group("year") or default_year)
    start = date(
        year
        if match.group("em") is None or not match.group("year")
        else (year if sm <= _MONTHS[match.group("em").lower()] else year - 1),
        sm,
        sd,
    )
    if match.group("year") and match.group("em"):
        # "August 15 to May 16, 2027" — the year belongs to the end date.
        em = _MONTHS[match.group("em").lower()]
        end_year = year
        start_year = year if sm <= em else year - 1
        start = date(start_year, sm, sd)
        end = date(end_year, em, int(match.group("ed")))
        return start, end
    if match.group("em"):
        em = _MONTHS[match.group("em").lower()]
        ed = int(match.group("ed"))
        end_year = default_year if em >= sm else default_year + 1
        start = date(default_year, sm, sd)
        return start, date(end_year, em, ed)
    return start, start


def drop_reason(card: dict, default_year: int) -> str | None:
    start, end = parse_date_range(card.get("date_text") or "", default_year)
    if start and end and (end - start).days > MAX_SPAN_DAYS:
        return "span"
    city = city_from_card(card)
    if city and area_from_city(city) is None:
        return "city"
    return None


def city_from_card(card: dict) -> str | None:
    maps = card.get("maps") or ""
    match = re.search(r"\|\s*([^|]+?),\s*TX\b", maps) or re.search(r"\n\s*([^,\n]+),\s*TX\b", maps)
    if match:
        return match.group(1).strip()
    return None


def area_from_city(city: str | None) -> tuple[str, str] | None:
    key = re.sub(r"\s+", " ", (city or "").strip().lower())
    return CITY_AREA.get(key) or NEARBY_CITIES.get(key)


def _cat_key(label: str) -> str:
    return html.unescape(label or "").strip().lower()


def topics_from_categories(labels: list[str]) -> list[str]:
    out = {CATEGORY_TOPICS[key] for key in (_cat_key(c) for c in labels) if key in CATEGORY_TOPICS}
    if any(_cat_key(c) == "live music" or (c or "").lower().startswith("live music") for c in labels):
        out.add("music")
    return sorted(out)


def audiences_from_categories(labels: list[str]) -> list[str]:
    keys = {_cat_key(c) for c in labels}
    out = set()
    if "nightlife" in keys:
        out.add("adult")
    if "family-friendly" in keys:
        out.add("all-ages")
    return sorted(out)


def wall_clock_local(value: str | None) -> datetime | None:
    """ISO stamp -> naive local Central.

    Some Destination Bryan JSON-LD values wear a fake ``Z`` the same way
    CitySpark does; those are wall-clock Central, not UTC. Stamps with a real
    offset are converted into America/Chicago.
    """
    if not value:
        return None
    raw = str(value).strip()
    if raw.endswith("Z") and "+00:00" not in raw:
        raw = raw[:-1]
        if raw.endswith(".000000"):
            raw = raw[:-7]
        try:
            return datetime.fromisoformat(raw)
        except ValueError:
            return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(ZoneInfo(TIMEZONE)).replace(tzinfo=None)
    return dt


def _occurrence(item: RawEvent) -> dict:
    jsonld = item.supplement.get("jsonld") or {}
    start = wall_clock_local(jsonld.get("startDate"))
    if start is None:
        day, _ = parse_date_range(item.record.get("date_text") or "", _window_year(item))
        if day is None:
            raise ScraperError("missing start")
        start = datetime(day.year, day.month, day.day)
        all_day = True
    else:
        all_day = False
    tzid = TIMEZONE
    start_aware = start.replace(tzinfo=ZoneInfo(tzid))
    tid = str(int(start_aware.timestamp() * 1000))
    item.occurrence_tid = tid
    occ: dict[str, Any] = {
        "source_occurrence_tid": tid,
        "start_local": start.isoformat(),
        "timezone": tzid,
        "all_day": all_day,
        "status": ("cancelled" if "EventCancelled" in str(jsonld.get("eventStatus") or "") else "scheduled"),
    }
    end = wall_clock_local(jsonld.get("endDate"))
    if end:
        occ["end_local"] = end.isoformat()
        if (end.date() - start.date()).days > MAX_SPAN_DAYS:
            raise _Drop()
    return occ


def _place_for(item: RawEvent) -> dict:
    jsonld = item.supplement.get("jsonld") or {}
    loc = jsonld.get("location") or {}
    addr = loc.get("address") or {}
    city = addr.get("addressLocality") or city_from_card(item.record)
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
    name = (loc.get("name") or "").strip() or (item.record.get("address") or "").strip() or display_city
    place: dict[str, Any] = {
        "slug": normalize.slugify(f"{name}-{display_city}"),
        "name": name,
        "city": display_city,
        "region": "TX",
        "area": area,
    }
    street = (addr.get("streetAddress") or item.record.get("address") or "").strip()
    if street:
        place["street"] = street
    postcode = addr.get("postalCode")
    if postcode:
        place["postcode"] = str(postcode)
    elif reso and reso.get("zip_code"):
        place["postcode"] = str(reso["zip_code"]).split("-")[0]
    rec = item.record
    try:
        if rec.get("lat"):
            place["latitude"] = float(rec["lat"])
        if rec.get("lng"):
            place["longitude"] = float(rec["lng"])
    except (TypeError, ValueError):
        pass
    return place


def _maybe_resolve_place(item: RawEvent, resolve: Any, cache: dict) -> None:
    jsonld = item.supplement.get("jsonld") or {}
    loc = (jsonld.get("location") or {}).get("address") or {}
    if loc.get("addressLocality"):
        return
    if city_from_card(item.record):
        return
    query = (item.record.get("maps") or item.record.get("address") or "").replace(" | ", ", ")
    if not query:
        return
    key = kbtx.venue_cache_key(None, query)
    if key not in cache:
        cache[key] = dict(resolve(query) or {"status": "no_match"})
    item.supplement["place_resolution"] = cache[key]


def _window_year(item: RawEvent) -> int:
    start, _ = parse_date_range(item.record.get("date_text") or "", 2026)
    return start.year if start else 2026


def _fetch_listing(client: HttpClient, window_start: date, window_end: date) -> list[dict]:
    cards: list[dict] = []
    page = 1
    while True:
        html_text = client.get_text(
            LIST_URL,
            params={
                "date-from": window_start.isoformat(),
                "date-to": window_end.isoformat(),
                "page": page,
            },
        )
        n_articles = html_text.count("<article")
        batch = parse_list_html(html_text)
        if n_articles == 0:
            break
        cards.extend(batch)
        if n_articles < PAGE_SIZE:
            break
        page += 1
        if page > 80:
            break
    return cards


def _category_index(client: HttpClient, window_start: date, window_end: date) -> dict[str, set[str]]:
    """entry_id -> extra category labels from Nightlife / Family-Friendly filters."""
    out: dict[str, set[str]] = {}
    for cat_id, label in ((NIGHTLIFE_ID, "Nightlife"), (FAMILY_ID, "Family-Friendly")):
        page = 1
        while True:
            html_text = client.get_text(
                LIST_URL,
                params={
                    "date-from": window_start.isoformat(),
                    "date-to": window_end.isoformat(),
                    "categories": cat_id,
                    "page": page,
                },
            )
            n_articles = html_text.count("<article")
            batch = parse_list_html(html_text)
            if n_articles == 0:
                break
            for card in batch:
                eid = card.get("entry_id")
                if eid:
                    out.setdefault(eid, set()).add(label)
            if n_articles < PAGE_SIZE:
                break
            page += 1
            if page > 80:
                break
    return out


def _fetch_jsonld(client: HttpClient, href: str) -> dict:
    text = client.get_text(href)
    match = re.search(r'<script type="application/ld\+json">(.*?)</script>', text, re.S | re.I)
    if not match:
        raise ScraperError(f"destbryan: no JSON-LD on {href}")
    data = json.loads(html.unescape(match.group(1)))
    if isinstance(data, dict) and "@graph" in data:
        for node in data["@graph"]:
            if isinstance(node, dict) and node.get("@type") == "Event":
                return node
    if isinstance(data, dict):
        return data
    raise ScraperError(f"destbryan: JSON-LD on {href} is not an Event")
