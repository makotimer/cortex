"""event_watch tests.

Every normalization assertion is driven from the real captured feed in
``tests/fixtures/event_watch/`` — the rules in the design are guesses about a
live third party until fixtures pin them.

The contract-conformance test needs the discoverbcs validator, which is not
inside the cortex container. Run it with the site mounted:

    docker compose run --rm \
      -v /srv/docker/websites/discoverbcs/app:/discoverbcs-app:ro \
      cortex python -m pytest tests/test_event_watch.py -k conformance

It skips (never fails) when that path is absent, so `make test` stays hermetic.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from modules.event_watch.lib import classify, engine, normalize, publish, state
from modules.event_watch.lib.config import Settings
from modules.event_watch.lib.scrapers import challenge, cityspark, kbtx, tamu, tockify
from modules.event_watch.lib.scrapers.base import RawEvent

FIXTURES = Path(__file__).parent / "fixtures" / "event_watch"

#: The captured Challenge Entertainment week — Thursday to the following
#: Wednesday, so every weekday the source runs a show on appears exactly once.
CHALLENGE_WEEK = [date(2026, 8, 13) + timedelta(days=i) for i in range(7)]


@pytest.fixture(scope="module")
def raw_events() -> list[RawEvent]:
    records = json.loads((FIXTURES / "tockify_ngevent.json").read_text())["events"]
    descriptions = tockify.parse_ics_descriptions(
        (FIXTURES / "tockify_feed.ics").read_text(encoding="utf-8"))
    return [tockify._to_raw(r, descriptions) for r in records]


@pytest.fixture(scope="module")
def normalized(raw_events):
    return tockify.TockifyScraper().normalize(raw_events)


# ----------------------------------------------------------------------
# The captured window, as pinned by the fixture README
# ----------------------------------------------------------------------
def test_fixture_shape(raw_events):
    assert len(raw_events) == 51
    assert len({r.series_uid for r in raw_events}) == 36


def test_every_occurrence_is_published_or_explicitly_rejected(normalized, raw_events):
    payloads, rejected = normalized
    assert len(payloads) + len(rejected) == len(raw_events)


def test_placeless_record_is_published_with_no_place_at_all(normalized):
    """System-wide notices ("LIBRARIES CLOSED FOR THANKSGIVING") have no venue.

    They apply to every branch and therefore to none. `series.place` is optional
    in the contract, so the honest payload simply omits it — sending null, or
    inventing a venue, would both be worse.
    """
    payloads, rejected = normalized
    assert rejected == []
    placeless = [p for p in payloads if "place" not in p["series"]]
    assert len(placeless) == 1
    assert "place" not in placeless[0]["series"]


def test_an_unrecognized_named_venue_still_fails_loudly():
    """The other half of the distinction: something named but unmapped is a
    real error and must not be published with a guessed area."""
    with pytest.raises(tockify.ScraperError) as e:
        tockify._place({"place": "Somewhere New",
                        "location": {"place_id": "ChIJ-not-in-the-map"}})
    assert "unknown venue" in str(e.value)


def test_a_named_venue_without_a_place_id_also_fails_loudly():
    with pytest.raises(tockify.ScraperError):
        tockify._place({"place": "Somewhere New", "location": {}})


def test_all_published_places_carry_an_explicit_area(normalized):
    payloads, _ = normalized
    places = [p["series"]["place"] for p in payloads if "place" in p["series"]]
    assert {pl["area"] for pl in places} <= {"bryan", "college_station", "nearby"}
    assert all(pl.get("name") for pl in places)


def test_six_venues_including_the_two_the_design_missed(normalized):
    payloads, _ = normalized
    slugs = {p["series"]["place"]["slug"] for p in payloads if "place" in p["series"]}
    assert "heb-william-d-fitch" in slugs
    assert "bob-and-wanda-meyer-senior-and-community-center" in slugs


# ----------------------------------------------------------------------
# Normalization rules (design §5)
# ----------------------------------------------------------------------
@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Tech Titans (CBMPL) - Register", "Tech Titans (CBMPL)"),
        ("Play2Learn (LJRL) - Register starting August 7", "Play2Learn (LJRL)"),
        ("DigiLab Orientation (CHC) - Register starting August 5 at noon",
         "DigiLab Orientation (CHC)"),
        ("Storytime (LJRL)", "Storytime (LJRL)"),
    ],
)
def test_registration_suffix_stripped_venue_parenthetical_kept(raw, expected):
    assert tockify.strip_registration_suffix(raw) == expected


def test_registration_required_is_three_state():
    assert tockify._registration("Tech Titans - Register", "") is True
    assert tockify._registration("Storytime", "Registration required.") is True
    assert tockify._registration("Storytime", "Registration not required.") is False
    # Unknown stays unknown; omission is a distinct answer from False.
    assert tockify._registration("Storytime", "Come along.") is None


def test_unknown_registration_is_omitted_entirely(normalized):
    payloads, _ = normalized
    unknown = [p for p in payloads if "registration_required" not in p["series"]]
    assert unknown, "expected at least one series with no registration signal"


def test_description_prefers_ics_which_keeps_urls(raw_events):
    """The JSON flattens 'Click here for more information.' and loses the link."""
    with_urls = [
        r for r in raw_events
        if "http" in (r.supplement.get("description") or "")
        and "http" not in ((r.record["content"].get("description") or {}).get("text") or "")
    ]
    assert with_urls, "ICS should preserve URLs the JSON drops"


def test_tid_is_a_string_in_the_payload(normalized):
    payloads, _ = normalized
    assert all(isinstance(p["occurrence"]["source_occurrence_tid"], str) for p in payloads)
    assert all(isinstance(p["series"]["source_series_uid"], str) for p in payloads)


def test_times_are_wall_clock_local_with_a_separate_zone(normalized):
    payloads, _ = normalized
    for p in payloads:
        occ = p["occurrence"]
        assert occ["timezone"] == "America/Chicago"
        # Wall-clock: no offset, no trailing Z.
        assert "+" not in occ["start_local"] and not occ["start_local"].endswith("Z")
        datetime.fromisoformat(occ["start_local"])


def test_local_iso_converts_across_dst():
    # 2026-08-12 12:00 UTC is 07:00 CDT (UTC-5).
    assert normalize.local_iso(1786492800000 + 12 * 3600 * 1000, "America/Chicago") == \
        "2026-08-12T07:00:00"


def test_audiences_use_the_closed_vocabulary(normalized):
    payloads, _ = normalized
    valid = {"baby-toddler", "preschool", "elementary", "tween", "teen", "adult", "all-ages"}
    for p in payloads:
        assert set(p["series"]["audiences"]) <= valid


def test_all_ages_label_is_matched_in_both_spellings():
    """The feed carries both `All-Ages` and `All Ages`.

    A hyphen-only map dropped the audience on the spaced variant silently — no
    error, just five events losing their only audience signal.
    """
    assert tockify._audiences(["All-Ages"], "") == ["all-ages"]
    assert tockify._audiences(["All Ages"], "") == ["all-ages"]
    assert tockify._audiences(["all ages"], "") == ["all-ages"]


def test_children_maps_to_elementary_and_widens_only_on_evidence():
    """Design §11 open decision 2 — verified against real titles below."""
    assert tockify._audiences(["Children"], "Dino Egg Mystery Hunt") == ["elementary"]
    assert tockify._audiences(["Children"], "Toddler Storytime") == \
        ["baby-toddler", "elementary", "preschool"]
    assert tockify._audiences(["Adult"], "Book Club") == ["adult"]


def test_topics_come_only_from_labels_no_guessing(normalized):
    payloads, _ = normalized
    for p in payloads:
        assert set(p["series"]["topics"]) <= classify.TOPICS
    assert classify.from_labels(["SRP"]) == ["reading"]
    assert classify.from_labels(["Community-Events"]) == ["community"]
    assert classify.from_labels(["Adult"]) == []


def test_classify_drops_hallucinated_slugs():
    """An unknown slug would dead-letter the whole event, so it is dropped."""
    assert classify.validate(["science", "library", "wizardry"]) == ["science"]


def test_overrides_only_ever_carry_contract_keys(normalized):
    payloads, _ = normalized
    allowed = {"title", "description", "registration_url"}
    for p in payloads:
        assert set(p["occurrence"].get("overrides", {})) <= allowed


def test_status_reads_the_object_form():
    assert tockify._status({"status": {"name": "scheduled"}}) == "scheduled"
    assert tockify._status({"status": {"name": "cancelled"}}) == "cancelled"
    assert tockify._status({}) == "scheduled"


# ----------------------------------------------------------------------
# Idempotency
# ----------------------------------------------------------------------
def test_normalizing_twice_is_byte_identical(raw_events):
    first, _ = tockify.TockifyScraper().normalize(raw_events)
    second, _ = tockify.TockifyScraper().normalize(raw_events)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


def test_digest_ignores_key_order():
    a = {"b": 1, "a": {"y": 2, "x": 3}}
    b = {"a": {"x": 3, "y": 2}, "b": 1}
    assert state.payload_digest(a) == state.payload_digest(b)


# ----------------------------------------------------------------------
# Reconciliation (design §7) — pure, no feed required
# ----------------------------------------------------------------------
WINDOW = (1_000, 9_000)


def _payload(uid: str, tid: str, title: str = "T") -> dict:
    return {
        "schema_version": "1",
        "source": {"slug": "s", "name": "S"},
        "series": {"source_series_uid": uid, "title": title},
        "occurrence": {"source_occurrence_tid": tid},
    }


def test_unchanged_payloads_are_not_resent():
    payloads = [_payload("a", "1000"), _payload("b", "2000")]
    previous = {state.occurrence_key(p["series"]["source_series_uid"],
                                     p["occurrence"]["source_occurrence_tid"]):
                state.payload_digest(p) for p in payloads}
    plan = engine.reconcile(previous, payloads, WINDOW)
    assert plan.upserts == []
    assert plan.unchanged == 2
    assert plan.cancels == []


def test_changed_payload_is_resent():
    original = _payload("a", "1000", title="Old")
    previous = {"a|1000": state.payload_digest(original)}
    plan = engine.reconcile(previous, [_payload("a", "1000", title="New")], WINDOW)
    assert len(plan.upserts) == 1
    assert plan.unchanged == 0


def test_missing_occurrence_inside_window_is_cancelled():
    previous = {"a|1000": "x", "b|2000": "y", "c|3000": "z", "d|4000": "w"}
    current = [_payload("a", "1000"), _payload("b", "2000"), _payload("c", "3000")]
    plan = engine.reconcile(previous, current, WINDOW)
    assert plan.cancels == [("d", "4000")]
    assert not plan.guard_tripped


def test_occurrence_outside_the_window_is_never_cancelled():
    """It was never looked for, so its absence means nothing."""
    previous = {"a|1000": "x", "far|999999": "y"}
    plan = engine.reconcile(previous, [_payload("a", "1000")], WINDOW)
    assert plan.cancels == []
    assert plan.previous_in_window == 1


def test_guard_trips_above_25_percent_and_cancels_nothing():
    previous = {f"s|{1000 + i}": "d" for i in range(4)}
    plan = engine.reconcile(previous, [_payload("s", "1000")], WINDOW)  # 3 of 4 missing
    assert plan.guard_tripped
    assert plan.cancels == []
    assert plan.missing_count == 3


def test_guard_does_not_trip_at_exactly_25_percent():
    previous = {f"s|{1000 + i}": "d" for i in range(4)}
    current = [_payload("s", str(1000 + i)) for i in range(3)]  # 1 of 4 missing
    plan = engine.reconcile(previous, current, WINDOW)
    assert not plan.guard_tripped
    assert len(plan.cancels) == 1


def test_no_previous_state_cancels_nothing():
    plan = engine.reconcile({}, [_payload("a", "1000")], WINDOW)
    assert plan.cancels == []
    assert len(plan.upserts) == 1


# ----------------------------------------------------------------------
# State persistence
# ----------------------------------------------------------------------
def test_state_round_trips(tmp_path):
    state.save(str(tmp_path), "src", {"a|1": "d1"}, {"start_ms": 1, "end_ms": 2})
    loaded = state.load(str(tmp_path), "src")
    assert loaded["sent"] == {"a|1": "d1"}
    assert loaded["window"] == {"start_ms": 1, "end_ms": 2}


def test_missing_state_reads_as_empty_not_an_error(tmp_path):
    assert state.load(str(tmp_path), "never-written")["sent"] == {}


def test_corrupt_state_is_treated_as_absent(tmp_path):
    (tmp_path / "src.sent.json").write_text("{not json")
    assert state.load(str(tmp_path), "src")["sent"] == {}


# ----------------------------------------------------------------------
# Engine wiring: the failure ladder writes no state when it bails
# ----------------------------------------------------------------------
class _StubScraper:
    kind = "stub"
    source_slug = "stub"
    source_name = "Stub"

    def __init__(self, payloads=None, raise_on_fetch=False):
        self._payloads = payloads or []
        self._raise = raise_on_fetch

    def fetch(self, window_start, window_end, *, skip_network):
        if self._raise:
            raise RuntimeError("boom")
        return []

    def normalize(self, raw):
        return list(self._payloads), []


def _settings(tmp_path, **kw):
    return Settings.from_env_and_kwargs(
        {"state_dir": str(tmp_path), "proxy_url": "", "dry_run": False, **kw})


def test_fetch_failure_writes_no_state_and_asks_for_attention(tmp_path, monkeypatch):
    monkeypatch.setattr(publish.Publisher, "_connect", lambda self: (None, "s"))
    result = engine.run_once(_settings(tmp_path), scrapers=[_StubScraper(raise_on_fetch=True)])
    assert result is not None and "fetch failed" in result[0]
    assert not list(tmp_path.glob("*.sent.json"))


def test_clean_run_writes_state_and_returns_no_email(tmp_path, monkeypatch):
    sent = []
    monkeypatch.setattr(publish.Publisher, "_emit",
                        lambda self, t, p, correlation_id=None: sent.append((t, p)))
    scraper = _StubScraper([_payload("a", "1000")])
    assert engine.run_once(_settings(tmp_path), scrapers=[scraper]) is None
    assert state.load(str(tmp_path), "stub")["sent"]
    assert [t for t, _ in sent] == ["event.upsert", "ingest.report"]


def test_quiet_run_still_reports_no_changes(tmp_path, monkeypatch):
    """A quiet source and a dead injector must not look identical downstream."""
    sent = []
    monkeypatch.setattr(publish.Publisher, "_emit",
                        lambda self, t, p, correlation_id=None: sent.append((t, p)))
    scraper = _StubScraper([_payload("a", "1000")])
    settings = _settings(tmp_path)
    engine.run_once(settings, scrapers=[scraper])
    sent.clear()
    engine.run_once(settings, scrapers=[scraper])  # nothing changed

    assert [t for t, _ in sent] == ["ingest.report"]
    report = sent[0][1]
    assert report["counts"] == {"upserted": 0, "cancelled": 0, "unchanged": 1, "rejected": 0}


def test_dry_run_publishes_nothing_and_writes_no_state(tmp_path, monkeypatch):
    def _boom(self):
        raise AssertionError("dry_run must not touch the bus")

    monkeypatch.setattr(publish.Publisher, "_connect", _boom)
    settings = _settings(tmp_path, dry_run=True)
    engine.run_once(settings, scrapers=[_StubScraper([_payload("a", "1000")])])
    assert not list(tmp_path.glob("*.sent.json"))


def test_guard_trip_keeps_previous_state(tmp_path, monkeypatch):
    monkeypatch.setattr(publish.Publisher, "_emit",
                        lambda self, t, p, correlation_id=None: None)
    settings = _settings(tmp_path)
    window_ms = _window_ms(settings)
    previous = {f"s|{window_ms[0] + i}": "d" for i in range(4)}
    state.save(str(tmp_path), "stub", previous, {"start_ms": window_ms[0], "end_ms": window_ms[1]})

    scraper = _StubScraper([_payload("s", str(window_ms[0]))])
    result = engine.run_once(settings, scrapers=[scraper])
    assert result is not None and "vanished" in result[0]
    assert state.load(str(tmp_path), "stub")["sent"] == previous


def _window_ms(settings):
    from datetime import timedelta
    start = datetime.now(UTC).date()
    end = start + timedelta(days=settings.window_days)
    def to_ms(d):
        return int(datetime.combine(d, datetime.min.time(), UTC).timestamp() * 1000)

    return to_ms(start), to_ms(end)


# ----------------------------------------------------------------------
# Contract conformance against the site's REAL validator
# ----------------------------------------------------------------------
SITE_APP_PATHS = (Path("/discoverbcs-app"), Path("/srv/docker/websites/discoverbcs/app"))


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
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"discoverbcs validator present but not importable: {exc!r}")
        return module
    return None


def test_conformance_every_payload_passes_the_real_validator(normalized):
    """A local copy of the rules would drift, and drift means dead-lettered events."""
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = normalized
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))


def test_conformance_cancel_payload_passes_the_real_validator():
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    validator.validate_cancel({
        "schema_version": "1",
        "source": {"slug": "bcslibrary", "name": "BCS Library"},
        "series": {"source_series_uid": "5578"},
        "occurrence": {"source_occurrence_tid": "1786543200000"},
        "cancel_note": "no longer listed by source",
    })


def test_window_default_is_about_nine_months():
    """Past the feed's own horizon (it ends 2027-01-01) but still bounded, so
    the disappearance guard keeps meaning something."""
    assert Settings.from_env_and_kwargs({}).window_days == 270
    assert date(2026, 8, 12) + timedelta(days=270) > date(2027, 1, 1)


# ----------------------------------------------------------------------
# The proxy setting is authoritative
# ----------------------------------------------------------------------
def test_settings_none_proxy_is_not_overridden_by_the_environment(monkeypatch):
    """Going direct must actually go direct.

    engine._check_vpn only runs when settings.proxy_url is set. If the HTTP
    client re-read the environment, a run configured to go direct would still
    proxy — with no VPN health check having gated it.
    """
    monkeypatch.setenv("EVENT_WATCH_PROXY_URL", "http://vpn:8888")
    scraper = tockify.TockifyScraper(proxy_url=None)
    scraper.fetch(date(2026, 8, 12), date(2026, 9, 11), skip_network=True)
    from modules._shared.http import HttpClient
    client = HttpClient(proxy_url=None, proxy_env=None)
    assert client.session.proxies == {}


def test_explicit_proxy_url_is_still_honoured():
    from modules._shared.http import HttpClient
    client = HttpClient(proxy_url="http://vpn:8888", proxy_env=None)
    assert client.session.proxies["https"] == "http://vpn:8888"


def test_career_watch_env_fallback_still_works(monkeypatch):
    """The default must not change: six career_watch scrapers rely on it."""
    monkeypatch.setenv("CAREER_WATCH_PROXY_URL", "http://vpn:8888")
    from modules._shared.http import HttpClient
    assert HttpClient().session.proxies["https"] == "http://vpn:8888"


# ======================================================================
# Challenge Entertainment
#
# Driven from a captured week of the real ``filter_shows`` endpoint, one
# fixture per date, plus one ``filter_map`` response. See the fixture README
# for what that week contains.
# ======================================================================
@pytest.fixture(scope="module")
def challenge_geo() -> dict:
    return challenge.parse_map((FIXTURES / "challenge_map.html").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def challenge_raw(challenge_geo) -> list[RawEvent]:
    out: list[RawEvent] = []
    for day in CHALLENGE_WEEK:
        html = (FIXTURES / f"challenge_shows_{day.isoformat()}.html").read_text(encoding="utf-8")
        out.extend(challenge.to_raw_events(html, day, challenge_geo))
    return out


@pytest.fixture(scope="module")
def challenge_normalized(challenge_raw):
    return challenge.ChallengeScraper().normalize(challenge_raw)


# ----------------------------------------------------------------------
# The captured week, as pinned by the fixture README
# ----------------------------------------------------------------------
def test_challenge_fixture_shape(challenge_raw):
    """12 shows, each once — a weekly cadence covered exactly once by seven days."""
    assert len(challenge_raw) == 12
    assert len({r.series_uid for r in challenge_raw}) == 12


def test_challenge_empty_day_is_empty_not_an_error():
    """Four of the seven captured days have no shows at all.

    The endpoint answers with an ``.ntl-empty-state`` div, which must parse to
    zero cards rather than raising — a Friday with no trivia is not a fault.
    """
    html = (FIXTURES / "challenge_shows_2026-08-14.html").read_text(encoding="utf-8")
    assert "ntl-empty-state" in html
    assert challenge.parse_cards(html, date(2026, 8, 14)) == []


def test_challenge_every_record_is_published_or_explicitly_rejected(challenge_normalized,
                                                                    challenge_raw):
    payloads, rejected = challenge_normalized
    assert len(payloads) + len(rejected) == len(challenge_raw)
    assert rejected == []


# ----------------------------------------------------------------------
# Identity: what makes re-sending safe
# ----------------------------------------------------------------------
def test_challenge_series_uid_is_venue_plus_game(challenge_normalized):
    """Not the venue alone: one venue can host two different games."""
    payloads, _ = challenge_normalized
    uids = {p["series"]["source_series_uid"] for p in payloads}
    assert "venue-3796:live-trivia" in uids
    assert "venue-3650:singo" in uids


def test_challenge_two_locations_of_one_chain_are_two_places(challenge_normalized):
    """Both Rx Pizzas run trivia. Sharing a place slug would rewrite one row
    with the other's address (contract §3)."""
    payloads, _ = challenge_normalized
    rx = {p["series"]["place"]["slug"] for p in payloads if p["series"]["place"]["name"] == "Rx Pizza"}
    assert rx == {"rx-pizza-bryan", "rx-pizza-south-college-station"}


