# bible_plan Study-Link + Rotating Prayer Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the `bible_plan` module's LLM commentary email with a short daily email that links to `study.coviecraft.dev` and lists a rotating, day-specific set of prayer topics.

**Architecture:** The reading plan still computes today's chapter (for the subject only). All commentary/LLM/biblehub/scripture-link code is deleted. A new pure-Python `lib/prayer.py` holds weekday prayer-topic tables and a deterministic weekly-rotation function. `render.py` and `main.py` are rewritten to assemble the simpler email; the module stops depending on `OPENAI_API_KEY`.

**Tech Stack:** Python 3.12, pytest. Tests run locally with `.venv/bin/pytest` (fast iteration) and in-container with `make test`.

**Spec:** `docs/superpowers/specs/2026-06-07-bible-plan-study-redesign-design.md`

---

## File Structure

- **Create:** `modules/bible_plan/lib/prayer.py` — weekday prayer tables + `prayer_for()` rotation.
- **Create:** `tests/test_bible_prayer.py` — unit tests for the rotation logic.
- **Modify:** `modules/bible_plan/lib/config.py` — slim `Settings`; add `prayer_count`.
- **Modify:** `modules/bible_plan/lib/render.py` — new `assemble_email_html` signature.
- **Modify:** `modules/bible_plan/main.py` — wire chapter + prayer into the email; new `meta`.
- **Modify:** `modules/bible_plan/lib/__init__.py` — re-export the new API; drop deleted exports.
- **Delete:** `modules/bible_plan/lib/llm.py`, `modules/bible_plan/lib/biblehub.py`, `modules/bible_plan/lib/links.py`.
- **Modify:** `tests/test_bible_plan.py` — drop commentary/link/llm cases; assert new email + meta.
- **Modify:** `tests/test_llm_unit.py` — remove the bible `generate_commentary` test (keep the shared `OpenAIChat` test).
- **Modify:** `tests/manual_live_runs/test_bible_live.py` — assert the new email shape.
- **Modify:** `modules/bible_plan/README.md`, `/srv/docker/cortex/CLAUDE.md` (the cortex-level one, not the monorepo root), `.env.example` — docs/config follow-ups.

---

## Task 1: New `lib/prayer.py` with rotation tests

**Files:**
- Create: `modules/bible_plan/lib/prayer.py`
- Test: `tests/test_bible_prayer.py`

This task is fully additive — it touches nothing else, so the suite stays green throughout.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_bible_prayer.py`:

```python
import datetime as dt

from modules.bible_plan.lib import prayer


def test_weekday_labels():
    # date.weekday(): Mon=0 .. Sun=6
    assert prayer.WEEKDAY_PRAYERS[6][0] == "Lord's Day"
    assert "visible church" in prayer.WEEKDAY_PRAYERS[2][0]
    monday = dt.date(2025, 9, 8)  # a Monday
    label, _ = prayer.prayer_for(monday, monday, 3)
    assert label == "Monday"


def test_subset_size_and_membership_when_count_lt_n():
    monday = dt.date(2025, 9, 8)  # Monday, 8 topics
    _, topics = prayer.prayer_for(monday, monday, 3)
    full = prayer.WEEKDAY_PRAYERS[0][1]
    assert len(topics) == 3
    assert all(t in full for t in topics)
    assert len(set(topics)) == 3  # no duplicates within a single week


def test_count_ge_n_returns_full_list():
    saturday = dt.date(2025, 9, 13)  # Saturday, 6 topics
    _, topics = prayer.prayer_for(saturday, saturday, 99)
    assert topics == prayer.WEEKDAY_PRAYERS[5][1]


def test_rotation_advances_each_week():
    plan_start = dt.date(2025, 9, 8)  # Monday
    wk0 = prayer.prayer_for(plan_start, plan_start, 3)[1]
    wk1 = prayer.prayer_for(plan_start + dt.timedelta(days=7), plan_start, 3)[1]
    assert wk0 != wk1


