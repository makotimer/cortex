"""Visit College Station event_watch tests.

Fixture is a captured Algolia ``sectionName:Events`` response
(``visitcstx_events.json``).
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
from modules.event_watch.lib.scrapers import visitcstx
from modules.event_watch.lib.scrapers.base import RawEvent

FIXTURES = Path(__file__).parent / "fixtures" / "event_watch"
SITE_APP_PATHS = (
    Path("/discoverbcs-app"),
    Path("/srv/docker/websites/discoverbcs/app"),
)


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
def visitcstx_raw() -> list[RawEvent]:
    hits = json.loads((FIXTURES / "visitcstx_events.json").read_text())["hits"]
    return [visitcstx.to_raw(h) for h in hits]


@pytest.fixture(scope="module")
def visitcstx_normalized(visitcstx_raw):
    return visitcstx.VisitCstxScraper().normalize(visitcstx_raw)


def test_visitcstx_fixture_shape(visitcstx_raw):
    assert len(visitcstx_raw) == 86
    assert len({r.series_uid for r in visitcstx_raw}) == 67


def test_visitcstx_farmers_market_is_one_series_many_saturdays(visitcstx_raw, visitcstx_normalized):
    market = [r for r in visitcstx_raw if r.record.get("title") == "Aggieland Farmers Market"]
    assert len(market) == 20
    assert len({r.series_uid for r in market}) == 1
    payloads, _ = visitcstx_normalized
    published = [p for p in payloads if p["series"]["title"] == "Aggieland Farmers Market"]
    assert len(published) == 20
    assert len({p["occurrence"]["source_occurrence_tid"] for p in published}) == 20


def test_visitcstx_source_identity(visitcstx_normalized):
    payloads, _ = visitcstx_normalized
    assert payloads
    for p in payloads:
        assert p["source"]["slug"] == "visitcstx"
        assert p["source"]["name"] == "Visit College Station"
        assert p["source"]["kind"] == "feed"
        assert p["series"]["organization"]["slug"] == "visitcstx"


def test_visitcstx_fake_utc_is_wall_clock_central(visitcstx_normalized):
    payloads, _ = visitcstx_normalized
    harvest = next(p for p in payloads if p["series"]["title"] == "Daytime Harvest Festival")
    assert harvest["occurrence"]["start_local"].startswith("2026-08-15T09:00")
    assert harvest["occurrence"]["timezone"] == "America/Chicago"
    assert harvest["series"]["place"]["city"] == "Bryan"
    assert harvest["series"]["place"]["area"] == "bryan"


def test_visitcstx_places_are_bryan_cs_or_nearby(visitcstx_normalized):
    payloads, _ = visitcstx_normalized
    cities = {p["series"]["place"]["city"] for p in payloads if "place" in p["series"]}
    assert "College Station" in cities
    assert "Bryan" in cities
    for p in payloads:
        if "place" not in p["series"]:
            continue
        place = p["series"]["place"]
        assert place["area"] in {"bryan", "college_station", "nearby"}
        assert place.get("region") == "TX"


def test_visitcstx_area_from_city():
    assert visitcstx.area_from_city("Bryan") == ("Bryan", "bryan")
    assert visitcstx.area_from_city("College Station") == ("College Station", "college_station")
    assert visitcstx.area_from_city("Wellborn") == ("Wellborn", "nearby")
    assert visitcstx.area_from_city("Houston") is None


def test_visitcstx_city_from_address_line_tolerates_spacing():
    assert visitcstx.city_from_address(["3232 Briarcrest Dr", "Bryan , Texas 77802"]) == "Bryan"
    assert visitcstx.city_from_address(["1500 Harvey Rd", "College Station, Tx 77840"]) == "College Station"


def test_visitcstx_drops_spans_longer_than_14_days():
    raw = visitcstx.to_raw({
        "id": 1,
        "title": "Long exhibit",
        "startDate": 1_000_000,
        "endDate": 1_000_000 + 20 * 86400,
        "address": ["Somewhere", "College Station, Texas 77840"],
        "eventCategories": ["Exhibits"],
    })
    payloads, rejected = visitcstx.VisitCstxScraper().normalize([raw])
    assert payloads == []
    assert rejected == []


def test_visitcstx_keeps_seven_day_spirit_week(visitcstx_normalized):
    payloads, _ = visitcstx_normalized
    titles = {p["series"]["title"] for p in payloads}
    assert any("Spirit of 150 Week" in t for t in titles)


def test_visitcstx_topics_and_audiences():
    assert visitcstx.topics_from_categories(["Arts & Culture"]) == ["arts"]
    assert visitcstx.topics_from_categories(["Live Music"]) == ["music"]
    assert visitcstx.topics_from_categories(["Texas A&M Sports", "Sports"]) == ["sports"]
    assert visitcstx.topics_from_categories(["Festivals", "Markets"]) == ["community"]
    assert visitcstx.audiences_from_categories(["Family Friendly"]) == ["all-ages"]
    assert visitcstx.audiences_from_categories(["Live Music"]) == []


def test_visitcstx_free_events_set_is_free(visitcstx_normalized):
    payloads, _ = visitcstx_normalized
    market = next(p for p in payloads if p["series"]["title"] == "Aggieland Farmers Market")
    assert market["series"].get("is_free") is True


def test_visitcstx_is_registered_and_not_in_default_kinds():
    assert "visitcstx" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "visitcstx"}))
    assert [s.kind for s in scrapers] == ["visitcstx"]
    assert scrapers[0].source_slug == "visitcstx"


def test_visitcstx_skip_network_fetches_nothing():
    assert visitcstx.VisitCstxScraper().fetch(
        date(2026, 8, 15), date(2027, 5, 12), skip_network=True) == []


def test_visitcstx_normalizing_twice_is_byte_identical(visitcstx_raw):
    first, _ = visitcstx.VisitCstxScraper().normalize(visitcstx_raw)
    second, _ = visitcstx.VisitCstxScraper().normalize(visitcstx_raw)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


def test_conformance_visitcstx_payloads_pass_the_real_validator(visitcstx_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = visitcstx_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
