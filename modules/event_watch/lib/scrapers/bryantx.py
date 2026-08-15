"""City of Bryan public calendar, via the GOVstack / CivicPlus list fragment.

The listing is HTML. Pagination is a documented AJAX partial
(``/default/_List?Page=N``, 0-based). There is no public JSON or ICS that
covers the same window.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError

LIST_URL = "https://events.bryantx.gov/default/_List"
SITE = "https://events.bryantx.gov"
TIMEZONE = "America/Chicago"

ORGANIZATION = {
    "slug": "bryantx",
    "name": "City of Bryan",
    "website_url": "https://www.bryantx.gov/",
}

CITY_AREA = {
    "bryan": ("Bryan", "bryan"),
    "college station": ("College Station", "college_station"),
}

CATEGORY_TOPICS = {
    "parks and recreation": "outdoors",
    "downtown": "community",
    "midtown": "community",
    "general": "community",
    "holiday closures": "community",
    "city council": "community",
    "planning and zoning": "community",
    "special meeting": "community",
    "board of adjustments and appeals": "community",
    "building and standards commission": "community",
    "design review board": "community",
    "historic landmark commission": "community",
    "zoning board of adjustment": "community",
}

_DETAIL = re.compile(
    r"/default/Detail/(\d{4}-\d{2}-\d{2})-(\d{4})-(.+)$",
)
_TX_ZIP = re.compile(r"^TX(?:\s+(\d{5})(?:-\d{4})?)?$", re.I)
_FREE = re.compile(
    r"admission is free|this senior social is free|free to participate|"
    r"it'?s free to participate|\bis free\b",
    re.I,
)
_FAMILY = re.compile(r"family-friendly|family friendly", re.I)
_ADULT = re.compile(r"55\s*\+|55 and older|ages 55", re.I)


class BryanTxScraper(BaseEventScraper):
    kind = "bryantx"
    source_slug = "bryantx"
    source_name = "City of Bryan Calendar"
    verify_url = "https://events.bryantx.gov/default/List"

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
        page = 0
        while True:
            html = client.get_text(
                LIST_URL,
                params={
                    "StartDate": window_start.strftime("%m/%d/%Y"),
                    "EndDate": window_end.strftime("%m/%d/%Y"),
                    "Page": page,
                },
            )
            cards = parse_list_html(html)
            if not cards:
                break
            raw.extend(to_raw(card) for card in cards)
            page += 1
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
        title = (rec.get("title") or "").strip()
        if not title:
            raise ScraperError("missing title")
        description = normalize.clean_text(rec.get("description"))
        category = (rec.get("category") or "").strip()
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "topics": topics_from_category(category),
            "audiences": audiences_from_text(description or ""),
            "place": parse_place(rec.get("where") or ""),
        }
        if description:
            series["description"] = description
        if rec.get("href"):
            series["source_url"] = urljoin(SITE, rec["href"])
        if is_free(description or ""):
            series["is_free"] = True
        return series


def parse_list_html(html: str) -> list[dict]:
    """One ``_List`` page -> card dicts. Pure."""
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[dict] = []
    for item in soup.select(".icrt-calendarListItem"):
        card = _parse_card(item)
        if card:
            out.append(card)
    return out


def to_raw(card: dict) -> RawEvent:
    return RawEvent(
        series_uid=str(card.get("series_uid") or ""),
        occurrence_tid=str(card.get("occurrence_tid") or ""),
        record=card,
    )


def topics_from_category(category: str) -> list[str]:
    key = (category or "").strip().lower()
    topic = CATEGORY_TOPICS.get(key)
    return [topic] if topic else []


def audiences_from_text(text: str) -> list[str]:
    out: set[str] = set()
    if _FAMILY.search(text or ""):
        out.add("all-ages")
    if _ADULT.search(text or ""):
        out.add("adult")
    return sorted(out)


def is_free(text: str) -> bool:
    return bool(_FREE.search(text or ""))


def city_area(city: str | None) -> tuple[str, str] | None:
    return CITY_AREA.get((city or "").strip().lower())


def parse_place(where: str) -> dict:
    parts = [p.strip() for p in (where or "").split(",") if p.strip()]
    city, area, postcode = "Bryan", "bryan", None
    if parts:
        zip_match = _TX_ZIP.match(parts[-1])
        if zip_match:
            if zip_match.group(1):
                postcode = zip_match.group(1)
            parts = parts[:-1]
    if parts:
        mapped = city_area(parts[-1])
        if mapped:
            city, area = mapped
            parts = parts[:-1]
    name = parts[0] if parts else city
    place: dict[str, Any] = {
        "slug": normalize.slugify(f"{name}-{city}"),
        "name": name,
        "city": city,
        "region": "TX",
        "area": area,
    }
    if len(parts) > 1:
        place["street"] = ", ".join(parts[1:])
    if postcode:
        place["postcode"] = postcode
    return place


def parse_detail_path(href: str) -> tuple[str, str, str] | None:
    match = _DETAIL.search(href or "")
    if not match:
        return None
    return match.group(1), match.group(2), match.group(3)


def _parse_card(item: Any) -> dict | None:
    title_a = item.select_one("a.meta-title")
    if title_a is None:
        return None
    href = title_a.get("href") or ""
    parsed = parse_detail_path(href)
    if parsed is None:
        return None
    day, hhmm, slug = parsed
    meta = item.select_one(".icrt-calendarListItemMeta") or item
    time_p = meta.find("p")
    where = ""
    if time_p is not None:
        time_text = time_p.get_text(" ", strip=True)
        if "|" in time_text:
            where = time_text.split("|", 1)[1].strip()
    desc = item.select_one(".icrt-calendarListItemDesc")
    return {
        "href": href,
        "title": title_a.get_text(strip=True),
        "category": _category_after(title_a),
        "where": where,
        "description": desc.get_text("\n", strip=True) if desc else "",
        "series_uid": slug,
        "occurrence_tid": f"{day}-{hhmm}",
        "day": day,
        "hhmm": hhmm,
    }


def _category_after(title_a: Any) -> str:
    bits: list[str] = []
    for sib in title_a.next_siblings:
        if getattr(sib, "name", None) == "p":
            break
        text = sib.get_text(" ", strip=True) if hasattr(sib, "get_text") else str(sib).strip()
        if text:
            bits.append(text)
    return " ".join(bits).strip()


def _occurrence(item: RawEvent) -> dict:
    rec = item.record
    day = rec.get("day") or ""
    hhmm = rec.get("hhmm") or ""
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(day)) or not re.fullmatch(r"\d{4}", str(hhmm)):
        raise ScraperError("missing occurrence timestamp")
    hour, minute = hhmm[:2], hhmm[2:]
    return {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": f"{day}T{hour}:{minute}:00",
        "timezone": TIMEZONE,
        "all_day": hhmm == "0000",
        "status": "scheduled",
    }