def test_rotation_covers_all_topics_over_cycle():
    # Monday n=8, count=3 -> ceil(8/3)=3 weeks should cover every topic.
    plan_start = dt.date(2025, 9, 8)
    seen: set[str] = set()
    for w in range(3):
        d = plan_start + dt.timedelta(days=7 * w)
        seen.update(prayer.prayer_for(d, plan_start, 3)[1])
    assert seen == set(prayer.WEEKDAY_PRAYERS[0][1])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/pytest tests/test_bible_prayer.py -q`
Expected: FAIL — `ModuleNotFoundError` / `AttributeError` (no `prayer` module yet).

- [ ] **Step 3: Implement `lib/prayer.py`**

Create `modules/bible_plan/lib/prayer.py`:

```python
from __future__ import annotations

from datetime import date

from .dates import days_since

# Weekday prayer themes keyed by Python date.weekday() (Mon=0 .. Sun=6).
# Each value is (display label, ordered list of discrete prayer topics).
WEEKDAY_PRAYERS: dict[int, tuple[str, list[str]]] = {
    0: (
        "Monday",
        [
            "Personal sanctification",
            "Mortification of sin",
            "Growth in holiness",
            "Humility",
            "Self-control",
            "Illumination of Scripture",
            "Prayerfulness",
            "Dependence upon the Spirit",
        ],
    ),
    1: (
        "Tuesday",
        [
            "Marriage",
            "Children",
            "Family worship",
            "Homeschooling",
            "Discipline with gentleness",
            "Joyful obedience",
            "Household peace",
            "Protection from worldliness",
            "Raising covenant children in the fear of God",
        ],
    ),
    2: (
        "Wednesday (The visible church)",
        [
            "Elders",
            "Deacons",
            "Preaching",
            "Reverent worship",
            "Psalm singing",
            "Unity and purity",
            "Church discipline",
            "Confessional faithfulness",
            "Church plants",
            "Perseverance of the saints",
        ],
    ),
    3: (
        "Thursday",
        [
            "Gospel advance",
            "Missions",
            "Evangelism",
            "Hospitality",
            "Mercy ministry",
            "Boldness with neighbors and coworkers",
            "Revival grounded in truth",
            "Growth of Christ's kingdom among the nations",
        ],
    ),
    4: (
        "Friday",
        [
            "Civil authorities",
            "Justice",
            "Peace and order in society",
            "Protection of the weak and unborn",
            "Cultural wisdom",
            "Suffering saints",
            "The persecuted church",
            "Steadfast hope in Christ's return",
        ],
    ),
    5: (
        "Saturday",
        [
            "Thanksgiving and confession from the past week",
            "Preparation for the Lord's Day",
            "Physical and spiritual rest",
            "Reconciliation where needed",
            "Meditation on upcoming worship",
            "Consecration of the household for Sabbath delight",
        ],
    ),
    6: (
        "Lord's Day",
        [
            "Worship preparation and participation",
            "Resurrection joy",
            "Covenant renewal",
            "Sermon application",
            "Sacraments",
            "Fellowship",
            "Hospitality",
            "Thanksgiving for the ordinary means of grace",
        ],
    ),
}


