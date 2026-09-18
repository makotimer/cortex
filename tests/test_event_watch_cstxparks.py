"""College Station Parks & Rec RecTrac event_watch tests.

Fixture captured 2026-09-14 through gluetun from
``https://rectrac.cstx.gov/webtrac/web/search.html?module=AR&category=Events``.
Vermont Systems WebTrac 3.1; Events category only. Encoding repaired
(RecTrac sends UTF-8 without a charset; requests guessed latin-1).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from modules.event_watch.lib import engine, state
from modules.event_watch.lib.config import Settings
from modules.event_watch.lib.scrapers import cstxparks
from modules.event_watch.lib.scrapers.base import RawEvent

FIXTURES = Path(__file__).parent / "fixtures" / "event_watch"
SITE_APP_PATHS = (
    Path("/discoverbcs-app"),
    Path("/srv/docker/websites/discoverbcs/app"),
)
WINDOW_START = date(2026, 8, 1)
WINDOW_END = date(2027, 5, 1)


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
def search_html() -> str:
    return (FIXTURES / "cstxparks_search.html").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def cstx_rows(search_html) -> list[dict]:
    return cstxparks.parse_search(search_html)


@pytest.fixture(scope="module")
def cstx_raw(cstx_rows) -> list[RawEvent]:
    return [cstxparks.to_raw(row) for row in cstx_rows]


@pytest.fixture(scope="module")
def cstx_normalized(cstx_raw):
    return cstxparks.CstxParksScraper().normalize(cstx_raw)


def test_fixture_is_five_activities_ten_sections(cstx_rows):
    assert len(cstx_rows) == 10
    assert {r["code"] for r in cstx_rows} == {
        "LCN-41", "LCN-96", "PKS-01", "PKS-17", "PKS-31",
    }


def test_lick_creek_overnight_is_one_series_two_fridays(cstx_raw, cstx_normalized):
    dark = [r for r in cstx_raw if r.series_uid == "lcn-41"]
    assert len(dark) == 2
    payloads, _ = cstx_normalized
    published = [p for p in payloads if p["series"]["source_series_uid"] == "lcn-41"]
    assert len(published) == 2
    first = next(
        p for p in published
        if p["occurrence"]["start_local"] == "2026-11-06T18:00:00"
    )
    assert first["occurrence"]["end_local"] == "2026-11-07T07:00:00"
    assert first["occurrence"]["timezone"] == "America/Chicago"
    assert first["occurrence"]["all_day"] is False
    assert first["occurrence"]["source_occurrence_tid"] == "1794009600000"
    assert first["series"]["title"] == "Lick Creek Park In The Dark"
    assert first["series"]["place"]["name"] == "Lick Creek Park"
    assert first["series"]["place"]["street"] == "13600 Rock Prairie Rd"
    assert first["series"]["place"]["city"] == "College Station"
    assert first["series"]["place"]["area"] == "college_station"
    assert first["series"]["indoor"] is False
    assert "camp" in first["series"]["topics"]
    assert "outdoors" in first["series"]["topics"]
    assert first["series"]["audiences"] == ["all-ages"]
    assert first["series"]["registration_required"] is True
    assert first["series"]["cost_low_cents"] == 800
    parsed = urlparse(first["series"]["source_url"])
    assert parsed.path.endswith("/iteminfo.html")
    qs = parse_qs(parsed.query)
    assert qs["FMID"] == ["79072183"]
    assert "SessionID" not in qs
    assert "_csrf_token" not in qs


def test_unavailable_grandparents_day_is_still_published(cstx_normalized):
    payloads, _ = cstx_normalized
    walk = next(p for p in payloads if p["series"]["source_series_uid"] == "lcn-96")
    assert walk["series"]["title"] == "Walk Like an Engineer \u2013 Grandparents Day"
    assert walk["occurrence"]["start_local"] == "2026-09-13T08:30:00"
    assert walk["occurrence"]["end_local"] == "2026-09-13T10:30:00"
    assert walk["series"]["age_min"] == 1
    assert walk["series"]["age_max"] == 99
    assert walk["series"]["cost_low_cents"] == 500


def test_history_luncheon_is_one_series_five_wednesdays(cstx_raw, cstx_normalized):
    raw = [r for r in cstx_raw if r.series_uid == "pks-17"]
    assert len(raw) == 5
    payloads, _ = cstx_normalized
    published = [p for p in payloads if p["series"]["source_series_uid"] == "pks-17"]
    assert len(published) == 5
    assert {p["series"]["title"] for p in published} == {"Exploring History Luncheon"}
    october = next(
        p for p in published
        if p["occurrence"]["start_local"] == "2026-10-21T11:30:00"
    )
    assert october["occurrence"]["end_local"] == "2026-10-21T13:00:00"
    assert october["occurrence"].get("overrides", {}).get("title") == (
        "Exploring History Luncheon - October"
    )
    assert october["series"]["place"]["name"] == "Larry J. Ringer Library"
    assert october["series"]["place"]["street"] == "1818 Harvey Mitchell Pkwy S"
    assert october["series"]["indoor"] is True
    assert "history" in october["series"]["topics"]
    assert "is_free" not in october["series"]
    assert october["series"]["cost_low_cents"] == 1000


def test_movie_in_the_park_is_free(cstx_normalized):
    payloads, _ = cstx_normalized
    movie = next(p for p in payloads if p["series"]["source_series_uid"] == "pks-31")
    assert movie["series"]["title"] == "Movie in the Park: Jurassic Park (PG-13)"
    assert movie["occurrence"]["start_local"] == "2026-11-06T17:00:00"
    assert movie["occurrence"]["end_local"] == "2026-11-06T22:00:00"
    assert movie["series"]["is_free"] is True
    assert movie["series"]["cost_low_cents"] == 0
    assert movie["series"]["place"]["name"] == "Wolf Pen Creek Park"
    assert movie["series"]["place"]["street"] == "1015 Colgate Dr"
    assert movie["series"]["indoor"] is False


def test_jingle_bell_fun_run_uses_rectrac_clock_not_the_blurb(cstx_normalized):
    payloads, _ = cstx_normalized
    run = next(p for p in payloads if p["series"]["source_series_uid"] == "pks-01")
    assert run["occurrence"]["start_local"] == "2026-12-05T15:00:00"
    assert run["occurrence"]["end_local"] == "2026-12-05T18:00:00"
    assert run["series"]["place"]["name"] == "Stephen C. Beachy Central Park"
    assert run["series"]["place"]["street"] == "1000 Krenek Tap Rd"
    assert run["series"]["cost_low_cents"] == 1500


def test_source_identity(cstx_normalized):
    payloads, rejected = cstx_normalized
    assert rejected == []
    assert len(payloads) == 10
    for p in payloads:
        assert p["source"]["slug"] == "cstxparks"
        assert p["source"]["name"] == "College Station Parks and Recreation"
        assert p["source"]["kind"] == "feed"
        assert p["series"]["organization"]["slug"] == "cstx-parks"
        assert p["series"]["place"]["area"] == "college_station"
        assert p["series"]["place"]["region"] == "TX"
        assert p["series"]["place"]["city"] == "College Station"


def test_unknown_location_is_rejected(cstx_raw):
    item = cstx_raw[0]
    bad = RawEvent(
        series_uid=item.series_uid,
        occurrence_tid=item.occurrence_tid,
        record={**item.record, "location": "Some Other City Park"},
    )
    payloads, rejected = cstxparks.CstxParksScraper().normalize([bad])
    assert payloads == []
    assert len(rejected) == 1
    assert "unknown venue" in rejected[0]["reason"]


def test_result_range_from_fixture(search_html):
    assert cstxparks.result_range(search_html) == (1, 10, 10)


def test_fetch_walks_begrow_until_the_range_is_complete(search_html):
    page2 = search_html.replace(
        "Showing results 1-10 of 10",
        "Showing results 11-11 of 11",
        1,
    )
    page1 = search_html.replace(
        "Showing results 1-10 of 10",
        "Showing results 1-10 of 11",
        1,
    )

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[dict | None] = []

        def get_text(self, url, *, params=None, encoding=None, **kwargs):
            self.calls.append(params)
            beg = int((params or {}).get("begrow") or 1)
            return page1 if beg == 1 else page2

    scraper = cstxparks.CstxParksScraper()
    scraper._client = FakeClient()
    raw = scraper.fetch(WINDOW_START, WINDOW_END, skip_network=False)
    assert [c.get("begrow") if c else None for c in scraper._client.calls] == [None, 11]
    # Page 2 reuses the same 10 FMIDs; fetch must not duplicate.
    assert len(raw) == 10
    assert len({r.occurrence_tid for r in raw}) == 10


def test_fetch_drops_rows_outside_the_window(search_html):
    class FakeClient:
        def get_text(self, url, *, params=None, encoding=None, **kwargs):
            return search_html

    scraper = cstxparks.CstxParksScraper()
    scraper._client = FakeClient()
    raw = scraper.fetch(date(2026, 9, 14), date(2027, 5, 1), skip_network=False)
    uids = {r.series_uid for r in raw}
    assert "lcn-96" not in uids  # 2026-09-13
    assert "pks-17" in uids
    lunch = [r for r in raw if r.series_uid == "pks-17"]
    starts = {r.record["start"].date() for r in lunch}
    assert date(2026, 8, 19) not in starts
    assert date(2026, 10, 21) in starts


def test_cstxparks_is_registered_and_not_in_default_kinds():
    assert "cstxparks" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(
        Settings.from_env_and_kwargs({"kinds": "cstxparks"})
    )
    assert [s.kind for s in scrapers] == ["cstxparks"]
    assert scrapers[0].source_slug == "cstxparks"


def test_cstxparks_skip_network_fetches_nothing():
    assert cstxparks.CstxParksScraper().fetch(
        WINDOW_START, WINDOW_END, skip_network=True
    ) == []


def test_normalizing_twice_is_byte_identical(cstx_raw):
    first, _ = cstxparks.CstxParksScraper().normalize(cstx_raw)
    second, _ = cstxparks.CstxParksScraper().normalize(cstx_raw)
    assert [state.payload_digest(p) for p in first] == [
        state.payload_digest(p) for p in second
    ]


def test_conformance_cstxparks_payloads_pass_the_real_validator(cstx_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = cstx_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
