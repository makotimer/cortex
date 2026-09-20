"""Brazos Valley Chess Association Google Calendar tests.

Fixture: ``bvchess_basic.ics`` — the public ICS for the "BVCA Monthly Event
Calendar" captured 2026-09-19 (12 monthly Swiss tournaments, one venue, no
RRULEs). The site's /events page is a Squarespace shell around this calendar.
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
from modules.event_watch.lib.scrapers import bvchess
from modules.event_watch.lib.scrapers.base import RawEvent

FIXTURES = Path(__file__).parent / "fixtures" / "event_watch"
SITE_APP_PATHS = (
    Path("/discoverbcs-app"),
    Path("/srv/docker/websites/discoverbcs/app"),
)
WINDOW_START = date(2026, 9, 19)
WINDOW_END = date(2027, 6, 16)


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
def ics_text() -> str:
    return (FIXTURES / "bvchess_basic.ics").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def bvchess_records(ics_text) -> list[dict]:
    return bvchess.parse_ics(ics_text)


@pytest.fixture(scope="module")
def bvchess_raw(bvchess_records) -> list[RawEvent]:
    return [bvchess.to_raw(rec) for rec in bvchess_records]


@pytest.fixture(scope="module")
def bvchess_windowed(bvchess_raw) -> list[RawEvent]:
    return [r for r in bvchess_raw if bvchess.in_window(r, WINDOW_START, WINDOW_END)]


@pytest.fixture(scope="module")
def bvchess_normalized(bvchess_windowed):
    return bvchess.BvChessScraper().normalize(bvchess_windowed)


def _september(payloads) -> dict:
    return next(p for p in payloads if p["series"]["title"] == "September BVCA Swiss")


# --- parse_ics -------------------------------------------------------------


def test_parse_ics_yields_one_record_per_vevent(bvchess_records):
    assert len(bvchess_records) == 12
    assert all(r["uid"] and r["summary"] and r["dtstart"] for r in bvchess_records)


def test_parse_ics_unfolds_and_unescapes_location(bvchess_records):
    sept = next(r for r in bvchess_records if r["summary"] == "September BVCA Swiss")
    assert sept["location"] == "First Baptist Church Bryan, 3100 Cambridge Dr, Bryan, TX 77802, USA"


def test_parse_ics_reads_utc_stamps_as_aware_datetimes(bvchess_records):
    sept = next(r for r in bvchess_records if r["summary"] == "September BVCA Swiss")
    assert sept["dtstart"].isoformat() == "2026-09-19T14:30:00+00:00"
    assert sept["dtend"].isoformat() == "2026-09-19T21:30:00+00:00"
    assert sept["all_day"] is False


def test_parse_ics_reads_tzid_and_all_day_forms():
    text = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:a@google.com\r\n"
        "DTSTART;TZID=America/Chicago:20261003T100000\r\n"
        "DTEND;TZID=America/Chicago:20261003T120000\r\n"
        "SUMMARY:Local\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:b@google.com\r\n"
        "DTSTART;VALUE=DATE:20261010\r\nDTEND;VALUE=DATE:20261011\r\n"
        "SUMMARY:Whole day\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    local, whole = bvchess.parse_ics(text)
    assert local["dtstart"].isoformat() == "2026-10-03T10:00:00-05:00"
    assert local["all_day"] is False
    assert whole["dtstart"] == date(2026, 10, 10)
    assert whole["dtend"] == date(2026, 10, 11)
    assert whole["all_day"] is True


def test_parse_ics_keeps_rrule_and_status(bvchess_records):
    text = (
        "BEGIN:VEVENT\r\nUID:r@google.com\r\nDTSTART:20261003T150000Z\r\n"
        "RRULE:FREQ=WEEKLY\r\nSTATUS:CANCELLED\r\nSUMMARY:Weekly\r\nEND:VEVENT\r\n"
    )
    (rec,) = bvchess.parse_ics(text)
    assert rec["rrule"] == "FREQ=WEEKLY"
    assert rec["status"] == "CANCELLED"
    assert all(r["rrule"] is None for r in bvchess_records)


# --- identity + window -----------------------------------------------------


def test_to_raw_uses_uid_and_utc_start_stamp(bvchess_raw):
    sept = next(r for r in bvchess_raw if r.record["summary"] == "September BVCA Swiss")
    assert sept.series_uid == "2l8dd6p2ouqibu72hvnnvla568@google.com"
    assert sept.occurrence_tid == "20260919T143000Z"


def test_in_window_uses_the_local_date(bvchess_raw):
    dec = next(r for r in bvchess_raw if r.record["summary"] == "December BVCA Swiss")
    assert bvchess.in_window(dec, date(2026, 12, 5), date(2026, 12, 5))
    assert not bvchess.in_window(dec, date(2026, 12, 6), date(2027, 1, 1))


def test_window_drops_past_tournaments(bvchess_windowed):
    titles = {r.record["summary"] for r in bvchess_windowed}
    assert titles == {
        "September BVCA Swiss", "October BVCA Swiss",
        "November BVCA Swiss", "December BVCA Swiss",
    }


# --- normalize -------------------------------------------------------------


def test_normalize_times_are_chicago_wall_clock(bvchess_normalized):
    payloads, rejected = bvchess_normalized
    assert rejected == []
    sept = _september(payloads)
    assert sept["occurrence"]["start_local"] == "2026-09-19T09:30:00"
    assert sept["occurrence"]["end_local"] == "2026-09-19T16:30:00"
    assert sept["occurrence"]["timezone"] == "America/Chicago"
    assert sept["occurrence"]["all_day"] is False
    assert sept["occurrence"]["status"] == "scheduled"
    # November is after the DST change: same 9:30 wall clock, different UTC.
    nov = next(p for p in payloads if p["series"]["title"] == "November BVCA Swiss")
    assert nov["occurrence"]["start_local"] == "2026-11-14T09:30:00"


def test_normalize_maps_the_venue_editorially(bvchess_normalized):
    payloads, _ = bvchess_normalized
    for p in payloads:
        place = p["series"]["place"]
        assert place["slug"] == "first-baptist-church-bryan"
        assert place["name"] == "First Baptist Church Bryan"
        assert place["street"] == "3100 Cambridge Dr"
        assert place["city"] == "Bryan"
        assert place["postcode"] == "77802"
        assert place["area"] == "bryan"
        assert place["indoor"] is True


def test_normalize_rejects_an_unknown_venue_loudly():
    item = RawEvent(
        series_uid="x@google.com",
        occurrence_tid="20261003T150000Z",
        record={
            "uid": "x@google.com", "summary": "Road Swiss",
            "dtstart": bvchess.parse_ics(
                "BEGIN:VEVENT\r\nUID:x\r\nDTSTART:20261003T150000Z\r\nEND:VEVENT\r\n"
            )[0]["dtstart"],
            "dtend": None, "all_day": False,
            "location": "Somewhere Else, Navasota, TX", "description": "",
            "status": "CONFIRMED", "rrule": None,
        },
    )
    payloads, rejected = bvchess.BvChessScraper().normalize([item])
    assert payloads == []
    assert len(rejected) == 1
    assert "Somewhere Else" in rejected[0]["reason"]
    assert rejected[0]["series_uid"] == "x@google.com"


def test_normalize_rejects_a_recurring_event_rather_than_expanding_it():
    (rec,) = bvchess.parse_ics(
        "BEGIN:VEVENT\r\nUID:r@google.com\r\nDTSTART:20261003T150000Z\r\n"
        "RRULE:FREQ=WEEKLY\r\nSUMMARY:Weekly\r\n"
        "LOCATION:First Baptist Church Bryan\\, 3100 Cambridge Dr\\, Bryan\\, TX 77802\\, USA\r\n"
        "END:VEVENT\r\n"
    )
    payloads, rejected = bvchess.BvChessScraper().normalize([bvchess.to_raw(rec)])
    assert payloads == []
    assert "RRULE" in rejected[0]["reason"]


def test_normalize_cancelled_status_carries_through():
    (rec,) = bvchess.parse_ics(
        "BEGIN:VEVENT\r\nUID:c@google.com\r\nDTSTART:20261003T150000Z\r\n"
        "STATUS:CANCELLED\r\nSUMMARY:Gone\r\n"
        "LOCATION:First Baptist Church Bryan\\, 3100 Cambridge Dr\\, Bryan\\, TX 77802\\, USA\r\n"
        "END:VEVENT\r\n"
    )
    payloads, _ = bvchess.BvChessScraper().normalize([bvchess.to_raw(rec)])
    assert payloads[0]["occurrence"]["status"] == "cancelled"


def test_normalize_description_is_plain_text(bvchess_normalized):
    payloads, _ = bvchess_normalized
    desc = _september(payloads)["series"]["description"]
    assert "<" not in desc
    assert "Registration Form" in desc
    assert "Round 1: 9:30 AM" in desc
    assert "(45 minutes with a delay" in desc  # nbsp in the source
    assert "Please bring your own board and clock" in desc
    assert "Saturday, September" in desc


def test_normalize_registration_form_link_becomes_registration_url(bvchess_normalized):
    payloads, _ = bvchess_normalized
    series = _september(payloads)["series"]
    assert series["registration_required"] is True
    assert series["registration_url"].startswith("https://")
    assert "docs.google.com/forms" in series["registration_url"] or "forms.gle" in series["registration_url"]


def test_normalize_entry_fee_is_the_cost(bvchess_normalized):
    payloads, _ = bvchess_normalized
    series = _september(payloads)["series"]
    assert series["is_free"] is False
    assert series["cost_low_cents"] == 2500
    assert series["cost_note"] == "$25 CASH entry fee."
    assert series["cost_basis"] == "per_person"


def test_normalize_source_identity_topics_and_link(bvchess_normalized):
    payloads, _ = bvchess_normalized
    p = _september(payloads)
    assert p["source"] == {"slug": "bvchess", "name": "Brazos Valley Chess Association", "kind": "feed"}
    assert p["series"]["organization"]["slug"] == "bvchess"
    assert p["series"]["organization"]["website_url"] == "https://www.brazosvalleychess.com/"
    assert p["series"]["source_url"] == "https://www.brazosvalleychess.com/events"
    assert p["series"]["topics"] == ["sports"]
    assert p["series"]["audiences"] == []
    assert "age_min" not in p["series"]
    assert p["series"]["source_series_uid"].endswith("@google.com")


# --- plumbing --------------------------------------------------------------


def test_bvchess_is_registered_and_not_in_default_kinds():
    assert "bvchess" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "bvchess"}))
    assert [s.kind for s in scrapers] == ["bvchess"]
    assert scrapers[0].source_slug == "bvchess"
    assert scrapers[0].verify_url == bvchess.ICS_URL


def test_bvchess_skip_network_fetches_nothing():
    assert bvchess.BvChessScraper().fetch(WINDOW_START, WINDOW_END, skip_network=True) == []


def test_bvchess_normalizing_twice_is_byte_identical(bvchess_windowed):
    first, _ = bvchess.BvChessScraper().normalize(bvchess_windowed)
    second, _ = bvchess.BvChessScraper().normalize(bvchess_windowed)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


def test_conformance_bvchess_payloads_pass_the_real_validator(bvchess_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = bvchess_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
