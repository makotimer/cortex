"""College Station Parks & Rec events, via RecTrac WebTrac search HTML.

Public catalog is ``/webtrac/web/search.html?module=AR&category=Events``.
Leagues, swim lessons and other program categories stay out. Session and
CSRF tokens in a pasted URL are not part of the fetch.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta
from typing import Any
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup, Tag

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError

SITE = "https://rectrac.cstx.gov"
SEARCH_URL = f"{SITE}/webtrac/web/search.html"
ITEMINFO_URL = f"{SITE}/webtrac/web/iteminfo.html"
TIMEZONE = "America/Chicago"

ORGANIZATION = {
    "slug": "cstx-parks",
    "name": "College Station Parks and Recreation",
    "website_url": "https://www.cstx.gov/parks-recreation-culture/",
}

#: RecTrac location cell -> place. Keys are whitespace-normalized lowercase
#: of the source string, including RecTrac's own typos. Unmapped is a loud
#: reject, not a guess.
VENUES: dict[str, dict[str, Any]] = {
    "lick creek park": {
        "slug": "lick-creek-park",
        "name": "Lick Creek Park",
        "street": "13600 Rock Prairie Rd",
        "city": "College Station",
        "region": "TX",
        "postcode": "77845",
        "area": "college_station",
        "indoor": False,
    },
    "central park": {
        "slug": "stephen-c-beachy-central-park",
        "name": "Stephen C. Beachy Central Park",
        "street": "1000 Krenek Tap Rd",
        "city": "College Station",
        "region": "TX",
        "postcode": "77840",
        "area": "college_station",
        "indoor": False,
    },
    "larrry j ringer lib.": {
        "slug": "larry-j-ringer-library",
        "name": "Larry J. Ringer Library",
        "street": "1818 Harvey Mitchell Pkwy S",
        "city": "College Station",
        "region": "TX",
        "postcode": "77845",
        "area": "college_station",
        "indoor": True,
    },
    "wolf pen creek park": {
        "slug": "wolf-pen-creek-park",
        "name": "Wolf Pen Creek Park",
        "street": "1015 Colgate Dr",
        "city": "College Station",
        "region": "TX",
        "postcode": "77840",
        "area": "college_station",
        "indoor": False,
    },
    "adamson lagoon": {
        "slug": "adamson-lagoon",
        "name": "Adamson Lagoon",
        "street": "1900 Anderson St",
        "city": "College Station",
        "region": "TX",
        "postcode": "77840",
        "area": "college_station",
        "indoor": False,
    },
    "bee creek park": {
        "slug": "bee-creek-park",
        "name": "Bee Creek Park",
        "street": "1900 Anderson St",
        "city": "College Station",
        "region": "TX",
        "postcode": "77840",
        "area": "college_station",
        "indoor": False,
    },
    "lemontree park": {
        "slug": "lemontree-park",
        "name": "Lemontree Park",
        "street": "1300 Lemon Tree Ln",
        "city": "College Station",
        "region": "TX",
        "postcode": "77840",
        "area": "college_station",
        "indoor": False,
    },
    "lincoln recreation center": {
        "slug": "lincoln-recreation-center",
        "name": "Lincoln Recreation Center",
        "street": "1809 Deacon Dr",
        "city": "College Station",
        "region": "TX",
        "postcode": "77840",
        "area": "college_station",
        "indoor": True,
    },
    "brian bachmann community park": {
        "slug": "brian-bachmann-community-park",
        "name": "Brian Bachmann Community Park",
        "street": "1600 Rock Prairie Rd",
        "city": "College Station",
        "region": "TX",
        "postcode": "77845",
        "area": "college_station",
        "indoor": False,
    },
    "southwood community center": {
        "slug": "southwood-community-center",
        "name": "Southwood Community Center",
        "street": "201 Southwest Pkwy",
        "city": "College Station",
        "region": "TX",
        "postcode": "77840",
        "area": "college_station",
        "indoor": True,
    },
    "cindy hallaran pool": {
        "slug": "cindy-hallaran-pool",
        "name": "Cindy Hallaran Pool",
        "street": "1600 Rock Prairie Rd",
        "city": "College Station",
        "region": "TX",
        "postcode": "77845",
        "area": "college_station",
        "indoor": False,
    },
    "veterans park": {
        "slug": "veterans-park",
        "name": "Veterans Park",
        "street": "3101 Harvey Rd",
        "city": "College Station",
        "region": "TX",
        "postcode": "77845",
        "area": "college_station",
        "indoor": False,
    },
    "w.a. tarrow park": {
        "slug": "wa-tarrow-park",
        "name": "W.A. Tarrow Park",
        "street": "600 Tarrow St",
        "city": "College Station",
        "region": "TX",
        "postcode": "77840",
        "area": "college_station",
        "indoor": False,
    },
    "meyer senior & community center": {
        "slug": "bob-and-wanda-meyer-senior-and-community-center",
        "name": "Bob and Wanda Meyer Senior and Community Center",
        "street": "2275 Dartmouth St",
        "city": "College Station",
        "region": "TX",
        "postcode": "77840",
        "area": "college_station",
        "indoor": True,
    },
    "jingle bell fun run start line": {
        "slug": "jingle-bell-fun-run-start-line",
        "name": "Jingle Bell Fun Run Start Line",
        "street": "810 Holleman Drive East",
        "city": "College Station",
        "region": "TX",
        "postcode": "77840",
        "area": "college_station",
        "indoor": False,
    },
}

_RANGE = re.compile(
    r"Showing results\s+(\d+)\s*-\s*(\d+)\s+of\s+(\d+)",
    re.I,
)
_FMID = re.compile(r"[?&]FMID=(\d+)", re.I)
_MDY = re.compile(r"(\d{1,2})/(\d{1,2})/(\d{4})")
_CLOCK = re.compile(
    r"(?P<h>\d{1,2}):(?P<m>\d{2})\s*(?P<ap>a\.?m\.?|p\.?m\.?)",
    re.I,
)
_AGES = re.compile(r"(\d+)\s*-\s*(\d+)")
_FEE = re.compile(r"\$(\d+(?:\.\d+)?)")


class CstxParksScraper(BaseEventScraper):
    kind = "cstxparks"
    source_slug = "cstxparks"
    source_name = ORGANIZATION["name"]
    verify_url = f"{SEARCH_URL}?module=AR&category=Events"

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
        seen: set[str] = set()
        begrow: int | None = None
        while True:
            params: dict[str, Any] = {
                "module": "AR",
                "category": "Events",
                "display": "Detail",
            }
            if begrow is not None:
                params["begrow"] = begrow
            html = client.get_text(SEARCH_URL, params=params, encoding="utf-8")
            rows = parse_search(html)
            rng = result_range(html)
            if not rows and begrow is None:
                if "Activity Search" not in html and "arwebsearch" not in html:
                    raise ScraperError("cstxparks: not a RecTrac activity search page")
                break
            for row in rows:
                item = to_raw(row)
                key = str(row.get("fmid") or item.occurrence_tid)
                if not key or key in seen:
                    continue
                if not in_window(item, window_start, window_end):
                    continue
                seen.add(key)
                raw.append(item)
            if rng is None or rng[1] >= rng[2]:
                break
            nxt = rng[1] + 1
            if nxt == begrow:
                break
            begrow = nxt
        return raw

    def normalize(self, raw: list[RawEvent]) -> tuple[list[dict], list[dict]]:
        payloads: list[dict] = []
        rejected: list[dict] = []
        for item in raw:
            try:
                series = self._series(item)
                occurrence = _occurrence(item)
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
        title = (rec.get("series_title") or rec.get("title") or "").strip()
        if not title:
            raise ScraperError("missing title")
        place = lookup_venue(rec.get("location") or "")
        indoor = place.pop("indoor", None)
        description = normalize.clean_text(rec.get("description"))
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "place": place,
            "topics": topics_from(title, description or ""),
            "audiences": ["all-ages"],
            "registration_required": True,
            "source_url": iteminfo_url(rec.get("fmid")),
        }
        if description:
            series["description"] = description
        if indoor is not None:
            series["indoor"] = indoor
        url = series["source_url"]
        if url:
            series["registration_url"] = url
        ages = rec.get("ages") or ""
        match = _AGES.search(ages)
        if match:
            series["age_min"] = int(match.group(1))
            series["age_max"] = int(match.group(2))
        cents = resident_cents(rec.get("fee") or "")
        if cents is not None:
            series["cost_low_cents"] = cents
            if cents == 0:
                series["is_free"] = True
            note = normalize.clean_text(rec.get("fee"))
            if note:
                series["cost_note"] = note
        return series


def parse_search(html: str) -> list[dict[str, Any]]:
    """RecTrac Detail search page -> one dict per section row. Pure."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[dict[str, Any]] = []
    for block in soup.select("div.result-content"):
        header = block.select_one(".result-header h2")
        series_title = ""
        code = ""
        if header:
            span = header.find("span")
            em = header.find("em")
            series_title = _text(span) if span else _text(header)
            code = _text(em)
        desc_el = block.select_one(".result-header__description")
        description = desc_el.get_text("\n", strip=True) if desc_el else ""
        for tr in block.select("table tbody tr"):
            if not isinstance(tr, Tag):
                continue
            row = _parse_row(tr, series_title=series_title, code=code, description=description)
            if row:
                out.append(row)
    return out


