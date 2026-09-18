"""City of College Station Timely calendar event_watch tests.

Fixture captured 2026-09-14 through gluetun from
``timelyapp.time.ly/api/calendars/54735353/events`` (Type=Public) plus
per-id detail records for venue addresses. Widget key is public in
``events.timely.fun`` embed.js.
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
from modules.event_watch.lib.scrapers import cstxcal
from modules.event_watch.lib.scrapers.base import RawEvent

FIXTURES = Path(__file__).parent / "fixtures" / "event_watch"
SITE_APP_PATHS = (
    Path("/discoverbcs-app"),
    Path("/srv/docker/websites/discoverbcs/app"),
)
WINDOW_START = date(2026, 9, 1)
WINDOW_END = date(2027, 1, 15)


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
    return json.loads((FIXTURES / "cstxcal_events.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def list_items(captured) -> list[dict]:
    items: list[dict] = []
    for page in captured["pages"]:
        items.extend(page["data"]["items"])
    return items


@pytest.fixture(scope="module")
def cstx_raw(list_items, captured) -> list[RawEvent]:
    details = captured["details"]
    raw: list[RawEvent] = []
    for item in list_items:
        merged = cstxcal.merge_detail(item, details.get(str(item["id"])))
        raw.append(cstxcal.to_raw(merged))
    return raw


@pytest.fixture(scope="module")
def cstx_normalized(cstx_raw):
    return cstxcal.CstxCalScraper().normalize(cstx_raw)


def test_fixture_is_fourteen_occurrences_across_eleven_series(list_items):
    assert len(list_items) == 14
    assert len({item["id"] for item in list_items}) == 11


def test_luncheon_is_ringer_library_history_and_registration(cstx_normalized):
    payloads, _ = cstx_normalized
    lunch = next(
        p for p in payloads if p["series"]["source_series_uid"] == "78437913"
    )
    assert lunch["series"]["title"].startswith("Exploring History Luncheon")
    assert lunch["occurrence"]["start_local"] == "2026-09-16T11:30:00"
    assert lunch["occurrence"]["end_local"] == "2026-09-16T13:00:00"
    assert lunch["occurrence"]["timezone"] == "America/Chicago"
    assert lunch["occurrence"]["all_day"] is False
    assert lunch["occurrence"]["source_occurrence_tid"] == "1789576200000"
    assert lunch["series"]["place"]["name"] == "Larry J. Ringer Library"
    assert lunch["series"]["place"]["street"] == "1818 Harvey Mitchell Parkway South"
    assert lunch["series"]["place"]["city"] == "College Station"
    assert lunch["series"]["place"]["area"] == "college_station"
    assert lunch["series"]["indoor"] is True
    assert "history" in lunch["series"]["topics"]
    assert lunch["series"]["audiences"] == ["all-ages"]
    assert lunch["series"]["registration_required"] is True
    assert "is_free" not in lunch["series"]
    parsed = urlparse(lunch["series"]["source_url"])
    assert parsed.netloc == "events.timely.fun"
    assert parsed.path.endswith("/event/reflecting-on-three-decades-with-city-government")
    assert "instance_id" not in parsed.query
    assert "Historic Preservation Committee" in (lunch["series"].get("description") or "")
    assert "<p>" not in (lunch["series"].get("description") or "")


def test_christmas_lights_is_one_series_three_nights(cstx_raw, cstx_normalized):
    rows = [r for r in cstx_raw if r.series_uid == "78432368"]
    assert len(rows) == 3
    payloads, _ = cstx_normalized
    published = [p for p in payloads if p["series"]["source_series_uid"] == "78432368"]
    assert len(published) == 3
    tids = {p["occurrence"]["source_occurrence_tid"] for p in published}
    assert len(tids) == 3
    first = next(
        p for p in published
        if p["occurrence"]["start_local"] == "2026-11-26T18:00:00"
    )
    assert first["occurrence"]["source_occurrence_tid"] == "1795737600000"
    assert first["series"]["place"]["name"] == "Stephen C. Beachy Central Park"
    assert first["series"]["place"]["street"] == "1000 Krenek Tap Road"
    assert first["series"]["indoor"] is False
    assert "community" in first["series"]["topics"]


def test_city_council_meeting_is_kept_as_community(cstx_normalized):
    payloads, _ = cstx_normalized
    council = [
        p for p in payloads if p["series"]["title"] == "City Council Meeting"
    ]
    assert len(council) == 2
    one = next(
        p for p in council
        if p["occurrence"]["start_local"] == "2026-09-24T18:00:00"
    )
    assert one["series"]["place"]["name"] == "College Station City Hall"
    assert one["series"]["place"]["street"] == "1101 Texas Avenue"
    assert one["series"]["topics"] == ["community"]
    assert one["occurrence"]["source_occurrence_tid"] == "1790290800000"


def test_office_closures_are_dropped(cstx_normalized):
    payloads, rejected = cstx_normalized
    titles = {p["series"]["title"] for p in payloads}
    assert "City Offices Closed" not in titles
    assert not any("Closed" in (r.get("reason") or "") for r in rejected)


def test_seniors_night_is_free_adult_dance(cstx_normalized):
    payloads, _ = cstx_normalized
    night = next(
        p for p in payloads if p["series"]["title"].startswith("Seniors Night Out")
    )
    assert night["series"]["is_free"] is True
    assert night["series"]["audiences"] == ["adult"]
    assert "arts" in night["series"]["topics"]
    assert night["series"]["place"]["name"] == "Southwood Community Center"


def test_yoga_is_one_series_two_saturdays(cstx_normalized):
    payloads, _ = cstx_normalized
    yoga = [p for p in payloads if p["series"]["title"] == "Yoga in the Park"]
    assert len(yoga) == 2
    assert {p["occurrence"]["start_local"] for p in yoga} == {
        "2026-09-26T08:00:00",
        "2026-10-10T08:00:00",
    }
    assert yoga[0]["series"]["is_free"] is True
    assert "nature" in yoga[0]["series"]["topics"]
    assert "outdoors" in yoga[0]["series"]["topics"]


def test_source_identity(cstx_normalized):
    payloads, rejected = cstx_normalized
    assert rejected == []
    assert len(payloads) == 13
    for p in payloads:
        assert p["source"]["slug"] == "cstxcal"
        assert p["source"]["name"] == "City of College Station Calendar"
        assert p["source"]["kind"] == "feed"
        assert p["series"]["organization"]["slug"] == "cstx"
        assert p["series"]["place"]["area"] == "college_station"
        assert p["series"]["place"]["region"] == "TX"
        assert p["series"]["place"]["city"] == "College Station"


def test_unknown_venue_is_rejected(cstx_raw):
    item = next(r for r in cstx_raw if r.series_uid == "78437913")
    rec = json.loads(json.dumps(item.record))
    rec["taxonomies"]["taxonomy_venue"] = [{
        "id": 1, "title": "A Park In Another City", "item_type": "taxonomy_venue",
    }]
    bad = RawEvent(
        series_uid=item.series_uid,
        occurrence_tid=item.occurrence_tid,
        record=rec,
    )
    payloads, rejected = cstxcal.CstxCalScraper().normalize([bad])
    assert payloads == []
    assert len(rejected) == 1
    assert "unknown venue" in rejected[0]["reason"]


def test_cancelled_rows_are_dropped():
    rec = {
        "id": 1,
        "title": "Cancelled Picnic",
        "instance": "20260920080000",
        "start_datetime": "2026-09-20 08:00:00",
        "timezone": "CST6CDT",
        "allday": False,
        "event_status": "cancelled",
        "taxonomies": {
            "taxonomy_category": [{"title": "Event"}],
            "taxonomy_venue": [{
                "title": "Wolf Pen Creek Park",
                "address": "1015 Colgate Drive",
                "city": "College Station",
                "postal_code": "77840",
            }],
        },
    }
    payloads, rejected = cstxcal.CstxCalScraper().normalize([cstxcal.to_raw(rec)])
    assert payloads == []
    assert rejected == []


def test_fetch_pages_then_merges_detail(captured):
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict | None]] = []

        def get_json(self, url, *, params=None, headers=None, **kwargs):
            self.calls.append((url, dict(params or {})))
            if url.rstrip("/").endswith("/events"):
                page = int((params or {}).get("page") or 1)
                return captured["pages"][page - 1]
            eid = url.rstrip("/").rsplit("/", 1)[-1]
            return {"data": captured["details"][eid]}

    scraper = cstxcal.CstxCalScraper()
    scraper._client = FakeClient()
    raw = scraper.fetch(WINDOW_START, WINDOW_END, skip_network=False)
    event_calls = [c for c in scraper._client.calls if c[0].rstrip("/").endswith("/events")]
    assert [c[1].get("page") for c in event_calls] == [1, 2]
    assert event_calls[0][1]["filter_groups_659989886"] == cstxcal.PUBLIC_TYPE_ID
    assert len(raw) == 14
    lunch = next(r for r in raw if r.series_uid == "78437913")
    venue = (lunch.record.get("taxonomies") or {}).get("taxonomy_venue") or []
    assert venue[0]["address"] == "1818 Harvey Mitchell Parkway South"


def test_fetch_drops_rows_outside_the_window(captured):
    class FakeClient:
        def get_json(self, url, *, params=None, headers=None, **kwargs):
            if url.rstrip("/").endswith("/events"):
                page = int((params or {}).get("page") or 1)
                return captured["pages"][page - 1]
            eid = url.rstrip("/").rsplit("/", 1)[-1]
            return {"data": captured["details"][eid]}

    scraper = cstxcal.CstxCalScraper()
    scraper._client = FakeClient()
    raw = scraper.fetch(date(2026, 9, 15), WINDOW_END, skip_network=False)
    titles = {r.record["title"] for r in raw}
    assert "Active Transportation Advisory Board Meeting" not in titles
    assert "Historic Preservation Committee Meeting" not in titles
    assert any(t.startswith("Exploring History Luncheon") for t in titles)


def test_cstxcal_is_registered_and_not_in_default_kinds():
    assert "cstxcal" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(
        Settings.from_env_and_kwargs({"kinds": "cstxcal"})
    )
    assert [s.kind for s in scrapers] == ["cstxcal"]
    assert scrapers[0].source_slug == "cstxcal"


def test_cstxcal_skip_network_fetches_nothing():
    assert cstxcal.CstxCalScraper().fetch(
        WINDOW_START, WINDOW_END, skip_network=True
    ) == []


def test_normalizing_twice_is_byte_identical(cstx_raw):
    first, _ = cstxcal.CstxCalScraper().normalize(cstx_raw)
    second, _ = cstxcal.CstxCalScraper().normalize(cstx_raw)
    assert [state.payload_digest(p) for p in first] == [
        state.payload_digest(p) for p in second
    ]


def test_conformance_cstxcal_payloads_pass_the_real_validator(cstx_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = cstx_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
