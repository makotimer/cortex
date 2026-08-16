"""Lowe's Kids Club workshop tests.

Fixture captured 2026-08-16 from
``https://www.lowes.com/workshopdata?template=REGISTRATION&types=WORKSHOP&closed=false``.
The feed is national; only the College Station store is in scope.
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
from modules.event_watch.lib.scrapers import lowes
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
def workshopdata() -> dict:
    return json.loads((FIXTURES / "lowes_workshopdata.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def lowes_raw(workshopdata) -> list[RawEvent]:
    return [
        item
        for event in lowes.parse_workshopdata(workshopdata)
        if lowes.keep_kids_workshop(event)
        for item in lowes.to_raw_events(event)
        if lowes.in_window(item, WINDOW_START, WINDOW_END)
    ]


@pytest.fixture(scope="module")
def lowes_normalized(lowes_raw):
    return lowes.LowesScraper().normalize(lowes_raw)


def test_workshopdata_keeps_five_free_kids_workshops(workshopdata):
    kept = [e for e in lowes.parse_workshopdata(workshopdata) if lowes.keep_kids_workshop(e)]
    assert [e["url"] for e in kept] == [
        "haunted-house",
        "firefighting-plane",
        "holiday-engine",
        "holiday-trolley-car",
        "winter-play-lodge",
    ]


def test_workshopdata_drops_paid_and_no_location(workshopdata):
    all_urls = {e["url"] for e in lowes.parse_workshopdata(workshopdata)}
    assert "mrbeast-swarm-twister-workshop" in all_urls
    assert "senior-builder-redemption-portal" in all_urls
    kept = {e["url"] for e in lowes.parse_workshopdata(workshopdata) if lowes.keep_kids_workshop(e)}
    assert "mrbeast-swarm-twister-workshop" not in kept
    assert "senior-builder-redemption-portal" not in kept
    assert "test-d-v2-senior-builder-admin-only" not in kept


def test_eastern_z_becomes_ten_to_one_central():
    """start/end are Eastern wall-clock wearing a Z. Publish 10:00–13:00 Chicago."""
    event = {
        "id": "7047a9f8-7fc2-456d-add7-13a2e4d4daf6",
        "url": "haunted-house",
        "name": "Haunted House",
        "start": "2026-09-12T14:00:00.000Z",
        "end": "2026-09-12T17:00:59.000Z",
        "subType": "KIDS",
        "isPaid": False,
        "tags": [],
        "meta": {"en-US": {"tileContent": {"registrationPage": {"note": "Spooky."}}}},
    }
    items = lowes.to_raw_events(event)
    assert len(items) == 1
    payloads, rejected = lowes.LowesScraper().normalize(items)
    assert rejected == []
    occ = payloads[0]["occurrence"]
    assert occ["start_local"] == "2026-09-12T10:00:00"
    assert occ["end_local"] == "2026-09-12T13:00:00"
    assert occ["timezone"] == "America/Chicago"


def test_november_est_z_is_still_the_same_calendar_day():
    event = {
        "id": "b2c883f7-5e6b-4281-9d43-e4035c81009c",
        "url": "holiday-engine",
        "name": "Holiday Engine",
        "start": "2026-11-14T15:00:00.000Z",
        "end": "2026-11-14T18:00:59.000Z",
        "subType": "KIDS",
        "isPaid": False,
        "tags": [],
        "meta": {},
    }
    items = lowes.to_raw_events(event)
    payloads, _ = lowes.LowesScraper().normalize(items)
    assert payloads[0]["occurrence"]["start_local"] == "2026-11-14T10:00:00"


def test_haunted_house_title_and_registration_url(lowes_normalized):
    payloads, _ = lowes_normalized
    haunted = next(p for p in payloads if p["series"]["source_series_uid"].startswith("7047a9f8"))
    assert haunted["series"]["title"] == "Kids Workshop: Haunted House"
    assert haunted["series"]["registration_url"] == "https://www.lowes.com/events/register/haunted-house"
    assert haunted["series"]["source_url"] == "https://www.lowes.com/events/register/haunted-house"
    assert haunted["series"]["is_free"] is True
    assert haunted["series"]["registration_required"] is True
    assert haunted["series"]["indoor"] is True
    assert haunted["series"]["topics"] == ["crafts"]
    assert "age_min" not in haunted["series"]
    assert "Haunted House" in (haunted["series"].get("description") or "")


def test_place_is_college_station_lowes(lowes_normalized):
    payloads, rejected = lowes_normalized
    assert rejected == []
    assert len(payloads) == 5
    for p in payloads:
        assert p["source"]["slug"] == "lowes"
        assert p["source"]["name"] == "Lowe's"
        assert p["source"]["kind"] == "feed"
        assert p["series"]["organization"]["slug"] == "lowes"
        place = p["series"]["place"]
        assert place["slug"] == "lowes-college-station"
        assert place["name"] == "College Station Lowe's"
        assert place["street"] == "4451 State Highway 6 S"
        assert place["city"] == "College Station"
        assert place["postcode"] == "77845"
        assert place["area"] == "college_station"


def test_occurrence_tid_includes_store(lowes_normalized):
    payloads, _ = lowes_normalized
    haunted = next(p for p in payloads if "haunted-house" in p["series"]["registration_url"])
    assert haunted["occurrence"]["source_occurrence_tid"] == "7047a9f8-7fc2-456d-add7-13a2e4d4daf6:3032"
    assert haunted["series"]["source_series_uid"] == "7047a9f8-7fc2-456d-add7-13a2e4d4daf6"


def test_window_drops_events_before_start(workshopdata):
    raw = [
        item
        for event in lowes.parse_workshopdata(workshopdata)
        if lowes.keep_kids_workshop(event)
        for item in lowes.to_raw_events(event)
        if lowes.in_window(item, date(2026, 10, 1), date(2026, 11, 1))
    ]
    payloads, _ = lowes.LowesScraper().normalize(raw)
    urls = {p["series"]["registration_url"] for p in payloads}
    assert urls == {"https://www.lowes.com/events/register/firefighting-plane"}


def test_lowes_verify_url_is_not_the_bot_walled_api():
    # The VPN probe uses requests' default UA. workshopdata 403s that
    # and the engine then burns exits trying to "fix" a live tunnel.
    assert "workshopdata" not in lowes.LowesScraper.verify_url
    assert "lowes.com" not in lowes.LowesScraper.verify_url


def test_lowes_is_registered_and_not_in_default_kinds():
    assert "lowes" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "lowes"}))
    assert [s.kind for s in scrapers] == ["lowes"]
    assert scrapers[0].source_slug == "lowes"


def test_lowes_skip_network_fetches_nothing():
    assert lowes.LowesScraper().fetch(WINDOW_START, WINDOW_END, skip_network=True) == []


def test_lowes_normalizing_twice_is_byte_identical(lowes_raw):
    first, _ = lowes.LowesScraper().normalize(lowes_raw)
    second, _ = lowes.LowesScraper().normalize(lowes_raw)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


def test_conformance_lowes_payloads_pass_the_real_validator(lowes_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = lowes_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
