"""Challenge Entertainment — pub trivia, Singo and bingo shows, via its AJAX API.

The public ``/shows/`` page is a jQuery shell that renders nothing server-side;
everything comes from two ``admin-ajax.php`` actions, which is why this reads the
API rather than driving a browser:

* ``filter_shows`` — an HTML fragment of ``.ntl-card`` elements
* ``filter_map``   — a ``<script>`` carrying ``window.gmapLocations``, which is
  the only place latitude, longitude and the postcode appear

Both are unauthenticated GET-shaped reads that happen to want POST.

Two things about this source shape the whole file.

**One request per day, not one for the window.** With no ``selected_date`` the
endpoint answers with one card per show carrying only its *next* occurrence, and
a date pill with no year (``"Tonight, 7:00 pm"``, ``"Wed, Aug 19, 8:00 pm"``).
Expanding ``"Wednesdays, 8:00 pm"`` locally would mean inventing dates the source
never asserted — and would silently invent the ones it cancelled. Asking for each
date instead gets occurrences the source stands behind, with per-date cancellation
included, and removes year inference entirely because we supplied the date. It
costs one request per day, which is why ``max_window_days`` is small.

**Venues are an editorial gate, not just a lookup.** These shows run in family
restaurants *and* in 21+ bars, and discoverbcs.org is a family guide. ``VENUES``
records the audience band per venue; a venue that is not listed is rejected
loudly and emailed rather than guessed at, exactly like an unmapped Tockify
venue. Everything factual — name, street, city, postcode, coordinates — still
comes from the live feed, so the table only ever holds judgement.

Every rule below is pinned by ``tests/fixtures/event_watch/challenge_*`` — see
that directory's README for what the captured week contains.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from typing import Any
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from modules._shared.http import HttpClient

from .. import normalize
from .base import BaseEventScraper, RawEvent, ScraperError

AJAX_URL = "https://challengeentertainment.com/wp-admin/admin-ajax.php"
SHOWS_URL = "https://challengeentertainment.com/shows/"

#: The search Challenge's own site would run for Bryan and College Station. 77840
#: is central College Station; 25 miles reaches Bryan, Navasota and Caldwell
#: without pulling in Houston or Austin.
GEO_INPUT = "77840"
GEO_RADIUS = 25

TZID = "America/Chicago"

ORGANIZATION = {
    "slug": "challenge-entertainment",
    "name": "Challenge Entertainment",
    "website_url": "https://challengeentertainment.com/",
}

#: Audience banding per venue, keyed by the feed's own ``data-venue-key``.
#:
#: This is the one thing the source cannot tell us and the one thing a family
#: guide most needs: "Live Trivia" reads identically whether it runs at a mini
#: golf course or a whiskey bar. ``all-ages`` means a family could turn up with
#: children; ``adult`` means they could not.
#:
#: ``indoor`` is optional and three-state — present only where it is genuinely
#: known, because omitted means unknown and that is not the same answer as
#: ``False`` (contract §5).
#:
#: A venue missing from here is rejected, not guessed. Adding one is a
#: deliberate judgement call, which is the point.
VENUES: dict[str, dict[str, Any]] = {
    # --- family venues -----------------------------------------------------
    "venue-3796": {"audiences": ["all-ages"], "indoor": False, "note": "PopStroke — mini golf"},
    "venue-1436": {"audiences": ["all-ages"], "indoor": False, "note": "The Green at Century Square lawn"},
    "venue-1419": {"audiences": ["all-ages"], "indoor": True, "note": "Rx Pizza, Bryan"},
    "venue-1421": {"audiences": ["all-ages"], "indoor": True, "note": "Rx Pizza, south College Station"},
    "venue-1425": {"audiences": ["all-ages"], "indoor": True, "note": "South Flo Pizza, inside HEB"},
    "venue-3650": {"audiences": ["all-ages"], "indoor": True, "note": "Mas Fajitas"},
    # --- bars: published, but banded adult ---------------------------------
    "venue-1417": {"audiences": ["adult"], "indoor": True, "note": "Rough Draught Whiskey Bar"},
    "venue-1440": {"audiences": ["adult"], "indoor": True, "note": "The Owl Pub & Grill"},
    "venue-1366": {"audiences": ["adult"], "indoor": True, "note": "Angry Elephant"},
    "venue-1387": {"audiences": ["adult"], "indoor": True, "note": "Duddley's Draw"},
    "venue-1379": {"audiences": ["adult"], "indoor": True, "note": "Carney's Pub & Grill"},
    "venue-3867": {"audiences": ["adult"], "indoor": True, "note": "Murphy's Law"},
}

#: Topics per game, from the closed vocabulary in the intake contract §4.
#:
#: Nothing in those 13 slugs means "quiz night", so every game gets ``community``
#: — a recurring local gathering is what these actually are. The music games earn
#: ``music`` outright. A game absent from here still publishes, with
#: ``community`` alone, because a missing topic is recoverable and a wrong one is
#: not.
GAME_TOPICS: dict[str, list[str]] = {
    "live-trivia": ["community"],
    "pub-poll": ["community"],
    "ballistic-bingo": ["community"],
    "xtreme-bar-bingo": ["community"],
    "singo": ["community", "music"],
    "music-match": ["community", "music"],
}
DEFAULT_TOPICS = ["community"]

#: City -> contract area. Supplied by the injector; the site derives nothing from
#: addresses (contract §5). Anything else in the 25-mile radius is `nearby`.
AREAS = {"bryan": "bryan", "college station": "college_station"}

_TIME = re.compile(r"(\d{1,2}):(\d{2})\s*([ap])\.?m\.?", re.I)
_VENUE_PATH = re.compile(r"/venue/([^/]+)/?")
_GMAP_LOCATIONS = re.compile(r"window\.gmapLocations\s*=\s*(\[.*?\])\s*;", re.S)
_MAP_QUERY = re.compile(r"[?&]query=([^\"&]+)")
_POSTCODE = re.compile(r"\b(\d{5})(?:-\d{4})?\s*$")


class ChallengeScraper(BaseEventScraper):
    kind = "challenge"
    source_slug = "challenge-entertainment"
    source_name = ORGANIZATION["name"]
    verify_url = SHOWS_URL
    #: One HTTP request per day in the window, so the window is the request
    #: budget. Five weeks is past every frequency the source offers (weekly,
    #: bi-weekly, monthly, 2x/month), so it observes each series' full cycle at
    #: least once while keeping a run to ~36 requests.
    max_window_days = 35

    def __init__(self, proxy_url: str | None = None) -> None:
        self._proxy_url = proxy_url
        self._client: HttpClient | None = None

    # ---------------- I/O ----------------
    def fetch(
        self, window_start: date, window_end: date, *, skip_network: bool
    ) -> list[RawEvent]:
        if skip_network:
            return []
        # proxy_env=None on purpose — see the same note in tockify.py: Settings
        # is the single authority on proxying, and letting the client re-read
        # the environment would proxy a run that skipped the VPN health check.
        client = self._client or HttpClient(
            user_agent="CortexEventWatch/1.0 (+https://discoverbcs.org)",
            proxy_url=self._proxy_url,
            proxy_env=None,
        )
        self._client = client

        # Coordinates and postcodes come only from the map action. A failure
        # here costs precision, not the run: the cards still carry street, city
        # and state.
        geo: dict[str, dict[str, Any]] = {}
        try:
            geo = parse_map(client.post_text(AJAX_URL, data=self._form(action="filter_map")))
        except Exception:
            geo = {}

        raw: list[RawEvent] = []
        day = window_start
        while day < window_end:
            html = client.post_text(
                AJAX_URL, data=self._form(action="filter_shows", selected_date=day.isoformat())
            )
            raw.extend(to_raw_events(html, day, geo))
            day += timedelta(days=1)

        if not raw:
            # Every day empty means the filter stopped matching — a renamed
            # parameter, a moved endpoint, a changed radius. Five weeks of
            # genuinely nothing is not a thing this source does, and returning
            # [] would look to the engine like a calendar that emptied.
            raise ScraperError(
                f"challenge: no shows in any of {(window_end - window_start).days} days "
                f"for geo_input={GEO_INPUT!r} radius={GEO_RADIUS}"
            )
        return raw

    @staticmethod
    def _form(**extra: str) -> dict[str, Any]:
        """The filter payload, sent whole.

        Every filter is sent even when blank, because that is what the site's
        own JS posts and an endpoint that distinguishes "absent" from "empty" is
        not something to discover in production.
        """
        return {
            "game": "",
            "show_info_day": "",
            "show_info_frequency": "",
            "state": "",
            "geo_input": GEO_INPUT,
            "geo_radius": GEO_RADIUS,
            **extra,
        }

    # ---------------- pure ----------------
    def normalize(self, raw: list[RawEvent]) -> tuple[list[dict], list[dict]]:
        by_series: dict[str, list[RawEvent]] = {}
        for item in raw:
            by_series.setdefault(item.series_uid, []).append(item)

        payloads: list[dict] = []
        rejected: list[dict] = []
        for uid, items in by_series.items():
            try:
                series = self._series(uid, items[0])
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
                    "occurrence": _occurrence(item),
                }
                for item in items
            )
        return payloads, rejected

    def _series(self, uid: str, base: RawEvent) -> dict:
        card = base.record
        venue_key = card["venue_key"]
        venue = VENUES.get(venue_key)
        if not venue:
            raise ScraperError(
                f"unknown venue {venue_key!r} ({card.get('venue')!r}, {card.get('address')!r}); "
                "add it to VENUES with an explicit audience band"
            )

        game, venue_name = card["game"], card["venue"]
        series: dict[str, Any] = {
            "source_series_uid": uid,
            "title": f"{game} at {venue_name}",
            "description": _description(card),
            "organization": dict(ORGANIZATION),
            "place": _place(card, base.supplement.get("geo") or {}),
            "audiences": list(venue["audiences"]),
            "topics": list(GAME_TOPICS.get(card["game_slug"], DEFAULT_TOPICS)),
        }
        if card.get("permalink"):
            series["source_url"] = card["permalink"]
        if "indoor" in venue:
            series["indoor"] = bool(venue["indoor"])
        return series


# --------------------------------------------------------------------------
# Pure helpers. Module-level so tests can drive them straight from fixtures.
# --------------------------------------------------------------------------
def to_raw_events(html: str, day: date, geo: dict[str, dict[str, Any]]) -> list[RawEvent]:
    """One day's ``filter_shows`` fragment -> RawEvents. Pure."""
    return [
        RawEvent(
            series_uid=card["series_uid"],
            occurrence_tid=card["occurrence_tid"],
            record=card,
            supplement={"geo": geo.get(card["geo_key"], {})},
        )
        for card in parse_cards(html, day)
    ]


