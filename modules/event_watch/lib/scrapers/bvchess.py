"""Brazos Valley Chess Association, via its public Google Calendar ICS.

``brazosvalleychess.com/events`` is a Squarespace shell around an embedded
Google Calendar ("BVCA Monthly Event Calendar"). The calendar's public ICS is
the source: one VEVENT per tournament, no RRULEs, times as UTC ``Z`` stamps
with ``X-WR-TIMEZONE:America/Chicago``.

One VEVENT is one series with one occurrence — the monthly Swiss tournaments
carry distinct titles and UIDs, so there is no series to fold them into. A
VEVENT that grows an RRULE is rejected loudly rather than expanded: inventing
dates the source only implied is how a cancelled week gets published.

**VENUES is an editorial gate.** Every tournament so far is at First Baptist
Church Bryan; an unlisted LOCATION rejects (and emails) rather than guessing
an area.
"""

from __future__ import annotations

import html
import re
from datetime import UTC, date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError, UnknownVenueError

CALENDAR_ID = (
    "a13fc07a824a568eb2c9616a08b404f39dd37021c00953ba4d61d1e170b577fd"
    "@group.calendar.google.com"
)
ICS_URL = f"https://calendar.google.com/calendar/ical/{CALENDAR_ID}/public/basic.ics"
SITE = "https://www.brazosvalleychess.com"
EVENTS_URL = SITE + "/events"
TIMEZONE = "America/Chicago"
TZ = ZoneInfo(TIMEZONE)

ORGANIZATION = {
    "slug": "bvchess",
    "name": "Brazos Valley Chess Association",
    "website_url": SITE + "/",
}

#: Keyed on the ICS LOCATION, lowercased. Judgement only — see the docstring.
VENUES: dict[str, dict[str, Any]] = {
    "first baptist church bryan, 3100 cambridge dr, bryan, tx 77802, usa": {
        "slug": "first-baptist-church-bryan",
        "name": "First Baptist Church Bryan",
        "street": "3100 Cambridge Dr",
        "city": "Bryan",
        "region": "TX",
        "postcode": "77802",
        "area": "bryan",
        "indoor": True,
    },
}