def prayer_for(target: date, plan_start: date, count: int) -> tuple[str, list[str]]:
    """Return (label, selected topics) for ``target``.

    A window of ``count`` topics is selected from the weekday's list. The window
    advances by ``count`` each week (weeks measured from ``plan_start``), so the
    same weekday cycles through every topic over ceil(n / count) weeks. These are
    prayer *topics* to cover, never a written-out prayer.
    """
    label, topics = WEEKDAY_PRAYERS[target.weekday()]
    n = len(topics)
    if count >= n:
        return label, list(topics)
    week = days_since(plan_start, target) // 7  # floor division; safe if negative
    start = (week * count) % n                  # non-negative for positive n
    selected = [topics[(start + i) % n] for i in range(count)]
    return label, selected
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/test_bible_prayer.py -q`
Expected: PASS (5 passed).

- [ ] **Step 5: Commit**

```bash
git add modules/bible_plan/lib/prayer.py tests/test_bible_prayer.py
git commit -m "bible_plan: add weekday prayer tables with weekly rotation"
```

---

## Task 2: Core swap — rewrite email, config, main; delete commentary code

This is one atomic transition: the old `render`/`main`/`__init__`/`config` and the deleted
libs are interdependent, so intermediate states do not import cleanly. Make all edits, get
the whole suite green, then commit once.

**Files:**
- Modify: `modules/bible_plan/lib/render.py`
- Modify: `modules/bible_plan/lib/config.py`
- Modify: `modules/bible_plan/main.py`
- Modify: `modules/bible_plan/lib/__init__.py`
- Delete: `modules/bible_plan/lib/llm.py`, `modules/bible_plan/lib/biblehub.py`, `modules/bible_plan/lib/links.py`
- Modify: `tests/test_bible_plan.py`
- Modify: `tests/test_llm_unit.py`

- [ ] **Step 1: Rewrite `tests/test_bible_plan.py` to the new expectations (the failing test)**

Replace the entire file with:

```python
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
    assert "study.coviecraft.dev" in html
    assert meta["message"] == expected_first
    assert meta["idx"] == 0
    assert meta["prayer_day"]
    assert meta["prayer_topics"]
    # Every selected prayer topic appears in the rendered email.
    for topic in meta["prayer_topics"]:
        assert topic in html
    # Commentary/LLM/link fields are gone.
    assert "llm" not in meta
    assert "links" not in meta


def test_force_index_overrides_before_start():
    items = lib.plan.load_plan(None)
    assert len(items) >= 2
    expected_forced = f"{items[1].book} {items[1].chapter}"

    result = main.run(for_date="2025-09-01", force_index=1)
    html, meta = result
    assert meta["message"] == expected_forced
    assert meta["idx"] == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_bible_plan.py -q`
Expected: FAIL (the old `main`/`meta` still produce `llm`/`links`; `study.coviecraft.dev` absent).

- [ ] **Step 3: Rewrite `modules/bible_plan/lib/render.py`**

Replace the entire file with (header block preserved verbatim from the old file):

```python
from __future__ import annotations

import html

_HEADER = """<table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#0f3d3e;">
                <tr>
                    <td align="center" style="padding:14px 10px;">
                    <span style="font-family:Arial,Helvetica,sans-serif;font-size:14px;letter-spacing:1px;color:#ffffff;text-transform:uppercase;">
                        • ⏸️ •  • 🙏 •
                    </span>
                    </td>
                </tr>
            </table>

            <table role="presentation" border="0" cellpadding="0" cellspacing="0" width="100%" style="background:#f8f5f0;">
            <tr>
                <td align="center" style="padding:30px 20px 40px;">
                <!-- Big, warm headline -->
                <h1 style="font-family:'Georgia', serif; font-size:28px; color:#5d4037; margin:0 0 12px;">
                    ✧ Pause & Pray ✧
                </h1>
                <!-- One-line invitation -->
                <p style="font-family:'Helvetica Neue',Arial,sans-serif; font-size:18px; color:#6d4c41; line-height:1.4; margin:0 0 20px; max-width:480px;">
                    “Open my eyes, that I may see wondrous things from Your law.” <em>(Ps. 119:18)</em>
                </p>
                <!-- Subtle verse footer -->
                <p style="font-family:'Georgia',serif; font-size:14px; color:#8d6e63; font-style:italic; margin:20px 0 0;">
                    “…that you may be filled with the knowledge of His will in all wisdom and spiritual understanding.” — Colossians 1:9
                </p>
                </td>
            </tr>
            </table>

            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#0f3d3e;">
            <tr>
                <td align="center" style="padding:14px 10px;">
                <span style="font-family:Arial,Helvetica,sans-serif;font-size:14px;letter-spacing:1px;color:#ffffff;text-transform:uppercase;">
                    ➡️ Proceed ➡️
                </span>
                </td>
            </tr>
            </table>"""  # noqa: E501


