"""Destination Bryan event_watch tests.

Listing fixtures: ``destbryan_list_page1.html`` / ``page2.html``.
Detail fixtures: ``destbryan_details.json`` (schema.org Event JSON-LD).
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
from modules.event_watch.lib.scrapers import destbryan
from modules.event_watch.lib.scrapers.base import RawEvent

FIXTURES = Path(__file__).parent / "fixtures" / "event_watch"
SITE_APP_PATHS = (
    Path("/discoverbcs-app"),
    Path("/srv/docker/websites/discoverbcs/app"),
)


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
def destbryan_cards() -> list[dict]:
    out: list[dict] = []
    for name in ("destbryan_list_page1.html", "destbryan_list_page2.html"):
        out.extend(destbryan.parse_list_html((FIXTURES / name).read_text(encoding="utf-8")))
    return out


@pytest.fixture(scope="module")
def destbryan_raw(destbryan_cards) -> list[RawEvent]:
    details = json.loads((FIXTURES / "destbryan_details.json").read_text(encoding="utf-8"))
    raw = []
    for card in destbryan_cards:
        item = destbryan.to_raw(card)
        href = card.get("href") or ""
        if href in details:
            item.supplement["jsonld"] = details[href]
        raw.append(item)
    return raw


@pytest.fixture(scope="module")
def destbryan_normalized(destbryan_raw):
    return destbryan.DestBryanScraper().normalize(destbryan_raw)


def test_destbryan_fixture_shape(destbryan_cards):
    assert len(destbryan_cards) >= 23
    assert all(c.get("entry_id") and c.get("title") for c in destbryan_cards)


def test_destbryan_same_entry_on_two_days_is_two_cards():
    """A residency is one Craft entry listed once per night."""
    html = """
    <div>
    <article class="card" data-entry-id="1">
      <a data-dms-partner-name="Live Music" href="/events/a/"></a>
      <span class="card__date-heading">August 15</span>
    </article>
    <article class="card" data-entry-id="1">
      <a data-dms-partner-name="Live Music" href="/events/a/"></a>
      <span class="card__date-heading">August 16</span>
    </article>
    </div>
    """
    cards = destbryan.parse_list_html(html)
    assert len(cards) == 2
    assert {c["date_text"] for c in cards} == {"August 15", "August 16"}


def test_destbryan_source_identity(destbryan_normalized):
    payloads, _ = destbryan_normalized
    assert payloads
    for p in payloads:
        assert p["source"]["slug"] == "destinationbryan"
        assert p["source"]["name"] == "Destination Bryan"
        assert p["source"]["kind"] == "feed"
        assert p["series"]["organization"]["slug"] == "destinationbryan"


def test_destbryan_drops_spans_longer_than_14_days(destbryan_normalized):
    payloads, _ = destbryan_normalized
    titles = {p["series"]["title"] for p in payloads}
    assert not any("Legacy of a Mighty River" in t for t in titles)
    assert not any("E Pluribus Unum" in t for t in titles)


def test_destbryan_keeps_single_day_listings(destbryan_normalized):
    payloads, _ = destbryan_normalized
    titles = {p["series"]["title"] for p in payloads}
    assert any("Community Yoga" in t for t in titles)
    assert any("Cadillac Ranch" in t for t in titles)


def test_destbryan_places_are_bryan_cs_or_nearby(destbryan_normalized):
    payloads, _ = destbryan_normalized
    for p in payloads:
        if "place" not in p["series"]:
            continue
        place = p["series"]["place"]
        assert place["area"] in {"bryan", "college_station", "nearby"}
        assert place["city"]
        assert place.get("region") == "TX"


def test_destbryan_cadillac_ranch_is_bryan(destbryan_normalized):
    payloads, _ = destbryan_normalized
    hit = next(p for p in payloads if "Cadillac Ranch" in p["series"]["title"])
    assert hit["series"]["place"]["city"] == "Bryan"
    assert hit["series"]["place"]["area"] == "bryan"
    assert hit["occurrence"]["start_local"].startswith("2026-08-15T19:00")


def test_destbryan_area_from_city():
    assert destbryan.area_from_city("Bryan") == ("Bryan", "bryan")
    assert destbryan.area_from_city("College Station") == ("College Station", "college_station")
    assert destbryan.area_from_city("Wellborn") == ("Wellborn", "nearby")
    assert destbryan.area_from_city("Kurten") == ("Kurten", "nearby")
    assert destbryan.area_from_city("Wixon Valley") == ("Wixon Valley", "nearby")
    assert destbryan.area_from_city("Millican") == ("Millican", "nearby")
    assert destbryan.area_from_city("Houston") is None


def test_destbryan_topics_and_audiences():
    assert destbryan.topics_from_categories(["Arts & Culture"]) == ["arts"]
    assert destbryan.topics_from_categories(["Live Music"]) == ["music"]
    assert destbryan.topics_from_categories(["Sports", "Aggie Sports"]) == ["sports"]
    assert destbryan.topics_from_categories(["Outdoors"]) == ["outdoors"]
    assert destbryan.topics_from_categories(["Fairs & Festivals"]) == ["community"]
    assert destbryan.audiences_from_categories(["Nightlife"]) == ["adult"]
    assert destbryan.audiences_from_categories(["Family-Friendly"]) == ["all-ages"]
    assert destbryan.audiences_from_categories(["Arts & Culture"]) == []


def test_destbryan_free_category_sets_is_free(destbryan_normalized):
    payloads, _ = destbryan_normalized
    yoga = next(p for p in payloads if "Community Yoga" in p["series"]["title"])
    assert yoga["series"].get("is_free") is True


def test_destbryan_is_registered_and_not_in_default_kinds():
    assert "destbryan" not in Settings.from_env_and_kwargs({}).kinds
    scrapers = engine._default_scrapers(Settings.from_env_and_kwargs({"kinds": "destbryan"}))
    assert [s.kind for s in scrapers] == ["destbryan"]
    assert scrapers[0].source_slug == "destinationbryan"


def test_destbryan_skip_network_fetches_nothing():
    assert destbryan.DestBryanScraper().fetch(
        date(2026, 8, 15), date(2026, 9, 15), skip_network=True) == []


def test_destbryan_normalizing_twice_is_byte_identical(destbryan_raw):
    first, _ = destbryan.DestBryanScraper().normalize(destbryan_raw)
    second, _ = destbryan.DestBryanScraper().normalize(destbryan_raw)
    assert [state.payload_digest(p) for p in first] == [state.payload_digest(p) for p in second]


def test_conformance_destbryan_payloads_pass_the_real_validator(destbryan_normalized):
    validator = _load_site_validator()
    if validator is None:
        pytest.skip("discoverbcs app not mounted; see this file's docstring")
    payloads, _ = destbryan_normalized
    assert payloads
    for payload in payloads:
        validator.validate_upsert(json.loads(json.dumps(payload)))