_FOLD = re.compile(r"\r?\n[ \t]")
_PROP = re.compile(r"^([A-Z-]+)((?:;[^:]*)?):(.*)$")
_TAG = re.compile(r"<[^>]+>")
_BR = re.compile(r"<br\s*/?>|</p>|</div>", re.I)
_ANCHOR = re.compile(r"<a\b[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.I | re.S)
_REGISTRATION = re.compile(r"registration", re.I)
# "$25 CASH entry fee." — the sentence the fee sits in, verbatim.
_FEE = re.compile(r"(?:^|\n)([^\n]*\$(\d+(?:\.\d{2})?)[^\n]*?\bentry fee\b[^\n]*)", re.I)


class BvChessScraper(BaseEventScraper):
    kind = "bvchess"
    source_slug = "bvchess"
    source_name = ORGANIZATION["name"]
    verify_url = ICS_URL

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
        text = client.get_text(ICS_URL)
        if "BEGIN:VCALENDAR" not in (text or ""):
            raise ScraperError("bvchess: ICS feed is not a calendar")
        raw = [to_raw(rec) for rec in parse_ics(text)]
        return [item for item in raw if in_window(item, window_start, window_end)]

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


# --- ICS -------------------------------------------------------------------


def parse_ics(text: str) -> list[dict]:
    """Unfolded VEVENT blocks -> plain records. Pure.

    ``dtstart``/``dtend`` are aware datetimes, or dates when ``VALUE=DATE``
    (``all_day``). Text properties are unescaped per RFC 5545.
    """
    unfolded = _FOLD.sub("", text or "")
    out: list[dict] = []
    for block in unfolded.split("BEGIN:VEVENT")[1:]:
        body = block.split("END:VEVENT", 1)[0]
        props: dict[str, tuple[str, str]] = {}
        for line in body.splitlines():
            match = _PROP.match(line)
            if match:
                name, params, value = match.groups()
                props.setdefault(name, (params, value))
        if "UID" not in props or "DTSTART" not in props:
            continue
        start, all_day = _parse_dt(*props["DTSTART"])
        end = _parse_dt(*props["DTEND"])[0] if "DTEND" in props else None
        out.append({
            "uid": props["UID"][1].strip(),
            "summary": _unescape(props.get("SUMMARY", ("", ""))[1]),
            "location": _unescape(props.get("LOCATION", ("", ""))[1]),
            "description": _unescape(props.get("DESCRIPTION", ("", ""))[1]),
            "status": (props.get("STATUS", ("", ""))[1] or "").strip().upper() or None,
            "rrule": props["RRULE"][1].strip() if "RRULE" in props else None,
            "dtstart": start,
            "dtend": end,
            "all_day": all_day,
        })
    return out


def _parse_dt(params: str, value: str) -> tuple[datetime | date, bool]:
    value = value.strip()
    if "VALUE=DATE" in params.upper():
        return date(int(value[:4]), int(value[4:6]), int(value[6:8])), True
    naive = datetime.strptime(value.rstrip("Z"), "%Y%m%dT%H%M%S")
    if value.endswith("Z"):
        return naive.replace(tzinfo=UTC), False
    tzid = re.search(r"TZID=([^;]+)", params)
    return naive.replace(tzinfo=ZoneInfo(tzid.group(1)) if tzid else TZ), False


def _unescape(value: str) -> str:
    out = (value or "").replace("\\n", "\n").replace("\\N", "\n")
    out = out.replace("\\,", ",").replace("\\;", ";").replace("\\\\", "\\")
    return out.strip()


# --- identity + window -----------------------------------------------------


def to_raw(record: dict) -> RawEvent:
    return RawEvent(
        series_uid=record["uid"],
        occurrence_tid=_stamp(record["dtstart"]),
        record=record,
    )


def in_window(item: RawEvent, window_start: date, window_end: date) -> bool:
    start = _local_date(item.record.get("dtstart"))
    if start is None:
        return False
    return window_start <= start <= window_end


def _stamp(value: datetime | date) -> str:
    if isinstance(value, datetime):
        return value.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return value.strftime("%Y%m%d")


def _local_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(TZ).date()
    if isinstance(value, date):
        return value
    return None


# --- normalize -------------------------------------------------------------


def _series(item: RawEvent) -> dict:
    rec = item.record
    title = normalize.clean_text(rec.get("summary")) or ""
    if not title:
        raise ScraperError("missing title")
    if rec.get("rrule"):
        raise ScraperError(f"RRULE on {title!r} ({rec['rrule']}); not expanding recurring events")
    location = (rec.get("location") or "").strip()
    place = VENUES.get(location.lower())
    if place is None:
        raise UnknownVenueError(f"unknown venue {location!r} for {title!r}")

    series: dict[str, Any] = {
        "source_series_uid": item.series_uid,
        "title": title,
        "source_url": EVENTS_URL,
        "organization": dict(ORGANIZATION),
        "place": dict(place),
        "topics": ["sports"],
        "audiences": [],
    }
    raw_desc = rec.get("description") or ""
    description = html_to_text(raw_desc)
    if description:
        series["description"] = description
    registration_url = _registration_url(raw_desc)
    if registration_url:
        series["registration_required"] = True
        series["registration_url"] = registration_url
    fee = _FEE.search(description)
    if fee:
        series["is_free"] = False
        series["cost_low_cents"] = round(float(fee.group(2)) * 100)
        series["cost_note"] = fee.group(1).strip()
        series["cost_basis"] = "per_person"
    return series


def _occurrence(item: RawEvent) -> dict:
    rec = item.record
    start = rec.get("dtstart")
    if start is None:
        raise ScraperError("missing DTSTART")
    occ: dict[str, Any] = {
        "source_occurrence_tid": item.occurrence_tid,
        "timezone": TIMEZONE,
        "all_day": bool(rec.get("all_day")),
        "status": "cancelled" if rec.get("status") == "CANCELLED" else "scheduled",
    }
    occ["start_local"] = _local_iso(start)
    end = rec.get("dtend")
    if end is not None:
        occ["end_local"] = _local_iso(end)
    return occ


def _local_iso(value: datetime | date) -> str:
    if isinstance(value, datetime):
        return value.astimezone(TZ).replace(tzinfo=None).isoformat()
    return datetime(value.year, value.month, value.day).isoformat()


def html_to_text(value: str) -> str:
    """Google's HTML description -> plain text, link labels kept. Pure."""
    text = _BR.sub("\n", value or "")
    text = _TAG.sub("", text)
    text = html.unescape(text).replace("\xa0", " ").replace("**", "")
    return normalize.clean_text(text) or ""


def _registration_url(value: str) -> str | None:
    for href, label in _ANCHOR.findall(value or ""):
        if _REGISTRATION.search(_TAG.sub("", label)):
            return html.unescape(href)
    return None
