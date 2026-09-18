"""TAMU Youth Camps (Localist) event_watch tests.

Fixture captured 2026-09-15 from
``https://youthcamps.tamu.edu/api/2/events?start=2026-09-15&days=270&pp=100``.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

import pytest

from modules.event_watch.lib import engine, state
from modules.event_watch.lib.config import Settings
from modules.event_watch.lib.scrapers import youthcamps
from modules.event_watch.lib.scrapers.base import RawEvent

FIXTURES = Path(__file__).parent / "fixtures" / "event_watch"
SITE_APP_PATHS = (
    Path("/discoverbcs-app"),
    Path("/srv/docker/websites/discoverbcs/app"),
)
WINDOW_START = date(2026, 9, 15)
WINDOW_END = date(2027, 6, 12)


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
def captured() -> dict:
    return json.loads((FIXTURES / "youthcamps_events.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def list_items(captured) -> list[dict]:
    return [wrap["event"] for wrap in captured["events"]]


@pytest.fixture(scope="module")
def youth_raw(list_items) -> list[RawEvent]:
    return [youthcamps.to_raw(item) for item in list_items]


@pytest.fixture(scope="module")
def youth_normalized(youth_raw):
    return youthcamps.YouthCampsScraper().normalize(youth_raw)


def test_fixture_is_forty_eight_occurrences_across_twenty_six_series(list_items):
    assert len(list_items) == 48
    assert len({item["id"] for item in list_items}) == 26


def test_occurrence_tid_is_start_millis_not_localist_id(youth_raw):
    garden = next(
        r for r in youth_raw
        if r.series_uid == "53825657984753"
        and r.record["event_instances"][0]["event_instance"]["start"].startswith("2026-09-15")
    )
    assert garden.occurrence_tid == "1789448400000"
    assert garden.occurrence_tid != "53825657985779"


def test_garden_club_is_seven_all_day_campus_nights(youth_normalized):
    payloads, _ = youth_normalized
    garden = [
        p for p in payloads if p["series"]["source_series_uid"] == "53825657984753"
    ]
    assert len(garden) == 7
    first = next(
        p for p in garden if p["occurrence"]["start_local"] == "2026-09-15T00:00:00"
    )
    assert first["occurrence"]["all_day"] is True
    assert first["occurrence"]["timezone"] == "America/Chicago"
    assert "end_local" not in first["occurrence"]
    assert first["occurrence"]["source_occurrence_tid"] == "1789448400000"
    assert first["series"]["place"]["name"] == "Texas A&M University"
    assert first["series"]["place"]["city"] == "College Station"
    assert first["series"]["place"]["area"] == "college_station"
    assert first["series"]["audiences"] == ["all-ages"]
    assert "nature" in first["series"]["topics"]
    assert "is_free" not in first["series"]
    parsed = urlparse(first["series"]["source_url"])
    assert parsed.netloc == "youthcamps.tamu.edu"
    assert parsed.path == "/event/garden-club"
    assert "dig, discover, and grow" in (first["series"].get("description") or "").lower()


def test_rec_sports_hangout_two_is_timed_with_registration(youth_normalized):
    payloads, _ = youth_normalized
    hangout = next(
        p for p in payloads if p["series"]["source_series_uid"] == "53799773540541"
    )
    assert hangout["series"]["title"] == "Rec Sports: Hangout #2"
    assert hangout["occurrence"]["start_local"] == "2026-10-30T18:30:00"
    assert hangout["occurrence"]["end_local"] == "2026-10-30T21:00:00"
    assert hangout["occurrence"]["all_day"] is False
    assert hangout["occurrence"]["source_occurrence_tid"] == "1793403000000"
    assert "sports" in hangout["series"]["topics"]
    assert hangout["series"]["registration_url"] == (
        "https://recsports.tamu.edu/programs/youthprograms/"
    )
    assert hangout["series"]["registration_required"] is True
    assert "is_free" not in hangout["series"]


def test_chemistry_open_house_is_free_science(youth_normalized):
    payloads, _ = youth_normalized
    chem = next(
        p for p in payloads if p["series"]["source_series_uid"] == "53799430387525"
    )
    assert chem["occurrence"]["start_local"] == "2026-10-24T10:00:00"
    assert chem["occurrence"]["end_local"] == "2026-10-24T15:00:00"
    assert chem["occurrence"]["source_occurrence_tid"] == "1792854000000"
    assert chem["series"]["is_free"] is True
    assert "science" in chem["series"]["topics"]


def test_campus_buildings_without_city_are_college_station(youth_normalized):
    payloads, _ = youth_normalized
    williams = next(
        p for p in payloads if p["series"]["source_series_uid"] == "53575959379817"
    )
    kyle = next(
        p for p in payloads if p["series"]["source_series_uid"] == "53575992505258"
    )
    assert williams["series"]["place"]["name"] == "Jack K. Williams Administration Building"
    assert williams["series"]["place"]["city"] == "College Station"
    assert williams["series"]["place"]["area"] == "college_station"
    assert williams["occurrence"]["start_local"] == "2026-10-02T16:00:00"
    assert williams["occurrence"]["source_occurrence_tid"] == "1790974800000"
    assert kyle["series"]["place"]["name"] == "Kyle Field"
    assert kyle["series"]["place"]["area"] == "college_station"
    assert kyle["occurrence"]["start_local"] == "2026-10-02T21:00:00"
    assert kyle["occurrence"]["source_occurrence_tid"] == "1790992800000"


def test_out_of_town_listings_are_dropped(youth_normalized):
    payloads, rejected = youth_normalized
    titles = {p["series"]["title"] for p in payloads}
    assert "Aggieland Saturday by the Sea" not in titles
    assert not any("Veterinary Science Camp" in t for t in titles)
    assert not any("Galveston" in (r.get("location") or "") for r in rejected)
    assert not any("Glen Rose" in (r.get("location") or "") for r in rejected)
    assert not any("Fossil Rim" in (r.get("location") or "") for r in rejected)


def test_garden_exploration_days_is_a_camp(youth_normalized):
    payloads, _ = youth_normalized
    days = next(
        p for p in payloads if p["series"]["source_series_uid"] == "53798730961054"
    )
    assert "camp" in days["series"]["topics"]
    assert "nature" in days["series"]["topics"]


def test_jazz_concert_gets_music(youth_normalized):
    payloads, _ = youth_normalized
    jazz = next(
        p for p in payloads if p["series"]["source_series_uid"] == "53947748065816"
    )
    assert "music" in jazz["series"]["topics"]


def test_source_identity(youth_normalized):
    payloads, rejected = youth_normalized
    assert rejected == []
    assert len(payloads) == 46
    for p in payloads:
        assert p["source"]["slug"] == "youthcamps"
        assert p["source"]["name"] == "TAMU Youth Camps"
        assert p["source"]["kind"] == "feed"
        assert p["series"]["organization"]["slug"] == "youthcamps"
        assert p["series"]["place"]["area"] == "college_station"
        assert p["series"]["place"]["city"] == "College Station"
        assert p["series"]["place"]["region"] == "TX"


def test_unknown_venue_is_rejected(youth_raw):
    item = next(r for r in youth_raw if r.series_uid == "53825657984753")
    rec = json.loads(json.dumps(item.record))
    rec["location"] = "A Park In Another City"
    rec["location_name"] = "A Park In Another City"
    rec["geo"] = {
        "latitude": None, "longitude": None, "street": None,
        "city": None, "state": None, "country": None, "zip": None,
    }
    bad = RawEvent(
        series_uid=item.series_uid,
        occurrence_tid=item.occurrence_tid,
        record=rec,
    )
    payloads, rejected = youthcamps.YouthCampsScraper().normalize([bad])
    assert payloads == []
    assert len(rejected) == 1
    assert "unknown venue" in rejected[0]["reason"]


def test_cancelled_rows_are_dropped():
    rec = {
        "id": 1,
        "title": "Cancelled Camp",
        "status": "cancelled",
        "location": "Texas A&M University - College Station",
        "localist_url": "https://youthcamps.tamu.edu/event/cancelled-camp",
        "filters": {
            "event_cost": [{"name": "No Cost"}],
            "event_audiences": [{"name": "Open to all ages"}],
        },
        "event_instances": [{
            "event_instance": {
                "id": 2,
                "start": "2026-09-20T00:00:00-05:00",
                "end": None,
                "all_day": True,
            },
        }],
    }
    payloads, rejected = youthcamps.YouthCampsScraper().normalize(
        [youthcamps.to_raw(rec)]
    )
    assert payloads == []
    assert rejected == []


def test_fetch_pages_then_filters_window(captured):
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict | None]] = []

        def get_json(self, url, *, params=None, headers=None, **kwargs):
            self.calls.append((url, dict(params or {})))
            page = int((params or {}).get("page") or 1)
            events = captured["events"]
            mid = 20
            if page == 1:
                return {
                    "events": events[:mid],
                    "page": {
                        "current": 1, "size": 20, "total": 3,
                        "total_items": len(events), "next_page": 2,
                    },
                }
            if page == 2:
                return {
                    "events": events[mid:40],
                    "page": {
                        "current": 2, "size": 20, "total": 3,
                        "total_items": len(events), "next_page": 3,
                    },
                }
            return {
                "events": events[40:],
                "page": {
                    "current": 3, "size": 20, "total": 3,
                    "total_items": len(events), "next_page": None,
                },
            }

    scraper = youthcamps.YouthCampsScraper()
    scraper._client = FakeClient()
    raw = scraper.fetch(WINDOW_START, WINDOW_END, skip_network=False)
    assert [c[1].get("page") for c in scraper._client.calls] == [1, 2, 3]
    assert scraper._client.calls[0][1]["start"] == "2026-09-15"
    assert scraper._client.calls[0][1]["days"] == 270
    assert scraper._client.calls[0][1]["pp"] == 100
    assert len(raw) == 48
    assert raw[0].series_uid == "53825657984753"


def test_fetch_drops_rows_outside_the_window(captured):
    class FakeClient:
        def get_json(self, url, *, params=None, headers=None, **kwargs):
            return {"events": captured["events"], "page": captured["page"]}

    scraper = youthcamps.YouthCampsScraper()
    scraper._client = FakeClient()
    raw = scraper.fetch(date(2026, 10, 1), date(2026, 10, 3), skip_network=False)
    titles = {r.record["title"].strip() for r in raw}
    assert "Garden Club" not in titles
    assert "150th Celebration Opening Ceremony" in titles
    assert "150th Celebration Kickoff at Kyle" in titles
    assert "Wind Symphony Ensemble Concert" in titles
    assert "Band Ensembles Concert" not in titles


def test_youthcamps_is_registered_and_not_in_default_kinds():
    assert "youthcamps" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(
        Settings.from_env_and_kwargs({"kinds": "youthcamps"})
    )
    assert [s.kind for s in scrapers] == ["youthcamps"]
    assert scrapers[0].source_slug == "youthcamps"


def test_youthcamps_skip_network_fetches_nothing():
    assert youthcamps.YouthCampsScraper().fetch(
        WINDOW_START, WINDOW_END, skip_network=True
    ) == []


def test_normalizing_twice_is_byte_identical(youth_raw):
    first, _ = youthcamps.YouthCampsScraper().normalize(youth_raw)
    second, _ = youthcamps.YouthCampsScraper().normalize(youth_raw)
    assert [state.payload_digest(p) for p in first] == [
        state.payload_digest(p) for p in second
    ]


def test_conformance_youthcamps_payloads_pass_the_real_validator(youth_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = youth_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
