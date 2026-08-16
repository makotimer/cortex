"""REI College Station classes & events tests.

Fixtures captured 2026-08-16 from
``https://www.rei.com/events/p/us-tx-college-station``. The public list
is a 100-mile radius; only College Station REI (location id 214) is in
scope.
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
from modules.event_watch.lib.scrapers import rei
from modules.event_watch.lib.scrapers.base import RawEvent, ScraperError

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
def rei_html() -> str:
    return (FIXTURES / "rei_list.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def rei_search() -> dict:
    return json.loads((FIXTURES / "rei_search.json").read_text(encoding="utf-8"))["search"]


@pytest.fixture(scope="module")
def rei_raw(rei_html) -> list[RawEvent]:
    return [
        rei.to_raw(session)
        for session in rei.parse_list(rei_html)
        if rei.is_college_station(session)
    ]


@pytest.fixture(scope="module")
def rei_normalized(rei_raw):
    return rei.ReiScraper().normalize(rei_raw)


def test_rei_list_parses_twelve_courses(rei_html):
    sessions = rei.parse_list(rei_html)
    assert len({s["courseId"] for s in sessions}) == 12


def test_rei_keeps_only_college_station_sessions(rei_raw):
    assert len(rei_raw) == 6
    assert {r.record["location"]["id"] for r in rei_raw} == {"214"}
    assert {r.record["location"]["city"] for r in rei_raw} == {"College Station"}
    uids = {r.series_uid for r in rei_raw}
    assert uids == {"88665", "88660", "101874", "nplc", "90093", "32764"}
    assert "43586" not in uids  # Backpacking Basics is Austin / Houston
    assert "brds" not in uids  # Travis Audubon is Austin


def test_rei_search_json_matches_html_parse(rei_html, rei_search):
    from_html = rei.parse_list(rei_html)
    from_json = rei.parse_search(rei_search)
    assert [s["sessionId"] for s in from_html] == [s["sessionId"] for s in from_json]


def test_rei_missing_model_data_is_a_fetch_failure():
    with pytest.raises(ScraperError, match="modelData"):
        rei.parse_list((FIXTURES / "rei_list_empty.html").read_text(encoding="utf-8"))


def test_rei_bike_chains_is_wednesday_six_to_eight_central(rei_normalized):
    payloads, _ = rei_normalized
    bike = next(p for p in payloads if p["series"]["source_series_uid"] == "88660")
    assert bike["series"]["title"] == "Bike Chains and Derailleurs Workshop"
    assert bike["occurrence"]["start_local"] == "2026-08-19T18:00:00"
    assert bike["occurrence"]["end_local"] == "2026-08-19T20:00:00"
    assert bike["occurrence"]["timezone"] == "America/Chicago"
    assert bike["occurrence"]["all_day"] is False
    assert bike["occurrence"]["status"] == "scheduled"


def test_rei_does_not_believe_the_session_los_angeles_timezone(rei_normalized):
    """session.timeZone is America/Los_Angeles on every CS row; the store is Chicago."""
    payloads, _ = rei_normalized
    lands = next(p for p in payloads if p["series"]["source_series_uid"] == "nplc")
    assert lands["occurrence"]["start_local"] == "2026-09-19T11:00:00"
    assert lands["occurrence"]["end_local"] == "2026-09-19T15:00:00"


def test_rei_free_class_sets_is_free(rei_normalized):
    payloads, _ = rei_normalized
    running = next(p for p in payloads if p["series"]["source_series_uid"] == "101874")
    assert running["series"]["title"] == "Road Running Basics"
    assert running["series"]["is_free"] is True
    assert "cost_low_cents" not in running["series"]
    assert running["series"]["topics"] == ["sports"]
    assert running["series"]["indoor"] is True


def test_rei_paid_workshop_states_member_price(rei_normalized):
    payloads, _ = rei_normalized
    flat = next(p for p in payloads if p["series"]["source_series_uid"] == "88665")
    assert flat["series"]["title"] == "Flat Tire Repair Workshop"
    assert "is_free" not in flat["series"]
    assert flat["series"]["cost_low_cents"] == 2500
    assert flat["series"]["cost_note"] == "from $25"
    assert flat["series"]["topics"] == ["sports"]
    assert flat["series"]["registration_required"] is True


def test_rei_public_lands_day_is_outdoors_nature(rei_normalized):
    payloads, _ = rei_normalized
    lands = next(p for p in payloads if p["series"]["source_series_uid"] == "nplc")
    assert lands["series"]["title"] == "Celebrate National Public Lands Day at REI"
    assert lands["series"]["is_free"] is True
    assert lands["series"]["topics"] == ["nature", "outdoors"]
    assert "indoor" not in lands["series"]


def test_rei_camping_workshop_is_outdoors(rei_normalized):
    payloads, _ = rei_normalized
    camp = next(p for p in payloads if p["series"]["source_series_uid"] == "90093")
    assert camp["series"]["title"] == "Beginner's Camping Workshop"
    assert camp["series"]["topics"] == ["outdoors"]
    assert camp["series"]["cost_low_cents"] == 1500
    assert camp["occurrence"]["start_local"] == "2026-10-21T18:00:00"


def test_rei_place_and_source_identity(rei_normalized):
    payloads, rejected = rei_normalized
    assert rejected == []
    assert payloads
    for p in payloads:
        assert p["source"]["slug"] == "rei"
        assert p["source"]["name"] == "REI Co-op"
        assert p["source"]["kind"] == "feed"
        assert p["series"]["organization"]["slug"] == "rei"
        assert p["series"]["organization"]["name"] == "REI Co-op"
        place = p["series"]["place"]
        assert place["name"] == "College Station REI"
        assert place["street"] == "615 University Dr. E #300"
        assert place["city"] == "College Station"
        assert place["region"] == "TX"
        assert place["postcode"] == "77840"
        assert place["area"] == "college_station"
        assert p["series"]["audiences"] == ["all-ages"]
        assert p["series"]["source_url"].startswith("https://www.rei.com/events/")


def test_rei_series_uid_is_course_id_and_tid_is_start_ms(rei_normalized):
    payloads, _ = rei_normalized
    bike = next(p for p in payloads if p["series"]["source_series_uid"] == "88660")
    assert bike["occurrence"]["source_occurrence_tid"] == "1787180400000"


def test_rei_is_registered_and_not_in_default_kinds():
    assert "rei" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "rei"}))
    assert [s.kind for s in scrapers] == ["rei"]
    assert scrapers[0].source_slug == "rei"


def test_rei_skip_network_fetches_nothing():
    assert rei.ReiScraper().fetch(WINDOW_START, WINDOW_END, skip_network=True) == []


def test_rei_normalizing_twice_is_byte_identical(rei_raw):
    first, _ = rei.ReiScraper().normalize(rei_raw)
    second, _ = rei.ReiScraper().normalize(rei_raw)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


def test_conformance_rei_payloads_pass_the_real_validator(rei_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = rei_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