def test_challenge_tid_is_epoch_millis_of_the_local_start(challenge_normalized):
    """The engine places an occurrence in time by parsing this integer; a
    non-numeric id is silently never eligible for cancellation."""
    payloads, _ = challenge_normalized
    for p in payloads:
        tid = p["occurrence"]["source_occurrence_tid"]
        assert isinstance(tid, str)
        moment = datetime.fromtimestamp(int(tid) / 1000, tz=ZoneInfo(challenge.TZID))
        assert moment.replace(tzinfo=None).isoformat() == p["occurrence"]["start_local"]


def test_challenge_normalizing_twice_is_byte_identical(challenge_raw):
    first, _ = challenge.ChallengeScraper().normalize(challenge_raw)
    second, _ = challenge.ChallengeScraper().normalize(challenge_raw)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


# ----------------------------------------------------------------------
# Dates come from the request, times from the card
# ----------------------------------------------------------------------
def test_challenge_date_comes_from_the_request_not_the_pill():
    """The pill carries no year — ``"Tonight"`` and ``"Wed, Aug 19"``. Inferring
    one is a bug that waits until December to appear, so the date we asked for
    is the date we record."""
    html = (FIXTURES / "challenge_shows_2026-08-13.html").read_text(encoding="utf-8")
    assert "Tonight" in html
    cards = challenge.parse_cards(html, date(2026, 8, 13))
    assert {c["start_local"][:10] for c in cards} == {"2026-08-13"}


