"""BCS Chamber GrowthZone event_watch tests.

Fixture: ``bcschamber_events.xml`` — verbatim ``GET /api/events`` captured
2026-08-15 (32 ``EventDisplay`` rows).
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
from modules.event_watch.lib.scrapers import bcschamber
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
def chamber_records() -> list[dict]:
    xml = (FIXTURES / "bcschamber_events.xml").read_text(encoding="utf-8")
    return bcschamber.parse_events_xml(xml)


@pytest.fixture(scope="module")
def chamber_raw(chamber_records) -> list[RawEvent]:
    return [bcschamber.to_raw(rec) for rec in chamber_records]


@pytest.fixture(scope="module")
def chamber_normalized(chamber_raw):
    return bcschamber.BcsChamberScraper().normalize(chamber_raw)


def _by_id(payloads: list[dict], event_id: str) -> dict:
    return next(p for p in payloads if p["series"]["source_series_uid"] == event_id)


def test_bcschamber_fixture_has_32_approved_events(chamber_records):
    assert len(chamber_records) == 32
    assert all(r["event_id"] for r in chamber_records)
    assert all(r["title"] for r in chamber_records)
    assert all(r["start_local"] for r in chamber_records)


def test_bcschamber_skips_unapproved_rows():
    xml = """
    <ArrayOfEventDisplay>
      <EventDisplay>
        <EventID>1</EventID>
        <Name>Hidden</Name>
        <Status>PENDING</Status>
        <StartDate>2026-09-01T12:00:00</StartDate>
        <EndDate>2026-09-01T13:00:00</EndDate>
        <IsAllDayEvent>false</IsAllDayEvent>
        <Slug>hidden</Slug>
      </EventDisplay>
      <EventDisplay>
        <EventID>2</EventID>
        <Name>Shown</Name>
        <Status>APPROVED</Status>
        <StartDate>2026-09-01T12:00:00</StartDate>
        <EndDate>2026-09-01T13:00:00</EndDate>
        <IsAllDayEvent>false</IsAllDayEvent>
        <Slug>shown</Slug>
      </EventDisplay>
    </ArrayOfEventDisplay>
    """
    records = bcschamber.parse_events_xml(xml)
    assert [r["event_id"] for r in records] == ["2"]


def test_bcschamber_times_are_wall_clock_not_utc(chamber_raw):
    frost = next(r for r in chamber_raw if r.series_uid == "3745")
    assert frost.record["start_local"] == "2026-09-24T17:30:00"
    assert frost.record["end_local"] == "2026-09-24T19:00:00"
    assert frost.occurrence_tid == bcschamber.local_to_tid("2026-09-24T17:30:00")
    assert frost.occurrence_tid.isdigit()


def test_bcschamber_prefers_location_desc_over_dummy_map(chamber_normalized):
    payloads, _ = chamber_normalized
    zeal = _by_id(payloads, "3755")
    place = zeal["series"]["place"]
    assert place["city"] == "Bryan"
    assert place["area"] == "bryan"
    assert place["postcode"] == "77803"
    assert "607" in (place.get("street") or "")
    assert "2700" not in (place.get("street") or "")
    assert "Kimbell" in place["name"]


def test_bcschamber_lucky_goat_hudson_oaks_is_bryan(chamber_normalized):
    payloads, _ = chamber_normalized
    goat = _by_id(payloads, "3883")
    assert goat["series"]["place"]["city"] == "Bryan"
    assert goat["series"]["place"]["area"] == "bryan"
    assert goat["series"]["place"]["postcode"] == "77802"


def test_bcschamber_reads_city_from_description_when_location_blank(chamber_normalized):
    payloads, _ = chamber_normalized
    frost = _by_id(payloads, "3745")
    assert frost["series"]["place"]["city"] == "College Station"
    assert frost["series"]["place"]["area"] == "college_station"
    parc = _by_id(payloads, "3747")
    assert parc["series"]["place"]["city"] == "Bryan"
    assert parc["series"]["place"]["area"] == "bryan"


def test_bcschamber_hilton_college_station_name_supplies_city(chamber_normalized):
    payloads, _ = chamber_normalized
    lunch = _by_id(payloads, "3854")
    assert lunch["series"]["place"]["city"] == "College Station"
    assert lunch["series"]["place"]["area"] == "college_station"


def test_bcschamber_rejects_events_with_no_city(chamber_normalized):
    payloads, rejected = chamber_normalized
    rejected_ids = {r["series_uid"] for r in rejected}
    # No location at all — still a loud reject, on purpose.
    assert "3855" in rejected_ids  # Lobsterfest — empty location
    assert "3889" in rejected_ids  # St. Joseph — name only
    # Recoverable, so they publish now: the venue map supplies the city.
    bvcog = _by_id(payloads, "3849")
    assert bvcog["series"]["place"]["city"] == "Bryan"
    expo = _by_id(payloads, "3862")
    assert expo["series"]["place"]["city"] == "Bryan"
    assert expo["series"]["place"]["area"] == "bryan"
    published = {p["series"]["source_series_uid"] for p in payloads}
    assert rejected_ids.isdisjoint(published)


def test_bcschamber_live_only_streets_resolve_via_injected_resolver():
    # 3898 and 3896 belong here, not in the fixture: 3898 is a street with no
    # city (resolver path) and 3896 is the bare venue name (map path).
    rec_3898 = {
        "event_id": "3898",
        "title": "Leadership Brazos ALL CLASS REUNION",
        "start_local": "2026-10-09T18:00:00",
        "end_local": "2026-10-09T21:00:00",
        "location_html": (
            "Phillips Event Center<br />1929 Country Club Drive"
        ),
        "description": "Sponsored by the City of Bryan",
        "slug": "leadership-brazos-all-class-reunion",
        "categories": [2],
        "admission": "",
        "all_day": False,
    }
    payloads, rejected = bcschamber.BcsChamberScraper().normalize(
        [bcschamber.to_raw(rec_3898)]
    )
    assert rejected == []
    place = payloads[0]["series"]["place"]
    assert place["city"] == "Bryan"
    assert place["street"] == "1929 Country Club Drive"

    rec_3896 = {
        "event_id": "3896",
        "title": "Ag Breakfast 2026",
        "start_local": "2026-11-24T07:30:00",
        "end_local": "2026-11-24T09:30:00",
        "location_html": "Brazos County Expo",
        "description": "",
        "slug": "ag-breakfast-2026",
        "categories": [4],
        "admission": "",
        "all_day": False,
    }
    payloads, rejected = bcschamber.BcsChamberScraper().normalize(
        [bcschamber.to_raw(rec_3896)]
    )
    assert rejected == []
    place = payloads[0]["series"]["place"]
    assert place["city"] == "Bryan"
    assert place["area"] == "bryan"
    assert place["name"] == "Brazos County Expo"


def test_bcschamber_bare_venue_name_resolves_from_map():
    rec = {
        "event_id": "3862",
        "title": "Youth Career Fair 2026",
        "start_local": "2026-10-07T08:00:00",
        "end_local": "2026-10-07T15:00:00",
        "location_html": "Brazos County Expo",
        "description": "",
        "slug": "youth-career-fair-2026",
        "categories": [],
        "admission": "",
        "all_day": False,
    }
    payloads, rejected = bcschamber.BcsChamberScraper().normalize([bcschamber.to_raw(rec)])
    assert rejected == []
    place = payloads[0]["series"]["place"]
    assert place["city"] == "Bryan"
    assert place["area"] == "bryan"
    assert place["name"] == "Brazos County Expo"
    assert place["street"] == "5827 Leonard Rd"


def test_bcschamber_unmapped_bare_name_still_rejects():
    rec = {
        "event_id": "3889",
        "title": "Business After Hours 2026 - St. Joseph Health",
        "start_local": "2026-11-19T17:30:00",
        "end_local": "2026-11-19T19:00:00",
        "location_html": "Some New Hall",
        "description": "Some New Hall",
        "slug": "bah-st-joseph",
        "categories": [2],
        "admission": "",
        "all_day": False,
    }
    payloads, rejected = bcschamber.BcsChamberScraper().normalize([bcschamber.to_raw(rec)])
    assert payloads == []
    assert [r["series_uid"] for r in rejected] == ["3889"]
    assert rejected[0]["reason"] == "missing city"


def test_bcschamber_venue_map_does_not_override_text():
    rec = {
        "event_id": "9",
        "title": "Mapped, With Explicit City",
        "start_local": "2026-09-01T12:00:00",
        "end_local": "2026-09-01T13:00:00",
        "location_html": "Brazos Center<br />4444 Other Road<br />College Station, TX 77840",
        "description": "",
        "slug": "mapped-explicit",
        "categories": [],
        "admission": "",
        "all_day": False,
    }
    payloads, rejected = bcschamber.BcsChamberScraper().normalize([bcschamber.to_raw(rec)])
    assert rejected == []
    place = payloads[0]["series"]["place"]
    assert place["city"] == "College Station"
    assert place["area"] == "college_station"
    assert place["postcode"] == "77840"
    assert "4444 Other Road" in (place.get("street") or "")
    assert "3232 Briarcrest" not in (place.get("street") or "")


def test_bcschamber_drops_non_bcs_cities():
    rec = {
        "event_id": "9",
        "title": "Away",
        "start_local": "2026-09-01T12:00:00",
        "end_local": "2026-09-01T13:00:00",
        "location_html": "100 Main St<br />Houston, TX 77002",
        "description": "",
        "slug": "away",
        "categories": [],
        "admission": "",
        "all_day": False,
    }
    payloads, rejected = bcschamber.BcsChamberScraper().normalize([bcschamber.to_raw(rec)])
    assert payloads == []
    assert rejected == []


def test_bcschamber_category_topics():
    assert bcschamber.topics_from_categories([6]) == ["community"]
    assert bcschamber.topics_from_categories([2]) == ["community"]
    assert bcschamber.topics_from_categories([4]) == ["community"]
    assert bcschamber.topics_from_categories([]) == []


def test_bcschamber_ribbon_cutting_is_community(chamber_normalized):
    payloads, _ = chamber_normalized
    goat = _by_id(payloads, "3883")
    assert goat["series"]["topics"] == ["community"]
    assert goat["series"]["audiences"] == []


def test_bcschamber_uncategorized_has_no_topics(chamber_normalized):
    payloads, _ = chamber_normalized
    frost = _by_id(payloads, "3745")
    assert frost["series"]["topics"] == []


def test_bcschamber_free_only_when_admission_says_so(chamber_normalized):
    payloads, _ = chamber_normalized
    senior = _by_id(payloads, "3870")
    assert senior["series"].get("is_free") is True
    outlook = _by_id(payloads, "3854")
    assert "is_free" not in outlook["series"]


def test_bcschamber_source_url_and_identity(chamber_normalized):
    payloads, _ = chamber_normalized
    goat = _by_id(payloads, "3883")
    assert goat["source"]["slug"] == "bcs-chamber"
    assert goat["source"]["name"] == "Bryan-College Station Chamber of Commerce"
    assert goat["source"]["kind"] == "feed"
    assert goat["series"]["organization"]["slug"] == "bcs-chamber"
    assert goat["series"]["source_url"] == (
        "https://business.bcschamber.org/events/details/ribbon-cutting-lucky-goat-coffee-hudson-oaks-3883"
    )
    assert goat["occurrence"]["timezone"] == "America/Chicago"
    assert goat["occurrence"]["start_local"] == "2026-08-20T11:30:00"
    assert goat["occurrence"]["end_local"] == "2026-08-20T12:00:00"


def test_bcschamber_window_keeps_by_start_date(chamber_raw):
    kept = [r for r in chamber_raw if bcschamber.in_window(r, date(2026, 10, 1), WINDOW_END)]
    ids = {r.series_uid for r in kept}
    assert "3883" not in ids  # Aug 20
    assert "3745" not in ids  # Sep 24
    assert "3888" in ids  # Oct 26


def test_bcschamber_is_registered_and_not_in_default_kinds():
    assert "bcschamber" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "bcschamber"}))
    assert [s.kind for s in scrapers] == ["bcschamber"]
    assert scrapers[0].source_slug == "bcs-chamber"


def test_bcschamber_skip_network_fetches_nothing():
    calls = []
    scraper = bcschamber.BcsChamberScraper(resolve=lambda q: calls.append(q) or {})
    assert scraper.fetch(WINDOW_START, WINDOW_END, skip_network=True) == []
    assert calls == []


def test_bcschamber_fetch_resolves_street_without_city(tmp_path):
    xml = (FIXTURES / "bcschamber_events.xml").read_text(encoding="utf-8")
    # The BVCOG venue is mapped, so swap it for an unmapped venue that still
    # carries a street line — that is the shape the resolver exists for
    # (3898 "Phillips Event Center" pre-map, e.g.).
    xml = xml.replace("Brazos Valley Council of Governments", "Some New Hall")
    xml = xml.replace("3991 East 29th Street", "1234 Mapped Over Street")
    scraper = bcschamber.BcsChamberScraper(state_dir=str(tmp_path))
    queried: list[str] = []

    def fake_resolve(query: str) -> dict:
        queried.append(query)
        return {"status": "matched", "city": "Bryan", "zip_code": "77801"}

    scraper._resolve = fake_resolve
    scraper._client = _StubClient(xml)
    raw = scraper.fetch(WINDOW_START, WINDOW_END, skip_network=False)
    assert raw, "fixture window should keep events"
    target = [item for item in raw if item.series_uid == "3849"]
    assert target, "3849 must be in the fixture window"
    reso = target[0].supplement.get("place_resolution")
    assert reso == {"status": "matched", "city": "Bryan", "zip_code": "77801"}
    assert "1234 Mapped Over Street" in queried
    # Ribbon cuttings already carry a City, TX line — never looked up.
    assert not any("607 N Main Street" in q for q in queried)
    # The result persists to the address cache for the next run.
    assert state.load_addresses(str(tmp_path), "bcs-chamber")


class _StubClient:
    def __init__(self, xml: str) -> None:
        self._xml = xml

    def get_text(self, url: str) -> str:
        return self._xml


def _street_record(event_id: str, location: str) -> dict:
    return {
        "event_id": event_id,
        "title": f"Street No City {event_id}",
        "start_local": "2026-10-01T12:00:00",
        "end_local": "2026-10-01T13:00:00",
        "location_html": location,
        "description": "",
        "slug": f"street-{event_id}",
        "categories": [],
        "admission": "",
        "all_day": False,
    }


def test_bcschamber_resolved_street_publishes_with_resolver_city():
    rec = _street_record("1", "Some New Hall<br />1234 Mapped Over Street")
    raw = bcschamber.to_raw(rec)
    raw.supplement["place_resolution"] = {
        "status": "matched",
        "city": "Bryan",
        "zip_code": "77801",
        "matched_address": "1234 Mapped Over Street, Bryan, TX 77801",
    }
    payloads, rejected = bcschamber.BcsChamberScraper().normalize([raw])
    assert rejected == []
    place = payloads[0]["series"]["place"]
    assert place["city"] == "Bryan"
    assert place["area"] == "bryan"
    assert place["postcode"] == "77801"
    assert "1234 Mapped Over Street" in (place.get("street") or "")


def test_bcschamber_resolved_out_of_area_drops_silently():
    rec = _street_record("2", "Some New Hall<br />1234 Mapped Over Street")
    raw = bcschamber.to_raw(rec)
    raw.supplement["place_resolution"] = {"status": "out_of_area"}
    payloads, rejected = bcschamber.BcsChamberScraper().normalize([raw])
    assert payloads == []
    assert rejected == []


def test_bcschamber_resolved_unmatched_city_drops():
    rec = _street_record("3", "Some New Hall<br />1234 Mapped Over Street")
    raw = bcschamber.to_raw(rec)
    raw.supplement["place_resolution"] = {
        "status": "matched",
        "city": "Hearne",
        "zip_code": "77859",
    }
    payloads, rejected = bcschamber.BcsChamberScraper().normalize([raw])
    assert payloads == []
    assert rejected == []


def test_bcschamber_resolved_no_match_rejects():
    rec = _street_record("4", "Some New Hall<br />1234 Mapped Over Street")
    raw = bcschamber.to_raw(rec)
    raw.supplement["place_resolution"] = {"status": "no_match"}
    payloads, rejected = bcschamber.BcsChamberScraper().normalize([raw])
    assert payloads == []
    assert [r["series_uid"] for r in rejected] == ["4"]
    assert "no_match" in rejected[0]["reason"]


def test_bcschamber_normalizing_twice_is_byte_identical(chamber_raw):
    first, _ = bcschamber.BcsChamberScraper().normalize(chamber_raw)
    second, _ = bcschamber.BcsChamberScraper().normalize(chamber_raw)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


#: Every payload the ladder accepted before the venue map / resolution steps
#: ran, pinned to the exact ``series.place`` it produced. 3849 and 3862 were
#: rejects then and are not pinned: they are new publishes, not regressions.
#: The live-only 3896/3898 are absent from this fixture by design.
#: Row = (event_id, slug, name, city, area, street, postcode); None = absent.
_PRE_LADDER_ROWS: tuple[tuple[str, str, str, str, str, str | None, str | None], ...] = (
    ("3745", "frost-bank-tower-point-branch-college-station", "Frost Bank Tower Point Branch",
     "College Station", "college_station", "4425 Texas 6 Frontage Rd in College Station", None),
    ("3747", "parc-at-traditions-bryan", "Parc at Traditions",
     "Bryan", "bryan", "3095 Club Drive in Bryan", None),
    ("3755", "the-kimbell-cultural-event-center-bryan", "The Kimbell Cultural Event Center",
     "Bryan", "bryan", "607 N Main Street", "77803"),
    ("3774", "1507-s-college-ave-bryan", "1507 S College Ave",
     "Bryan", "bryan", "1507 S College Ave", "77801"),
    ("3837", "12815-farm-to-market-rd-2154-college-station", "12815 Farm to Market Rd 2154",
     "College Station", "college_station", "12815 Farm to Market Rd 2154", "77845"),
    ("3839", "1718-briarcrest-drive-bryan", "1718 Briarcrest Drive",
     "Bryan", "bryan", "1718 Briarcrest Drive", "77802"),
    ("3840", "1461-greens-prairie-rd-college-station", "1461 Greens Prairie Rd",
     "College Station", "college_station", "1461 Greens Prairie Rd", "77845"),
    ("3854", "hilton-college-station-college-station", "Hilton College Station",
     "College Station", "college_station", None, None),
    ("3857", "7125-coyote-run-bryan", "7125 Coyote Run",
     "Bryan", "bryan", "7125 Coyote Run", "77808"),
    ("3864", "3037-east-29th-street-bryan", "3037 East 29th Street",
     "Bryan", "bryan", "3037 East 29th Street", "77802"),
    ("3870", "hudson-oaks-senior-community-bryan", "Hudson Oaks Senior Community",
     "Bryan", "bryan", "3345 University Dr. E", "77802"),
    ("3871", "1733-briarcrest-drive-bryan", "1733 Briarcrest Drive",
     "Bryan", "bryan", "1733 Briarcrest Drive", "77802"),
    ("3872", "1301-pavilion-avenue-college-station", "1301 Pavilion Avenue",
     "College Station", "college_station", "1301 Pavilion Avenue", "77845"),
    ("3873", "2405-texas-avenue-s-college-station", "2405 Texas Avenue S",
     "College Station", "college_station", "2405 Texas Avenue S", "77840"),
    ("3874", "505-university-drive-e-college-station", "505 University Drive E",
     "College Station", "college_station", "505 University Drive E", "77840"),
    ("3875", "lakeside-at-millican-reserve-college-station", "Lakeside at Millican Reserve",
     "College Station", "college_station", "3900 Millican Creek Trail", "77845"),
    ("3877", "1733-briarcrest-drive-bryan", "1733 Briarcrest Drive",
     "Bryan", "bryan", "1733 Briarcrest Drive", "77802"),
    ("3878", "307-s-main-st-bryan", "307 S. Main St",
     "Bryan", "bryan", "307 S. Main St", "77803"),
    ("3879", "1333-n-earl-rudder-freeway-bryan", "1333 N Earl Rudder Freeway",
     "Bryan", "bryan", "1333 N Earl Rudder Freeway", "77803"),
    ("3880", "first-financial-bank-college-station", "First Financial Bank",
     "College Station", "college_station", "2445 Harvey Mitchell Parkway South", None),
    ("3881", "hilton-garden-inn-college-station-bryan", "Hilton Garden Inn College Station",
     "Bryan", "bryan", "3081 University Drive East | Bryan", "77802"),
    ("3882", "brazos-center-bryan", "Brazos Center",
     "Bryan", "bryan", "3232 Briarcrest Drive", "77802"),
    ("3883", "3349-university-drive-e-bryan", "3349 University Drive E",
     "Bryan", "bryan", "3349 University Drive E", "77802"),
    ("3884", "2709-texas-avenue-south-college-station", "2709 Texas Avenue South",
     "College Station", "college_station", "2709 Texas Avenue South", "77840"),
    ("3885", "2425-earl-rudder-fwy-s-college-station", "2425 Earl Rudder Fwy S",
     "College Station", "college_station", "2425 Earl Rudder Fwy S", "77845"),
    ("3886", "2800-barron-road-college-station", "2800 Barron Road",
     "College Station", "college_station", "2800 Barron Road", "77845"),
    ("3887", "2701-s-texas-avenue-bryan", "2701 S Texas Avenue",
     "Bryan", "bryan", "2701 S Texas Avenue", "77802"),
    ("3888", "coopers-old-time-pit-bbq-college-station", "Coopers Old Time Pit BBQ",
     "College Station", "college_station", "3055 Earl Rudder Freeway South", "77845"),
)


def _expected_place(row: tuple) -> dict:
    _, slug, name, city, area, street, postcode = row
    place: dict[str, str] = {
        "slug": slug, "name": name, "city": city, "region": "TX", "area": area,
    }
    if street is not None:
        place["street"] = street
    if postcode is not None:
        place["postcode"] = postcode
    return place


def test_bcschamber_pre_ladder_place_is_byte_identical(chamber_normalized):
    """Every payload accepted before the ladder keeps its exact place dict."""
    payloads, _ = chamber_normalized
    for row in _PRE_LADDER_ROWS:
        event_id = row[0]
        place = _by_id(payloads, event_id)["series"]["place"]
        assert place == _expected_place(row), f"{event_id} place changed"


def test_conformance_bcschamber_payloads_pass_the_real_validator(chamber_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = chamber_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
