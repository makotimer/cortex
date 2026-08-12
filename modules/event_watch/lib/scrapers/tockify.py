"""Bryan + College Station Public Library System, via Tockify.

Both endpoints are public and unauthenticated; there is nothing to scrape in the
HTML sense, as the library's WordPress page merely embeds this calendar.

Every rule below is pinned by ``tests/fixtures/event_watch/`` — see that
directory's README for what the captured window actually contains and where it
differs from the design's earlier sample.
"""
from __future__ import annotations

import re
from datetime import UTC, date, datetime
from typing import Any

from modules._shared.http import HttpClient

from .. import classify, normalize
from .base import BaseEventScraper, RawEvent, ScraperError

NGEVENT_URL = "https://tockify.com/api/ngevent"
ICS_URL = "https://tockify.com/api/feeds/ics/bcslibrary"
CALNAME = "bcslibrary"

ORGANIZATION = {
    "slug": "bcs-library",
    "name": "Bryan + College Station Public Library System",
    "website_url": "https://www.bcslibrary.org/",
}

#: Venues keyed by Google place_id, which is the only identifier the feed gives
#: that is stable across spelling changes. `area` is supplied by the injector —
#: the site holds no per-source mapping rules (contract §5).
#:
#: The design named four venues; the real feed carries six, including an
#: outreach event in an HEB and one at the Meyer community center. A venue that
#: is not here fails loudly rather than guessing an area.
VENUES: dict[str, dict[str, Any]] = {
    "ChIJ3eluT6KBRoYReSlt1R8lgPg": {
        "slug": "clara-b-mounce-public-library",
        "name": "Clara B. Mounce Public Library",
        "street": "201 E 26th St", "city": "Bryan", "postcode": "77803",
        "area": "bryan",
    },
    "ChIJydv_UpqERoYRLpl7Co4mLb4": {
        "slug": "larry-j-ringer-library",
        "name": "Larry J. Ringer Library",
        "street": "1818 Harvey Mitchell Pkwy S", "city": "College Station",
        "postcode": "77845", "area": "college_station",
    },
    "ChIJsdeM96KBRoYR4hu2SuhCfuY": {
        "slug": "carnegie-history-center",
        "name": "Carnegie History Center",
        "street": "111 S Main St", "city": "Bryan", "postcode": "77803",
        "area": "bryan",
    },
    "ChIJ677R-cqaRoYRcCA1ucYNHLA": {
        "slug": "heb-william-d-fitch",
        "name": "HEB (William D. Fitch Pkwy)",
        "street": "949 William D. Fitch Pkwy", "city": "College Station",
        "postcode": "77845", "area": "college_station",
    },
    "ChIJFRL-aWaERoYR7MHSRULjuGA": {
        "slug": "bob-and-wanda-meyer-senior-and-community-center",
        "name": "Bob and Wanda Meyer Senior and Community Center",
        "street": "2275 Dartmouth St", "city": "College Station",
        "postcode": "77840", "area": "college_station",
    },
}

AUDIENCE_MAP = {
    "Adult": "adult",
    "Teen": "teen",
    "Tween": "tween",
    "All-Ages": "all-ages",
}

#: `Children` is broad, so it maps to `elementary` and only gains a younger band
#: when the text says so outright (design §11 open decision 2). Sending parents
#: of four-year-olds to school-age events is the failure being avoided.
PRESCHOOL_HINTS = ("storytime", "story time", "preschool", "pre-k")
BABY_HINTS = ("baby", "babies", "toddler", "infant", "lapsit")

# The en and em dashes are deliberate: real titles use all three separators.
_REGISTER_SUFFIX = re.compile(r"\s*[-–—]\s*Register\b.*$", re.I)  # noqa: RUF001
_NOT_REQUIRED = re.compile(r"registration\s+(is\s+)?not\s+required|no\s+registration", re.I)
_REQUIRED = re.compile(r"registration\s+(is\s+)?required", re.I)


