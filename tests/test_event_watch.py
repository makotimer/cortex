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

import pytest

from modules.event_watch.lib import classify, engine, normalize, publish, state
from modules.event_watch.lib.config import Settings
from modules.event_watch.lib.scrapers import tockify
from modules.event_watch.lib.scrapers.base import RawEvent

FIXTURES = Path(__file__).parent / "fixtures" / "event_watch"


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
