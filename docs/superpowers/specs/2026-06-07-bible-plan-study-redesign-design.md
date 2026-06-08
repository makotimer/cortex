# bible_plan redesign — study link + rotating prayer focus

**Date:** 2026-06-07
**Status:** Approved, pending implementation

## Summary

Drastically simplify the morning `bible_plan` module. Today it computes the day's
chapter, builds Calvin / Matthew Henry commentary URLs, and uses an LLM to merge a
full Reformed exposition into a large HTML email. After this change the email becomes a
short daily invitation:

1. A single link to the study site (`https://study.coviecraft.dev`), which already
   computes and shows *today's* chapter on its homepage.
2. A rotating set of day-specific **prayer topics** to pray over — never a written,
   readable prayer.

The reading plan is still tracked internally, but only to name the chapter in the email
subject. All LLM and commentary machinery is removed; the module no longer depends on
`OPENAI_API_KEY`.

## Motivation

- The study site (`websites/coviecraft/study`) now hosts the study content. It uses the
  **identical** `chapter_plan.json` and the **same** `PLAN_START` anchor (`2025-09-13`)
  as this module, so its homepage always shows the same chapter the module would compute.
  Duplicating commentary generation in the email is redundant.
- The daily email's remaining job is to (a) point the reader at the study and (b) give a
  structured, rotating prayer focus tied to the day of the week.

## Scope decisions (resolved during brainstorming)

- **Email content:** study link + prayer only. Drop the LLM commentary, the biblehub
  Calvin/Matthew Henry links, and the YouVersion/NKJV scripture link.