class TockifyScraper(BaseEventScraper):
    kind = "tockify"
    source_slug = CALNAME
    source_name = ORGANIZATION["name"]

    def __init__(self, proxy_url: str | None = None) -> None:
        self._proxy_url = proxy_url
        self._client: HttpClient | None = None

    # ---------------- I/O ----------------
    def fetch(
        self, window_start: date, window_end: date, *, skip_network: bool
    ) -> list[RawEvent]:
        if skip_network:
            return []
        client = self._client or HttpClient(
            user_agent="CortexEventWatch/1.0 (+https://discoverbcs.org)",
            proxy_url=self._proxy_url,
            proxy_env="EVENT_WATCH_PROXY_URL",
        )
        self._client = client

        params = {
            "calname": CALNAME,
            "startms": _to_millis(window_start),
            "endms": _to_millis(window_end),
        }
        data = client.get_json(NGEVENT_URL, params=params)
        events = (data or {}).get("events")
        if not isinstance(events, list):
            raise ScraperError(f"tockify: no 'events' array in response for {CALNAME!r}")

        # The ICS feed is the whole calendar, not the window. It is fetched for
        # its DESCRIPTION, which preserves URLs the JSON's description.text
        # flattens away ("Click here for more information." loses its link).
        descriptions: dict[str, str] = {}
        try:
            descriptions = parse_ics_descriptions(client.get_text(ICS_URL))
        except Exception:
            # A missing ICS costs link fidelity, not the run.
            descriptions = {}

        return [_to_raw(e, descriptions) for e in events]

    # ---------------- pure ----------------
    def normalize(self, raw: list[RawEvent]) -> tuple[list[dict], list[dict]]:
        by_series: dict[str, list[RawEvent]] = {}
        for item in raw:
            by_series.setdefault(item.series_uid, []).append(item)

        payloads: list[dict] = []
        rejected: list[dict] = []
        for uid, items in by_series.items():
            base = _base_record(items)
            try:
                series = self._series(uid, base)
            except ScraperError as exc:
                rejected.extend(
                    {"series_uid": uid, "occurrence_tid": i.occurrence_tid, "reason": str(exc)}
                    for i in items
                )
                continue
            payloads.extend(
                {
                    "schema_version": "1",
                    "source": {"slug": self.source_slug, "name": self.source_name, "kind": "feed"},
                    "series": series,
                    "occurrence": _occurrence(item, base),
                }
                for item in items
            )
        return payloads, rejected

    def _series(self, uid: str, base: RawEvent) -> dict:
        content = base.record.get("content") or {}
        title = strip_registration_suffix(content.get("summary", {}).get("text") or "")
        description = base.supplement.get("description") or normalize.clean_text(
            (content.get("description") or {}).get("text")
        )
        labels = _labels(base.record)

        series: dict[str, Any] = {
            "source_series_uid": uid,
            "title": title,
            "organization": dict(ORGANIZATION),
            "place": _place(content),
            "is_free": True,  # every library programme in the feed is free
            "audiences": _audiences(labels, f"{title}\n{description or ''}"),
            "topics": classify.from_labels(labels),
        }
        if description:
            series["description"] = description
        if url := _detail_url(base.record):
            series["source_url"] = url
        registration = _registration(title, description or "")
        if registration is not None:
            series["registration_required"] = registration
        return series


# --------------------------------------------------------------------------
# Pure helpers. Kept module-level so tests can drive them directly.
# --------------------------------------------------------------------------
def strip_registration_suffix(title: str) -> str:
    """Drop a trailing ``- Register…``, keeping the venue parenthetical.

    ``"Tech Titans (CBMPL) - Register"`` -> ``"Tech Titans (CBMPL)"``. The site's
    matcher normalizes ``(CBMPL)`` away itself and it is useful signal in the
    admin, so it stays.
    """
    return _REGISTER_SUFFIX.sub("", title or "").strip()


def _registration(title: str, text: str) -> bool | None:
    """Three-state. Omission means unknown, which is not the same as False."""
    if _NOT_REQUIRED.search(text):
        return False
    if re.search(r"\bregister\b", title or "", re.I) or _REQUIRED.search(text):
        return True
    return None


def _audiences(labels: list[str], text: str) -> list[str]:
    out = {AUDIENCE_MAP[label] for label in labels if label in AUDIENCE_MAP}
    if "Children" in labels:
        out.add("elementary")
        low = (text or "").lower()
        if any(h in low for h in PRESCHOOL_HINTS):
            out.add("preschool")
        if any(h in low for h in BABY_HINTS):
            out.add("baby-toddler")
    return sorted(out)


def _place(content: dict) -> dict:
    location = content.get("location") or {}
    place_id = location.get("place_id")
    venue = VENUES.get(place_id or "")
    if not venue:
        raise ScraperError(
            f"unknown venue place_id={place_id!r} name={content.get('place')!r}; "
            "add it to VENUES with an explicit area"
        )
    place = {
        "slug": venue["slug"], "name": venue["name"],
        "street": venue["street"], "city": venue["city"],
        "region": "TX", "postcode": venue["postcode"],
        "external_place_id": place_id, "area": venue["area"],
    }
    if isinstance(location.get("latitude"), (int, float)):
        place["latitude"] = float(location["latitude"])
    if isinstance(location.get("longitude"), (int, float)):
        place["longitude"] = float(location["longitude"])
    return place