def parse_cards(html: str, day: date) -> list[dict]:
    """Parse ``.ntl-card`` elements for one known date.

    ``day`` is passed in rather than read from the card because we asked for it:
    the pill renders ``"Tonight"`` or ``"Wed, Aug 19"`` with no year, and inferring
    one across a New Year boundary is a bug waiting for December. The pill is
    still read — for the clock time, which appears nowhere else per-occurrence.

    A card missing a venue key or a parseable time is skipped rather than raised
    on: one malformed card must not cost the other eleven. The engine's
    disappearance guard is what catches a day that parses to nothing.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    out: list[dict] = []
    for node in soup.select("div.ntl-card"):
        venue_key = _attr(node, "data-venue-key")
        game = _text(node, ".ntl-card-title")
        venue = _text(node, ".ntl-card-venue")
        if not venue_key or not game or not venue:
            continue

        pill = _text(node, ".ntl-card-date-pill")
        schedule = _text(node, ".ntl-card-schedule")
        # The pill is the occurrence's own time; the schedule is the series'.
        # They agree today, but the pill wins because a one-off time change
        # would show up there first.
        clock = _clock(pill) or _clock(schedule)
        if clock is None:
            continue
        hour, minute = clock

        street, city, region = _split_address(_text(node, ".ntl-card-address"))
        game_slug = normalize.slugify(game)
        start = datetime(day.year, day.month, day.day, hour, minute, tzinfo=ZoneInfo(TZID))
        permalink = _attr(node, "data-permalink")
        cancelled_label = _text(node, ".ntl-card-cancelled-label")

        out.append({
            "venue_key": venue_key,
            "series_uid": f"{venue_key}:{game_slug}",
            # Epoch milliseconds, matching Tockify's tid: the engine places an
            # occurrence in time by parsing this, and only occurrences inside
            # the fetched window are ever eligible for cancellation.
            "occurrence_tid": str(int(start.timestamp() * 1000)),
            "game": game,
            "game_slug": game_slug,
            "venue": venue,
            "address": _text(node, ".ntl-card-address"),
            "street": street,
            "city": city,
            "region": region,
            "area": AREAS.get(city.strip().lower(), "nearby"),
            "place_slug": _place_slug(permalink, venue, city),
            "permalink": permalink or None,
            "date": day.isoformat(),
            "start_local": start.replace(tzinfo=None).isoformat(),
            "schedule": schedule,
            # Both signals, because the source shows both: the class styles the
            # card and the label is the words a reader sees.
            "cancelled": "ntl-card-cancelled" in _attr(node, "class").split()
                         or bool(cancelled_label),
            "cancelled_label": cancelled_label,
            "geo_key": _geo_key(street, city),
        })
    return out


def parse_map(html: str) -> dict[str, dict[str, Any]]:
    """``filter_map`` -> ``{geo_key: {latitude, longitude, postcode}}``.

    The payload is a ``<script>`` assigning a JSON array, and it carries no venue
    key — so the join back to a card is on street and city, the two fields both
    responses spell the same way. The postcode is only in the Google Maps
    directions link inside each popup's HTML.
    """
    match = _GMAP_LOCATIONS.search(html or "")
    if not match:
        return {}
    try:
        locations = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}

    out: dict[str, dict[str, Any]] = {}
    for loc in locations if isinstance(locations, list) else []:
        content = loc.get("content") or ""
        query = _MAP_QUERY.search(content)
        if not query:
            continue
        street, city, _region = _split_address(
            unquote(query.group(1).replace("+", " ")).replace("&amp;", "&")
        )
        entry: dict[str, Any] = {}
        if isinstance(loc.get("lat"), (int, float)):
            entry["latitude"] = float(loc["lat"])
        if isinstance(loc.get("lng"), (int, float)):
            entry["longitude"] = float(loc["lng"])
        # "...Bryan, TX 77803" — the state and postcode share the last comma field.
        if postcode := _POSTCODE.search(_region):
            entry["postcode"] = postcode.group(1)
        if entry:
            out[_geo_key(street, city)] = entry
    return out


def _description(card: dict) -> str:
    """What the calendar shows. The recurrence lives here or nowhere.

    The contract has no series-level recurrence field — an occurrence is what a
    family attends (§3) — so "Wednesdays, 8:00 pm" is written into the prose
    where a reader can act on it, rather than dropped for having nowhere tidy
    to go.
    """
    parts = [f"{card['game']} at {card['venue']}, hosted by {ORGANIZATION['name']}."]
    if card.get("schedule"):
        parts.append(f"Recurring schedule: {card['schedule']}.")
    return " ".join(parts)


def _place(card: dict, geo: dict[str, Any]) -> dict:
    place: dict[str, Any] = {
        "slug": card["place_slug"],
        "name": card["venue"],
        "city": card["city"],
        "region": card["region"] or "TX",
        "area": card["area"],
    }
    if card.get("street"):
        place["street"] = card["street"]
    for key in ("latitude", "longitude", "postcode"):
        if key in geo:
            place[key] = geo[key]
    return place


def _occurrence(item: RawEvent) -> dict:
    card = item.record
    return {
        "source_occurrence_tid": item.occurrence_tid,
        "start_local": card["start_local"],
        "timezone": TZID,
        "all_day": False,
        # Per-date, not per-series: the source cancels one week of a weekly show
        # and leaves the rest standing, which is exactly what the fixture week
        # shows for Duddley's Draw.
        "status": "cancelled" if card["cancelled"] else "scheduled",
    }


def _clock(text: str) -> tuple[int, int] | None:
    """``"🎉 Tonight, 7:00 pm"`` -> ``(19, 0)``. None when there is no time at all."""
    match = _TIME.search(text or "")
    if not match:
        return None
    hour, minute, meridiem = int(match.group(1)), int(match.group(2)), match.group(3).lower()
    if not (1 <= hour <= 12) or minute > 59:
        return None
    hour %= 12
    if meridiem == "p":
        hour += 12
    return hour, minute


def _split_address(address: str) -> tuple[str, str, str]:
    """``"255 Ball St, College Station, TX"`` -> ``("255 Ball St", "College Station", "TX")``.

    Split from the right: the street is the part that contains stray commas
    ("Suite B"), never the city or the state.
    """
    parts = [p.strip() for p in (address or "").split(",") if p.strip()]
    if len(parts) < 3:
        return (parts[0] if parts else "", parts[1] if len(parts) > 1 else "", "")
    return ", ".join(parts[:-2]), parts[-2], parts[-1]


def _geo_key(street: str, city: str) -> str:
    """Join key between a card and a map pin, which share no id."""
    return f"{normalize.slugify(street)}|{normalize.slugify(city)}"


def _place_slug(permalink: str, venue: str, city: str) -> str:
    """The source's own venue slug, which already disambiguates two Rx Pizzas.

    ``https://challengeentertainment.com/venue/rx-pizza-bryan/`` -> ``rx-pizza-bryan``.
    Falls back to name + city, because a place slug is place identity and two
    venues sharing one would rewrite a shared row (contract §3).
    """
    match = _VENUE_PATH.search(permalink or "")
    if match:
        return match.group(1)
    return normalize.slugify(f"{venue} {city}")


def _text(node: Any, selector: str) -> str:
    found = node.select_one(selector)
    return normalize.clean_text(found.get_text(" ", strip=True)) or "" if found else ""


def _attr(node: Any, name: str) -> str:
    """One attribute, always as a string.

    bs4 hands back a list for multi-valued attributes — ``class`` above all —
    and a bare ``.strip()`` on that is a runtime error waiting for the first
    card that carries two classes.
    """
    value = node.get(name)
    if isinstance(value, list):
        return " ".join(str(v) for v in value)
    return str(value or "").strip()
