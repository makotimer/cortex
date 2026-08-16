"""KBTX Marketplace (incentRev) coupon tests.

Fixture captured 2026-08-16 from ``https://kbtx.incentrev.com/bryan/deals``.
A new coupon is posted as one all-day occurrence on the day it first
appears; later runs keep that date.
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
from modules.event_watch.lib.scrapers import kbtxdeals
from modules.event_watch.lib.scrapers.base import RawEvent

FIXTURES = Path(__file__).parent / "fixtures" / "event_watch"
SITE_APP_PATHS = (
    Path("/discoverbcs-app"),
    Path("/srv/docker/websites/discoverbcs/app"),
)
TODAY = date(2026, 8, 16)


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
def deals() -> list[dict]:
    return kbtxdeals.parse_deals(
        (FIXTURES / "kbtxdeals.html").read_text(encoding="utf-8")
    )


@pytest.fixture(scope="module")
def deals_stamped(deals) -> list[dict]:
    cards = [dict(d) for d in deals]
    kbtxdeals.stamp_first_seen(cards, {}, TODAY)
    return cards


@pytest.fixture(scope="module")
def deals_raw(deals_stamped) -> list[RawEvent]:
    return [kbtxdeals.to_raw(d) for d in deals_stamped]


@pytest.fixture(scope="module")
def deals_normalized(deals_raw):
    return kbtxdeals.KbtxDealsScraper().normalize(deals_raw)


def test_kbtxdeals_parses_ten_merchants(deals):
    assert len(deals) == 10
    merchants = [d["merchant"] for d in deals]
    assert merchants[0] == "Feral Fitness"
    assert "Degallery" in merchants
    assert "Quinntastic Bites" in merchants
    assert {d["deal_id"] for d in deals} == {
        "9052658", "9052669", "9052668", "9052662", "9052638",
        "9052639", "9052654", "9052641", "9052648", "9052647",
    }


def test_kbtxdeals_feral_fitness_prices_and_headline(deals):
    feral = next(d for d in deals if d["deal_id"] == "9052658")
    assert feral["headline"] == (
        "1 Month Membership and 2 Personal Training Sessions for $35 (a $170 Value!)"
    )
    assert feral["pay_cents"] == 3500
    assert feral["was_cents"] == 17000
    assert feral["url"].endswith("/bryan/9052658-feral-fitness")


def test_kbtxdeals_new_coupon_is_posted_on_first_seen_day(deals):
    cards = [dict(d) for d in deals]
    seen = kbtxdeals.stamp_first_seen(cards, {}, date(2026, 8, 16))
    assert all(c["first_seen"] == date(2026, 8, 16) for c in cards)
    later = [dict(d) for d in deals]
    kbtxdeals.stamp_first_seen(later, seen, date(2026, 8, 20))
    assert all(c["first_seen"] == date(2026, 8, 16) for c in later)
    extra = {
        "deal_id": "9999999",
        "merchant": "New Shop",
        "headline": "Half off",
        "url": "https://kbtx.incentrev.com/bryan/9999999-new-shop",
    }
    later.append(extra)
    kbtxdeals.stamp_first_seen(later, seen, date(2026, 8, 20))
    assert extra["first_seen"] == date(2026, 8, 20)


def test_kbtxdeals_occurrence_is_all_day_on_first_seen(deals_normalized):
    payloads, _ = deals_normalized
    feral = next(p for p in payloads if p["series"]["source_series_uid"] == "9052658")
    assert feral["series"]["title"] == (
        "Feral Fitness: 1 Month Membership and 2 Personal Training Sessions "
        "for $35 (a $170 Value!)"
    )
    assert feral["occurrence"]["all_day"] is True
    assert feral["occurrence"]["start_local"] == "2026-08-16T00:00:00"
    assert feral["occurrence"]["timezone"] == "America/Chicago"
    assert feral["series"]["cost_low_cents"] == 3500
    assert feral["series"]["cost_note"] == "$35 (was $170)"
    assert feral["series"]["topics"] == ["sports"]
    assert feral["series"]["registration_url"].endswith("/bryan/9052658-feral-fitness")


def test_kbtxdeals_degallery_is_arts(deals_normalized):
    payloads, _ = deals_normalized
    art = next(p for p in payloads if p["series"]["source_series_uid"] == "9052641")
    assert art["series"]["title"].startswith("Degallery:")
    assert art["series"]["topics"] == ["arts"]


def test_kbtxdeals_place_and_source_identity(deals_normalized):
    payloads, rejected = deals_normalized
    assert rejected == []
    assert len(payloads) == 10
    for p in payloads:
        assert p["source"]["slug"] == "kbtx-marketplace"
        assert p["source"]["name"] == "KBTX Marketplace"
        assert p["series"]["organization"]["slug"] == "kbtx-marketplace"
        place = p["series"]["place"]
        assert place["city"] == "Bryan"
        assert place["area"] == "bryan"
        assert p["series"]["audiences"] == ["all-ages"]


def test_kbtxdeals_is_registered_and_not_in_default_kinds():
    assert "kbtxdeals" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(
        Settings.from_env_and_kwargs({"kinds": "kbtxdeals"})
    )
    assert [s.kind for s in scrapers] == ["kbtxdeals"]
    assert scrapers[0].source_slug == "kbtx-marketplace"


def test_kbtxdeals_skip_network_fetches_nothing():
    assert kbtxdeals.KbtxDealsScraper().fetch(
        TODAY, date(2026, 12, 31), skip_network=True
    ) == []


def test_kbtxdeals_normalizing_twice_is_byte_identical(deals_raw):
    first, _ = kbtxdeals.KbtxDealsScraper().normalize(deals_raw)
    second, _ = kbtxdeals.KbtxDealsScraper().normalize(deals_raw)
    assert [state.payload_digest(p) for p in first] == [
        state.payload_digest(p) for p in second
    ]


def test_conformance_kbtxdeals_payloads_pass_the_real_validator(deals_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = deals_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
