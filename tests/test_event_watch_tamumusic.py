"""Texas A&M Music Activities LiveWhale tests.

Fixture: ``tamumusic_events.json`` — ``GET /live/json/events/group/Music Activities``
captured 2026-08-15 (8 concerts, thumbnails stripped).
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
from modules.event_watch.lib.scrapers import tamu, tamumusic
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
def music_raw() -> list[RawEvent]:
    records = json.loads((FIXTURES / "tamumusic_events.json").read_text())
    return [tamu.to_raw(r) for r in records]


@pytest.fixture(scope="module")
def music_normalized(music_raw):
    return tamumusic.TamuMusicScraper().normalize(music_raw)


def test_tamumusic_fixture_has_eight_concerts(music_raw):
    assert len(music_raw) == 8
    assert {r.record["group_title"] for r in music_raw} == {"Music Activities"}
    assert all(r.series_uid and r.occurrence_tid for r in music_raw)


def test_tamumusic_uses_native_id_not_the_syndicated_copy(music_raw):
    jazz = next(r for r in music_raw if r.series_uid == "385689")
    assert jazz.record["title"].startswith("University Jazz Ensembles")
    assert jazz.record.get("parent") in (None, "")
    assert jazz.occurrence_tid == str(int(jazz.record["date_ts"]) * 1000)


def test_tamumusic_source_is_not_the_main_tamu_kind(music_normalized):
    payloads, _ = music_normalized
    assert payloads
    for p in payloads:
        assert p["source"]["slug"] == "tamu-music"
        assert p["source"]["name"] == "Texas A&M Music Activities"
        assert p["source"]["kind"] == "feed"
        assert p["source"]["slug"] != "tamu"


def test_tamumusic_jazz_is_wall_clock_seven_pm(music_normalized):
    payloads, _ = music_normalized
    jazz = next(p for p in payloads if p["series"]["source_series_uid"] == "385689")
    assert jazz["occurrence"]["start_local"] == "2026-09-25T19:00:00"
    assert jazz["occurrence"]["timezone"] == "America/Chicago"
    assert "+" not in jazz["occurrence"]["start_local"]
    datetime.fromisoformat(jazz["occurrence"]["start_local"])


def test_tamumusic_rudder_is_college_station(music_normalized):
    payloads, _ = music_normalized
    for p in payloads:
        place = p["series"]["place"]
        assert place["city"] == "College Station"
        assert place["area"] == "college_station"
        assert "Rudder" in place["name"]


def test_tamumusic_topics_include_music(music_normalized):
    payloads, _ = music_normalized
    for p in payloads:
        assert "music" in p["series"]["topics"]


def test_tamumusic_does_not_unregister_tamu():
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "tamu"}))
    assert [s.kind for s in scrapers] == ["tamu"]


def test_tamumusic_is_registered_and_not_in_default_kinds():
    assert "tamumusic" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "tamumusic"}))
    assert [s.kind for s in scrapers] == ["tamumusic"]
    assert scrapers[0].source_slug == "tamu-music"


def test_tamumusic_skip_network_fetches_nothing():
    assert tamumusic.TamuMusicScraper().fetch(WINDOW_START, WINDOW_END, skip_network=True) == []


def test_tamumusic_normalizing_twice_is_byte_identical(music_raw):
    first, _ = tamumusic.TamuMusicScraper().normalize(music_raw)
    second, _ = tamumusic.TamuMusicScraper().normalize(music_raw)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


def test_conformance_tamumusic_payloads_pass_the_real_validator(music_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = music_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
