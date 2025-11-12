# Bible Plan Module  
**Daily chapter-by-chapter** through the Bible — **merged commentary** + **YouVersion links**.

```text
┌─ chapter_plan.json (1-year loop)
│   ├─ Start: 2025-09-06 → Genesis 1
│   ├─ Today: Psalms 148 → email
│   └─ Tomorrow: Genesis 2
└─ ChatGPT → merge Calvin + Matthew Henry
```

## What it does

 - One chapter per day from chapter_plan.json (loops forever)
 - Merged commentary via ChatGPT (Reformed lens: Calvin, Knox, Sproul)
 - Daily email → Scripture + exposition + family worship ideas
 - YouVersion links for every reference → opens app on phone
 - No LLM? → Just raw links to Calvin + Matthew Henry

## config.json – Daily @ 04:55 (Mon–Thu)

```json
{
  "id": "bible-plan",
  "module": "modules.bible_plan",
  "trigger": {
    "daily_time": {
      "time": "04:55",
      "day_of_week": "mon-thu"
    }
  },
  "kwargs": {
    "email_to_env": "BIBLE_PLAN_EMAIL_TO"  // → .env list
  },
  "send_email": true,
  "summary": "Bible plan (Mon–Thu @ 04:55)"
}
```

## Trigger Ideas

| Goal                  | Trigger JSON (paste into `trigger`) |
|-----------------------|-------------------------------------|
| **Every morning**     | ```json { "daily_time": { "time": "05:00" } } ``` |
| **Weekdays early**    | ```json { "daily_time": { "time": "04:55", "day_of_week": "mon-thu" } } ``` |
| **Weekends later**    | ```json { "daily_time": { "time": "05:55", "day_of_week": "fri-sun" } } ``` |
| **Multiple times**    | ```json { "daily_time": { "time": ["05:00", "12:00", "18:00"] } } ``` |
| **One-shot test**     | ```json { "date": "2025-11-03T00:00:00" } ``` |
| **Cron**              | ```json { "cron": "0 5 * * *" } ``` |

## Optional kwargs

 - `for_date`: `"YYYY-MM-DD"` → override today
 - `force_index`: `123` → jump to plan item #123
 - `commentary_model_env`: `"OPENAI_MODEL_BIBLE"` → LLM model
 - `commentary_temp_env`: `"OPENAI_TEMP_BIBLE"` → LLM temp

## .env (one-time)
```
BIBLE_PLAN_EMAIL_TO=you@example.com,family@example.com
BIBLE_PLAN_START=2025-09-06  # plan[0] = Psalms 148
BIBLE_PLAN_ENABLE_LLM=1      # 0 = no ChatGPT
BIBLE_PLAN_SKIP_PROBE=1      # 0 = check commentary URLs
OPENAI_MODEL_BIBLE=gpt-4o-mini
OPENAI_TEMP_BIBLE=0.2
```

## How it works (30-second explainer)
```
run(**kwargs)           # → main.py
└─ load_plan()          # → chapter_plan.json
   └─ generate_commentary()  # → ChatGPT merge (Calvin + MH)
      └─ assemble_email_html()  # → full report + YouVersion links
```
1. Date → index → days since BIBLE_PLAN_START
2. Plan item → e.g., "Genesis 1" → book=Genesis, chapter=1
3. URLs → Calvin + Matthew Henry (probe if enabled)
4. ChatGPT → Reformed merge (if enabled) → sections like "Exposition" + "Family Worship"
5. Links → every ref (e.g., John 3:16) → \<a> to open YouVersion on phone
6. Email → full HTML report