import datetime as dt
import json

import pytest

from modules.bible_plan import lib, main


@pytest.fixture(autouse=True)
def fixed_env(monkeypatch):
    """Predictable environment for all tests."""
    monkeypatch.setenv("TZ", "UTC")
    monkeypatch.setenv("BIBLE_PLAN_START", "2025-09-06")
    yield


@pytest.fixture(autouse=True)
def _plan_dir(tmp_path, monkeypatch):
    """Redirect chapter_plan.json lookup to a writable temp dir."""
    plan_dir = tmp_path / "plan"
    plan_dir.mkdir()
    plan = ["Psalms 148", "Genesis 1", "Genesis 2"]
    (plan_dir / "chapter_plan.json").write_text(json.dumps(plan))
    monkeypatch.setenv("BIBLE_PLAN_DIR", str(plan_dir))


@pytest.fixture
def temp_plan(tmp_path):
    plan = ["Psalms 148", "Genesis 1", "Genesis 2"]
    plan_path = tmp_path / "chapter_plan.json"
    plan_path.write_text(json.dumps(plan))
    return plan_path


# ---- lib layer ----

def test_load_plan_valid(temp_plan):
    items = lib.plan.load_plan(str(temp_plan.parent))
    assert len(items) == 3
    assert items[0].book == "Psalms"
    assert items[0].chapter == 148


@pytest.mark.parametrize(
    "bad_json",
    ["{}", json.dumps([]), json.dumps(["Genesis x"])],
)
def test_load_plan_invalid(temp_plan, bad_json):
    temp_plan.write_text(bad_json)
    with pytest.raises(ValueError):
        lib.plan.load_plan(str(temp_plan.parent))


def test_dates_math():
    start = dt.date(2025, 9, 6)
    target = dt.date(2025, 9, 10)
    assert lib.dates.days_since(start, target) == 4


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


def test_logging_bridge_no_error():
    lib.log.activity({"test": True})
    lib.log.error({"error": True})


# ---- main.run() integration ----

def test_run_before_start_returns_none():
    assert main.run(for_date="2025-09-05") is None


def test_run_after_start_returns_html():
    items = lib.plan.load_plan(None)
    assert items, "Plan must not be empty"
    expected_first = f"{items[0].book} {items[0].chapter}"

    result = main.run(for_date="2025-09-06")
    assert isinstance(result, tuple)
    html, meta = result

    assert "<table role=" in html
    # Psalm 51:10 now leads the three opening verses near the top.
    assert "Ps. 51:10" in html
    # The study-site link is still present.
    assert "study.coviecraft.dev" in html
    assert meta["message"] == expected_first
    assert meta["idx"] == 0
    assert meta["prayer_day"]
    assert meta["prayer_topics"]
    # Commentary/LLM/link fields are gone.
    assert "llm" not in meta
    assert "links" not in meta
    # --- Remaining content after the three opening verses is commented out for now. ---
    # # Every selected prayer topic appears in the rendered email.
    # for topic in meta["prayer_topics"]:
    #     assert topic in html
    # # Brief illumination-prayer suggestions, closing with the Lord's Prayer (NKJV).
    # assert "Praying for Illumination" in html
    # assert "Ask for illumination" in html
    # assert "Our Father in heaven" in html
    # assert "NKJV" in html


def test_force_index_overrides_before_start():
    items = lib.plan.load_plan(None)
    assert len(items) >= 2
    expected_forced = f"{items[1].book} {items[1].chapter}"

    result = main.run(for_date="2025-09-01", force_index=1)
    _html, meta = result
    assert meta["message"] == expected_forced
    assert meta["idx"] == 1
