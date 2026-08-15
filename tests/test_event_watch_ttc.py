"""Theater Company of Bryan/College Station Squarespace calendar tests.

Fixture: ``ttc_calendar.json`` — slimmed ``GET /calendar?format=json``
upcoming list captured 2026-08-15 (74 performances).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

from modules.event_watch.lib import engine, state
from modules.event_watch.lib.config import Settings
from modules.event_watch.lib.scrapers import ttc
from modules.event_watch.lib.scrapers.base import RawEvent

FIXTURES = Path(__file__).parent / "fixtures" / "event_watch"
SITE_APP_PATHS = (
    Path("/discoverbcs-app"),
    Path("/srv/docker/websites/discoverbcs/app"),
)
WINDOW_START = date(2026, 8, 15)
WINDOW_END = date(2027, 5, 12)


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
def ttc_records() -> list[dict]:
    data = json.loads((FIXTURES / "ttc_calendar.json").read_text(encoding="utf-8"))
    return ttc.parse_calendar_json(data)


@pytest.fixture(scope="module")
def ttc_raw(ttc_records) -> list[RawEvent]:
    return [ttc.to_raw(rec) for rec in ttc_records]


@pytest.fixture(scope="module")
def ttc_windowed(ttc_raw) -> list[RawEvent]:
    return [r for r in ttc_raw if ttc.in_window(r, WINDOW_START, WINDOW_END)]


@pytest.fixture(scope="module")
def ttc_normalized(ttc_windowed):
    return ttc.TtcScraper().normalize(ttc_windowed)


def test_ttc_parses_seventy_four_upcoming(ttc_records):
    assert len(ttc_records) == 74
    assert all(r["title"] and r["start_ms"] for r in ttc_records)


def test_ttc_drops_work_week(ttc_normalized):
    payloads, _ = ttc_normalized
    titles = {p["series"]["title"] for p in payloads}
    assert "TTC Work Week" not in titles
    assert any(t == "35mm" for t in titles)
    assert any("Auditions" in t for t in titles)


def test_ttc_copy_night_joins_the_real_series(ttc_normalized):
    payloads, _ = ttc_normalized
    succeed = [
        p
        for p in payloads
        if p["series"]["source_series_uid"] == "how-to-succeed-in-business-without-really-trying"
    ]
    assert len(succeed) == 12
    assert {p["series"]["title"] for p in succeed} == {"How to Succeed in Business Without Really Trying"}


def test_ttc_thirty_five_mm_opening_is_wall_clock(ttc_normalized):
    payloads, _ = ttc_normalized
    opening = next(
        p
        for p in payloads
        if p["series"]["title"] == "35mm" and p["occurrence"]["start_local"].startswith("2026-08-21")
    )
    assert opening["occurrence"]["start_local"] == "2026-08-21T19:00:00"
    assert opening["occurrence"]["end_local"] == "2026-08-21T21:00:00"
    assert opening["occurrence"]["timezone"] == "America/Chicago"
    datetime.fromisoformat(opening["occurrence"]["start_local"])


def test_ttc_ignores_nyc_map_pin(ttc_normalized):
    payloads, _ = ttc_normalized
    for p in payloads:
        place = p["series"]["place"]
        assert place["city"] == "Bryan"
        assert place["area"] == "bryan"
        assert place["street"] == "3125 S Texas Ave, Ste 500"
        assert place["postcode"] == "77802"
        assert place.get("latitude") in (None, "") or abs(place["latitude"] - 40.72) > 1


def test_ttc_source_identity_and_permalink(ttc_normalized):
    payloads, _ = ttc_normalized
    opening = next(p for p in payloads if p["series"]["source_url"].endswith("/calendar/35mm"))
    assert opening["source"]["slug"] == "theatre-company"
    assert opening["source"]["name"] == "The Theater Company of Bryan / College Station"
    assert opening["source"]["kind"] == "feed"
    assert opening["series"]["organization"]["slug"] == "theatre-company"
    assert opening["series"]["topics"] == []
    assert opening["series"]["audiences"] == []
    assert opening["series"]["source_url"].startswith("https://www.theatrecompany.com/")


def test_ttc_occurrence_tid_is_start_ms(ttc_normalized):
    payloads, _ = ttc_normalized
    for p in payloads:
        tid = p["occurrence"]["source_occurrence_tid"]
        assert tid.isdigit()
        assert int(tid) > 10**12


def test_ttc_is_registered_and_not_in_default_kinds():
    assert "ttc" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "ttc"}))
    assert [s.kind for s in scrapers] == ["ttc"]
    assert scrapers[0].source_slug == "theatre-company"


def test_ttc_skip_network_fetches_nothing():
    assert ttc.TtcScraper().fetch(WINDOW_START, WINDOW_END, skip_network=True) == []


def test_ttc_normalizing_twice_is_byte_identical(ttc_windowed):
    first, _ = ttc.TtcScraper().normalize(ttc_windowed)
    second, _ = ttc.TtcScraper().normalize(ttc_windowed)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


def test_conformance_ttc_payloads_pass_the_real_validator(ttc_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = ttc_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
