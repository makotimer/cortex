"""Sew Much Class Rain POS calendar tests.

Fixture is a trimmed month grid captured 2026-08-16 from
``/module/events.htm?pageComponentId=5886934``. PHD open-sew,
4-packs, closed notices and training are out of scope; named
classes and trips stay.
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
from modules.event_watch.lib.scrapers import sewmuchclass
from modules.event_watch.lib.scrapers.base import RawEvent

FIXTURES = Path(__file__).parent / "fixtures" / "event_watch"
SITE_APP_PATHS = (
    Path("/discoverbcs-app"),
    Path("/srv/docker/websites/discoverbcs/app"),
)
WINDOW_START = date(2026, 8, 1)
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
def smc_cards() -> list[dict]:
    return sewmuchclass.parse_calendar(
        (FIXTURES / "sewmuchclass_calendar.html").read_text(encoding="utf-8")
    )


@pytest.fixture(scope="module")
def smc_raw(smc_cards) -> list[RawEvent]:
    return [
        sewmuchclass.to_raw(card)
        for card in smc_cards
        if not sewmuchclass.is_dropped(card["title"])
    ]


@pytest.fixture(scope="module")
def smc_normalized(smc_raw):
    return sewmuchclass.SewMuchClassScraper().normalize(smc_raw)


def test_smc_parses_eleven_cards(smc_cards):
    assert len(smc_cards) == 11
    titles = [c["title"] for c in smc_cards]
    assert "PHD Monthly 4-PACK" in titles
    assert "Sashiko Basics - In Studio" in titles


def test_smc_drops_phd_closed_and_training(smc_cards):
    kept = [c["title"] for c in smc_cards if not sewmuchclass.is_dropped(c["title"])]
    assert kept == [
        "Sewing Fun-damentals: In Studio",
        "Bird Song Mini Quilt: In Studio",
        "Sashiko Basics - In Studio",
        "Summer Wedding: In Studio",
        "Finish Your ByAnnie Bags",
        "2026 International Quilt Festival - Houston BUS TRIP",
    ]


def test_smc_sashiko_is_930_central(smc_normalized):
    payloads, _ = smc_normalized
    sashiko = next(p for p in payloads if p["series"]["source_series_uid"] == "4558194")
    assert sashiko["series"]["title"] == "Sashiko Basics - In Studio"
    assert sashiko["occurrence"]["start_local"] == "2026-08-15T09:30:00"
    assert "end_local" not in sashiko["occurrence"]
    assert sashiko["occurrence"]["timezone"] == "America/Chicago"
    assert sashiko["series"]["topics"] == ["crafts"]
    assert sashiko["series"]["audiences"] == ["adult"]
    assert sashiko["occurrence"]["source_occurrence_tid"] == "1786804200000"


def test_smc_bus_trip_is_a_field_trip(smc_normalized):
    payloads, _ = smc_normalized
    trip = next(p for p in payloads if p["series"]["source_series_uid"] == "4487748")
    assert trip["series"]["title"] == "2026 International Quilt Festival - Houston BUS TRIP"
    assert trip["occurrence"]["start_local"] == "2026-11-13T07:30:00"
    assert trip["series"]["field_trip"] is True
    assert trip["series"]["topics"] == ["crafts"]


def test_smc_place_and_source_identity(smc_normalized):
    payloads, rejected = smc_normalized
    assert rejected == []
    assert len(payloads) == 6
    for p in payloads:
        assert p["source"]["slug"] == "sewmuchclass"
        assert p["source"]["name"] == "Sew Much Class"
        assert p["series"]["organization"]["slug"] == "sewmuchclass"
        place = p["series"]["place"]
        assert place["street"] == "2700 Earl Rudder Freeway S Suite 1100"
        assert place["city"] == "College Station"
        assert place["postcode"] == "77845"
        assert place["area"] == "college_station"
        assert p["series"]["indoor"] is True
        assert p["series"]["registration_required"] is True


def test_smc_is_registered_and_not_in_default_kinds():
    assert "sewmuchclass" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(
        Settings.from_env_and_kwargs({"kinds": "sewmuchclass"})
    )
    assert [s.kind for s in scrapers] == ["sewmuchclass"]
    assert scrapers[0].source_slug == "sewmuchclass"


def test_smc_skip_network_fetches_nothing():
    assert sewmuchclass.SewMuchClassScraper().fetch(
        WINDOW_START, WINDOW_END, skip_network=True
    ) == []


def test_smc_normalizing_twice_is_byte_identical(smc_raw):
    first, _ = sewmuchclass.SewMuchClassScraper().normalize(smc_raw)
    second, _ = sewmuchclass.SewMuchClassScraper().normalize(smc_raw)
    assert [state.payload_digest(p) for p in first] == [
        state.payload_digest(p) for p in second
    ]


def test_conformance_smc_payloads_pass_the_real_validator(smc_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = smc_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