- **Study link target:** the bare homepage `https://study.coviecraft.dev` (no deep link
  needed — the site derives today's reading itself).
- **Prayer source:** deterministic, **no LLM**. Rotate a subset of each day's fixed
  theme list.
- **Rotation meaning:** rotating subset — show a few of the day's topics, advancing the
  window each week so the same weekday emphasizes different topics over time and cycles
  through the whole list.

## Architecture

### Files removed
- `lib/llm.py` — LLM commentary generation.
- `lib/biblehub.py` — Calvin / Matthew Henry URL building + probing.
- `lib/links.py` — NKJV/YouVersion link + inline linkifier (nothing left to link).
- `tests/test_llm_unit.py` bible case (the `generate_commentary` test) — removed; if the
  file only contains that test, remove the file.

### Files unchanged
- `chapter_plan.json` — still the source of truth for today's chapter.
- `lib/plan.py` — `load_plan` / `PlanItem` unchanged.
- `lib/dates.py` — `resolve_date` / `days_since` unchanged (also reused by rotation).
- `lib/logging_bridge.py` — unchanged.

### Files changed

**`lib/config.py`** — slim `Settings` to:
- `plan_start: str`
- `tz_name: str`
- `prayer_count: int` (new; default `3`, env override `BIBLE_PLAN_PRAYER_COUNT`)

Drop `skip_probe` and `enable_llm`.

**`lib/render.py`** — rewrite `assemble_email_html`:

```python
def assemble_email_html(study_url: str, prayer_title: str, prayer_topics: list[str]) -> str
```

- Keep the existing "✧ Pause & Pray ✧" header table block.
- Section **"Today's Study"** — one link to `study_url`.
- Section **"Prayer Focus — {prayer_title}"** — a `<ul>` with one `<li>` per topic.
- All interpolated values HTML-escaped (reuse existing `_esc` / `_section` helpers).

**`main.py`** — rewrite `run()`:
- Keep signature minimal: `run(*, for_date=None, force_index=None)`. Drop the
  `commentary_*` kwargs.
- Compute `idx` / `item` / before-start `None` return exactly as today.
- Resolve the weekday prayer set via `lib/prayer.py` for `target`.
- Build email with `assemble_email_html("https://study.coviecraft.dev", title, topics)`.
- `meta`:
  - keep: `subject` = `Daily Reading {book} {chapter} - {target}`, `message` =
    `{book} {chapter}`, `plan_start`, `idx`, `for_date`.
  - drop: `links`, `llm`, `skip_probe`.
  - add: `prayer_day` (the weekday label) and `prayer_topics` (the selected list).

### New file: `lib/prayer.py`

Holds the weekday theme tables and the rotation function.

**Weekday → label + topics** (Python `date.weekday()`: Mon=0 … Sun=6):

- **Mon (0):** Personal sanctification; Mortification of sin; Growth in holiness;
  Humility; Self-control; Illumination of Scripture; Prayerfulness; Dependence upon the
  Spirit.
- **Tue (1):** Marriage; Children; Family worship; Homeschooling; Discipline with
  gentleness; Joyful obedience; Household peace; Protection from worldliness; Raising
  covenant children in the fear of God.
- **Wed (2)** — label **"Wednesday (The visible church)"**: Elders; Deacons; Preaching;
  Reverent worship; Psalm singing; Unity and purity; Church discipline; Confessional
  faithfulness; Church plants; Perseverance of the saints.
- **Thu (3):** Gospel advance; Missions; Evangelism; Hospitality; Mercy ministry;
  Boldness with neighbors and coworkers; Revival grounded in truth; Growth of Christ's
  kingdom among the nations.
- **Fri (4):** Civil authorities; Justice; Peace and order in society; Protection of the
  weak and unborn; Cultural wisdom; Suffering saints; The persecuted church; Steadfast
  hope in Christ's return.
- **Sat (5):** Thanksgiving and confession from the past week; Preparation for the
  Lord's Day; Physical and spiritual rest; Reconciliation where needed; Meditation on
  upcoming worship; Consecration of the household for Sabbath delight.
- **Sun (6)** — label **"Lord's Day"**: Worship preparation and participation;
  Resurrection joy; Covenant renewal; Sermon application; Sacraments; Fellowship;
  Hospitality; Thanksgiving for the ordinary means of grace.

Each day stored as `(label, [topics])`. Labels are the liturgical ones above, not raw
weekday names where they differ (Sun → "Lord's Day", Wed → "Wednesday (The visible
church)").

**Rotation** — deterministic from the date:

```python
def prayer_for(target: date, plan_start: date, count: int) -> tuple[str, list[str]]:
    label, topics = WEEKDAY_PRAYERS[target.weekday()]
    n = len(topics)
    if count >= n:
        return label, list(topics)
    week = days_since(plan_start, target) // 7   # integer floor; may be negative
    start = (week * count) % n                    # Python % is non-negative for positive n
    selected = [topics[(start + i) % n] for i in range(count)]
    return label, selected
```

Stepping the window by `count` each week partitions the list, so the union of
consecutive weeks covers **every** topic within `ceil(n / count)` weeks before the
pattern repeats. Example: Monday's 8 topics with `count=3` → windows
`[0,1,2] [3,4,5] [6,7,0]` → all topics seen within 3 weeks.

Within a single week the selected indices `(start+i) % n` for `i in 0..count-1` are
distinct whenever `count <= n` (guarded by the `count >= n` early return).

## Data flow

```
run(for_date?, force_index?)
  └─ load() ............... Settings(plan_start, tz_name, prayer_count)
  └─ resolve_date() ....... target date
  └─ load_plan() + idx .... PlanItem(book, chapter)   [unchanged; None before start]
  └─ prayer_for() ......... (label, [topics])          [new]
  └─ assemble_email_html("https://study.coviecraft.dev", label, topics)
  └─ return html, meta
```

## Error handling

- Before plan start with no `force_index`: return `None` (unchanged behavior, still logs
  `no_output_before_start`).
- `prayer_for` is pure and total for any date (every weekday has a non-empty list), so it
  cannot fail for valid input.
- `prayer_count` from env is coerced to `int`; a missing/invalid value falls back to `3`.

## Testing

Rewrite `tests/test_bible_plan.py`:

- **Keep:** `load_plan` valid/invalid, single-chapter books, `days_since` math,
  `run` before-start returns `None`, `run` after-start returns `(html, meta)` with
  correct `message`, `force_index` override.
- **Remove:** `test_nkjv_link_and_linkify`, `test_commentary_url_no_network`, and the
  `meta.get("llm") is False` assertion.
- **Update:** the `fixed_env` fixture drops `BIBLE_PLAN_SKIP_PROBE` / `BIBLE_PLAN_ENABLE_LLM`.
- **Add (`prayer.py`):**
  - weekday→label mapping (Sun → "Lord's Day", Wed label contains "visible church").
  - subset size == `prayer_count` when `count < n`; == `n` when `count >= n`.
  - selected topics are a subset of that day's full list, with no duplicates in a week.
  - coverage: over `ceil(n/count)` consecutive weeks the union equals the full list.
  - `run` meta includes `prayer_day` and `prayer_topics`; email HTML contains the study
    URL and each selected topic.

Remove the bible `generate_commentary` test from `tests/test_llm_unit.py` (delete the
file if it has no other tests).

`tests/test_trigger_reading.py` (job selection) is unaffected.

`tests/manual_live_runs/test_bible_live.py` updated to assert the new email shape (study
link + prayer section) rather than commentary.

## Docs / config follow-ups

- `modules/bible_plan/README.md` — rewrite to describe the new behavior; drop the
  ChatGPT/commentary and `BIBLE_PLAN_ENABLE_LLM` / `BIBLE_PLAN_SKIP_PROBE` /
  `OPENAI_*` references; document `BIBLE_PLAN_PRAYER_COUNT`.
- `cortex/CLAUDE.md` — update the `bible_plan` line that says it "uses lib/llm.py for
  content generation (requires OPENAI_API_KEY)".
- `.env.example` — note `bible_plan` no longer needs `OPENAI_API_KEY` (the key is still
  used elsewhere, so leave it; just remove the bible-specific flags if listed).

## Out of scope

- No change to scheduling (`config.json` Mon–Thu / Fri–Sun jobs still cover all 7 days,
  so every weekday's prayer set is reachable).
- No deep-linking to chapter pages on the study site.
- No change to the study site itself.