def result_range(html: str) -> tuple[int, int, int] | None:
    match = _RANGE.search(html or "")
    if not match:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def to_raw(row: dict[str, Any]) -> RawEvent:
    start: datetime | None = row.get("start")
    start_ms = _millis(start) if isinstance(start, datetime) else None
    code = str(row.get("code") or "")
    return RawEvent(
        series_uid=normalize.slugify(code) or normalize.slugify(row.get("series_title") or ""),
        occurrence_tid=str(start_ms) if start_ms is not None else str(row.get("fmid") or ""),
        record=row,
    )


def in_window(item: RawEvent, window_start: date, window_end: date) -> bool:
    start = item.record.get("start")
    if not isinstance(start, datetime):
        return False
    return window_start <= start.date() < window_end


def lookup_venue(location: str) -> dict[str, Any]:
    key = " ".join((location or "").lower().split())
    venue = VENUES.get(key)
    if venue is None:
        raise ScraperError(
            f"unknown venue {location!r}; add it to VENUES with an explicit area"
        )
    return {k: v for k, v in venue.items()}


def topics_from(title: str, description: str) -> list[str]:
    blob = f"{title} {description}".lower()
    topics: list[str] = ["community"]
    if "history" in blob:
        topics.append("history")
    if "movie" in blob:
        topics.append("arts")
    if "fun run" in blob or re.search(r"\brun\b", blob):
        topics.append("sports")
    if "engineer" in blob:
        topics.append("science")
    if "in the dark" in blob or "camp" in blob or "hike" in blob or "nature" in blob:
        topics.append("outdoors")
        topics.append("nature")
    if "in the dark" in blob or "camp" in blob:
        topics.append("camp")
    seen: set[str] = set()
    out: list[str] = []
    for topic in topics:
        if topic not in seen:
            seen.add(topic)
            out.append(topic)
    return out


