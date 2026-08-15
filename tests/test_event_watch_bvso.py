"""Brazos Valley Symphony Orchestra event_watch tests."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from modules.event_watch.lib import engine, state
from modules.event_watch.lib.config import Settings
from modules.event_watch.lib.scrapers import bvso
from modules.event_watch.lib.scrapers.base import RawEvent

FIXTURES = Path(__file__).parent / "fixtures" / "event_watch"
SITE_APP_PATHS = (
    Path("/discoverbcs-app"),
    Path("/srv/docker/websites/discoverbcs/app"),
)

SHOW_FILES = {
    "beethovens-fifth": "bvso_show_beethovens-fifth.html",
    "a-holiday-concert": "bvso_show_a-holiday-concert.html",
    "the-nutcracker": "bvso_show_the-nutcracker.html",
    "bach-to-tchaikovsky": "bvso_show_bach-to-tchaikovsky.html",
    "season-release-party": "bvso_show_season-release-party.html",
}

SEASON_SLUGS = {
    "beethovens-fifth",
    "french-masters-and-bolero",
    "a-holiday-concert",
    "breakin-classical",
    "dvorak-to-john-williams",
    "star-wars-a-new-hope-in-concert",
    "scheherazade",
}


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


def _read(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def concerts():
    return bvso.parse_concerts(_read("bvso_concerts.html"))


@pytest.fixture(scope="module")
def tc_events():
    return bvso.parse_tc_events(_read("bvso_tc_events.json"))


def _raw_from_show(slug: str, *, season_years, tickets_url: str | None = None) -> list[RawEvent]:
    show = bvso.parse_show(_read(SHOW_FILES[slug]))
    title = show.get("title") or slug
    return bvso.to_raws(
        slug,
        title,
        show,
        season_years=season_years,
        tickets_url=tickets_url or f"https://bvso.org/tc-events/{slug}/",
    )


@pytest.fixture(scope="module")
def bvso_raw(concerts) -> list[RawEvent]:
    season_years = concerts["season_years"]
    raw: list[RawEvent] = []
    for slug in SHOW_FILES:
        years = season_years if slug in SEASON_SLUGS else None
        raw.extend(_raw_from_show(slug, season_years=years))
    return raw


@pytest.fixture(scope="module")
def bvso_normalized(bvso_raw):
    return bvso.BvsoScraper().normalize(bvso_raw)


def test_parse_concerts_has_season_years_and_seven_cards(concerts):
    assert concerts["season_years"] == (2026, 2027)
    slugs = [c["slug"] for c in concerts["cards"]]
    assert slugs == [
        "beethovens-fifth",
        "french-masters-and-bolero",
        "a-holiday-concert",
        "breakin-classical",
        "dvorak-to-john-williams",
        "star-wars-a-new-hope-in-concert",
        "scheherazade",
    ]
    first = concerts["cards"][0]
    assert first["day"] == 27
    assert first["month"] == 9
    assert first["show_url"] == "https://bvso.org/show-item/beethovens-fifth/"
    assert first["tickets_url"] == "https://bvso.org/tc-events/beethovens-fifth/"


def test_parse_tc_events_union_by_slug_is_not_a_second_catalog(concerts, tc_events):
    concert_slugs = {c["slug"] for c in concerts["cards"]}
    rest_slugs = {p["slug"] for p in tc_events}
    assert concert_slugs <= rest_slugs
    assert len(rest_slugs) == 19
    assert "the-nutcracker" in rest_slugs
    assert "bach-to-tchaikovsky" in rest_slugs


def test_year_from_season_splits_on_the_calendar_year():
    assert bvso.year_from_season(9, (2026, 2027)) == 2026
    assert bvso.year_from_season(12, (2026, 2027)) == 2026
    assert bvso.year_from_season(1, (2026, 2027)) == 2027
    assert bvso.year_from_season(4, (2026, 2027)) == 2027


def test_concert_starts_not_the_reception():
    show = bvso.parse_show(_read("bvso_show_beethovens-fifth.html"))
    assert show["concert_clock"] == (17, 0)
    assert show["venue_text"] and "Rudder Theatre" in show["venue_text"]


def test_beethoven_is_27_sep_2026_at_5pm(bvso_normalized):
    payloads, _ = bvso_normalized
    hit = next(p for p in payloads if p["series"]["source_series_uid"] == "beethovens-fifth")
    assert hit["occurrence"]["start_local"] == "2026-09-27T17:00:00"
    assert hit["occurrence"]["all_day"] is False
    assert hit["occurrence"]["timezone"] == "America/Chicago"


def test_scheherazade_is_april_2027_from_the_season_heading():
    raw = _raw_from_show("beethovens-fifth", season_years=(2026, 2027))
    # January–April season cards use the second year; pin it via a season card
    # we already have plus the helper, then a spring title from concerts.
    assert bvso.year_from_season(4, (2026, 2027)) == 2027
    show = bvso.parse_show(_read("bvso_show_beethovens-fifth.html"))
    show = {**show, "month": 4, "day": 25, "title": "Scheherazade"}
    items = bvso.to_raws("scheherazade", "Scheherazade", show, season_years=(2026, 2027))
    payloads, _ = bvso.BvsoScraper().normalize(items)
    assert payloads[0]["occurrence"]["start_local"] == "2027-04-25T17:00:00"
    assert raw  # season fixture still produces at least one occurrence


def test_holiday_concert_is_christ_church(bvso_normalized):
    payloads, _ = bvso_normalized
    hit = next(p for p in payloads if p["series"]["source_series_uid"] == "a-holiday-concert")
    place = hit["series"]["place"]
    assert place["slug"] == "christ-church-college-station"
    assert place["city"] == "College Station"
    assert place["area"] == "college_station"
    assert place["postcode"] == "77845"
    assert hit["occurrence"]["start_local"] == "2026-12-13T17:00:00"


def test_rudder_theatre_is_college_station(bvso_normalized):
    payloads, _ = bvso_normalized
    hit = next(p for p in payloads if p["series"]["source_series_uid"] == "beethovens-fifth")
    place = hit["series"]["place"]
    assert place["slug"] == "rudder-theatre"
    assert place["city"] == "College Station"
    assert place["area"] == "college_station"


def test_nutcracker_emits_one_occurrence_per_performance():
    items = _raw_from_show("the-nutcracker", season_years=None)
    assert len(items) == 3
    payloads, rejected = bvso.BvsoScraper().normalize(items)
    assert rejected == []
    starts = sorted(p["occurrence"]["start_local"] for p in payloads)
    assert starts == [
        "2025-12-05T19:00:00",
        "2025-12-06T14:00:00",
        "2025-12-06T18:30:00",
    ]
    assert {p["series"]["source_series_uid"] for p in payloads} == {"the-nutcracker"}
    assert payloads[0]["series"]["place"]["slug"] == "rudder-auditorium"


def test_leftover_without_a_year_is_dropped():
    items = _raw_from_show("bach-to-tchaikovsky", season_years=None)
    assert items == []
    items = _raw_from_show("season-release-party", season_years=None)
    assert items == []


def test_leftover_is_not_rolled_into_the_current_season():
    # Even if a caller mistakenly passes the concerts season years, leftover
    # pages without an explicit year still must not invent 2026-09-21.
    show = bvso.parse_show(_read("bvso_show_bach-to-tchaikovsky.html"))
    items = bvso.to_raws(
        "bach-to-tchaikovsky",
        "Bach to Tchaikovsky",
        show,
        season_years=None,
    )
    assert items == []


def test_unknown_venue_is_rejected_loudly():
    show = {
        "title": "Season Release Party",
        "month": 4,
        "day": 14,
        "concert_clock": (16, 30),
        "venue_text": "Benjamin Knox Gallery",
        "description": "Gallery reception.",
        "dated_starts": [{"date": date(2026, 4, 14), "clock": (16, 30)}],
    }
    items = bvso.to_raws(
        "season-release-party",
        "Season Release Party",
        show,
        season_years=None,
    )
    payloads, rejected = bvso.BvsoScraper().normalize(items)
    assert payloads == []
    assert rejected
    assert "venue" in rejected[0]["reason"].lower()


def test_source_identity_and_topics(bvso_normalized):
    payloads, _ = bvso_normalized
    season = [p for p in payloads if p["series"]["source_series_uid"] in SEASON_SLUGS]
    assert season
    for p in season:
        assert p["source"]["slug"] == "bvso"
        assert p["source"]["name"] == "Brazos Valley Symphony Orchestra"
        assert p["source"]["kind"] == "feed"
        assert p["series"]["organization"]["slug"] == "bvso"
        assert p["series"]["topics"] == ["music"]
        assert p["series"]["source_url"].startswith("https://bvso.org/show-item/")
        assert p["series"]["registration_url"].startswith("https://bvso.org/tc-events/")


def test_bvso_is_registered_and_not_in_default_kinds():
    assert "bvso" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "bvso"}))
    assert [s.kind for s in scrapers] == ["bvso"]
    assert scrapers[0].source_slug == "bvso"


def test_bvso_skip_network_fetches_nothing():
    assert bvso.BvsoScraper().fetch(
        date(2026, 8, 15), date(2027, 5, 12), skip_network=True) == []


def test_bvso_normalizing_twice_is_byte_identical(bvso_raw):
    first, _ = bvso.BvsoScraper().normalize(bvso_raw)
    second, _ = bvso.BvsoScraper().normalize(bvso_raw)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


def test_conformance_bvso_payloads_pass_the_real_validator(bvso_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = bvso_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
