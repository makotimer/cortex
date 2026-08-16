"""Home Depot Kids Workshops at the College Station store.

US HTML is Akamai-blocked. The official US rule is stable: first Saturday
of each month, 9 a.m. to noon, ages 5–12, free. Kit names are national and
are read from the Canada workshops page (same kits, different Saturday).
Dates are generated locally; Canada is never used as a calendar.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, datetime, time
from typing import Any

import requests
from bs4 import BeautifulSoup

from .base import BaseEventScraper, RawEvent, ScraperError

CANADA_URL = "https://www.homedepot.ca/en/home/ideas-how-to/workshops.html"
REGISTER_URL = "https://www.homedepot.com/c/kids-workshop"
#: US HTML is Akamai 403. Canada times out unproxied. The VPN probe
#: uses requests' default UA, so we verify the tunnel via tockify.
VERIFY_URL = "https://tockify.com/"
TIMEZONE = "America/Chicago"
START_CLOCK = time(9, 0)
END_CLOCK = time(12, 0)
STORE_ID = "6559"

ORGANIZATION = {
    "slug": "home-depot",
    "name": "The Home Depot",
    "website_url": "https://www.homedepot.com/",
}

PLACE = {
    "slug": "home-depot-college-station",
    "name": "College Station Home Depot",
    "street": "1615 University Dr E",
    "city": "College Station",
    "region": "TX",
    "postcode": "77840",
    "area": "college_station",
}

DESCRIPTION = (
    "Free in-store Kids Workshop the first Saturday of each month, "
    "9 a.m. to noon, while supplies last. Ages 5–12. Register on "
    "The Home Depot website and choose the College Station store."
)

#: Last confirmed national kit names from the Canada page (2026-08-16).
#: Live parse wins; this is what we publish if Canada times out.
FALLBACK_KITS: dict[tuple[int, int], str] = {
    (2026, 9): "School Bus Organizer",
    (2026, 10): "Witch Candy Box",
    (2026, 11): "Dump Truck",
    (2026, 12): "Holiday Train",
}

_MONTHS = {name: i for i, name in enumerate(calendar.month_name) if name}
_BUILD = re.compile(
    r"Build a (.+?)\s+Saturday\s+"
    r"(January|February|March|April|May|June|July|August|September|"
    r"October|November|December)\s+(\d{1,2}),\s+(\d{4})",
    re.I,
)

BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


class HomeDepotScraper(BaseEventScraper):
    kind = "homedepot"
    source_slug = "homedepot"
    source_name = ORGANIZATION["name"]
    verify_url = VERIFY_URL

    def __init__(self, proxy_url: str | None = None) -> None:
        self._proxy_url = proxy_url

    def fetch(
        self, window_start: date, window_end: date, *, skip_network: bool
    ) -> list[RawEvent]:
        if skip_network:
            return []
        kits = dict(FALLBACK_KITS)
        try:
            # One shot — HttpClient's retry adapter would wait ~90s on
            # a Canada timeout, and the names are not load-bearing.
            proxies = None
            if self._proxy_url:
                proxies = {"http": self._proxy_url, "https": self._proxy_url}
            resp = requests.get(
                CANADA_URL,
                timeout=12,
                headers={"User-Agent": BROWSER_UA},
                proxies=proxies,
            )
            resp.raise_for_status()
            kits.update(parse_kit_names(resp.text))
        except Exception:
            pass
        return occurrences_for_window(window_start, window_end, kits)

    def normalize(self, raw: list[RawEvent]) -> tuple[list[dict], list[dict]]:
        payloads: list[dict] = []
        rejected: list[dict] = []
        for item in raw:
            try:
                series = _series(item)
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


def parse_kit_names(html: str) -> dict[tuple[int, int], str]:
    """Canada workshops HTML -> {(year, month): kit name}. First date wins.

    Pure. Canada Saturdays are discarded; only the month of the kit is kept.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    text = soup.get_text(" ", strip=True).replace("\xa0", " ")
    names: dict[tuple[int, int], str] = {}
    for match in _BUILD.finditer(text):
        kit = match.group(1).strip()
        month = _MONTHS[match.group(2).title()]
        year = int(match.group(4))
        key = (year, month)
        if key not in names:
            names[key] = kit
    return names


def first_saturday(year: int, month: int) -> date:
    first = date(year, month, 1)
    offset = (calendar.SATURDAY - first.weekday()) % 7
    return date(year, month, 1 + offset)


def first_saturdays(window_start: date, window_end: date) -> list[date]:
    out: list[date] = []
    year, month = window_start.year, window_start.month
    while True:
        day = first_saturday(year, month)
        if day >= window_end:
            break
        if day >= window_start:
            out.append(day)
        month += 1
        if month == 13:
            month = 1
            year += 1
    return out


def occurrences_for_window(
    window_start: date,
    window_end: date,
    kit_by_month: dict[tuple[int, int], str],
) -> list[RawEvent]:
    """First Saturdays in the window, with a kit name when Canada has one."""
    raw: list[RawEvent] = []
    for day in first_saturdays(window_start, window_end):
        kit = kit_by_month.get((day.year, day.month))
        raw.append(RawEvent(
            series_uid=f"kids-workshop-{day:%Y-%m}",
            occurrence_tid=f"{day.isoformat()}:{STORE_ID}",
            record={"day": day, "kit": kit},
        ))
    return raw


def _series(item: RawEvent) -> dict[str, Any]:
    kit = (item.record.get("kit") or "").strip() or None
    title = f"Kids Workshop: {kit}" if kit else "Kids Workshop"
    return {
        "source_series_uid": item.series_uid,
        "title": title,
        "description": DESCRIPTION,
        "source_url": REGISTER_URL,
        "registration_url": REGISTER_URL,
        "organization": dict(ORGANIZATION),
        "place": dict(PLACE),
        "topics": ["crafts"],
        "audiences": ["elementary", "tween"],
        "age_min": 5,
        "age_max": 12,
        "is_free": True,
        "indoor": True,
    }


def _occurrence(item: RawEvent) -> dict[str, Any]:
    day = item.record.get("day")
    if not isinstance(day, date):
        raise ScraperError("missing start")
    return {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": datetime.combine(day, START_CLOCK).isoformat(),
        "end_local": datetime.combine(day, END_CLOCK).isoformat(),
        "timezone": TIMEZONE,
        "all_day": False,
        "status": "scheduled",
    }
