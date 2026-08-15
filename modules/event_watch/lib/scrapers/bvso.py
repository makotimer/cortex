"""Brazos Valley Symphony Orchestra concerts.

Season cards live on ``/concerts/`` (day + month, year from the 2026-2027
heading). Tickera ``tc_events`` lists the same slugs plus leftovers. Dates,
clock and venue are on ``/show-item/{slug}/``. Leftovers without an explicit
year are dropped rather than rolled into the current season.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from html import unescape
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError

SITE = "https://bvso.org"
CONCERTS_URL = f"{SITE}/concerts/"
TC_EVENTS_URL = f"{SITE}/wp-json/wp/v2/tc_events"
SHOW_URL = f"{SITE}/show-item/"
TIMEZONE = "America/Chicago"

ORGANIZATION = {
    "slug": "bvso",
    "name": "Brazos Valley Symphony Orchestra",
    "website_url": SITE + "/",
}

VENUES: dict[str, dict[str, Any]] = {
    "rudder-theatre": {
        "slug": "rudder-theatre",
        "name": "Rudder Theatre",
        "street": "401 Joe Routt Blvd",
        "city": "College Station",
        "region": "TX",
        "postcode": "77843",
        "area": "college_station",
        "latitude": 30.612556,
        "longitude": -96.341333,
    },
    "rudder-auditorium": {
        "slug": "rudder-auditorium",
        "name": "Rudder Auditorium",
        "street": "401 Joe Routt Blvd",
        "city": "College Station",
        "region": "TX",
        "postcode": "77843",
        "area": "college_station",
        "latitude": 30.612556,
        "longitude": -96.341333,
    },
    "christ-church-college-station": {
        "slug": "christ-church-college-station",
        "name": "Christ Church, College Station",
        "street": "4201 State Highway 6 S",
        "city": "College Station",
        "region": "TX",
        "postcode": "77845",
        "area": "college_station",
        "latitude": 30.56785,
        "longitude": -96.2674,
    },
}

_MONTHS = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_MONTH_NAMES = (
    "January|February|March|April|May|June|July|August|"
    "September|October|November|December"
)
_SEASON = re.compile(r"(20\d{2})\s*[–-]\s*(20\d{2}|\d{2})")
_CLOCK = re.compile(
    r"\b(\d{1,2})(?::(\d{2}))?\s*(a\.m\.|p\.m\.|am|pm)(?!\w)",
    re.I,
)
_CONCERT_STARTS = re.compile(r"Concert\s+Starts:\s*(.+)", re.I)
_DATED = re.compile(
    rf"({_MONTH_NAMES})\s+(\d{{1,2}}),?\s+(20\d{{2}})"
    rf"(?:\s*[|]\s*([^\n]+))?",
    re.I,
)
_VENUE_PATTERNS = (
    (re.compile(r"rudder\s+auditorium", re.I), "rudder-auditorium"),
    (re.compile(r"rudder\s+theat(?:re|er)", re.I), "rudder-theatre"),
    (re.compile(r"christ\s+church", re.I), "christ-church-college-station"),
)
_SLUG_FROM_SHOW = re.compile(r"/show-item/([^/]+)/?")
_SLUG_FROM_TICKETS = re.compile(r"/tc-events/([^/]+)/?")


class _Drop(Exception):
    """Not an error — no parseable future date, vanish quietly."""


class BvsoScraper(BaseEventScraper):
    kind = "bvso"
    source_slug = "bvso"
    source_name = ORGANIZATION["name"]
    verify_url = CONCERTS_URL

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
        concerts = parse_concerts(client.get_text(CONCERTS_URL))
        posts = parse_tc_events(_get_tc_events(client))
        catalog = _union(concerts, posts)
        raw: list[RawEvent] = []
        for slug, meta in catalog.items():
            html_text = _get_show(client, slug)
            if html_text is None:
                continue
            show = parse_show(html_text)
            season_years = concerts["season_years"] if meta.get("from_season") else None
            title = show.get("title") or meta.get("title") or slug
            items = to_raws(
                slug,
                title,
                show,
                season_years=season_years,
                tickets_url=meta.get("tickets_url"),
            )
            for item in items:
                day = item.record.get("date")
                if isinstance(day, date) and not (window_start <= day < window_end):
                    continue
                raw.append(item)
        return raw

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
        description = normalize.clean_text(rec.get("description"))
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "place": place_for(rec.get("venue_text")),
            "topics": ["music"],
            "indoor": True,
        }
        if description:
            series["description"] = description
        if rec.get("show_url"):
            series["source_url"] = rec["show_url"]
        if rec.get("tickets_url"):
            series["registration_url"] = rec["tickets_url"]
        return series


def parse_concerts(html_text: str) -> dict[str, Any]:
    """``/concerts/`` season heading + cards. Pure."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    heading = soup.select_one("h2.mkdf-st-title")
    season_years = parse_season_years(heading.get_text(" ", strip=True) if heading else "")
    cards: list[dict] = []
    for item in soup.select(".mkdf-event-list-item"):
        title_el = item.select_one(".mkdf-eli-title")
        date_el = item.select_one(".mkdf-el-date-separated")
        show_url = ""
        tickets_url = ""
        for a in item.select("a[href]"):
            href = a.get("href") or ""
            if "/show-item/" in href:
                show_url = href
            elif "/tc-events/" in href:
                tickets_url = href
        slug = _slug_from_url(show_url) or _slug_from_url(tickets_url)
        if not slug:
            continue
        day = None
        month = None
        if date_el:
            h1 = date_el.find("h1")
            h6 = date_el.find("h6")
            try:
                day = int((h1.get_text(strip=True) if h1 else "").strip())
            except ValueError:
                day = None
            month = _MONTHS.get((h6.get_text(strip=True) if h6 else "").strip().lower())
        cards.append({
            "slug": slug,
            "title": unescape((title_el.get_text(" ", strip=True) if title_el else "").strip()),
            "show_url": show_url or f"{SHOW_URL}{slug}/",
            "tickets_url": tickets_url or f"{SITE}/tc-events/{slug}/",
            "day": day,
            "month": month,
            "from_season": True,
        })
    return {"season_years": season_years, "cards": cards}


