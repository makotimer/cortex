"""Home Depot Kids Workshop tests.

US HTML is Akamai 403. Kit names come from the Canada workshops page
(same national kits, different Saturday). Dates are generated: first
Saturday of each month, 9:00–12:00 America/Chicago.

Canada fixture captured 2026-08-16 from
``https://www.homedepot.ca/en/home/ideas-how-to/workshops.html``.
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
from modules.event_watch.lib.scrapers import homedepot
from modules.event_watch.lib.scrapers.base import RawEvent

FIXTURES = Path(__file__).parent / "fixtures" / "event_watch"
SITE_APP_PATHS = (
    Path("/discoverbcs-app"),
    Path("/srv/docker/websites/discoverbcs/app"),
)
WINDOW_START = date(2026, 8, 16)
WINDOW_END = date(2027, 1, 1)


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
def canada_html() -> str:
    return (FIXTURES / "homedepot_ca_workshops.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def kit_by_month(canada_html) -> dict[tuple[int, int], str]:
    return homedepot.parse_kit_names(canada_html)


@pytest.fixture(scope="module")
def hd_raw(kit_by_month) -> list[RawEvent]:
    return homedepot.occurrences_for_window(WINDOW_START, WINDOW_END, kit_by_month)


@pytest.fixture(scope="module")
def hd_normalized(hd_raw):
    return homedepot.HomeDepotScraper().normalize(hd_raw)


def test_first_saturday_of_september_2026_is_the_fifth():
    assert homedepot.first_saturday(2026, 9) == date(2026, 9, 5)
    assert homedepot.first_saturday(2026, 8) == date(2026, 8, 1)
    assert homedepot.first_saturday(2026, 11) == date(2026, 11, 7)


def test_fallback_kits_match_the_canada_fixture(kit_by_month):
    for key, name in homedepot.FALLBACK_KITS.items():
        assert kit_by_month[key] == name


def test_canada_page_maps_month_to_kit_name(kit_by_month):
    assert kit_by_month[(2026, 9)] == "School Bus Organizer"
    assert kit_by_month[(2026, 10)] == "Witch Candy Box"
    assert kit_by_month[(2026, 11)] == "Dump Truck"
    assert kit_by_month[(2026, 12)] == "Holiday Train"
    # Two Canada kits in November; first (earliest date) wins.
    assert kit_by_month[(2026, 11)] != "Christmas Car Ornament"


def test_window_emits_first_saturdays_not_canada_dates(hd_raw):
    days = [r.record["day"] for r in hd_raw]
    assert date(2026, 9, 5) in days
    assert date(2026, 10, 3) in days
    assert date(2026, 11, 7) in days
    assert date(2026, 12, 5) in days
    assert date(2026, 8, 1) not in days  # before window
    assert date(2026, 9, 12) not in days  # Canada Saturday
    assert date(2027, 1, 2) not in days  # window end is exclusive Jan 1


def test_september_uses_canada_kit_name(hd_normalized):
    payloads, _ = hd_normalized
    sep = next(p for p in payloads if p["occurrence"]["start_local"].startswith("2026-09-05"))
    assert sep["series"]["title"] == "Kids Workshop: School Bus Organizer"
    assert sep["series"]["registration_url"] == "https://www.homedepot.com/c/kids-workshop"
    assert sep["series"]["source_url"] == "https://www.homedepot.com/c/kids-workshop"
    assert sep["occurrence"]["start_local"] == "2026-09-05T09:00:00"
    assert sep["occurrence"]["end_local"] == "2026-09-05T12:00:00"
    assert sep["occurrence"]["timezone"] == "America/Chicago"


def test_month_without_kit_name_is_generic():
    raw = homedepot.occurrences_for_window(
        date(2027, 1, 1), date(2027, 2, 1), {}
    )
    payloads, _ = homedepot.HomeDepotScraper().normalize(raw)
    assert len(payloads) == 1
    assert payloads[0]["series"]["title"] == "Kids Workshop"
    assert payloads[0]["occurrence"]["start_local"] == "2027-01-02T09:00:00"


def test_ages_and_place(hd_normalized):
    payloads, rejected = hd_normalized
    assert rejected == []
    assert payloads
    for p in payloads:
        assert p["source"]["slug"] == "homedepot"
        assert p["source"]["name"] == "The Home Depot"
        assert p["source"]["kind"] == "feed"
        assert p["series"]["organization"]["slug"] == "home-depot"
        assert p["series"]["is_free"] is True
        assert p["series"]["indoor"] is True
        assert p["series"]["topics"] == ["crafts"]
        assert p["series"]["age_min"] == 5
        assert p["series"]["age_max"] == 12
        assert "registration_required" not in p["series"]
        place = p["series"]["place"]
        assert place["slug"] == "home-depot-college-station"
        assert place["name"] == "College Station Home Depot"
        assert place["street"] == "1615 University Dr E"
        assert place["city"] == "College Station"
        assert place["postcode"] == "77840"
        assert place["area"] == "college_station"


def test_series_uid_is_year_month_and_tid_includes_store(hd_normalized):
    payloads, _ = hd_normalized
    sep = next(p for p in payloads if p["occurrence"]["start_local"].startswith("2026-09-05"))
    assert sep["series"]["source_series_uid"] == "kids-workshop-2026-09"
    assert sep["occurrence"]["source_occurrence_tid"] == "2026-09-05:6559"


def test_homedepot_is_registered_and_not_in_default_kinds():
    assert "homedepot" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "homedepot"}))
    assert [s.kind for s in scrapers] == ["homedepot"]
    assert scrapers[0].source_slug == "homedepot"


def test_homedepot_skip_network_fetches_nothing():
    assert homedepot.HomeDepotScraper().fetch(WINDOW_START, WINDOW_END, skip_network=True) == []


def test_homedepot_normalizing_twice_is_byte_identical(hd_raw):
    first, _ = homedepot.HomeDepotScraper().normalize(hd_raw)
    second, _ = homedepot.HomeDepotScraper().normalize(hd_raw)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


def test_conformance_homedepot_payloads_pass_the_real_validator(hd_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = hd_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