def resident_cents(fee: str) -> int | None:
    match = _FEE.search(fee or "")
    if not match:
        return None
    return round(float(match.group(1)) * 100)


def iteminfo_url(fmid: Any) -> str:
    fid = str(fmid or "").strip()
    if not fid:
        return f"{SEARCH_URL}?module=AR&category=Events"
    return f"{ITEMINFO_URL}?Module=AR&FMID={fid}"


def _occurrence(item: RawEvent) -> dict[str, Any]:
    start = item.record.get("start")
    if not isinstance(start, datetime):
        raise ScraperError("missing start")
    start_ms = _millis(start)
    occ: dict[str, Any] = {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": normalize.local_iso(start_ms, TIMEZONE),
        "timezone": TIMEZONE,
        "all_day": False,
        "status": "scheduled",
    }
    end = item.record.get("end")
    if isinstance(end, datetime) and end != start:
        occ["end_local"] = normalize.local_iso(_millis(end), TIMEZONE)
    section_title = (item.record.get("title") or "").strip()
    series_title = (item.record.get("series_title") or "").strip()
    if section_title and series_title and section_title != series_title:
        occ["overrides"] = {"title": section_title}
    return occ


def _parse_row(
    tr: Tag, *, series_title: str, code: str, description: str
) -> dict[str, Any] | None:
    activity = _cell(tr, "Activity")
    link = activity.find("a") if activity else None
    href = str(link.get("href") or "") if link else ""
    fmid = _fmid(href)
    title = _text(link) if link else _text(activity)
    if not fmid and not title:
        return None
    location = _text(_cell(tr, "Location"))
    fee = _text(_cell(tr, "Fee (Res/Non-Res)"))
    ages = _text(_cell(tr, "Ages"))
    cart = _cell(tr, "Add To Cart")
    cart_href = ""
    if cart:
        cart_link = cart.find("a")
        if cart_link:
            cart_href = str(cart_link.get("href") or "")
    start, end = _row_span(
        cart_href,
        dates=_text(_cell(tr, "Dates")),
        times=_text(_cell(tr, "Times")),
    )
    return {
        "series_title": series_title,
        "code": code,
        "description": description,
        "title": title,
        "fmid": fmid,
        "location": location,
        "fee": fee,
        "ages": ages,
        "url": href,
        "start": start,
        "end": end,
    }