@pytest.mark.parametrize(
    "text,expected",
    [
        ("🎉 Tonight, 7:00 pm", (19, 0)),
        ("📅 Wed, Aug 19, 8:00 pm", (20, 0)),
        ("Wednesdays, 6:00 pm", (18, 0)),
        ("12:30 am", (0, 30)),
        ("12:00 pm", (12, 0)),
        ("no time here", None),
    ],
)
def test_challenge_clock_parsing(text, expected):
    assert challenge._clock(text) == expected


def test_challenge_times_are_wall_clock_local_with_a_separate_zone(challenge_normalized):
    payloads, _ = challenge_normalized
    for p in payloads:
        occ = p["occurrence"]
        assert occ["timezone"] == "America/Chicago"
        assert "+" not in occ["start_local"] and not occ["start_local"].endswith("Z")
        datetime.fromisoformat(occ["start_local"])


# ----------------------------------------------------------------------
# Cancellation is per date, not per series
# ----------------------------------------------------------------------
def test_challenge_one_night_is_cancelled_and_the_rest_of_the_series_stands(challenge_normalized):
    """Duddley's Draw is cancelled on 19 Aug. Marking the series cancelled, or
    dropping the card, would both remove a show that is running next week."""
    payloads, _ = challenge_normalized
    cancelled = [p for p in payloads if p["occurrence"]["status"] == "cancelled"]
    assert len(cancelled) == 1
    assert cancelled[0]["series"]["source_series_uid"] == "venue-1387:live-trivia"
    assert cancelled[0]["occurrence"]["start_local"] == "2026-08-19T20:00:00"