def _esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def _section(title: str, body_html: str) -> str:
    return f'<section style="margin:12px 0;"><h3 style="margin:0 0 6px 0;">{_esc(title)}</h3>{body_html}</section>'


def assemble_email_html(study_url: str, prayer_title: str, prayer_topics: list[str]) -> str:
    study_html = (
        f'<p style="margin:0;">📖 <a href="{_esc(study_url)}">'
        "Read today’s study at study.coviecraft.dev</a></p>"
    )
    items = "".join(f"<li>{_esc(t)}</li>" for t in prayer_topics)
    prayer_html = f"<ul>{items}</ul>"
    return (
        _HEADER
        + _section("Today’s Study", study_html)
        + _section(f"Prayer Focus — {prayer_title}", prayer_html)
    )
```

- [ ] **Step 4: Rewrite `modules/bible_plan/lib/config.py`**

Replace the entire file with:

```python
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    plan_start: str
    tz_name: str
    prayer_count: int


def _int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def load() -> Settings:
    return Settings(
        plan_start=os.getenv("BIBLE_PLAN_START", "2025-09-13"),
        tz_name=os.getenv("TZ", "UTC"),
        prayer_count=max(1, _int(os.getenv("BIBLE_PLAN_PRAYER_COUNT"), 3)),
    )
```

- [ ] **Step 5: Rewrite `modules/bible_plan/main.py`**

Replace the entire file with:

```python
from __future__ import annotations

from datetime import datetime
from typing import Any

from .lib import (
    assemble_email_html,
    days_since,
    load,
    load_plan,
    log,
    prayer_for,
    resolve_date,
)

STUDY_URL = "https://study.coviecraft.dev"


def run(
    *,
    for_date: str | None = None,
    force_index: int | None = None,
) -> str | None | tuple[str, dict[str, Any]]:
    cfg = load()
    start = datetime.strptime(cfg.plan_start, "%Y-%m-%d").date()
    target = resolve_date(for_date, cfg.tz_name)

    plan = load_plan()

    if force_index is not None:
        idx = int(force_index)
    else:
        delta = days_since(start, target)
        if delta < 0:
            log.activity({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "source": "modules.bible_plan",
                "event": "no_output_before_start",
                "for_date": str(target),
                "start_date": cfg.plan_start,
            })
            return None
        idx = delta

    item = plan[idx % len(plan)]
    prayer_title, prayer_topics = prayer_for(target, start, cfg.prayer_count)

    html = assemble_email_html(STUDY_URL, prayer_title, prayer_topics)
    meta = {
        "subject": f"Daily Reading {item.book} {item.chapter} - {target!s}",
        "message": f"{item.book} {item.chapter}",
        "plan_start": cfg.plan_start,
        "idx": idx,
        "for_date": str(target),
        "prayer_day": prayer_title,
        "prayer_topics": prayer_topics,
    }
    return html, meta
```

- [ ] **Step 6: Rewrite `modules/bible_plan/lib/__init__.py`**

Replace the entire file with:

```python
# Re-export lib API for tests and main.py
from . import logging_bridge as log
from .config import Settings, load
from .dates import days_since, resolve_date
from .plan import PlanItem, load_plan
from .prayer import WEEKDAY_PRAYERS, prayer_for
from .render import assemble_email_html

__all__ = [
    "PlanItem",
    "Settings",
    "WEEKDAY_PRAYERS",
    "assemble_email_html",
    "days_since",
    "load",
    "load_plan",
    "log",
    "prayer_for",
    "resolve_date",
]
```

- [ ] **Step 7: Delete the commentary/link/llm modules**

```bash
git rm modules/bible_plan/lib/llm.py modules/bible_plan/lib/biblehub.py modules/bible_plan/lib/links.py
```

- [ ] **Step 8: Remove the bible commentary test from `tests/test_llm_unit.py`**

Delete the `from modules.bible_plan import lib` import line and the entire
`test_generate_commentary_disabled_returns_none` function. Keep the rest of the file
intact (the `test_openai_chat_temperature_env_parsing` test and its `sys` / `ClassVar` /
`shared_utils` imports stay). After editing, the top of the file reads:

```python
# tests/test_llm_unit.py
import sys
from typing import ClassVar

