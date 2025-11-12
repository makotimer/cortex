import datetime as dt
import json
import os
import tempfile

import pytest

# Import the module under test
from modules.bible_plan import lib, main

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fixed_env(monkeypatch):
    """Set up predictable environment for all tests."""
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("BIBLE_PLAN_START", "2025-09-06")
    monkeypatch.setenv("BIBLE_PLAN_SKIP_PROBE", "1")  # no HTTP calls
    monkeypatch.setenv("BIBLE_PLAN_ENABLE_LLM", "0")  # disable commentary
    yield


@pytest.fixture
def temp_plan(tmp_path):
    """Create a minimal chapter_plan.json for tests."""
    plan = ["Psalms 148", "Genesis 1", "Genesis 2"]
    plan_path = tmp_path / "chapter_plan.json"
    plan_path.write_text(json.dumps(plan))
    return plan_path


# ---------------------------------------------------------------------------
# Unit tests for lib layer
# ---------------------------------------------------------------------------


def test_load_plan_valid(temp_plan):
    items = lib.plan.load_plan(str(temp_plan.parent))
    assert len(items) == 3
    assert items[0].book == "Psalms"
    assert items[0].chapter == 148


@pytest.mark.parametrize(
    "bad_json",
    [
        "{}",  # not a list
        json.dumps([]),  # empty
        json.dumps(["Genesis x"]),  # invalid format
    ],
)
def test_load_plan_invalid(temp_plan, bad_json):
    temp_plan.write_text(bad_json)
    with pytest.raises(ValueError):
        lib.plan.load_plan(str(temp_plan.parent))


def test_nkjv_link_and_linkify():
    html = lib.links.nkjv_link("John", 3, 16, 18)
    assert "John 3:16-18" in html
    text = "Today's reading: John 3:16-18."
    linked = lib.links.linkify_scripture_refs(text)
    assert "<a href=" in linked
    assert "John 3:16-18" in linked


def test_commentary_url_no_network(monkeypatch):
    # Even without requests, should return a URL string
    url = lib.biblehub.commentary_url("calvin", "Genesis", 1, probe=False)
    assert url.startswith("https://biblehub.com/commentaries/calvin/")


def test_dates_math():
    start = dt.date(2025, 9, 6)
    target = dt.date(2025, 9, 10)
    assert lib.dates.days_since(start, target) == 4


# ---------------------------------------------------------------------------
# Integration-style tests for main.run()
# ---------------------------------------------------------------------------


def test_run_before_start_returns_none():
    # Date before plan start
    html = main.run(for_date="2025-09-05")
    assert html is None


def test_run_after_start_returns_html():
    # Look up the real plan's first item so we assert correctly
    items = lib.plan.load_plan(None)
    assert items, "Real plan must not be empty"
    expected_first = f"{items[0].book} {items[0].chapter}"

    # On start date
    result = main.run(for_date="2025-09-06")
    assert isinstance(result, tuple)
    html, meta = result
    assert "<table role=" in html
    assert meta["message"] == expected_first
    assert meta.get("llm") is False


def test_force_index_overrides_before_start():
    # Use the real plan's second item as the forced expectation
    items = lib.plan.load_plan(None)
    assert len(items) >= 2, "Real plan must have at least two entries"
    expected_forced = f"{items[1].book} {items[1].chapter}"

    # force_index allows before start to still produce HTML
    result = main.run(for_date="2025-09-01", force_index=1)
    html, meta = result
    assert expected_forced in html
    assert meta["idx"] == 1


def test_logging_bridge_no_error(monkeypatch):
    # Should not raise even if service.logging_utils missing
    lib.log.activity({"test": True})
    lib.log.error({"error": True})


def test_load_plan_allows_single_chapter_books(tmp_path):
    plan = ["Philemon", "Jude", "2 John", "3 John", "Obadiah"]
    p = tmp_path / "chapter_plan.json"
    p.write_text(json.dumps(plan))
    items = lib.plan.load_plan(str(tmp_path))
    assert [(it.book, it.chapter) for it in items] == [
        ("Philemon", 1),
        ("Jude", 1),
        ("2 John", 1),
        ("3 John", 1),
        ("Obadiah", 1),
    ]
