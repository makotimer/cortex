"""Hyperbole Bookstore Bookmanager event_watch tests."""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

from modules.event_watch.lib import engine, state
from modules.event_watch.lib.config import Settings
from modules.event_watch.lib.scrapers import hyperbole
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
def hyperbole_rows() -> list[dict]:
    payload = json.loads((FIXTURES / "hyperbole_events.json").read_text(encoding="utf-8"))
    return payload["rows"]


@pytest.fixture(scope="module")
def hyperbole_raw(hyperbole_rows) -> list[RawEvent]:
    return [hyperbole.to_raw(row) for row in hyperbole_rows]


@pytest.fixture(scope="module")
def hyperbole_normalized(hyperbole_raw):
    return hyperbole.HyperboleScraper().normalize(hyperbole_raw)


def test_hyperbole_fixture_shape(hyperbole_raw):
    assert len(hyperbole_raw) == 35
    titles = {r.record["title"] for r in hyperbole_raw}
    assert "Children's Storytime" in titles
    assert "Annie Hartnett Author Event" in titles
    assert "Thriller Book Club" in titles


def test_storytime_is_one_series_many_saturdays(hyperbole_raw, hyperbole_normalized):
    story = [r for r in hyperbole_raw if r.series_uid == "childrens-storytime"]
    assert len(story) == 21
    payloads, _ = hyperbole_normalized
    story_p = [p for p in payloads if p["series"]["source_series_uid"] == "childrens-storytime"]
    assert len(story_p) == 21
    tids = {p["occurrence"]["source_occurrence_tid"] for p in story_p}
    assert "50500" in tids
    assert len(tids) == 21


def test_storytime_is_1030_not_1230_central(hyperbole_normalized):
    payloads, _ = hyperbole_normalized
    first = next(
        p for p in payloads
        if p["occurrence"]["source_occurrence_tid"] == "50500"
    )
    assert first["occurrence"]["start_local"] == "2026-08-15T10:30:00"
    assert first["occurrence"]["end_local"] == "2026-08-15T11:00:00"
    assert first["occurrence"]["timezone"] == "America/Chicago"
    assert first["occurrence"]["all_day"] is False


def test_wall_clock_is_los_angeles_display():
    # 1786815027 is 10:30:27 PDT; we publish the minute the site shows.
    start = hyperbole.wall_clock(1786815027)
    assert start == datetime(2026, 8, 15, 10, 30)
    end = hyperbole.wall_clock(1786816800)
    assert end == datetime(2026, 8, 15, 11, 0)


def test_source_identity(hyperbole_normalized):
    payloads, _ = hyperbole_normalized
    assert payloads
    for p in payloads:
        assert p["source"]["slug"] == "hyperbole"
        assert p["source"]["name"] == "Hyperbole Bookstore"
        assert p["source"]["kind"] == "feed"
        assert p["series"]["organization"]["slug"] == "hyperbole"
        assert p["series"]["topics"] == ["reading"]


def test_place_is_the_bookstore(hyperbole_normalized):
    payloads, _ = hyperbole_normalized
    for p in payloads:
        place = p["series"]["place"]
        assert place["slug"] == "hyperbole-bookstore"
        assert place["city"] == "College Station"
        assert place["area"] == "college_station"
        assert place["postcode"] == "77845"
        assert place["street"].startswith("1275 Arrington")


def test_storytime_is_free_and_all_ages(hyperbole_normalized):
    payloads, _ = hyperbole_normalized
    story = next(p for p in payloads if p["series"]["source_series_uid"] == "childrens-storytime")
    assert story["series"]["is_free"] is True
    assert "all-ages" in story["series"]["audiences"]


def test_author_event_does_not_guess_free_or_audience(hyperbole_normalized):
    payloads, _ = hyperbole_normalized
    hit = next(p for p in payloads if "Hartnett" in p["series"]["title"])
    assert "is_free" not in hit["series"]
    assert hit["series"]["audiences"] == []
    assert hit["series"]["source_url"] == "https://hyperbolebookstore.com/events/50518"


def test_description_strips_html(hyperbole_normalized):
    payloads, _ = hyperbole_normalized
    story = next(
        p for p in payloads
        if p["occurrence"]["source_occurrence_tid"] == "50500"
    )
    desc = story["series"]["description"]
    assert "<p>" not in desc
    assert "octopuses" in desc


def test_hyperbole_is_registered_and_not_in_default_kinds():
    assert "hyperbole" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "hyperbole"}))
    assert [s.kind for s in scrapers] == ["hyperbole"]
    assert scrapers[0].source_slug == "hyperbole"


def test_hyperbole_skip_network_fetches_nothing():
    assert hyperbole.HyperboleScraper().fetch(
        date(2026, 8, 15), date(2027, 5, 12), skip_network=True) == []


def test_hyperbole_normalizing_twice_is_byte_identical(hyperbole_raw):
    first, _ = hyperbole.HyperboleScraper().normalize(hyperbole_raw)
    second, _ = hyperbole.HyperboleScraper().normalize(hyperbole_raw)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


def test_conformance_hyperbole_payloads_pass_the_real_validator(hyperbole_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = hyperbole_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