from modules._shared import utils as shared_utils


def test_openai_chat_temperature_env_parsing(monkeypatch):
```

(the body of `test_openai_chat_temperature_env_parsing` is unchanged.)

- [ ] **Step 9: Run the full suite to verify green**

Run: `.venv/bin/pytest tests/test_bible_plan.py tests/test_bible_prayer.py tests/test_llm_unit.py -q`
Expected: PASS (no `ModuleNotFoundError` for the deleted libs; bible + prayer + llm-facade tests pass).

Then run the whole suite to catch any stragglers:

Run: `.venv/bin/pytest -q`
Expected: PASS (or only pre-existing unrelated skips). If anything imports the deleted
`links`/`biblehub`/`llm` symbols, fix that reference now.

- [ ] **Step 10: Lint**

Run: `.venv/bin/ruff check modules/bible_plan tests/test_bible_plan.py tests/test_bible_prayer.py tests/test_llm_unit.py`
Expected: no errors. (Fix import ordering / unused imports if flagged.)

- [ ] **Step 11: Commit**

```bash
git add modules/bible_plan tests/test_bible_plan.py tests/test_llm_unit.py
git commit -m "bible_plan: swap LLM commentary email for study link + prayer focus"
```

---

## Task 3: Update the live test for the new email shape

**Files:**
- Modify: `tests/manual_live_runs/test_bible_live.py`

The live test still drives `main.run` and sends a real email; only its commentary-era
comment and assertions need updating. It is skipped without `--live`, so this is a
docs-accuracy change.

- [ ] **Step 1: Remove the LLM comment and add a shape assertion**

In `tests/manual_live_runs/test_bible_live.py`:

1. Delete the two commented lines referencing LLM:
   ```python
   # Respect your suite-wide default; explicitly enable/disable LLM as desired:
   # monkeypatch.setenv("BIBLE_PLAN_ENABLE_LLM", "1")  # uncomment to force LLM on for live
   ```
2. Immediately after the existing `assert isinstance(html, str) and html.strip()` line,
   add:
   ```python
   assert "study.coviecraft.dev" in html
   ```

- [ ] **Step 2: Verify it still collects and skips cleanly (no `--live`)**

Run: `.venv/bin/pytest tests/manual_live_runs/test_bible_live.py -q`
Expected: PASS/SKIP with no collection or import errors.

- [ ] **Step 3: Commit**

```bash
git add tests/manual_live_runs/test_bible_live.py
git commit -m "bible_plan: update live test for study-link email"
```

---

## Task 4: Docs and config follow-ups

**Files:**
- Modify: `modules/bible_plan/README.md`
- Modify: `CLAUDE.md` (the cortex one, at `/srv/docker/cortex/CLAUDE.md`)
- Modify: `.env.example`

- [ ] **Step 1: Rewrite `modules/bible_plan/README.md`**

Replace the entire file with:

```markdown
# Bible Plan Module
**Daily prayer-and-study email** — links to the study site + a rotating, day-specific prayer focus.

## What it does

 - Computes today's chapter from `chapter_plan.json` (loops forever) — used for the email **subject** only.
 - Emails a link to the study site (`https://study.coviecraft.dev`), whose homepage shows
   today's reading (it uses the same plan and start date as this module).
 - Includes a **Prayer Focus** section: a rotating subset of that weekday's prayer topics.
   The window advances each week, cycling through every topic over time. These are prayer
   *topics to cover* — never a written-out prayer.
 - **No LLM / no OpenAI** — the email is assembled deterministically.

## Prayer rotation

Each weekday has a fixed list of topics (`lib/prayer.py`, keyed by `date.weekday()`,
Mon=0 … Sun=6; Sunday is labeled "Lord's Day", Wednesday "Wednesday (The visible
church)"). `prayer_for(target, plan_start, count)` selects a window of `count` topics that
steps forward by `count` each week, so consecutive weeks cover the whole list.

