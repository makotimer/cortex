"""Stage 12 events at the College Station Brookshire Brothers.

The public page is a Drupal Calendar View month table. There is no JSON,
ICS or JSON:API. Each cell already carries title, ``/node/{nid}`` and
smart-date start/end. Artist, free-admission wording, ages and
registration live on the node.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError

LIST_URL = "https://www.brookshirebrothers.com/college-station/stage12events"
SITE = "https://www.brookshirebrothers.com"
TIMEZONE = "America/Chicago"
_PAGER = "date_format=custom&date_pattern=F&use_previous_next=1&display_reset=0&pager_type=calendar_month"

ORGANIZATION = {
    "slug": "stage12",
    "name": "Stage 12",
    "website_url": LIST_URL,
}

PLACE = {
    "slug": "stage-12",
    "name": "Stage 12",
    "street": "455 George Bush Dr. W Suite 100",
    "city": "College Station",
    "region": "TX",
    "postcode": "77840",
    "area": "college_station",
}

_NID = re.compile(r"/node/(\d+)")
_NEXT_TS = re.compile(r"calendar_timestamp=(\d+)")
_CAPTION = re.compile(
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{4})",
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
_CLOCK_SUFFIX = re.compile(
    r"\s*@\s*\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\s*$",
    re.I,
)
_EDGE_JUNK = re.compile(r"^[\W_]+|[\W_]+$", re.UNICODE)
_ARTIST = re.compile(r"Artist:\s*(.+)", re.I)
_AGES = re.compile(r"\bages?\s+(\d{1,2})\s*[-\u2013]\s*(\d{1,2})\b", re.I)
_FREE = re.compile(
    r"\bfree\s+live\s+music\b|"
    r"\blive\s+free\s+music\b|"
    r"\bfree\s+movie\s+night\b|"
    r"\badmission\s+is\s+free\b|"
    r"\bfree\s+night\s+of\b",
    re.I,
)
_REGISTER = re.compile(r"\bregistration\b|\bregister\b", re.I)


class Stage12Scraper(BaseEventScraper):
    kind = "stage12"
    source_slug = "stage12"
    source_name = ORGANIZATION["name"]
    verify_url = LIST_URL

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

        url: str | None = LIST_URL
        seen: set[str] = set()
        raw: list[RawEvent] = []
        while url:
            month = parse_month(client.get_text(url))
            for card in month["events"]:
                nid = card["nid"]
                if nid in seen:
                    continue
                seen.add(nid)
                item = to_raw(card)
                if not in_window(item, window_start, window_end):
                    continue
                href = card.get("href") or f"/node/{nid}"
                item.supplement["detail"] = parse_detail(client.get_text(urljoin(SITE, href)))
                raw.append(item)
            url = _next_url(month, window_start, window_end)
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

    def _series(self, item: RawEvent) -> dict:
        rec = item.record
        detail = item.supplement.get("detail") or {}
        title = display_title(rec.get("title") or "", detail.get("artist"))
        if not title:
            raise ScraperError("missing title")
        description = detail.get("description")
        blob = " ".join(part for part in (title, description) if part)
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "place": dict(PLACE),
            "topics": topics_from_title(title),
            "audiences": audiences_from(title),
            "source_url": urljoin(SITE, rec.get("href") or f"/node/{item.series_uid}"),
        }
        if description:
            series["description"] = description
        if is_free(blob):
            series["is_free"] = True
        ages = ages_from(description or "")
        if ages:
            series["age_min"], series["age_max"] = ages
        if registration_required(blob):
            series["registration_required"] = True
        return series


def parse_month(html_text: str) -> dict[str, Any]:
    """Calendar-view month table -> caption, pager, event cards. Pure."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    table = soup.select_one("table.calendar-view-table")
    caption_el = soup.select_one("caption")
    caption = caption_el.get_text(strip=True) if caption_el else ""
    year, month = _table_ym(table)
    if year is None or month is None:
        year, month = _caption_ym(caption)

    next_ts: int | None = None
    for anchor in soup.select("a"):
        href = anchor.get("href") or ""
        if anchor.get_text(strip=True) != "Next" or "calendar_timestamp=" not in href:
            continue
        match = _NEXT_TS.search(href)
        if match:
            next_ts = int(match.group(1))
            break

    events: list[dict[str, Any]] = []
    for row in soup.select("li.calendar-view-day__row"):
        link = row.select_one("a[href]")
        if not link:
            continue
        href = link.get("href") or ""
        nid = nid_from_href(href)
        if not nid:
            continue
        stamps = [
            t.get("datetime")
            for t in row.select("time.datetime")
            if t.get("datetime") and not str(t.get("datetime")).startswith("P")
        ]
        events.append({
            "nid": nid,
            "title": link.get_text(strip=True),
            "href": href,
            "start": stamps[0] if stamps else None,
            "end": stamps[1] if len(stamps) > 1 else None,
        })
    return {
        "caption": caption,
        "year": year,
        "month": month,
        "next_ts": next_ts,
        "events": events,
    }