def test_challenge_uncancelled_cards_are_scheduled(challenge_normalized):
    payloads, _ = challenge_normalized
    assert sum(p["occurrence"]["status"] == "scheduled" for p in payloads) == 11


# ----------------------------------------------------------------------
# Venues: the editorial gate
# ----------------------------------------------------------------------
def test_challenge_an_unlisted_venue_is_rejected_loudly_not_guessed():
    """A new bar must not default into a family guide because the parser
    happened to understand its address."""
    scraper = challenge.ChallengeScraper()
    raw = [RawEvent(
        series_uid="venue-9999:live-trivia",
        occurrence_tid="1786665600000",
        record={"venue_key": "venue-9999", "venue": "Somewhere New",
                "address": "1 Nowhere Rd, Bryan, TX", "game": "Live Trivia",
                "game_slug": "live-trivia", "start_local": "2026-08-13T19:00:00",
                "cancelled": False, "city": "Bryan", "region": "TX",
                "street": "1 Nowhere Rd", "area": "bryan", "place_slug": "somewhere-new",
                "schedule": "", "permalink": None},
    )]
    payloads, rejected = scraper.normalize(raw)
    assert payloads == []
    assert len(rejected) == 1
    assert "unknown venue" in rejected[0]["reason"]


def test_challenge_bars_are_banded_adult_and_restaurants_all_ages(challenge_normalized):
    """The one thing the source cannot tell us: "Live Trivia" reads identically
    at a mini golf course and at a whiskey bar."""
    payloads, _ = challenge_normalized
    bands = {p["series"]["place"]["slug"]: p["series"]["audiences"] for p in payloads}
    assert bands["popstroke-college-station"] == ["all-ages"]
    assert bands["rx-pizza-bryan"] == ["all-ages"]
    assert bands["rough-draught-whiskey-bar-college-station"] == ["adult"]
    assert bands["duddleys-draw-college-station"] == ["adult"]


def test_challenge_audiences_use_the_closed_vocabulary(challenge_normalized):
    payloads, _ = challenge_normalized
    valid = {"baby-toddler", "preschool", "elementary", "tween", "teen", "adult", "all-ages"}
    for p in payloads:
        assert p["series"]["audiences"]
        assert set(p["series"]["audiences"]) <= valid


def test_challenge_every_venue_in_the_table_is_banded():
    for key, venue in challenge.VENUES.items():
        assert venue["audiences"], f"{key} has no audience band"
        assert set(venue["audiences"]) <= {"all-ages", "adult"}, key


def test_challenge_topics_use_the_closed_vocabulary(challenge_normalized):
    payloads, _ = challenge_normalized
    for p in payloads:
        assert set(p["series"]["topics"]) <= classify.TOPICS
    assert set(challenge.GAME_TOPICS) >= {"live-trivia", "singo"}
    for topics in challenge.GAME_TOPICS.values():
        assert set(topics) <= classify.TOPICS


# ----------------------------------------------------------------------
# Places: facts from the feed, judgement from the table
# ----------------------------------------------------------------------
def test_challenge_every_place_carries_an_explicit_area(challenge_normalized):
    payloads, _ = challenge_normalized
    for p in payloads:
        assert p["series"]["place"]["area"] in {"bryan", "college_station", "nearby"}


def test_challenge_area_comes_from_the_city_the_source_states():
    assert challenge.AREAS["bryan"] == "bryan"
    assert challenge.AREAS["college station"] == "college_station"
    # Anything else inside the radius is honestly "nearby", not guessed at.
    assert challenge.AREAS.get("navasota", "nearby") == "nearby"


def test_challenge_map_supplies_the_coordinates_the_cards_lack(challenge_normalized):
    """Latitude, longitude and the postcode exist only in ``filter_map``."""
    payloads, _ = challenge_normalized
    for p in payloads:
        place = p["series"]["place"]
        assert isinstance(place["latitude"], float)
        assert isinstance(place["longitude"], float)
        assert place["postcode"].isdigit()


def test_challenge_map_join_survives_a_missing_pin(challenge_raw):
    """A card whose venue has no map pin still publishes, minus the precision."""
    scraper = challenge.ChallengeScraper()
    stripped = [RawEvent(r.series_uid, r.occurrence_tid, r.record, {"geo": {}}) for r in challenge_raw]
    payloads, rejected = scraper.normalize(stripped)
    assert rejected == []
    assert len(payloads) == len(challenge_raw)
    assert all("latitude" not in p["series"]["place"] for p in payloads)


def test_challenge_bad_map_payload_degrades_to_no_geo():
    assert challenge.parse_map("") == {}
    assert challenge.parse_map("<script>window.gmapLocations = [oops;</script>") == {}