def _row_span(
    cart_href: str, *, dates: str, times: str
) -> tuple[datetime | None, datetime | None]:
    begin_date, begin_seconds, end_date, end_seconds = _cart_clock(cart_href)
    date_pair = _MDY.findall(dates or "")
    start_day = _mdy(begin_date) or (_to_date(date_pair[0]) if date_pair else None)
    end_day = _mdy(end_date) or (
        _to_date(date_pair[1]) if len(date_pair) > 1 else start_day
    )
    start_t = _seconds_to_time(begin_seconds) or _clock_at(times, 0)
    end_t = _seconds_to_time(end_seconds) or _clock_at(times, 1)
    start = _combine(start_day, start_t)
    end = _combine(end_day, end_t)
    if start and end and end < start and end_day == start_day:
        end = end + timedelta(days=1)
    return start, end


def _cart_clock(href: str) -> tuple[str | None, str | None, str | None, str | None]:
    if not href or href == "#":
        return None, None, None, None
    qs = parse_qs(urlparse(href).query)
    return (
        (qs.get("GlobalSalesArea_ARItemBeginDate") or [None])[0],
        (qs.get("GlobalSalesArea_ARItemBeginTime") or [None])[0],
        (qs.get("GlobalSalesArea_ARItemEndDate") or [None])[0],
        (qs.get("GlobalSalesArea_ARItemEndTime") or [None])[0],
    )


def _cell(tr: Tag, title: str) -> Tag | None:
    found = tr.find("td", attrs={"data-title": title})
    return found if isinstance(found, Tag) else None


def _text(node: Tag | None) -> str:
    if node is None:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def _fmid(href: str) -> str:
    match = _FMID.search(href or "")
    if match:
        return match.group(1)
    qs = parse_qs(urlparse(href or "").query)
    return (qs.get("FMID") or qs.get("ARFMIDList") or [""])[0]


def _mdy(value: str | None) -> date | None:
    if not value:
        return None
    match = _MDY.search(value)
    if not match:
        return None
    return _to_date(match.groups())


def _to_date(parts: tuple[str, ...] | list[str]) -> date:
    month, day, year = (int(parts[0]), int(parts[1]), int(parts[2]))
    return date(year, month, day)


def _seconds_to_time(value: str | None) -> time | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        total = int(value)
    except (TypeError, ValueError):
        return None
    total = total % (24 * 3600)
    hours, rem = divmod(total, 3600)
    minutes, _ = divmod(rem, 60)
    return time(hours, minutes)


def _clock_at(text: str, index: int) -> time | None:
    found = list(_CLOCK.finditer(text or ""))
    if index >= len(found):
        return None
    return _clock_match(found[index])


def _clock_match(match: re.Match[str]) -> time:
    hours = int(match.group("h"))
    minutes = int(match.group("m"))
    ap = match.group("ap").lower().replace(".", "")
    if ap == "am":
        if hours == 12:
            hours = 0
    elif hours != 12:
        hours += 12
    return time(hours, minutes)


def _combine(day: date | None, clock: time | None) -> datetime | None:
    if day is None or clock is None:
        return None
    return datetime(day.year, day.month, day.day, clock.hour, clock.minute)


def _millis(when: datetime) -> int:
    return int(when.replace(tzinfo=ZoneInfo(TIMEZONE)).timestamp() * 1000)
