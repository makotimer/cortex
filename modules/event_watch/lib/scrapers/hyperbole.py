"""Hyperbole Bookstore events, via Bookmanager ``event/getList``.

The public ``/events`` page is an empty React shell. Dates live on
``api.bookmanager.com`` as unix stamps that the shop UI renders in
``America/Los_Angeles``; wall-clock 10:30 there is the 10:30 the store
advertises, filed here as America/Chicago.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError

API = "https://api.bookmanager.com/customer"
SAN = "9932461"
STORE_ID = "1110171"
SITE = "https://hyperbolebookstore.com"
DISPLAY_TZ = "America/Los_Angeles"
TIMEZONE = "America/Chicago"

ORGANIZATION = {
    "slug": "hyperbole",
    "name": "Hyperbole Bookstore",
    "website_url": SITE + "/",
}

VENUE = {
    "slug": "hyperbole-bookstore",
    "name": "Hyperbole Bookstore",
    "street": "1275 Arrington Rd., Ste. 102",
    "city": "College Station",
    "region": "TX",
    "postcode": "77845",
    "area": "college_station",
    "latitude": 30.5368372,
    "longitude": -96.3013432,
}

_STORYTIME = re.compile(r"\bstorytime\b", re.I)


class HyperboleScraper(BaseEventScraper):
    kind = "hyperbole"
    source_slug = "hyperbole"
    source_name = ORGANIZATION["name"]
    verify_url = f"{API}/event/getList"

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
        session_id = _session(client)
        rows = parse_list(_get_list(client, session_id, window_start))
        raw: list[RawEvent] = []
        for row in rows:
            item = to_raw(row)
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
            "place": dict(VENUE),
            "topics": ["reading"],
            "audiences": audiences_from_title(title),
            "indoor": True,
        }
        if description:
            series["description"] = description
        if rec.get("source_url"):
            series["source_url"] = rec["source_url"]
        if is_free_storytime(title, rec.get("tickets") or []):
            series["is_free"] = True
        return series


def parse_list(payload: str | dict) -> list[dict]:
    """Bookmanager ``event/getList`` body -> row dicts. Pure."""
    if isinstance(payload, str):
        try:
            data = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise ScraperError("hyperbole: getList returned non-JSON") from exc
    else:
        data = payload
    if not isinstance(data, dict):
        raise ScraperError("hyperbole: getList is not an object")
    if data.get("error"):
        raise ScraperError(f"hyperbole: {data['error']}")
    rows = data.get("rows")
    if not isinstance(rows, list):
        raise ScraperError("hyperbole: getList rows is not a list")
    return rows


def to_raw(row: dict) -> RawEvent:
    info = row.get("info") or {}
    title = (info.get("name") or "").strip()
    eid = row.get("id")
    start = wall_clock(row.get("from"))
    if start is None:
        raise ScraperError(f"hyperbole: event {eid!r} has no start")
    html_desc = info.get("description") or ""
    description = _strip_html(html_desc)
    rec = {
        "id": eid,
        "title": title,
        "description": description,
        "date": start.date(),
        "start": start,
        "end": wall_clock(row.get("to")),
        "tickets": list(info.get("ticket") or []),
        "source_url": f"{SITE}/events/{eid}",
    }
    return RawEvent(
        series_uid=normalize.slugify(re.sub(r"['’]", "", title)),
        occurrence_tid=str(eid),
        record=rec,
    )


def wall_clock(ts: int | float | str | None) -> datetime | None:
    """Bookmanager unix stamp -> LA display time, seconds dropped."""
    if ts is None or ts == "":
        return None
    try:
        stamp = int(ts)
    except (TypeError, ValueError):
        return None
    if stamp <= 0:
        return None
    shown = datetime.fromtimestamp(stamp, tz=ZoneInfo(DISPLAY_TZ)).replace(tzinfo=None)
    return shown.replace(second=0, microsecond=0)


def audiences_from_title(title: str) -> list[str]:
    return ["all-ages"] if _STORYTIME.search(title or "") else []


def is_free_storytime(title: str, tickets: list) -> bool:
    return bool(_STORYTIME.search(title or "") and not tickets)


def _occurrence(item: RawEvent) -> dict:
    rec = item.record
    start: datetime | None = rec.get("start")
    if start is None:
        raise ScraperError("missing start")
    end: datetime | None = rec.get("end")
    out: dict[str, Any] = {
        "source_occurrence_tid": str(item.occurrence_tid),
        "start_local": start.isoformat(),
        "timezone": TIMEZONE,
        "all_day": False,
        "status": "scheduled",
    }
    if end and end > start:
        out["end_local"] = end.isoformat()
    description = rec.get("description")
    if description:
        out["overrides"] = {"description": description}
    return out


def _strip_html(html_text: str) -> str:
    if not html_text:
        return ""
    text = BeautifulSoup(html_text, "html.parser").get_text(" ", strip=True)
    return normalize.clean_text(text) or ""


def _session(client: HttpClient) -> str:
    resp = client.session.post(
        f"{API}/session/get",
        params={"_cb": SAN},
        data={"store_id": STORE_ID},
        timeout=client.timeout,
    )
    resp.raise_for_status()
    try:
        body = resp.json()
    except ValueError as exc:
        raise ScraperError("hyperbole: session/get returned non-JSON") from exc
    sid = (body or {}).get("session_id")
    if not sid:
        raise ScraperError(f"hyperbole: session/get failed: {body!r}")
    return str(sid)


def _get_list(client: HttpClient, session_id: str, window_start: date) -> dict:
    resp = client.session.post(
        f"{API}/event/getList",
        params={"_cb": SAN},
        data={
            "store_id": STORE_ID,
            "session_id": session_id,
            "from": window_start.strftime("%Y%m%d"),
            "increment_views": "0",
        },
        timeout=client.timeout,
    )
    resp.raise_for_status()
    try:
        body = resp.json()
    except ValueError as exc:
        raise ScraperError("hyperbole: getList returned non-JSON") from exc
    if not isinstance(body, dict):
        raise ScraperError("hyperbole: getList is not an object")
    if body.get("error"):
        raise ScraperError(f"hyperbole: {body['error']}")
    return body