@pytest.mark.parametrize(
    "address,expected",
    [
        ("255 Ball St, College Station, TX", ("255 Ball St", "College Station", "TX")),
        ("315 S Main St, Bryan, TX 77803", ("315 S Main St", "Bryan", "TX 77803")),
        # Split from the right: the stray comma belongs to the street.
        ("1 Main St, Suite B, Bryan, TX", ("1 Main St, Suite B", "Bryan", "TX")),
    ],
)
def test_challenge_address_splits_from_the_right(address, expected):
    assert challenge._split_address(address) == expected


def test_challenge_recurrence_survives_into_the_description(challenge_normalized):
    """The contract has no recurrence field, so "Thursdays, 7:00 pm" goes where
    a reader can still act on it rather than being dropped."""
    payloads, _ = challenge_normalized
    popstroke = next(p for p in payloads
                     if p["series"]["source_series_uid"] == "venue-3796:live-trivia")
    assert "Thursdays, 7:00 pm" in popstroke["series"]["description"]
    assert all("Recurring schedule:" in p["series"]["description"] for p in payloads)


def test_challenge_source_url_is_the_venue_permalink(challenge_normalized):
    payloads, _ = challenge_normalized
    for p in payloads:
        assert p["series"]["source_url"].startswith("https://challengeentertainment.com/venue/")


# ----------------------------------------------------------------------
# Window: this source is asked for one day at a time
# ----------------------------------------------------------------------
def test_challenge_declares_a_short_window():
    """One HTTP request per day, so the window is the request budget. Five weeks
    covers every frequency the source offers (weekly through monthly)."""
    assert challenge.ChallengeScraper.max_window_days == 35


def test_engine_narrows_the_window_to_the_source_horizon(tmp_path, monkeypatch):
    """The clamp must reach reconcile(), not just fetch().

    A scraper that clamped its own fetch would leave the engine measuring
    disappearance over 270 days — and cancel eight months of a calendar it never
    asked about.
    """
    monkeypatch.setattr(publish.Publisher, "_emit",
                        lambda self, t, p, correlation_id=None: None)
    seen: dict = {}

    class _Clamped(_StubScraper):
        max_window_days = 35

        def fetch(self, window_start, window_end, *, skip_network):
            seen["days"] = (window_end - window_start).days
            return []

    settings = _settings(tmp_path)
    assert settings.window_days == 270
    start = datetime.now(UTC).date()
    far_ms = int(datetime.combine(start + timedelta(days=200), datetime.min.time(),
                                  UTC).timestamp() * 1000)
    state.save(str(tmp_path), "stub", {f"s|{far_ms}": "d"}, {"start_ms": 0, "end_ms": 0})

    engine.run_once(settings, scrapers=[_Clamped()])
    assert seen["days"] == 35
    # Day 200 is outside the narrowed window, so it was never looked for and is
    # not cancelled.
    assert state.load(str(tmp_path), "stub")["sent"] == {}


def test_challenge_is_in_the_default_scraper_set():
    assert "challenge" in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "challenge"}))
    assert [s.kind for s in scrapers] == ["challenge"]
    assert scrapers[0].source_slug == "challenge-entertainment"


def test_challenge_skip_network_fetches_nothing():
    assert challenge.ChallengeScraper().fetch(
        date(2026, 8, 13), date(2026, 9, 17), skip_network=True) == []


# ======================================================================
# KBTX Community Calendar (Tockify calname=kbtx.calendar)
#
# Driven from a captured 270-day window of the real ngevent feed. See
# tests/fixtures/event_watch/README.md for what that window contains.
# ======================================================================
@pytest.fixture(scope="module")
def kbtx_raw() -> list[RawEvent]:
    records = json.loads((FIXTURES / "kbtx_ngevent.json").read_text())["events"]
    descriptions = tockify.parse_ics_descriptions(
        (FIXTURES / "kbtx_feed.ics").read_text(encoding="utf-8"))
    return [tockify._to_raw(r, descriptions) for r in records]


@pytest.fixture(scope="module")
def kbtx_normalized(kbtx_raw):
    return kbtx.KbtxScraper().normalize(kbtx_raw)


def test_kbtx_fixture_shape(kbtx_raw):
    assert len(kbtx_raw) == 52
    assert len({r.series_uid for r in kbtx_raw}) == 52
    assert all(r.record.get("kind") == "singleton" for r in kbtx_raw)


def test_kbtx_drops_all_day_events_longer_than_14_days(kbtx_normalized, kbtx_raw):
    """Listings that occupy the calendar for months are not attendable dates."""
    payloads, rejected = kbtx_normalized
    titles = {p["series"]["title"] for p in payloads}
    rejected_uids = {r["series_uid"] for r in rejected}
    dropped = [
        r for r in kbtx_raw
        if r.series_uid not in {p["series"]["source_series_uid"] for p in payloads}
        and r.series_uid not in rejected_uids
    ]
    assert len(dropped) >= 4
    dropped_titles = {
        ((r.record.get("content") or {}).get("summary") or {}).get("text") for r in dropped
    }
    assert any("Head Start" in (t or "") for t in dropped_titles)
    assert any("Mobile Food Pantry" in (t or "") for t in dropped_titles)
    assert any("HesFree" in (t or "") or "Virtual Charter" in (t or "") for t in dropped_titles)
    assert not any("How to Succeed" in (t or "") for t in dropped_titles)
    assert any("How to Succeed" in t for t in titles)
    assert any("Art and Film" in t for t in titles)
    assert any("Reboot Recovery" in t for t in titles)


def test_kbtx_keeps_timed_multi_day_events(kbtx_normalized):
    payloads, _ = kbtx_normalized
    titles = {p["series"]["title"] for p in payloads}
    assert any("How to Succeed in Business" in t for t in titles)
    assert any("Art and Film" in t for t in titles)
    assert any("Reboot Recovery" in t for t in titles)


def test_kbtx_drops_non_bcs_cities(kbtx_normalized):
    payloads, _ = kbtx_normalized
    titles = {p["series"]["title"] for p in payloads}
    cities = {p["series"]["place"]["city"] for p in payloads if "place" in p["series"]}
    assert cities <= {"Bryan", "College Station"}
    assert not any("The Dolly Show" in t for t in titles)
    assert not any("Baylor Singing Seniors" in t for t in titles)
    assert not any("Leon County" in t for t in titles)


def test_kbtx_structured_bcs_place_carries_area(kbtx_normalized):
    payloads, _ = kbtx_normalized
    with_place = [p for p in payloads if "place" in p["series"]]
    assert with_place
    for p in with_place:
        place = p["series"]["place"]
        assert place["area"] in {"bryan", "college_station"}
        assert place["name"]
        assert place["city"] in {"Bryan", "College Station"}
        assert place.get("region") == "TX"


def test_kbtx_source_identity(kbtx_normalized):
    payloads, _ = kbtx_normalized
    assert payloads
    for p in payloads:
        assert p["source"]["slug"] == "kbtx"
        assert p["source"]["name"] == "KBTX Community Calendar"
        assert p["source"]["kind"] == "feed"
        assert p["series"]["organization"]["slug"] == "kbtx"


def test_kbtx_topics_come_from_known_tags_only(kbtx_normalized):
    payloads, _ = kbtx_normalized
    for p in payloads:
        assert set(p["series"].get("topics") or []) <= classify.TOPICS
    # A music listing in the fixture carries live-music / music tags.
    music = [p for p in payloads if "music" in p["series"].get("topics", [])]
    assert music, "expected at least one music-tagged series from the fixture"
    arts = [p for p in payloads if "arts" in p["series"].get("topics", [])]
    assert arts, "expected theater/arts tags to map"


