"""Bush 41 Library upcoming-events tests."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path

import pytest

from modules.event_watch.lib import engine, state
from modules.event_watch.lib.config import Settings
from modules.event_watch.lib.scrapers import bush41
from modules.event_watch.lib.scrapers.base import RawEvent

FIXTURES = Path(__file__).parent / "fixtures" / "event_watch"
SITE_APP_PATHS = (
    Path("/discoverbcs-app"),
    Path("/srv/docker/websites/discoverbcs/app"),
)

DETAILS = {
    "250-years-courage-lessons-911-conversation-honorable-brian-birdwell":
        "bush41_250-years-courage-lessons-911-conversation-honorable-brian-birdwell.html",
    "national-treasure-how-declaration-independence-made-america":
        "bush41_national-treasure-how-declaration-independence-made-america.html",
    "scale-41-model-making-workshop":
        "bush41_scale-41-model-making-workshop.html",
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


@pytest.fixture(scope="module")
def bush41_raw() -> list[RawEvent]:
    cards = bush41.parse_upcoming(
        (FIXTURES / "bush41_upcoming.html").read_text(encoding="utf-8")
    )
    raw = []
    for card in cards:
        item = bush41.to_raw(card)
        fname = DETAILS.get(card["slug"])
        if fname:
            item.supplement["detail"] = bush41.parse_detail(
                (FIXTURES / fname).read_text(encoding="utf-8")
            )
        raw.append(item)
    return raw


@pytest.fixture(scope="module")
def bush41_normalized(bush41_raw):
    return bush41.Bush41Scraper().normalize(bush41_raw)


def test_bush41_fixture_shape(bush41_raw):
    assert len(bush41_raw) == 3
    slugs = {r.series_uid for r in bush41_raw}
    assert "250-years-courage-lessons-911-conversation-honorable-brian-birdwell" in slugs
    assert "scale-41-model-making-workshop" in slugs


def test_bush41_source_identity(bush41_normalized):
    payloads, _ = bush41_normalized
    assert len(payloads) == 3
    for p in payloads:
        assert p["source"]["slug"] == "bush41"
        assert p["source"]["name"] == "George H.W. Bush Presidential Library and Museum"
        assert p["source"]["kind"] == "feed"
        assert p["series"]["organization"]["slug"] == "bush41"


def test_bush41_birdwell_uses_10am_from_the_body(bush41_normalized):
    payloads, _ = bush41_normalized
    hit = next(p for p in payloads if "Birdwell" in p["series"]["title"])
    assert hit["occurrence"]["start_local"] == "2026-09-08T10:00:00"
    assert hit["occurrence"]["all_day"] is False
    assert hit["occurrence"]["timezone"] == "America/Chicago"


def test_bush41_events_without_a_clock_are_all_day(bush41_normalized):
    payloads, _ = bush41_normalized
    treasure = next(p for p in payloads if "National Treasure" in p["series"]["title"])
    workshop = next(p for p in payloads if "Scale 41" in p["series"]["title"])
    assert treasure["occurrence"]["start_local"].startswith("2026-10-22T00:00")
    assert treasure["occurrence"]["all_day"] is True
    assert workshop["occurrence"]["start_local"].startswith("2026-11-07T00:00")
    assert workshop["occurrence"]["all_day"] is True


def test_bush41_parse_clock():
    assert bush41.parse_clock("Join us on Tuesday, September 8, at 10 a.m.") == (10, 0)
    assert bush41.parse_clock("The program begins at 2 p.m. in the theater.") == (14, 0)
    assert bush41.parse_clock("Doors at 6:30 p.m.") == (18, 30)
    assert bush41.parse_clock("No time stated here.") is None


def test_bush41_place_is_the_library(bush41_normalized):
    payloads, _ = bush41_normalized
    for p in payloads:
        place = p["series"]["place"]
        assert place["city"] == "College Station"
        assert place["area"] == "college_station"
        assert place["postcode"] == "77845"
        assert "Bush" in place["name"]


def test_bush41_registration_required(bush41_normalized):
    payloads, _ = bush41_normalized
    assert all(p["series"].get("registration_required") is True for p in payloads)


def test_bush41_topics_are_history(bush41_normalized):
    payloads, _ = bush41_normalized
    for p in payloads:
        assert p["series"]["topics"] == ["history"]


def test_bush41_workshop_for_veterans_and_families_is_all_ages(bush41_normalized):
    payloads, _ = bush41_normalized
    workshop = next(p for p in payloads if "Scale 41" in p["series"]["title"])
    assert "all-ages" in workshop["series"]["audiences"]


def test_bush41_is_registered_and_not_in_default_kinds():
    assert "bush41" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "bush41"}))
    assert [s.kind for s in scrapers] == ["bush41"]
    assert scrapers[0].source_slug == "bush41"


def test_bush41_skip_network_fetches_nothing():
    assert bush41.Bush41Scraper().fetch(
        date(2026, 8, 15), date(2027, 5, 12), skip_network=True) == []


def test_bush41_normalizing_twice_is_byte_identical(bush41_raw):
    first, _ = bush41.Bush41Scraper().normalize(bush41_raw)
    second, _ = bush41.Bush41Scraper().normalize(bush41_raw)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


def test_conformance_bush41_payloads_pass_the_real_validator(bush41_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = bush41_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
