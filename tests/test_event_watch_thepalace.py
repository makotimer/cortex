"""The Palace (Bryan Tx Entertainment) live-show tests.

Fixtures captured 2026-08-16 from
``/movie-theater/thepalace/movies`` and each show's JSON-LD.
This is concerts, not multiplex showtimes.
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
from modules.event_watch.lib.scrapers import thepalace
from modules.event_watch.lib.scrapers.base import RawEvent

FIXTURES = Path(__file__).parent / "fixtures" / "event_watch"
SITE_APP_PATHS = (
    Path("/discoverbcs-app"),
    Path("/srv/docker/websites/discoverbcs/app"),
)
WINDOW_START = date(2026, 8, 16)
WINDOW_END = date(2026, 12, 31)


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
def palace_slugs() -> list[str]:
    return thepalace.parse_list(
        (FIXTURES / "thepalace_movies.html").read_text(encoding="utf-8")
    )


@pytest.fixture(scope="module")
def palace_raw() -> list[RawEvent]:
    rows = json.loads((FIXTURES / "thepalace_events.json").read_text(encoding="utf-8"))
    return [thepalace.to_raw(thepalace.parse_detail_payload(row)) for row in rows]


@pytest.fixture(scope="module")
def palace_normalized(palace_raw):
    return thepalace.ThePalaceScraper().normalize(palace_raw)


def test_palace_list_finds_six_slugs(palace_slugs):
    assert palace_slugs == [
        "GUNNAR-LATHAM",
        "KYLE-GATES",
        "ONE-PERCENT-SKYNYRD-FEATURING-BURN",
        "ROGER-CREAGER",
        "PARKER-RYAN",
        "RAY-WYLIE-HUBBARD",
    ]


def test_palace_roger_creager_is_friday_seven(palace_normalized):
    payloads, _ = palace_normalized
    roger = next(p for p in payloads if p["series"]["source_series_uid"] == "ROGER-CREAGER")
    assert roger["series"]["title"] == "Roger Creager"
    assert roger["occurrence"]["start_local"] == "2026-09-18T19:00:00"
    assert roger["occurrence"]["end_local"] == "2026-09-18T22:00:00"
    assert roger["occurrence"]["timezone"] == "America/Chicago"
    assert roger["series"]["topics"] == ["music"]
    assert roger["series"]["audiences"] == ["adult"]
    assert roger["series"]["indoor"] is True
    assert roger["series"]["registration_required"] is True


def test_palace_gunnar_tid_is_start_ms(palace_normalized):
    payloads, _ = palace_normalized
    gunnar = next(p for p in payloads if p["series"]["source_series_uid"] == "GUNNAR-LATHAM")
    assert gunnar["series"]["title"] == "Gunnar Latham"
    assert gunnar["occurrence"]["start_local"] == "2026-08-21T19:00:00"
    assert gunnar["occurrence"]["source_occurrence_tid"] == "1787356800000"


def test_palace_place_and_source_identity(palace_normalized):
    payloads, rejected = palace_normalized
    assert rejected == []
    assert len(payloads) == 6
    for p in payloads:
        assert p["source"]["slug"] == "the-palace"
        assert p["source"]["name"] == "The Palace"
        assert p["series"]["organization"]["slug"] == "the-palace"
        place = p["series"]["place"]
        assert place["street"] == "105 South Main Street"
        assert place["city"] == "Bryan"
        assert place["postcode"] == "77803"
        assert place["area"] == "bryan"
        assert p["series"]["source_url"].startswith(
            "https://www.bryantxentertainment.com/movie/ThePalace/"
        )


def test_palace_is_registered_and_not_in_default_kinds():
    assert "thepalace" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(
        Settings.from_env_and_kwargs({"kinds": "thepalace"})
    )
    assert [s.kind for s in scrapers] == ["thepalace"]
    assert scrapers[0].source_slug == "the-palace"


def test_palace_skip_network_fetches_nothing():
    assert thepalace.ThePalaceScraper().fetch(
        WINDOW_START, WINDOW_END, skip_network=True
    ) == []


def test_palace_normalizing_twice_is_byte_identical(palace_raw):
    first, _ = thepalace.ThePalaceScraper().normalize(palace_raw)
    second, _ = thepalace.ThePalaceScraper().normalize(palace_raw)
    assert [state.payload_digest(p) for p in first] == [
        state.payload_digest(p) for p in second
    ]


def test_palace_weekly_page_lists_four_series():
    html = (FIXTURES / "thepalace_weekly.html").read_text(encoding="utf-8")
    assert thepalace.weekly_kinds(html) == {
        "first-friday",
        "gameday",
        "open-mic",
        "maroon-white",
    }


def test_palace_weekly_expands_dated_occurrences():
    html = (FIXTURES / "thepalace_weekly.html").read_text(encoding="utf-8")
    raw = thepalace.expand_weekly(html, WINDOW_START, WINDOW_END)
    by = {}
    for item in raw:
        by.setdefault(item.series_uid, []).append(item)
    assert set(by) == {"first-friday", "gameday", "open-mic", "maroon-white"}

    first_fridays = [i.record["start"].date() for i in by["first-friday"]]
    assert first_fridays == [
        date(2026, 9, 4), date(2026, 10, 2), date(2026, 11, 6), date(2026, 12, 4),
    ]
    assert by["first-friday"][0].record["start"].hour == 18

    third = [i.record["start"].date() for i in by["maroon-white"]]
    assert date(2026, 8, 21) in third
    assert date(2026, 9, 18) in third
    assert by["maroon-white"][0].record["start"].hour == 18

    mics = by["open-mic"]
    assert mics[0].record["start"].date() == date(2026, 8, 19)
    assert mics[0].record["start"].hour == 19
    assert mics[0].record["end"].hour == 22
    assert all(i.record["start"].weekday() == 2 for i in mics)

    games = by["gameday"]
    days = {i.record["start"].date() for i in games}
    assert date(2026, 8, 22) not in days
    assert date(2026, 8, 23) not in days
    assert date(2026, 8, 29) in days
    assert date(2026, 9, 13) in days
    assert all(i.record["start"].hour == 11 for i in games)


def test_palace_weekly_normalize_marks_free_and_topics():
    html = (FIXTURES / "thepalace_weekly.html").read_text(encoding="utf-8")
    raw = thepalace.expand_weekly(html, WINDOW_START, WINDOW_END)
    payloads, rejected = thepalace.ThePalaceScraper().normalize(raw)
    assert rejected == []
    by_uid = {p["series"]["source_series_uid"]: p for p in payloads}
    assert by_uid["first-friday"]["series"]["title"] == "First Fridays at The Palace"
    assert by_uid["first-friday"]["series"]["is_free"] is True
    assert by_uid["first-friday"]["series"]["audiences"] == ["all-ages"]
    assert by_uid["first-friday"]["series"]["topics"] == ["music"]
    assert by_uid["open-mic"]["series"]["is_free"] is True
    assert by_uid["open-mic"]["series"]["topics"] == ["music"]
    assert by_uid["gameday"]["series"]["title"] == "Gameday on Main"
    assert by_uid["gameday"]["series"]["topics"] == ["sports"]
    assert "is_free" not in by_uid["gameday"]["series"]
    assert by_uid["maroon-white"]["series"]["topics"] == ["community"]
    assert payloads[0]["series"]["source_url"].endswith("/weekly-events")


def test_conformance_palace_weekly_payloads_pass_the_real_validator():
    html = (FIXTURES / "thepalace_weekly.html").read_text(encoding="utf-8")
    raw = thepalace.expand_weekly(html, WINDOW_START, WINDOW_END)
    payloads, _ = thepalace.ThePalaceScraper().normalize(raw)
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))


def test_conformance_palace_payloads_pass_the_real_validator(palace_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = palace_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
