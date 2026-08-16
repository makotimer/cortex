"""KBTX Marketplace coupons via incentRev.

The deals page is a voucher catalog, not a dated event grid. A coupon is
posted as one all-day occurrence on the day this scraper first sees it.
Later runs keep that date so a re-fetch does not walk the listing forward.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from modules._shared.http import HttpClient

from .. import normalize, state
from .base import BaseEventScraper, RawEvent, ScraperError

LIST_URL = "https://kbtx.incentrev.com/bryan/deals"
SITE = "https://kbtx.incentrev.com"
TIMEZONE = "America/Chicago"

ORGANIZATION = {
    "slug": "kbtx-marketplace",
    "name": "KBTX Marketplace",
    "website_url": LIST_URL,
}

_DEAL_ID = re.compile(r"/bryan/(\d+)-")
_MONEY = re.compile(r"\$(\d+(?:\.\d+)?)")

_ARTS = re.compile(r"gallery|art class|degallery|paint", re.I)
_SPORTS = re.compile(r"fitness|gym|training|workout", re.I)


class KbtxDealsScraper(BaseEventScraper):
    kind = "kbtxdeals"
    source_slug = "kbtx-marketplace"
    source_name = ORGANIZATION["name"]
    verify_url = LIST_URL

    def __init__(self, proxy_url: str | None = None, state_dir: str | None = None) -> None:
        self._proxy_url = proxy_url
        self._state_dir = state_dir
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
        deals = parse_deals(client.get_text(LIST_URL))
        seen: dict[str, str] = {}
        if self._state_dir:
            seen = state.load_first_seen(self._state_dir, self.source_slug)
        seen = stamp_first_seen(deals, seen, window_start)
        if self._state_dir:
            state.save_first_seen(self._state_dir, self.source_slug, seen)
        return [to_raw(d) for d in deals if d.get("first_seen")]

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
        merchant = (rec.get("merchant") or "").strip()
        headline = (rec.get("headline") or "").strip()
        title = ": ".join(part for part in (merchant, headline) if part)
        if not title:
            raise ScraperError("missing title")
        series: dict[str, Any] = {
            "source_series_uid": item.series_uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "place": {
                "slug": normalize.slugify(merchant) or "kbtx-marketplace-merchant",
                "name": merchant or "Bryan",
                "city": "Bryan",
                "region": "TX",
                "area": "bryan",
            },
            "topics": topics_from(merchant, headline),
            "audiences": ["all-ages"],
        }
        if rec.get("url"):
            series["source_url"] = rec["url"]
            series["registration_url"] = rec["url"]
        pay = rec.get("pay_cents")
        was = rec.get("was_cents")
        if isinstance(pay, int) and pay > 0:
            series["cost_low_cents"] = pay
            series["cost_note"] = _cost_note(pay, was)
        return series


def parse_deals(html_text: str) -> list[dict[str, Any]]:
    """Marketplace listing HTML -> deal dicts. Pure."""
    soup = BeautifulSoup(html_text or "", "html.parser")
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for card in soup.select(".deal"):
        merchant_el = card.select_one("h2.merchant")
        if not merchant_el:
            continue
        link = card.select_one('a[href*="/bryan/"]')
        href = link.get("href") if link else ""
        match = _DEAL_ID.search(href or "")
        if not match:
            continue
        deal_id = match.group(1)
        if deal_id in seen:
            continue
        headline_el = card.select_one(".dealheadline p") or card.select_one(".title p")
        url = href if str(href).startswith("http") else urljoin(SITE, href or "")
        seen.add(deal_id)
        out.append({
            "deal_id": deal_id,
            "merchant": merchant_el.get_text(" ", strip=True),
            "headline": headline_el.get_text(" ", strip=True) if headline_el else "",
            "url": url,
            "pay_cents": parse_money(
                (card.select_one(".price b") or card.select_one(".price")).get_text()
                if card.select_one(".price")
                else ""
            ),
            "was_cents": parse_money(
                card.select_one(".old-price").get_text() if card.select_one(".old-price") else ""
            ),
        })
    return out


def stamp_first_seen(
    deals: list[dict[str, Any]], seen: dict[str, str], today: date
) -> dict[str, str]:
    """Stamp each deal with the date it first appeared. Pure besides mutation of deals."""
    out = dict(seen)
    today_s = today.isoformat()
    for deal in deals:
        deal_id = str(deal.get("deal_id") or "")
        if not deal_id:
            continue
        if deal_id not in out:
            out[deal_id] = today_s
        try:
            deal["first_seen"] = date.fromisoformat(str(out[deal_id])[:10])
        except ValueError:
            deal["first_seen"] = today
            out[deal_id] = today_s
    return out


def parse_money(text: str) -> int | None:
    match = _MONEY.search(text or "")
    if not match:
        return None
    return int(round(float(match.group(1)) * 100))


def topics_from(merchant: str, headline: str) -> list[str]:
    blob = f"{merchant} {headline}"
    if _ARTS.search(blob):
        return ["arts"]
    if _SPORTS.search(blob):
        return ["sports"]
    return ["community"]


def to_raw(deal: dict[str, Any]) -> RawEvent:
    first: date | None = deal.get("first_seen")
    start_ms = None
    if isinstance(first, date):
        start = datetime(first.year, first.month, first.day, tzinfo=ZoneInfo(TIMEZONE))
        start_ms = int(start.timestamp() * 1000)
    return RawEvent(
        series_uid=str(deal.get("deal_id") or ""),
        occurrence_tid=str(start_ms) if start_ms is not None else "",
        record=dict(deal),
    )


def _occurrence(item: RawEvent) -> dict:
    first = item.record.get("first_seen")
    if not isinstance(first, date):
        raise ScraperError("missing first_seen")
    start = datetime(first.year, first.month, first.day, tzinfo=ZoneInfo(TIMEZONE))
    start_ms = int(start.timestamp() * 1000)
    return {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": normalize.local_iso(start_ms, TIMEZONE),
        "timezone": TIMEZONE,
        "all_day": True,
        "status": "scheduled",
    }


def _cost_note(pay_cents: int, was_cents: int | None) -> str:
    pay = _dollars(pay_cents)
    if was_cents:
        return f"{pay} (was {_dollars(was_cents)})"
    return pay


def _dollars(cents: int) -> str:
    if cents % 100:
        return f"${cents / 100:.2f}"
    return f"${cents // 100}"