def parse_detail(html_text: str) -> dict[str, Any]:
    """Event node description + artist. Pure."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    desc_el = soup.select_one(".field--name-field-event-description")
    text = desc_el.get_text("\n", strip=True) if desc_el else ""
    artist = None
    match = _ARTIST.search(text)
    if match:
        artist = match.group(1).strip() or None
    return {
        "description": normalize.clean_text(text),
        "artist": artist,
    }


def to_raw(card: dict) -> RawEvent:
    start_ms = iso_to_ms(card.get("start"))
    return RawEvent(
        series_uid=str(card.get("nid") or ""),
        occurrence_tid=str(start_ms) if start_ms is not None else "",
        record=dict(card),
    )


def clean_title(title: str) -> str:
    text = _CLOCK_SUFFIX.sub("", (title or "").strip())
    text = _EDGE_JUNK.sub("", text).strip()
    return normalize.clean_text(text) or ""


def display_title(title: str, artist: str | None) -> str:
    base = clean_title(title)
    name = (artist or "").strip()
    if name and name.lower() not in base.lower():
        return f"{base}: {name}"
    return base


def topics_from_title(title: str) -> list[str]:
    text = (title or "").lower()
    out: set[str] = set()
    if "movie night" in text:
        out.add("arts")
    if "craft" in text:
        out.add("crafts")
    if any(key in text for key in ("singo", "karaoke", "live music")):
        out.add("music")
    if "kids camp" in text or "junior sprouts" in text:
        out.add("camp")
    if "science" in text:
        out.add("science")
    if "ice cream" in text or "cookie" in text:
        out.add("community")
    return sorted(out)


def audiences_from(title: str) -> list[str]:
    text = (title or "").lower()
    if "kids camp" in text or "junior sprouts" in text:
        return ["elementary"]
    return ["all-ages"]


def is_free(text: str) -> bool:
    return bool(_FREE.search(text or ""))


def ages_from(text: str) -> tuple[int, int] | None:
    match = _AGES.search(text or "")
    if not match:
        return None
    lo, hi = int(match.group(1)), int(match.group(2))
    if lo > hi:
        lo, hi = hi, lo
    return lo, hi


def registration_required(text: str) -> bool:
    return bool(_REGISTER.search(text or ""))


def in_window(item: RawEvent, window_start: date, window_end: date) -> bool:
    start = _date_from_iso(item.record.get("start"))
    if start is None:
        return False
    return window_start <= start < window_end


def nid_from_href(href: str) -> str | None:
    match = _NID.search(href or "")
    return match.group(1) if match else None


def iso_to_ms(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(datetime.fromisoformat(value).timestamp() * 1000)
    except ValueError:
        return None


def _occurrence(item: RawEvent) -> dict:
    start_ms = iso_to_ms(item.record.get("start"))
    if start_ms is None:
        raise ScraperError("missing start")
    occ: dict[str, Any] = {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": normalize.local_iso(start_ms, TIMEZONE),
        "timezone": TIMEZONE,
        "all_day": False,
        "status": "scheduled",
    }
    end_ms = iso_to_ms(item.record.get("end"))
    if end_ms is not None:
        occ["end_local"] = normalize.local_iso(end_ms, TIMEZONE)
    return occ


def _next_url(month: dict[str, Any], window_start: date, window_end: date) -> str | None:
    year, mon = month.get("year"), month.get("month")
    month_start = _safe_month_start(year, mon)
    if month_start is not None and month_start >= window_end:
        return None
    if not month.get("events") and month_start is not None and month_start >= window_start:
        return None
    next_ts = month.get("next_ts")
    if not next_ts:
        return None
    next_month = datetime.fromtimestamp(int(next_ts), tz=ZoneInfo(TIMEZONE)).date()
    if next_month >= window_end:
        return None
    return f"{LIST_URL}?calendar_timestamp={int(next_ts)}&{_PAGER}"


def _table_ym(table: Any) -> tuple[int | None, int | None]:
    if table is None:
        return None, None
    try:
        year = int(table.get("data-calendar-view-year"))
        month = int(table.get("data-calendar-view-month"))
    except (TypeError, ValueError):
        return None, None
    if not (1 <= month <= 12) or year < 2000:
        return None, None
    return year, month


def _caption_ym(caption: str) -> tuple[int | None, int | None]:
    match = _CAPTION.search(caption or "")
    if not match:
        return None, None
    month = _MONTHS.get(match.group(1).lower())
    try:
        year = int(match.group(2))
    except ValueError:
        return None, None
    return year, month


def _safe_month_start(year: Any, month: Any) -> date | None:
    try:
        return date(int(year), int(month), 1)
    except (TypeError, ValueError):
        return None


def _date_from_iso(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None