def _occurrence(item: RawEvent, base: RawEvent) -> dict:
    when = item.record.get("when") or {}
    start = when.get("start") or {}
    tzid = start.get("tzid") or "America/Chicago"
    occ: dict[str, Any] = {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": normalize.local_iso(int(start["millis"]), tzid),
        "timezone": tzid,
        "all_day": bool(when.get("allDay")),
        "status": _status(item.record),
    }
    end = when.get("end") or {}
    if end.get("millis"):
        occ["end_local"] = normalize.local_iso(int(end["millis"]), end.get("tzid") or tzid)
    overrides = _overrides(item, base)
    if overrides:
        occ["overrides"] = overrides
    return occ


def _overrides(item: RawEvent, base: RawEvent) -> dict:
    """Per-occurrence differences, for `mod` records only.

    The contract accepts exactly ``title``, ``description`` and
    ``registration_url``; anything else is rejected outright.
    """
    if item.record.get("kind") != "mod" or item is base:
        return {}
    out = {}
    item_title = strip_registration_suffix(
        ((item.record.get("content") or {}).get("summary") or {}).get("text") or ""
    )
    base_title = strip_registration_suffix(
        ((base.record.get("content") or {}).get("summary") or {}).get("text") or ""
    )
    if item_title and item_title != base_title:
        out["title"] = item_title

    item_desc = item.supplement.get("description") or normalize.clean_text(
        ((item.record.get("content") or {}).get("description") or {}).get("text")
    )
    base_desc = base.supplement.get("description") or normalize.clean_text(
        ((base.record.get("content") or {}).get("description") or {}).get("text")
    )
    if item_desc and item_desc != base_desc:
        out["description"] = item_desc
    return out


def _status(record: dict) -> str:
    """The feed carries status as an object, e.g. ``{"name": "scheduled"}``."""
    status = record.get("status")
    name = status.get("name") if isinstance(status, dict) else status
    return "cancelled" if str(name).lower() == "cancelled" else "scheduled"


def _labels(record: dict) -> list[str]:
    tags = ((record.get("content") or {}).get("tagset") or {}).get("tags") or {}
    return list(tags.get("default") or [])


def _detail_url(record: dict) -> str | None:
    eid = record.get("eid") or {}
    uid, tid = eid.get("uid"), eid.get("tid")
    if uid and tid:
        return f"https://tockify.com/{CALNAME}/detail/{uid}/{tid}"
    return None


def _base_record(items: list[RawEvent]) -> RawEvent:
    """The record that best represents the series.

    A ``mod`` is a modified single occurrence, so it is a poor description of the
    series as a whole; prefer any non-mod sibling.
    """
    for item in items:
        if item.record.get("kind") != "mod":
            return item
    return items[0]


def _to_raw(record: dict, descriptions: dict[str, str]) -> RawEvent:
    eid = record.get("eid") or {}
    uid = str(eid.get("uid") or "")
    # tid arrives as an int; the contract's identity keys are strings.
    tid = str(eid.get("tid") or "")
    supplement = {}
    if uid in descriptions:
        supplement["description"] = descriptions[uid]
    return RawEvent(series_uid=uid, occurrence_tid=tid, record=record, supplement=supplement)


def parse_ics_descriptions(text: str) -> dict[str, str]:
    """Map series uid -> DESCRIPTION, from an unfolded ICS feed.

    ICS UIDs look like ``TKF/<calid>/<series>/<n>/<seq>/<rid>``; the third
    segment is the series uid the JSON calls ``eid.uid``.
    """
    unfolded = re.sub(r"\r?\n[ \t]", "", text or "")
    out: dict[str, str] = {}
    for block in unfolded.split("BEGIN:VEVENT")[1:]:
        uid_match = re.search(r"^UID:TKF/[^/]+/([^/\r\n]+)/", block, re.M)
        desc_match = re.search(r"^DESCRIPTION:(.*)$", block, re.M)
        if not uid_match or not desc_match:
            continue
        uid = uid_match.group(1)
        # Later blocks are later occurrences of the same series; the first
        # description is enough and keeps this deterministic.
        out.setdefault(uid, _unescape_ics(desc_match.group(1)))
    return out


def _unescape_ics(value: str) -> str:
    out = (value or "").replace("\\n", "\n").replace("\\,", ",")
    out = out.replace("\\;", ";").replace("\\\\", "\\")
    return normalize.clean_text(out) or ""


def _to_millis(day: date) -> int:
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp() * 1000)
