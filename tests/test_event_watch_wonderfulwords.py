"""Wonderful Words Bookshoppe Wix Events tests.

Fixtures captured 2026-08-16 from
``GET /_api/wix-events-web/v1/events`` (instance from ``/_api/v2/dynamicmodel``).
The public ``/event-list`` page is an events-viewer shell; cards are CSR.
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
from modules.event_watch.lib.scrapers import wonderfulwords
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
def ww_payload() -> dict:
    return json.loads((FIXTURES / "wonderfulwords_events.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def ww_raw(ww_payload) -> list[RawEvent]:
    return [
        wonderfulwords.to_raw(event)
        for event in wonderfulwords.parse_events(ww_payload)
        if wonderfulwords.is_scheduled(event)
    ]


@pytest.fixture(scope="module")
def ww_normalized(ww_raw):
    return wonderfulwords.WonderfulWordsScraper().normalize(ww_raw)


def test_ww_fixture_has_ninety_five_scheduled(ww_raw):
    assert len(ww_raw) == 95
    titles = {r.record["title"] for r in ww_raw}
    assert titles == {
        "Storytime",
        "Special Storytime!",
        "Wonderful Words Bookclub",
        "First Friday - Open Late!",
    }


def test_ww_series_uid_strips_the_occurrence_clock(ww_raw):
    story = [r for r in ww_raw if r.record["title"] == "Storytime"]
    assert len(story) == 75
    assert {r.series_uid for r in story} == {"storytime"}
    special = [r for r in ww_raw if r.record["title"] == "Special Storytime!"]
    assert {r.series_uid for r in special} == {"special-storytime"}
    assert {r.occurrence_tid for r in special} == {"1787067000000", "1787671800000"}


def test_ww_canceled_rows_are_not_scheduled(ww_payload):
    canceled = ww_payload["canceled_sample"]
    assert canceled
    assert wonderfulwords.is_scheduled(canceled[0]) is False


def test_ww_missing_events_list_is_a_fetch_failure():
    with pytest.raises(ScraperError, match="events"):
        wonderfulwords.parse_events({"total": 0})


def test_ww_special_storytime_is_1030_central(ww_normalized):
    payloads, _ = ww_normalized
    first = next(
        p for p in payloads
        if p["occurrence"]["source_occurrence_tid"] == "1787067000000"
    )
    assert first["series"]["title"] == "Special Storytime!"
    assert first["occurrence"]["start_local"] == "2026-08-18T10:30:00"
    assert first["occurrence"]["end_local"] == "2026-08-18T11:30:00"
    assert first["occurrence"]["timezone"] == "America/Chicago"
    assert first["occurrence"]["all_day"] is False
    assert first["series"]["topics"] == ["reading"]
    assert first["series"]["audiences"] == ["all-ages"]
    assert first["series"]["indoor"] is True
    assert first["series"]["source_url"].endswith(
        "/event-details/special-storytime-2026-08-18-10-30"
    )


def test_ww_bookclub_is_evening_and_adult(ww_normalized):
    payloads, _ = ww_normalized
    club = next(
        p for p in payloads
        if p["occurrence"]["source_occurrence_tid"] == "1787268600000"
    )
    assert club["series"]["title"] == "Wonderful Words Bookclub"
    assert club["occurrence"]["start_local"] == "2026-08-20T18:30:00"
    assert club["occurrence"]["end_local"] == "2026-08-20T20:00:00"
    assert club["series"]["topics"] == ["reading"]
    assert club["series"]["audiences"] == ["adult"]


def test_ww_first_friday_is_community(ww_normalized):
    payloads, _ = ww_normalized
    friday = next(
        p for p in payloads
        if p["occurrence"]["source_occurrence_tid"] == "1788562800000"
    )
    assert friday["series"]["title"] == "First Friday - Open Late!"
    assert friday["occurrence"]["start_local"] == "2026-09-04T18:00:00"
    assert friday["occurrence"]["end_local"] == "2026-09-04T21:00:00"
    assert friday["series"]["topics"] == ["community"]
    assert friday["series"]["audiences"] == ["all-ages"]


def test_ww_place_and_source_identity(ww_normalized):
    payloads, rejected = ww_normalized
    assert rejected == []
    assert payloads
    for p in payloads:
        assert p["source"]["slug"] == "wonderful-words"
        assert p["source"]["name"] == "Wonderful Words Bookshoppe"
        assert p["source"]["kind"] == "feed"
        assert p["series"]["organization"]["slug"] == "wonderful-words"
        place = p["series"]["place"]
        assert place["name"] == "Wonderful Words Bookshoppe"
        assert place["street"] == "210 W 26th St"
        assert place["city"] == "Bryan"
        assert place["region"] == "TX"
        assert place["postcode"] == "77803"
        assert place["area"] == "bryan"
        assert "is_free" not in p["series"]


def test_ww_is_registered_and_not_in_default_kinds():
    assert "wonderfulwords" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(
        Settings.from_env_and_kwargs({"kinds": "wonderfulwords"})
    )
    assert [s.kind for s in scrapers] == ["wonderfulwords"]
    assert scrapers[0].source_slug == "wonderful-words"


def test_ww_skip_network_fetches_nothing():
    assert wonderfulwords.WonderfulWordsScraper().fetch(
        WINDOW_START, WINDOW_END, skip_network=True
    ) == []


def test_ww_normalizing_twice_is_byte_identical(ww_raw):
    first, _ = wonderfulwords.WonderfulWordsScraper().normalize(ww_raw)
    second, _ = wonderfulwords.WonderfulWordsScraper().normalize(ww_raw)
    assert [state.payload_digest(p) for p in first] == [
        state.payload_digest(p) for p in second
    ]


def test_conformance_ww_payloads_pass_the_real_validator(ww_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = ww_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
