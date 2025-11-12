# Career Watch Module  
**Job scraper + deduper** — emails **new postings** from Lever/Greenhouse/Workday.

```text
┌─ career_watch_groups.the-archivist.json
│   ├─ lever:acme → 3 new postings
│   └─ greenhouse:foo → 1 new
└─ SQLite → dedupe → email HTML table
```

## What it does

 - Loads scraper groups from `local/config/career_watch_groups.<slug>.json`
 - (slug = sluggified person_env from .env, e.g., "SCRAPER_USER_1" → "the-archivist")
 - Parallel scrapes by kind (max_threads=8)
 - Dedupes via SQLite → only new postings
 - Emails HTML table → Title + Link (grouped by source)
 - Special modes: ingest-only (no email), email-all (even seen), skip-network (offline)

## config.json – Every 90 min (Mon–Sat)
```json
{
  "id": "career-watch",
  "module": "modules.career_watch",
  "trigger": {
    "daily_time": {
      "time": ["05:00", "06:30", "08:00", "09:30", "11:00", "12:30", "14:00", "15:30", "17:00", "18:30"],
      "day_of_week": "mon-sat"
    }
  },
  "kwargs": {
    "person_env": "SCRAPER_USER_1",  // → .env value (e.g., "The Archivist")
    "sqlite_path": "/app/local/state/careerwatch.db",
    "max_threads": 8
  },
  "send_email": true,
  "summary": "Career Watch (Mon–Sat, every 90 min)"
}
```

## Optional kwargs

 - `groups_path`: `"/app/local/config/custom.json"` → override file
 - `skip_network`: `true` → offline mode (use cache)
 - `email_all_even_if_seen`: `true` → email everything
 - `ingest_only_no_email`: `true` → scrape + DB, no email

## .env (one-time)
```
SCRAPER_USER_1="The Archivist"  # → career_watch_groups.the-archivist.json
SCRAPER_USER_2="Sidekick"       # → career_watch_groups.sidekick.json
```

## Groups file example (`career_watch_groups.the-archivist.json`):
```json
[
  { "kind": "lever", "source": "lever:acme", "params": { "tenant": "acme" } },
  { "kind": "greenhouse", "source": "greenhouse:foo", "params": { "tenant": "foo" } }
]
```

## How it works (30-second explainer)
```
run(**kwargs)           # → main.py
└─ run_once()           # → engine.py
   ├─ load groups.json  # → ScraperConfig by kind
   ├─ parallel scrape   # → ThreadPoolExecutor
   ├─ filter_new()      # → SQLite dedupe
   └─ build_tables()    # → HTML email
```
1. Load groups → from sluggified file (or `groups_path`)
2. Parallel scrape → by kind (Lever, Greenhouse, etc.)
3. Dedupe → insert-or-ignore in SQLite → return new only
4. Render → HTML tables (Title + Link) grouped by source
5. Email → if new (or forced) → full report