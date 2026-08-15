"""Brazos Valley Museum Upcoming Events tests.

Homepage fixture: ``bvmuseum_home.html`` — the Upcoming Events repeater
plus the exhibits repeater so the parser has to pick the right strip.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from modules.event_watch.lib import engine, state
from modules.event_watch.lib.config import Settings
from modules.event_watch.lib.scrapers import bvmuseum
from modules.event_watch.lib.scrapers.base import RawEvent

FIXTURES = Path(__file__).parent / "fixtures" / "event_watch"
SITE_APP_PATHS = (
    Path("/discoverbcs-app"),
    Path("/srv/docker/websites/discoverbcs/app"),
)
YEAR = 2026
WINDOW_START = date(2026, 8, 15)
WINDOW_END = date(2027, 5, 12)


def _load_site_validator():
    for app_dir in SITE_APP_PATHS:
        schema = app_dir / "intake_schema.py"
        if not schema.is_file():
            continue
        if str(app_dir) not in sys.path:
            sys.path.insert(0, str(app_dir))
        spec = importlib.util.spec_from_file_location("intake_schema", schema)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # pragma: no cover
            pytest.skip(f"discoverbcs validator present but not importable: {exc!r}")
        return module
    return None


@pytest.fixture(scope="module")
def bvm_cards() -> list[dict]:
    html = (FIXTURES / "bvmuseum_home.html").read_text(encoding="utf-8")
    return bvmuseum.parse_upcoming_html(html)


@pytest.fixture(scope="module")
def bvm_raw(bvm_cards) -> list[RawEvent]:
    return [bvmuseum.to_raw(card, year=YEAR) for card in bvm_cards]


@pytest.fixture(scope="module")
def bvm_windowed(bvm_raw) -> list[RawEvent]:
    return [item for item in bvm_raw if bvmuseum.in_window(item, WINDOW_START, WINDOW_END)]


@pytest.fixture(scope="module")
def bvm_normalized(bvm_windowed):
    return bvmuseum.BvMuseumScraper().normalize(bvm_windowed)


def test_bvmuseum_parses_four_upcoming_cards_and_ignores_exhibits(bvm_cards):
    assert len(bvm_cards) == 4
    titles = [c["title"] for c in bvm_cards]
    assert titles == [
        "SUMMER NATURE CAMP",
        "12TH ANNUAL WISH UPON A BUTTERFLY",
        "BUFFALO STAMPEDE",
        "BOONVILLE DAYS",
    ]
    assert all("DISCOVERY ROOM" not in c["title"] for c in bvm_cards)


def test_bvmuseum_learn_more_is_the_permalink(bvm_cards):
    hrefs = [c["href"] for c in bvm_cards]
    assert hrefs[0] in {
        "https://www.brazosvalleymuseum.org",
        "https://www.brazosvalleymuseum.org/",
    }
    assert hrefs[1].endswith("/wish-upon-a-butterfly-2")
    assert hrefs[2].endswith("/buffalo-stampede")
    assert hrefs[3].endswith("/boonville-days")


def test_bvmuseum_homepage_link_uses_repeater_item_id(bvm_raw):
    camp = next(r for r in bvm_raw if r.record["title"] == "SUMMER NATURE CAMP")
    assert camp.series_uid == "j9pmjbx7"


def test_bvmuseum_dedicated_pages_use_slug(bvm_raw):
    slugs = {r.series_uid for r in bvm_raw}
    assert "wish-upon-a-butterfly-2" in slugs
    assert "buffalo-stampede" in slugs
    assert "boonville-days" in slugs


def test_bvmuseum_assumes_window_year_from_homepage_month_day(bvm_raw):
    by_title = {r.record["title"]: r for r in bvm_raw}
    assert by_title["SUMMER NATURE CAMP"].record["start_local"] == "2026-06-02T09:00:00"
    assert by_title["SUMMER NATURE CAMP"].record["end_local"] == "2026-08-08T15:00:00"
    assert by_title["12TH ANNUAL WISH UPON A BUTTERFLY"].record["start_local"] == ("2026-07-25T09:00:00")
    assert by_title["12TH ANNUAL WISH UPON A BUTTERFLY"].record["end_local"] == ("2026-07-25T12:00:00")
    assert by_title["BOONVILLE DAYS"].record["start_local"] == "2026-10-18T09:00:00"
    assert by_title["BOONVILLE DAYS"].record["end_local"] == "2026-10-18T16:00:00"


def test_bvmuseum_stampede_time_comes_from_card_body(bvm_raw):
    stampede = next(r for r in bvm_raw if r.record["title"] == "BUFFALO STAMPEDE")
    assert stampede.record["start_local"] == "2026-10-18T07:30:00"
    assert "end_local" not in stampede.record or stampede.record["end_local"] is None


def test_bvmuseum_year_follows_the_window_not_the_clock(bvm_cards):
    raw = [bvmuseum.to_raw(c, year=2027) for c in bvm_cards]
    boonville = next(r for r in raw if r.record["title"] == "BOONVILLE DAYS")
    assert boonville.record["start_local"] == "2027-10-18T09:00:00"


def test_bvmuseum_drops_cards_already_past_the_window(bvm_windowed):
    titles = {r.record["title"] for r in bvm_windowed}
    assert titles == {"BUFFALO STAMPEDE", "BOONVILLE DAYS"}


def test_bvmuseum_keeps_a_span_that_still_overlaps_the_window(bvm_raw):
    kept = [r for r in bvm_raw if bvmuseum.in_window(r, date(2026, 8, 1), date(2026, 8, 31))]
    titles = {r.record["title"] for r in kept}
    assert "SUMMER NATURE CAMP" in titles
    assert "BUFFALO STAMPEDE" not in titles


def test_bvmuseum_occurrence_tid_is_start_epoch_ms(bvm_normalized):
    payloads, _ = bvm_normalized
    for payload in payloads:
        tid = payload["occurrence"]["source_occurrence_tid"]
        assert tid.isdigit()
        start = payload["occurrence"]["start_local"]
        assert tid == bvmuseum.local_to_tid(start)


def test_bvmuseum_source_identity(bvm_normalized):
    payloads, _ = bvm_normalized
    assert payloads
    for payload in payloads:
        assert payload["source"]["slug"] == "brazos-valley-museum"
        assert payload["source"]["name"] == "Brazos Valley Museum of Natural History"
        assert payload["source"]["kind"] == "feed"
        assert payload["series"]["organization"]["slug"] == "brazos-valley-museum"


def test_bvmuseum_place_is_the_museum_in_bryan(bvm_normalized):
    payloads, _ = bvm_normalized
    for payload in payloads:
        place = payload["series"]["place"]
        assert place["name"] == "Brazos Valley Museum of Natural History"
        assert place["street"] == "3232 Briarcrest Dr"
        assert place["city"] == "Bryan"
        assert place["region"] == "TX"
        assert place["postcode"] == "77802"
        assert place["area"] == "bryan"


def test_bvmuseum_no_guessed_topics_or_audiences(bvm_normalized):
    payloads, _ = bvm_normalized
    for payload in payloads:
        assert payload["series"]["topics"] == []
        assert payload["series"]["audiences"] == []


def test_bvmuseum_boonville_is_free_stampede_is_not(bvm_normalized):
    payloads, _ = bvm_normalized
    by_title = {p["series"]["title"]: p for p in payloads}
    assert by_title["BOONVILLE DAYS"]["series"].get("is_free") is True
    assert "is_free" not in by_title["BUFFALO STAMPEDE"]["series"]


def test_bvmuseum_is_registered_and_not_in_default_kinds():
    assert "bvmuseum" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "bvmuseum"}))
    assert [s.kind for s in scrapers] == ["bvmuseum"]
    assert scrapers[0].source_slug == "brazos-valley-museum"


def test_bvmuseum_skip_network_fetches_nothing():
    assert bvmuseum.BvMuseumScraper().fetch(WINDOW_START, WINDOW_END, skip_network=True) == []


def test_bvmuseum_normalizing_twice_is_byte_identical(bvm_windowed):
    first, _ = bvmuseum.BvMuseumScraper().normalize(bvm_windowed)
    second, _ = bvmuseum.BvMuseumScraper().normalize(bvm_windowed)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


def test_conformance_bvmuseum_payloads_pass_the_real_validator(bvm_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = bvm_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