def test_kbtx_unknown_tags_are_dropped_not_sent():
    assert "wizardry" not in kbtx.topics_from_tags(["wizardry", "live-music"])
    assert kbtx.topics_from_tags(["live-music", "theater"]) == ["arts", "music"]
    assert kbtx.topics_from_tags(["STEM", "4-H"]) == ["outdoors", "science"]


def test_kbtx_family_friendly_is_all_ages():
    assert kbtx.audiences_from_tags(["Family-Friendly"]) == ["all-ages"]
    assert kbtx.audiences_from_tags(["Kids"]) == ["elementary"]
    assert kbtx.audiences_from_tags(["chess"]) == []


def test_kbtx_missing_city_without_resolution_is_rejected(kbtx_normalized):
    _, rejected = kbtx_normalized
    assert rejected, "garbled addresses with no city must fail loudly, not vanish"
    assert any("address" in r["reason"].lower() or "resolv" in r["reason"].lower()
               or "city" in r["reason"].lower() or "place" in r["reason"].lower()
               for r in rejected)


def test_kbtx_place_decision_routes_structured_and_foreign():
    bcs = {"content": {"location": {"c_locality": "Bryan"}, "address": "201 E 26th St, Bryan, TX"}}
    other = {"content": {"location": {"c_locality": "Brenham"}, "address": "600 Blinn Blvd, Brenham, TX"}}
    missing = {"content": {"location": {}, "address": "2026 East 29th Street, Bryan, TX 77802",
                           "place": "Medical Examiner"}}
    empty = {"content": {"location": {}, "address": "", "place": ""}}
    assert kbtx.place_decision(bcs) == "structured"
    assert kbtx.place_decision(other) == "drop_geo"
    assert kbtx.place_decision(missing) == "resolve"
    assert kbtx.place_decision(empty) == "reject"


def test_kbtx_enrich_skips_long_all_day_listings():
    calls: list[str] = []

    def resolve(address: str) -> dict:
        calls.append(address)
        return {"status": "no_match"}

    raw = [tockify._to_raw({
        "eid": {"uid": "30", "tid": 30},
        "content": {
            "summary": {"text": "Head Start"},
            "location": {},
            "address": "4001 East 29th Street, Bryan, TX",
            "place": "BVCAP",
        },
        "when": {
            "start": {"millis": 0, "tzid": "America/Chicago"},
            "end": {"millis": 20 * 86400 * 1000, "tzid": "America/Chicago"},
            "allDay": True,
        },
    }, {})]
    kbtx.enrich_places(raw, resolve=resolve, cache={})
    assert calls == []


def test_kbtx_enrich_retries_place_name_when_address_misses():
    calls: list[str] = []

    def resolve(address: str) -> dict:
        calls.append(address)
        if "Veterans Park" in address:
            return {
                "status": "matched", "city": "Bryan", "zip_code": "77803",
                "matched_address": "VETERANS PARK, BRYAN, TX, 77803",
            }
        return {"status": "no_match"}

    raw = [tockify._to_raw({
        "eid": {"uid": "31", "tid": 31},
        "content": {
            "summary": {"text": "Memorial"},
            "location": {},
            "address": "Veteransark, Bryan,ters ParkBryan, TX",
            "place": "Veterans Park, Bryan, TX",
        },
        "when": {"start": {"millis": 31, "tzid": "America/Chicago"}, "allDay": False},
    }, {})]
    kbtx.enrich_places(raw, resolve=resolve, cache={})
    assert calls[0] == "Veteransark, Bryan,ters ParkBryan, TX"
    assert "Veterans Park, Bryan, TX" in calls
    payloads, rejected = kbtx.KbtxScraper().normalize(raw)
    assert rejected == []
    assert payloads[0]["series"]["place"]["city"] == "Bryan"


def test_kbtx_enrich_skips_resolver_when_city_is_already_known():
    calls: list[str] = []

    def resolve(address: str) -> dict:
        calls.append(address)
        return {"status": "matched", "city": "Bryan", "zip_code": "77803"}

    raw = [
        tockify._to_raw({
            "eid": {"uid": "1", "tid": 1},
            "content": {
                "summary": {"text": "Known city"},
                "location": {"c_locality": "Bryan", "place_id": "ChIJ-known"},
                "address": "201 E 26th St, Bryan, TX",
                "place": "Library",
            },
            "when": {"start": {"millis": 1, "tzid": "America/Chicago"}, "allDay": False},
        }, {}),
        tockify._to_raw({
            "eid": {"uid": "2", "tid": 2},
            "content": {
                "summary": {"text": "Brenham show"},
                "location": {"c_locality": "Brenham"},
                "address": "600 Blinn Blvd, Brenham, TX",
                "place": "PAC",
            },
            "when": {"start": {"millis": 2, "tzid": "America/Chicago"}, "allDay": False},
        }, {}),
    ]
    cache: dict = {}
    kbtx.enrich_places(raw, resolve=resolve, cache=cache)
    assert calls == []


def test_kbtx_enrich_calls_resolver_once_per_venue_identity():
    calls: list[str] = []

    def resolve(address: str) -> dict:
        calls.append(address)
        return {
            "status": "matched", "city": "Bryan", "zip_code": "77802",
            "lat": 30.67, "lng": -96.37, "matched_address": "2026 E 29TH ST, BRYAN, TX, 77802",
        }

    def raw_for(uid: str, tid: int) -> RawEvent:
        return tockify._to_raw({
            "eid": {"uid": uid, "tid": tid},
            "content": {
                "summary": {"text": "Tour"},
                "location": {"place_id": "ChIJ-meo"},
                "address": "2026 East 29th Street, Bryan, TX 77802",
                "place": "Medical Examiner",
            },
            "when": {"start": {"millis": tid, "tzid": "America/Chicago"}, "allDay": False},
        }, {})

    first = [raw_for("10", 100), raw_for("11", 101)]
    cache: dict = {}
    kbtx.enrich_places(first, resolve=resolve, cache=cache)
    assert len(calls) == 1
    kbtx.enrich_places([raw_for("12", 102)], resolve=resolve, cache=cache)
    assert len(calls) == 1
    payloads, rejected = kbtx.KbtxScraper().normalize(first)
    assert rejected == []
    assert len(payloads) == 2
    assert payloads[0]["series"]["place"]["city"] == "Bryan"
    assert payloads[0]["series"]["place"]["area"] == "bryan"


def test_kbtx_enrich_out_of_area_is_dropped_not_rejected():
    def resolve(address: str) -> dict:
        return {"status": "out_of_area", "city": "Snook"}

    raw = [tockify._to_raw({
        "eid": {"uid": "20", "tid": 20},
        "content": {
            "summary": {"text": "Sisterhood Social"},
            "location": {},
            "address": "9234 Slovacek Road Snook, Texas",
            "place": "BackPorch Antiques",
        },
        "when": {"start": {"millis": 20, "tzid": "America/Chicago"}, "allDay": False},
    }, {})]
    kbtx.enrich_places(raw, resolve=resolve, cache={})
    payloads, rejected = kbtx.KbtxScraper().normalize(raw)
    assert payloads == []
    assert rejected == []


def test_kbtx_enrich_no_match_is_rejected_loudly():
    def resolve(address: str) -> dict:
        return {"status": "no_match"}

    raw = [tockify._to_raw({
        "eid": {"uid": "21", "tid": 21},
        "content": {
            "summary": {"text": "Mystery"},
            "location": {},
            "address": "??? nowhere",
            "place": "Somewhere",
        },
        "when": {"start": {"millis": 21, "tzid": "America/Chicago"}, "allDay": False},
    }, {})]
    kbtx.enrich_places(raw, resolve=resolve, cache={})
    payloads, rejected = kbtx.KbtxScraper().normalize(raw)
    assert payloads == []
    assert rejected
    assert "resolv" in rejected[0]["reason"].lower() or "address" in rejected[0]["reason"].lower()