## config.json — scheduled twice to cover all 7 days

```json
{
  "id": "bible-plan-mon-thu-0455",
  "module": "modules.bible_plan",
  "trigger": { "daily_time": { "time": "04:55", "day_of_week": "mon-thu" } },
  "kwargs": { "email_to_env": "BIBLE_PLAN_EMAILS" },
  "send_email": true,
  "summary": "Bible plan (Mon-Thu @ 04:55)"
}
```

A second job covers `fri,sat,sun` (see `local/config.json`).

## Optional kwargs

 - `for_date`: `"YYYY-MM-DD"` → override today
 - `force_index`: `123` → jump to plan item #123 (also bypasses the before-start guard)

## .env (one-time)

```
BIBLE_PLAN_EMAILS=you@example.com,family@example.com
BIBLE_PLAN_START=2025-09-13   # plan day-1 anchor (matches the study site)
BIBLE_PLAN_PRAYER_COUNT=3     # prayer topics shown per day (default 3)
```

## How it works

```
run(**kwargs)              # → main.py
└─ load_plan()             # → chapter_plan.json (today's chapter, for the subject)
   └─ prayer_for()         # → lib/prayer.py (rotating weekday topics)
      └─ assemble_email_html()  # → study link + prayer focus
```
```

- [ ] **Step 2: Update the cortex `CLAUDE.md` bible_plan line**

In `/srv/docker/cortex/CLAUDE.md`, replace the line:

```
  bible_plan/   — daily Bible reading emails; Mon-Thu and Fri-Sun schedules; uses lib/llm.py for content generation (requires OPENAI_API_KEY)
```

with:

```
  bible_plan/   — daily prayer-and-study emails; Mon-Thu and Fri-Sun schedules; links to study.coviecraft.dev + a rotating weekday prayer focus (no LLM)
```

And update the `.env requirements` line:

```
- `OPENAI_API_KEY` — required by `modules/bible_plan/lib/llm.py` for email content generation
```

to:

```
- `OPENAI_API_KEY` — used by the shared `modules/_shared/utils.OpenAIChat` facade (not used by `bible_plan`, which no longer calls an LLM)
```

- [ ] **Step 3: Update `.env.example`**

In `.env.example`, under section `#  9. Bible Email Config`, replace the line:

```
BIBLE_PLAN_ENABLE_LLM=1
```

with:

```
BIBLE_PLAN_PRAYER_COUNT=3   # prayer topics shown per day (default 3)
```

(Leave the `#  5. OpenAI` section as-is — the shared facade still reads those keys.)

- [ ] **Step 4: Commit**

```bash
git add modules/bible_plan/README.md CLAUDE.md .env.example
git commit -m "docs: bible_plan study-link + prayer focus; drop OpenAI dependency"
```

---

## Self-Review notes

- **Spec coverage:** delete llm/biblehub/links (Task 2 Step 7); slim config + add
  `prayer_count` (Task 2 Step 4); new `prayer.py` tables + rotation (Task 1); render
  rewrite (Task 2 Step 3); main rewrite + meta `prayer_day`/`prayer_topics`, dropped
  `links`/`llm` (Task 2 Step 5); `__init__` re-exports (Task 2 Step 6); test rewrites
  (Tasks 1–3); docs (Task 4). All spec sections mapped.
- **Type consistency:** `prayer_for(target, plan_start, count) -> (str, list[str])` is
  defined in Task 1 and called identically in Task 2 Step 5. `assemble_email_html(study_url,
  prayer_title, prayer_topics)` defined in Task 2 Step 3 and called identically in Step 5.
  `Settings(plan_start, tz_name, prayer_count)` defined in Step 4 and read in Step 5.
- **Coverage math check:** Monday n=8, count=3 → windows start 0,3,6 → indices
  {0,1,2},{3,4,5},{6,7,0} → union = all 8 topics within 3 weeks. ✓
```
