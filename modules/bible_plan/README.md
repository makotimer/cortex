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
