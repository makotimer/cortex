"""Stage 12 (Brookshire Brothers) Drupal calendar tests.

Fixtures captured 2026-08-16 from
``https://www.brookshirebrothers.com/college-station/stage12events``.
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
from modules.event_watch.lib.scrapers import stage12
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


def _month(name: str) -> dict:
    return stage12.parse_month((FIXTURES / name).read_text(encoding="utf-8"))


def _raw_from(month: dict) -> list[RawEvent]:
    out: list[RawEvent] = []
    for card in month["events"]:
        item = stage12.to_raw(card)
        detail_path = FIXTURES / f"stage12_node_{card['nid']}.html"
        if detail_path.is_file():
            item.supplement["detail"] = stage12.parse_detail(detail_path.read_text(encoding="utf-8"))
        out.append(item)
    return out


@pytest.fixture(scope="module")
def august() -> dict:
    return _month("stage12_month_august.html")


@pytest.fixture(scope="module")
def september() -> dict:
    return _month("stage12_month_september.html")


@pytest.fixture(scope="module")
def empty_month() -> dict:
    return _month("stage12_month_empty.html")


@pytest.fixture(scope="module")
def stage12_raw(august, september) -> list[RawEvent]:
    seen: set[str] = set()
    raw: list[RawEvent] = []
    for month in (august, september):
        for item in _raw_from(month):
            if item.series_uid in seen:
                continue
            seen.add(item.series_uid)
            raw.append(item)
    return raw


@pytest.fixture(scope="module")
def stage12_normalized(stage12_raw):
    return stage12.Stage12Scraper().normalize(stage12_raw)


def test_stage12_august_parses_fifteen_rows(august):
    assert august["caption"] == "August 2026"
    assert august["year"] == 2026
    assert august["month"] == 8
    assert august["next_ts"] == 1788238800
    assert len(august["events"]) == 15
    nids = [e["nid"] for e in august["events"]]
    assert nids[0] == "500593"
    assert "500485" in nids
    assert "500489" in nids  # Sep 2 spillover


def test_stage12_september_continues_the_pager(september):
    assert september["caption"] == "September 2026"
    assert september["month"] == 9
    assert any(e["nid"] == "500451" for e in september["events"])
    assert any(e["nid"] == "500453" for e in september["events"])


def test_stage12_empty_month_has_no_events(empty_month):
    assert empty_month["caption"] == "January 2027"
    assert empty_month["events"] == []
    assert empty_month["next_ts"]


def test_stage12_spillover_nids_match_across_months(august, september):
    aug = {e["nid"] for e in august["events"]}
    sep = {e["nid"] for e in september["events"]}
    assert {"500489", "500634"} <= aug & sep


def test_stage12_clean_title_strips_emoji_and_clock():
    assert stage12.clean_title("🎬Movie Night: Encanto @ 7:00 PM") == "Movie Night: Encanto"
    assert stage12.clean_title("🎶Live Music🎶") == "Live Music"
    assert stage12.clean_title("🎨Craft Night + Live Music🎶") == "Craft Night + Live Music"
    assert stage12.clean_title("🎤Karaoke🎤") == "Karaoke"
    assert stage12.clean_title("Singo: Back to School") == "Singo: Back to School"


def test_stage12_live_music_title_includes_artist(stage12_normalized):
    payloads, _ = stage12_normalized
    fragments = next(p for p in payloads if p["series"]["source_series_uid"] == "500453")
    assert fragments["series"]["title"] == "Live Music: The Fragments"
    assert "The Fragments" in (fragments["series"].get("description") or "")


def test_stage12_uses_listing_times_not_doors(stage12_normalized):
    payloads, _ = stage12_normalized
    fragments = next(p for p in payloads if p["series"]["source_series_uid"] == "500453")
    assert fragments["occurrence"]["start_local"] == "2026-09-17T19:00:00"
    assert fragments["occurrence"]["end_local"] == "2026-09-17T21:00:00"
    assert fragments["occurrence"]["timezone"] == "America/Chicago"
    assert fragments["occurrence"]["all_day"] is False


def test_stage12_movie_night_is_free_arts(stage12_normalized):
    payloads, _ = stage12_normalized
    movie = next(p for p in payloads if p["series"]["source_series_uid"] == "500451")
    assert movie["series"]["title"] == "Movie Night: Monsters University"
    assert movie["series"]["is_free"] is True
    assert movie["series"]["topics"] == ["arts"]
    assert movie["series"]["audiences"] == ["all-ages"]
    assert movie["occurrence"]["start_local"] == "2026-09-11T19:00:00"
    assert movie["occurrence"]["end_local"] == "2026-09-11T21:00:00"


def test_stage12_kids_camp_states_ages_and_registration(stage12_normalized):
    payloads, _ = stage12_normalized
    camp = next(p for p in payloads if p["series"]["source_series_uid"] == "500593")
    assert camp["series"]["title"] == "Kids Camp - Junior Sprouts: Yummy Science"
    assert camp["series"]["age_min"] == 6
    assert camp["series"]["age_max"] == 10
    assert camp["series"]["registration_required"] is True
    assert camp["series"]["audiences"] == ["elementary"]
    assert camp["series"]["topics"] == ["camp", "science"]
    assert camp["occurrence"]["start_local"] == "2026-07-27T10:00:00"
    assert camp["occurrence"]["end_local"] == "2026-07-27T11:30:00"


def test_stage12_singo_omits_is_free(stage12_normalized):
    payloads, _ = stage12_normalized
    singo = next(p for p in payloads if p["series"]["source_series_uid"] == "500485")
    assert singo["series"]["title"] == "Singo: Back to School"
    assert "is_free" not in singo["series"]
    assert singo["series"]["topics"] == ["music"]
    assert singo["series"]["audiences"] == ["all-ages"]


def test_stage12_craft_night_is_crafts_and_music(stage12_normalized):
    payloads, _ = stage12_normalized
    craft = next(p for p in payloads if p["series"]["source_series_uid"] == "500630")
    assert craft["series"]["title"] == "Craft Night + Live Music: Keaton Kyzar"
    assert craft["series"]["topics"] == ["crafts", "music"]
    assert craft["series"]["is_free"] is True


def test_stage12_place_and_source_identity(stage12_normalized):
    payloads, _ = stage12_normalized
    assert payloads
    for p in payloads:
        assert p["source"]["slug"] == "stage12"
        assert p["source"]["name"] == "Stage 12"
        assert p["source"]["kind"] == "feed"
        assert p["series"]["organization"]["slug"] == "stage12"
        assert p["series"]["organization"]["name"] == "Stage 12"
        place = p["series"]["place"]
        assert place["name"] == "Stage 12"
        assert place["street"] == "455 George Bush Dr. W Suite 100"
        assert place["city"] == "College Station"
        assert place["region"] == "TX"
        assert place["postcode"] == "77840"
        assert place["area"] == "college_station"
        assert p["series"]["source_url"] == (
            f"https://www.brookshirebrothers.com/node/{p['series']['source_series_uid']}"
        )


def test_stage12_series_uid_is_nid_and_tid_is_start_ms(stage12_normalized):
    payloads, _ = stage12_normalized
    movie = next(p for p in payloads if p["series"]["source_series_uid"] == "500451")
    tid = movie["occurrence"]["source_occurrence_tid"]
    assert tid.isdigit()
    assert int(tid) > 10**12


def test_stage12_is_registered_and_not_in_default_kinds():
    assert "stage12" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "stage12"}))
    assert [s.kind for s in scrapers] == ["stage12"]
    assert scrapers[0].source_slug == "stage12"


def test_stage12_skip_network_fetches_nothing():
    assert stage12.Stage12Scraper().fetch(WINDOW_START, WINDOW_END, skip_network=True) == []


def test_stage12_normalizing_twice_is_byte_identical(stage12_raw):
    first, _ = stage12.Stage12Scraper().normalize(stage12_raw)
    second, _ = stage12.Stage12Scraper().normalize(stage12_raw)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


def test_conformance_stage12_payloads_pass_the_real_validator(stage12_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = stage12_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