def parse_tc_events(payload: str | list) -> list[dict]:
    """Published Tickera posts -> slug/title/tickets. Pure."""
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ScraperError("bvso: tc_events returned non-JSON") from exc
    else:
        data = payload
    if not isinstance(data, list):
        raise ScraperError("bvso: tc_events is not a list")
    out: list[dict] = []
    for post in data:
        if not isinstance(post, dict):
            continue
        if (post.get("status") or "publish") != "publish":
            continue
        slug = (post.get("slug") or "").strip()
        if not slug:
            continue
        title_obj = post.get("title") or {}
        title = title_obj.get("rendered") if isinstance(title_obj, dict) else title_obj
        out.append({
            "slug": slug,
            "title": unescape(str(title or "").strip()),
            "tickets_url": (post.get("link") or f"{SITE}/tc-events/{slug}/").strip(),
            "from_season": False,
        })
    return out


def parse_show(html_text: str) -> dict[str, Any]:
    """``/show-item/{slug}/`` date, Concert Starts, venue, dated nights. Pure."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    title_el = soup.select_one("h2.mkdf-page-title, h2")
    date_el = soup.select_one(".mkdf-single-show-date")
    desc_el = soup.select_one(".mkdf-single-show-description")
    month = None
    day = None
    if date_el:
        h1 = date_el.find("h1")
        h6 = date_el.find("h6")
        try:
            day = int((h1.get_text(strip=True) if h1 else "").strip())
        except ValueError:
            day = None
        month = _MONTHS.get((h6.get_text(strip=True) if h6 else "").strip().lower())
    description = desc_el.get_text("\n", strip=True) if desc_el else ""
    return {
        "title": unescape((title_el.get_text(" ", strip=True) if title_el else "").strip()),
        "month": month,
        "day": day,
        "description": description,
        "concert_clock": parse_concert_clock(description),
        "venue_text": _venue_line(description),
        "dated_starts": parse_dated_starts(description),
    }


def parse_season_years(text: str) -> tuple[int, int] | None:
    match = _SEASON.search(text or "")
    if not match:
        return None
    first = int(match.group(1))
    second = int(match.group(2))
    if second < 100:
        second = (first // 100) * 100 + second
        if second < first:
            second += 100
    return first, second


def year_from_season(month: int, season_years: tuple[int, int]) -> int:
    """Sep–Dec belong to the first year of a season heading; Jan–Aug the second."""
    first, second = season_years
    return first if 9 <= month <= 12 else second


def parse_clock(text: str) -> tuple[int, int] | None:
    match = _CLOCK.search(text or "")
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    mer = match.group(3).lower().replace(".", "")
    if not (1 <= hour <= 12) or minute > 59:
        return None
    hour %= 12
    if mer == "pm":
        hour += 12
    return hour, minute


def parse_concert_clock(text: str) -> tuple[int, int] | None:
    """Concert Starts wins; otherwise first non-reception clock."""
    labeled = _CONCERT_STARTS.search(text or "")
    if labeled:
        clock = parse_clock(labeled.group(1))
        if clock:
            return clock
    for line in (text or "").splitlines():
        if re.search(r"reception", line, re.I):
            continue
        clock = parse_clock(line)
        if clock:
            return clock
    return None


def parse_dated_starts(text: str) -> list[dict]:
    """``December 5, 2025 | 7 PM`` lines. Empty if the page has no year."""
    out: list[dict] = []
    for match in _DATED.finditer(text or ""):
        month = _MONTHS.get(match.group(1).lower())
        try:
            day = int(match.group(2))
            year = int(match.group(3))
            day_date = date(year, month, day) if month else None
        except ValueError:
            day_date = None
        if day_date is None:
            continue
        clocks = _clocks_in(match.group(4) or "")
        if clocks:
            for clock in clocks:
                out.append({"date": day_date, "clock": clock})
        else:
            out.append({"date": day_date, "clock": None})
    return out


def to_raws(
    slug: str,
    title: str,
    show: dict,
    *,
    season_years: tuple[int, int] | None,
    tickets_url: str | None = None,
) -> list[RawEvent]:
    """Show + optional season years -> one RawEvent per performance. Pure.

    ``season_years`` is only for slugs that appeared on ``/concerts/``. A leftover
    Tickera post must carry its own year on the page or it is dropped.
    """
    starts = list(show.get("dated_starts") or [])
    if not starts:
        month, day = show.get("month"), show.get("day")
        if month and day and season_years:
            try:
                starts = [{
                    "date": date(year_from_season(int(month), season_years), int(month), int(day)),
                    "clock": show.get("concert_clock"),
                }]
            except ValueError:
                starts = []
    if not starts:
        return []
    venue_text = show.get("venue_text") or ""
    description = show.get("description") or ""
    show_url = f"{SHOW_URL}{slug}/"
    out: list[RawEvent] = []
    for start in starts:
        day = start.get("date")
        clock = start.get("clock")
        if day is None:
            continue
        if clock:
            when = datetime(day.year, day.month, day.day, clock[0], clock[1], tzinfo=ZoneInfo(TIMEZONE))
        else:
            when = datetime(day.year, day.month, day.day, tzinfo=ZoneInfo(TIMEZONE))
        tid = str(int(when.timestamp() * 1000))
        out.append(RawEvent(
            series_uid=slug,
            occurrence_tid=tid,
            record={
                "slug": slug,
                "title": title,
                "date": day,
                "clock": clock,
                "venue_text": venue_text,
                "description": description,
                "show_url": show_url,
                "tickets_url": tickets_url,
            },
        ))
    return out


def place_for(venue_text: str | None) -> dict:
    """Map a hall name onto a pinned place. Unknown names fail loudly."""
    text = (venue_text or "").strip()
    if not text:
        raise ScraperError("unknown venue name=''; add it to VENUES with an explicit area")
    for pattern, key in _VENUE_PATTERNS:
        if pattern.search(text):
            return dict(VENUES[key])
    raise ScraperError(
        f"unknown venue name={text!r}; add it to VENUES with an explicit area"
    )


def _occurrence(item: RawEvent) -> dict:
    rec = item.record
    day: date | None = rec.get("date")
    if day is None:
        raise _Drop()
    clock = rec.get("clock")
    if clock:
        start = datetime(day.year, day.month, day.day, clock[0], clock[1])
        all_day = False
    else:
        start = datetime(day.year, day.month, day.day)
        all_day = True
    aware = start.replace(tzinfo=ZoneInfo(TIMEZONE))
    tid = str(int(aware.timestamp() * 1000))
    item.occurrence_tid = tid
    return {
        "source_occurrence_tid": tid,
        "start_local": start.isoformat(),
        "timezone": TIMEZONE,
        "all_day": all_day,
        "status": "scheduled",
    }


def _union(concerts: dict, posts: list[dict]) -> dict[str, dict]:
    catalog: dict[str, dict] = {}
    for card in concerts.get("cards") or []:
        catalog[card["slug"]] = dict(card)
    for post in posts:
        slug = post["slug"]
        if slug in catalog:
            if not catalog[slug].get("tickets_url") and post.get("tickets_url"):
                catalog[slug]["tickets_url"] = post["tickets_url"]
            continue
        catalog[slug] = dict(post)
    return catalog


def _slug_from_url(url: str) -> str:
    for pattern in (_SLUG_FROM_SHOW, _SLUG_FROM_TICKETS):
        match = pattern.search(url or "")
        if match:
            return match.group(1)
    return ""


def _venue_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        for pattern, _key in _VENUE_PATTERNS:
            if pattern.search(stripped):
                return stripped
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped and not parse_clock(stripped) and not re.search(
            r"time\s*&\s*location|reception|concert starts|date\s*&\s*time|^about$",
            stripped,
            re.I,
        ):
            # First non-time location-ish leftover (e.g. Benjamin Knox Gallery).
            if re.search(r"gallery|pavilion|library|church|theatre|theater|auditorium|hall", stripped, re.I):
                return stripped
    return ""


def _clocks_in(text: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for match in _CLOCK.finditer(text or ""):
        clock = parse_clock(match.group(0))
        if clock:
            out.append(clock)
    return out


def _get_tc_events(client: HttpClient) -> list:
    page = 1
    posts: list = []
    while page <= 20:
        resp = client.session.get(
            TC_EVENTS_URL,
            params={"per_page": 100, "page": page, "status": "publish"},
            timeout=client.timeout,
        )
        if resp.status_code == 400 and page > 1:
            break
        resp.raise_for_status()
        chunk = resp.json()
        if not isinstance(chunk, list) or not chunk:
            break
        posts.extend(chunk)
        total_pages = int(resp.headers.get("X-WP-TotalPages") or 1)
        if page >= total_pages:
            break
        page += 1
    return posts


def _get_show(client: HttpClient, slug: str) -> str | None:
    resp = client.session.get(urljoin(SHOW_URL, slug + "/"), timeout=client.timeout)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp.text
