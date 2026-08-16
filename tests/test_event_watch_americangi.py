"""Museum of the American G.I. The Events Calendar tests.

Fixture captured 2026-08-16 from
``https://americangimuseum.org/wp-json/tribe/events/v1/events``.
The public /event/ photo grid is a TEC Pro view; dates live on the REST feed.
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
from modules.event_watch.lib.scrapers import americangi
from modules.event_watch.lib.scrapers.base import RawEvent

FIXTURES = Path(__file__).parent / "fixtures" / "event_watch"
SITE_APP_PATHS = (
    Path("/discoverbcs-app"),
    Path("/srv/docker/websites/discoverbcs/app"),
)
WINDOW_START = date(2026, 8, 16)
WINDOW_END = date(2027, 5, 13)


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
def tribe_payload() -> dict:
    return json.loads((FIXTURES / "americangi_events.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def agi_raw(tribe_payload) -> list[RawEvent]:
    return [americangi.to_raw(ev) for ev in americangi.parse_events(tribe_payload)]


@pytest.fixture(scope="module")
def agi_normalized(agi_raw):
    return americangi.AmericanGiScraper().normalize(agi_raw)


def test_fixture_has_the_six_photo_view_events(agi_raw):
    slugs = [r.series_uid for r in agi_raw]
    assert slugs == [
        "tank-or-treat-2026",
        "history-in-motion-2026",
        "crafts-with-mrs-claus-2026",
        "paws-with-mrs-claus-2026",
        "living-history-school-day-2027",
        "living-history-weekend-2027",
    ]


def test_tank_or_treat_is_one_to_three_on_october_25(agi_normalized):
    payloads, _ = agi_normalized
    tank = next(p for p in payloads if p["series"]["source_series_uid"] == "tank-or-treat-2026")
    assert tank["series"]["title"] == "Tank or Treat 2026"
    assert tank["occurrence"]["start_local"] == "2026-10-25T13:00:00"
    assert tank["occurrence"]["end_local"] == "2026-10-25T15:00:00"
    assert tank["occurrence"]["timezone"] == "America/Chicago"
    assert tank["occurrence"]["all_day"] is False
    assert tank["series"]["source_url"] == "https://americangimuseum.org/events/tank-or-treat-2026/"


def test_history_in_motion_spans_two_days(agi_normalized):
    payloads, _ = agi_normalized
    him = next(p for p in payloads if p["series"]["source_series_uid"] == "history-in-motion-2026")
    assert him["occurrence"]["start_local"] == "2026-11-07T10:00:00"
    assert him["occurrence"]["end_local"] == "2026-11-08T15:00:00"


def test_living_history_school_day_is_a_field_trip(agi_normalized):
    payloads, _ = agi_normalized
    day = next(p for p in payloads if "school-day" in p["series"]["source_series_uid"])
    assert day["series"]["field_trip"] is True
    assert day["occurrence"]["start_local"] == "2027-04-02T09:30:00"
    assert day["occurrence"]["end_local"] == "2027-04-02T15:30:00"


def test_crafts_gets_crafts_topic_others_are_history(agi_normalized):
    payloads, _ = agi_normalized
    by_uid = {p["series"]["source_series_uid"]: p["series"]["topics"] for p in payloads}
    assert by_uid["crafts-with-mrs-claus-2026"] == ["crafts", "history"]
    assert by_uid["tank-or-treat-2026"] == ["community", "history"]
    assert by_uid["history-in-motion-2026"] == ["history"]
    assert by_uid["living-history-weekend-2027"] == ["history"]


def test_place_is_the_museum(agi_normalized):
    payloads, rejected = agi_normalized
    assert rejected == []
    assert len(payloads) == 6
    for p in payloads:
        assert p["source"]["slug"] == "american-gi-museum"
        assert p["source"]["name"] == "Museum of the American G.I."
        assert p["source"]["kind"] == "feed"
        place = p["series"]["place"]
        assert place["slug"] == "museum-of-the-american-gi"
        assert place["name"] == "Museum of the American G.I."
        assert place["street"] == "19124 Highway 6 South"
        assert place["city"] == "College Station"
        assert place["postcode"] == "77845"
        assert place["area"] == "college_station"
        assert p["series"]["audiences"] == ["all-ages"]
        assert "is_free" not in p["series"]  # cost is blank, do not invent


def test_occurrence_tid_is_start_local(agi_normalized):
    payloads, _ = agi_normalized
    tank = next(p for p in payloads if p["series"]["source_series_uid"] == "tank-or-treat-2026")
    assert tank["occurrence"]["source_occurrence_tid"] == "2026-10-25T13:00:00"


def test_americangi_is_registered_and_not_in_default_kinds():
    assert "americangi" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "americangi"}))
    assert [s.kind for s in scrapers] == ["americangi"]
    assert scrapers[0].source_slug == "american-gi-museum"


def test_americangi_skip_network_fetches_nothing():
    assert americangi.AmericanGiScraper().fetch(WINDOW_START, WINDOW_END, skip_network=True) == []


def test_americangi_normalizing_twice_is_byte_identical(agi_raw):
    first, _ = americangi.AmericanGiScraper().normalize(agi_raw)
    second, _ = americangi.AmericanGiScraper().normalize(agi_raw)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


def test_conformance_americangi_payloads_pass_the_real_validator(agi_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = agi_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