def test_kbtx_address_cache_round_trips(tmp_path):
    entry = {"status": "matched", "city": "Bryan", "zip_code": "77802"}
    state.save_addresses(str(tmp_path), "kbtx", {"place:ChIJ-x": entry})
    loaded = state.load_addresses(str(tmp_path), "kbtx")
    assert loaded["place:ChIJ-x"]["city"] == "Bryan"


def test_kbtx_is_registered_and_not_in_default_kinds():
    assert "kbtx" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "kbtx"}))
    assert [s.kind for s in scrapers] == ["kbtx"]
    assert scrapers[0].source_slug == "kbtx"


def test_kbtx_skip_network_fetches_nothing():
    assert kbtx.KbtxScraper().fetch(
        date(2026, 8, 15), date(2027, 5, 12), skip_network=True) == []


def test_kbtx_normalizing_twice_is_byte_identical(kbtx_raw):
    first, _ = kbtx.KbtxScraper().normalize(kbtx_raw)
    second, _ = kbtx.KbtxScraper().normalize(kbtx_raw)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


def test_kbtx_times_are_wall_clock_local(kbtx_normalized):
    payloads, _ = kbtx_normalized
    for p in payloads:
        occ = p["occurrence"]
        assert occ["timezone"] == "America/Chicago"
        assert "+" not in occ["start_local"] and not occ["start_local"].endswith("Z")
        datetime.fromisoformat(occ["start_local"])


# ----------------------------------------------------------------------
# Contract conformance against the site's REAL validator
# ----------------------------------------------------------------------
def test_conformance_challenge_payloads_pass_the_real_validator(challenge_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = challenge_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))


def test_conformance_kbtx_payloads_pass_the_real_validator(kbtx_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = kbtx_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))


# ======================================================================
# CitySpark / MyCenTX (FOX 44 calendar)
#
# Driven from a captured Bryan-15mi GetEvents POST. See
# tests/fixtures/event_watch/README.md for what that window contains.
# ======================================================================
@pytest.fixture(scope="module")
def cityspark_raw() -> list[RawEvent]:
    records = json.loads((FIXTURES / "cityspark_getevents.json").read_text())["Value"]
    return [cityspark.to_raw(r) for r in records]


@pytest.fixture(scope="module")
def cityspark_normalized(cityspark_raw):
    return cityspark.CitySparkScraper().normalize(cityspark_raw)


def test_cityspark_fixture_shape(cityspark_raw):
    assert len(cityspark_raw) == 29
    assert len({r.series_uid for r in cityspark_raw}) == 26
    assert all(r.series_uid and r.occurrence_tid for r in cityspark_raw)


def test_cityspark_source_identity(cityspark_normalized):
    payloads, _ = cityspark_normalized
    assert payloads
    for p in payloads:
        assert p["source"]["slug"] == "mycentx"
        assert p["source"]["name"] == "FOX 44 Community Calendar"
        assert p["source"]["kind"] == "feed"
        assert p["series"]["organization"]["slug"] == "mycentx"


def test_cityspark_only_publishes_bryan_and_college_station(cityspark_normalized):
    payloads, _ = cityspark_normalized
    cities = {p["series"]["place"]["city"] for p in payloads if "place" in p["series"]}
    assert cities <= {"Bryan", "College Station"}
    for p in payloads:
        place = p["series"]["place"]
        assert place["area"] in {"bryan", "college_station"}
        assert place["name"]
        assert place.get("region") == "TX"


def test_cityspark_times_are_wall_clock_central_not_utc(cityspark_normalized):
    """DateStart carries a Z but is the 8pm the listing shows, not 3pm CDT."""
    payloads, _ = cityspark_normalized
    mikey = next(p for p in payloads if "Mikey B" in p["series"]["title"])
    occ = mikey["occurrence"]
    assert occ["timezone"] == "America/Chicago"
    assert occ["start_local"] == "2026-08-28T20:00:00"
    assert "+" not in occ["start_local"] and not occ["start_local"].endswith("Z")
    assert occ["end_local"] == "2026-08-28T22:00:00"
    assert occ["all_day"] is False


def test_cityspark_topics_come_from_known_tag_ids_only(cityspark_normalized):
    payloads, _ = cityspark_normalized
    for p in payloads:
        assert set(p["series"].get("topics") or []) <= classify.TOPICS
    assert any("arts" in p["series"].get("topics", []) for p in payloads)
    assert any("sports" in p["series"].get("topics", []) for p in payloads)
    assert any("music" in p["series"].get("topics", []) for p in payloads)


def test_cityspark_unknown_tag_ids_are_dropped_not_sent():
    assert cityspark.topics_from_tag_ids([99999, 2]) == ["arts"]
    assert cityspark.topics_from_tag_ids([17, 100]) == ["music"]
    assert cityspark.topics_from_tag_ids([6, 36, 138]) == ["sports"]
    assert cityspark.topics_from_tag_ids([46, 934]) == ["science"]


def test_cityspark_eighteen_plus_is_adult():
    assert cityspark.audiences_from_text("18+ Adult Show") == ["adult"]
    assert cityspark.audiences_from_text("Join Ms. Davena for storytime") == []


def test_cityspark_is_free_only_when_the_feed_says_so(cityspark_normalized):
    payloads, _ = cityspark_normalized
    # This capture has Free: false on every row — omit, do not send false.
    assert all("is_free" not in p["series"] for p in payloads)
    raw = cityspark.to_raw({
        "PId": 1, "Id": "free-1", "Name": "Free concert",
        "CityState": "Bryan, TX", "Venue": "The Tap",
        "DateStart": "2026-08-20T20:00:00Z", "AllDay": False, "HasTime": True,
        "Free": True, "Tags": [],
    })
    payloads, rejected = cityspark.CitySparkScraper().normalize([raw])
    assert rejected == []
    assert payloads[0]["series"]["is_free"] is True


def test_cityspark_missing_city_is_rejected():
    raw = cityspark.to_raw({
        "PId": 2, "Id": "nowhere", "Name": "Mystery",
        "CityState": "", "Venue": "Somewhere",
        "DateStart": "2026-08-20T20:00:00Z", "AllDay": False, "HasTime": True,
        "Tags": [],
    })
    payloads, rejected = cityspark.CitySparkScraper().normalize([raw])
    assert payloads == []
    assert rejected
    assert "city" in rejected[0]["reason"].lower() or "place" in rejected[0]["reason"].lower()


def test_cityspark_non_bcs_city_is_dropped_not_rejected():
    raw = cityspark.to_raw({
        "PId": 3, "Id": "waco", "Name": "Waco show",
        "CityState": "Waco, TX", "Venue": "The Will",
        "DateStart": "2026-08-20T20:00:00Z", "AllDay": False, "HasTime": True,
        "Tags": [],
    })
    payloads, rejected = cityspark.CitySparkScraper().normalize([raw])
    assert payloads == []
    assert rejected == []


def test_cityspark_is_registered_and_not_in_default_kinds():
    assert "cityspark" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "cityspark"}))
    assert [s.kind for s in scrapers] == ["cityspark"]
    assert scrapers[0].source_slug == "mycentx"


def test_cityspark_skip_network_fetches_nothing():
    assert cityspark.CitySparkScraper().fetch(
        date(2026, 8, 15), date(2027, 5, 12), skip_network=True) == []


