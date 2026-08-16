"""Painting with a Twist College Station calendar tests.

Fixture captured 2026-08-16 from
``https://www.paintingwithatwist.com/studio/college-station/calendar/``.
``time.event-datetime`` uses a 12-hour clock in the ``T`` field
(``2026-08-16T03:00`` is 3pm, not 3am).
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
from modules.event_watch.lib.scrapers import pwat
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
def pwat_cards() -> list[dict]:
    return pwat.parse_calendar((FIXTURES / "pwat_calendar.html").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def pwat_raw(pwat_cards) -> list[RawEvent]:
    return [pwat.to_raw(card) for card in pwat_cards]


@pytest.fixture(scope="module")
def pwat_normalized(pwat_raw):
    return pwat.PwatScraper().normalize(pwat_raw)


def test_pwat_calendar_parses_twenty_seven_events(pwat_cards):
    assert len(pwat_cards) == 27
    ids = [c["event_id"] for c in pwat_cards]
    assert ids[0] == "4339801"
    assert "4368685" in ids  # Family Day owl
    assert len(set(ids)) == 27


def test_pwat_does_not_treat_datetime_attribute_as_24h(pwat_cards):
    cow = next(c for c in pwat_cards if c["event_id"] == "4339801")
    assert cow["datetime_attr"] == "2026-08-16T03:00"
    assert cow["start"].hour == 15
    assert cow["end"].hour == 17
    assert cow["start"].date() == date(2026, 8, 16)


def test_pwat_highland_cow_is_sunday_afternoon_forty_dollars(pwat_normalized):
    payloads, _ = pwat_normalized
    cow = next(p for p in payloads if p["series"]["source_series_uid"] == "4339801")
    assert cow["series"]["title"] == "A Rustic Highland Cow"
    assert cow["occurrence"]["start_local"] == "2026-08-16T15:00:00"
    assert cow["occurrence"]["end_local"] == "2026-08-16T17:00:00"
    assert cow["occurrence"]["timezone"] == "America/Chicago"
    assert cow["series"]["cost_low_cents"] == 4000
    assert cow["series"]["cost_note"] == "$40"
    assert cow["series"]["topics"] == ["arts"]
    assert cow["series"]["audiences"] == ["adult"]
    assert cow["series"]["indoor"] is True
    assert cow["series"]["registration_required"] is True


def test_pwat_family_day_is_all_ages(pwat_normalized):
    payloads, _ = pwat_normalized
    owl = next(p for p in payloads if p["series"]["source_series_uid"] == "4368685")
    assert owl["series"]["title"] == "Cutie Fall Owl - FAMILY DAY! ALL AGES!"
    assert owl["series"]["audiences"] == ["all-ages"]
    assert owl["occurrence"]["start_local"] == "2026-09-05T15:00:00"
    assert owl["occurrence"]["end_local"] == "2026-09-05T16:30:00"
    assert owl["series"]["cost_low_cents"] == 2800


def test_pwat_place_and_source_identity(pwat_normalized):
    payloads, rejected = pwat_normalized
    assert rejected == []
    assert len(payloads) == 27
    for p in payloads:
        assert p["source"]["slug"] == "painting-with-a-twist"
        assert p["source"]["name"] == "Painting with a Twist"
        assert p["source"]["kind"] == "feed"
        assert p["series"]["organization"]["slug"] == "painting-with-a-twist"
        place = p["series"]["place"]
        assert place["name"] == "Painting with a Twist"
        assert place["street"] == "1643 Texas Ave S"
        assert place["city"] == "College Station"
        assert place["postcode"] == "77840"
        assert place["area"] == "college_station"
        assert p["series"]["source_url"].startswith(
            "https://www.paintingwithatwist.com/studio/college-station/event/"
        )


def test_pwat_tid_is_start_ms(pwat_normalized):
    payloads, _ = pwat_normalized
    cow = next(p for p in payloads if p["series"]["source_series_uid"] == "4339801")
    assert cow["occurrence"]["source_occurrence_tid"] == "1786910400000"


def test_pwat_is_registered_and_not_in_default_kinds():
    assert "pwat" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "pwat"}))
    assert [s.kind for s in scrapers] == ["pwat"]
    assert scrapers[0].source_slug == "painting-with-a-twist"


def test_pwat_skip_network_fetches_nothing():
    assert pwat.PwatScraper().fetch(WINDOW_START, WINDOW_END, skip_network=True) == []


def test_pwat_normalizing_twice_is_byte_identical(pwat_raw):
    first, _ = pwat.PwatScraper().normalize(pwat_raw)
    second, _ = pwat.PwatScraper().normalize(pwat_raw)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


def test_conformance_pwat_payloads_pass_the_real_validator(pwat_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = pwat_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