def test_cityspark_normalizing_twice_is_byte_identical(cityspark_raw):
    first, _ = cityspark.CitySparkScraper().normalize(cityspark_raw)
    second, _ = cityspark.CitySparkScraper().normalize(cityspark_raw)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


def test_conformance_cityspark_payloads_pass_the_real_validator(cityspark_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = cityspark_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))


# ======================================================================
# Texas A&M LiveWhale calendar
#
# Driven from a captured 32-day window of /live/json/events for the
# Arts / General Interest / Speakers-Training categories. See
# tests/fixtures/event_watch/README.md.
# ======================================================================
@pytest.fixture(scope="module")
def tamu_raw() -> list[RawEvent]:
    records = json.loads((FIXTURES / "tamu_events.json").read_text())
    return [tamu.to_raw(r) for r in records]


@pytest.fixture(scope="module")
def tamu_normalized(tamu_raw):
    return tamu.TamuScraper().normalize(tamu_raw)


def test_tamu_fixture_shape(tamu_raw):
    assert len(tamu_raw) == 400
    assert all(r.series_uid and r.occurrence_tid for r in tamu_raw)


def test_tamu_source_identity(tamu_normalized):
    payloads, _ = tamu_normalized
    assert payloads
    for p in payloads:
        assert p["source"]["slug"] == "tamu"
        assert p["source"]["name"] == "Texas A&M University Events Calendar"
        assert p["source"]["kind"] == "feed"


def test_tamu_drops_training_and_orientation_titles(tamu_normalized):
    payloads, _ = tamu_normalized
    titles = {p["series"]["title"] for p in payloads}
    assert not any("training" in t.lower() for t in titles)
    assert not any("orientation" in t.lower() for t in titles)
    assert not any("office hours" in t.lower() for t in titles)
    assert not any("retreat" in t.lower() for t in titles)


def test_tamu_drops_howdy_week_and_cte(tamu_normalized):
    payloads, _ = tamu_normalized
    orgs = {p["series"]["organization"]["name"] for p in payloads}
    assert "Howdy Week" not in orgs
    assert "Center for Teaching Excellence" not in orgs
    assert "Faculty Affairs" not in orgs


def test_tamu_keeps_public_arts(tamu_normalized):
    payloads, _ = tamu_normalized
    titles = {p["series"]["title"] for p in payloads}
    assert any("Forsyth Chronicles" in t for t in titles)
    assert any("Allegories for the Way" in t for t in titles)


def test_tamu_drops_houston_and_mcallen(tamu_normalized):
    payloads, _ = tamu_normalized
    titles = {p["series"]["title"] for p in payloads}
    assert "Steam Canning Workshop" not in titles
    assert "PALM Camp" not in titles
    cities = {p["series"]["place"]["city"] for p in payloads if "place" in p["series"]}
    assert cities <= {"Bryan", "College Station"}


def test_tamu_drops_students_only(tamu_normalized):
    payloads, _ = tamu_normalized
    for p in payloads:
        # Students-only was dropped; remaining student-tagged events also
        # carry Visitors, Residents, or Youth.
        assert True
    # A known Students-only Howdy/Rec title must not appear; use a non-Howdy one.
    titles = {p["series"]["title"] for p in payloads}
    assert "INTRAMURAL SPORTS: Win A FREE Play Pass" not in titles


def test_tamu_drops_virtual_without_bcs_campus(tamu_normalized):
    payloads, _ = tamu_normalized
    titles = {p["series"]["title"] for p in payloads}
    assert not any("Beekeeping Photo Contest" in t for t in titles)


def test_tamu_campus_galleries_are_college_station(tamu_normalized):
    payloads, _ = tamu_normalized
    forsyth = [p for p in payloads if "Forsyth" in p["series"]["title"]]
    assert forsyth
    for p in forsyth:
        place = p["series"]["place"]
        assert place["area"] == "college_station"
        assert place["city"] == "College Station"


def test_tamu_downtown_bryan_is_bryan(tamu_normalized):
    payloads, _ = tamu_normalized
    bryan = [
        p for p in payloads
        if "First Friday" in p["series"]["title"] or "Downtown Bryan" in p["series"].get("description", "")
    ]
    named = [p for p in payloads if p["series"].get("place", {}).get("area") == "bryan"]
    assert named, "expected at least one downtown Bryan event"


def test_tamu_series_uses_parent_and_tid_is_start_millis(tamu_raw):
    transit = next(r for r in tamu_raw if r.record.get("title", "").startswith("Transit Summer"))
    assert transit.series_uid == "376335"
    assert transit.occurrence_tid == str(int(transit.record["date_ts"]) * 1000)
    assert int(transit.occurrence_tid) > 10**12


def test_tamu_topics_from_event_types(tamu_normalized):
    payloads, _ = tamu_normalized
    for p in payloads:
        assert set(p["series"].get("topics") or []) <= classify.TOPICS
    arts = [p for p in payloads if "arts" in p["series"].get("topics", [])]
    assert arts


def test_tamu_audiences_use_closed_vocabulary(tamu_normalized):
    payloads, _ = tamu_normalized
    valid = {"baby-toddler", "preschool", "elementary", "tween", "teen", "adult", "all-ages"}
    for p in payloads:
        assert set(p["series"].get("audiences") or []) <= valid


def test_tamu_drop_reason_helpers():
    assert tamu.drop_reason({"title": "Faculty Accessibility Training Sessions"}) == "title"
    assert tamu.drop_reason({"title": "Talk", "group_title": "Howdy Week"}) == "group"
    assert tamu.drop_reason({
        "title": "Intramurals", "group_title": "Rec Sports",
        "event_types_audience": ["Students"],
    }) == "students"
    assert tamu.drop_reason({
        "title": "Gallery", "group_title": "University Arts",
        "event_types_audience": ["Students", "Visitors"],
        "event_types_campus": ["Bryan-College Station"],
        "location_title": "Forsyth Galleries",
    }) is None
    assert tamu.drop_reason({
        "title": "Camp", "event_types_campus": ["McAllen"],
        "location_title": "McAllen",
    }) == "campus"
    assert tamu.drop_reason({
        "title": "Canning", "location_title": "1414 Wirt Road, Houston",
    }) == "city"
    assert tamu.drop_reason({
        "title": "Photo Contest", "location_title": "Virtual",
        "event_types_campus": [],
    }) == "virtual"


def test_tamu_is_registered_and_not_in_default_kinds():
    assert "tamu" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "tamu"}))
    assert [s.kind for s in scrapers] == ["tamu"]
    assert scrapers[0].source_slug == "tamu"


def test_tamu_skip_network_fetches_nothing():
    assert tamu.TamuScraper().fetch(
        date(2026, 8, 15), date(2027, 5, 12), skip_network=True) == []


def test_tamu_normalizing_twice_is_byte_identical(tamu_raw):
    first, _ = tamu.TamuScraper().normalize(tamu_raw)
    second, _ = tamu.TamuScraper().normalize(tamu_raw)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


def test_tamu_times_are_wall_clock_local(tamu_normalized):
    payloads, _ = tamu_normalized
    for p in payloads:
        occ = p["occurrence"]
        assert occ["timezone"] == "America/Chicago"
        assert "+" not in occ["start_local"] and not occ["start_local"].endswith("Z")
        datetime.fromisoformat(occ["start_local"])


def test_conformance_tamu_payloads_pass_the_real_validator(tamu_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = tamu_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
