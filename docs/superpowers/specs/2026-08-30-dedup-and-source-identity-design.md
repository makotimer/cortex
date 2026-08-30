# Discover BCS — Aggressive cross-source merge + source-side identity

Date: 2026-08-30
Status: approved for implementation

Two coordinated changes, in two repos. They are not interchangeable.

1. **discoverbcs** (`/srv/docker/websites/discoverbcs`) — the only place two *different* sources can be recognized as the same event. Make auto-merge fire on the pairs a human already sees as duplicates.
2. **event_watch** (`/srv/docker/cortex/modules/event_watch`) — the only place a *single* source should collapse its own clones. The site matcher must keep refusing same-source pairs.

Out of scope: dropping nightlife, dropping TAMU academic listings, changing the closed topic/audience vocabulary, or retagging Arts/Community dump-tags. Those remain product follow-ups.

---

## 1. Why now

A review of the public calendar for 2026-08-31 through 2026-09-09 (259 published occurrences) found the same night listed two or three times for events a visitor would treat as one: First Friday Open House at Made Well (KBTX + TAMU), Tyler Halverson at The Tap (FOX 44 twice + Visit College Station), First Friday downtown (City of Bryan + Destination Bryan), Movie Night at Stage 12, Giovannie & the Hired Guns, 9/11 Remembrance, and others.

The matcher is not idle. `/admin/duplicates` already holds **39 open** pairs, many scoring **0.944** with identical title, start, and venue. Auto-merge is 0.95. Eight auto-merges exist, all TAMU vs TAMU Music with byte-identical titles. The pairs a family notices never reach 0.95, and several never reach the 0.80 review floor.

Product decisions for this work (2026-08-30):

| # | Decision |
|---|---|
| Nightlife | Keep trivia, karaoke, open mic, bar concerts. Do not filter them out. |
| TAMU feed | Keep the campus calendar as-is, including seminars. Do not add a campus-internal drop list here. |
| Auto-merge | Merge more. Prefer fixing signals so true duplicates score ≥ 0.95 over lowering the threshold first. |
| Same-source clones | Fix in the injector. Do not teach `dedupe.py` to compare two rows from the same `source_id`. |

---

## 2. What is going wrong

### 2.1 Cross-source (site job)

Scoring today (`app/dedupe.py`):

```
WEIGHTS = title 0.40, start 0.25, venue 0.20, registration_url 0.10, organization 0.05
AUTO_MERGE = 0.95, REVIEW = 0.80
CRITICAL_START_GAP_S = 90 * 60
```

Venue is 1.0 only on equal `place_id`, equal `external_place_id`, or **exact** lowercased street strings. Anything else is 0.0 **and** a critical `venue` conflict, which blocks auto-merge even if the blended score were high.

Organization is 1.0 only on equal `organization_id`. Community calendars each send themselves as the organization (KBTX Community Calendar vs KAMU Community Calendar vs Visit College Station). That 0.05 disagreement is why identical events land at **0.85 / 0.90 = 0.944**.

Title uses `difflib.SequenceMatcher` after stripping a parenthetical venue suffix. Aggregators prefix (`Live Music at The Tap: Tyler Halverson` vs `Tyler Halverson`) and the ratio falls to ~0.58.

Worked pairs from production (2026-08-30):

| Pair | Sources | Why it failed |
|---|---|---|
| Made Well Open House 329 / 549 | kbtx vs tamu | Title 1.0, start 1.0, venue 0.0 (`600 East 27th Street` vs street = `Made Well House 600 E. 27th Bryan`). Score **0.722**. Never queued. |
| Tyler Halverson 362 / 1396 | mycentx vs visitcstx | Same street `815 Harvey Road` so venue 1.0; title 0.577. Score **0.756**. Never queued. |
| First Friday 396 / 1566 | bryantx vs destinationbryan | Same place row; title 0.471. Score **0.709**. Never queued. |
| Lake Walk Run Club 1293 / 2525 | lakewalk vs destinationbryan | Title 1.0, start 1.0, `Drive` vs `Dr`. Score **0.722**. Never queued. |
| Birdwell 1460 / 2396 | bush41 vs kbtx | Score **0.890**, state `open`. Waiting on a human. |
| Movie Night 1400 / 2621 | visitcstx vs stage12 | Score **0.872**, state `open`. |
| Giovannie 1401 / 2558 | visitcstx vs mycentx | Score **0.805**, state `open` (and FOX 44 also cloned itself; see §2.2). |
| 4-H Club Fair et al. | kbtx vs tamu / visitcstx | Score **0.944**, state `open`. Org disagreement only. |

Must **not** merge (venue conflict is correct):

- Amanda Churchill 2026-09-16 17:30 — Mounce Library (bcslibrary / bryantx) vs Hyperbole Bookstore. Same author, two real events.
- Rx Pizza trivia the same night at `200 W 26th St` Bryan vs `1664 Greens Prairie Rd` College Station. Two restaurants, identical display name `Rx Pizza`.

Known limitation, **out of scope**: Missouri State at Kyle Field as all-day (FOX 44 / Destination Bryan) vs 6:00 pm kickoff (Visit CS). Start gap > 90 minutes is a critical conflict. Do not relax the start window to swallow all-day vs timed.

### 2.2 Same-source (injector job)

`dedupe.reconcile` only considers other `source_id`s. `ingest.py` identity is `(source, series_uid)` then `(series, source_occurrence_tid)`. That is correct. The clones below are the injector sending two keys for one happening.

**FOX 44 / CitySpark (`mycentx`).** Series 171 Tyler Halverson: occurrence 362 tid `260729ihPn2Ebfc0KhjjZhv-OtzQ` (first seen 2026-08-15) and 2554 tid `260830oaM0hRHGJU2AzyWRoAx3og` (first seen 2026-08-30). Same `source_series_uid` `19499473`, same start. `cityspark.py` sets `occurrence_tid=str(record.get("Id") or "")`. CitySpark's `Id` is a scrape-batch token, not a stable instance id. `engine._tid_in_window` only treats numeric tids as cancellable, so the old row is never `event.cancel`'d.

**TAMU Garden Club.** 564 series `387508` (`calendar.tamu.edu/agrilife/event/387508-…`) vs 1995 series `390935` (`agriliferegister.tamu.edu/taes-ev-032`). Same Tuesday 9:30, same place. Two Localist/AgriLife listings of one program. Collapse inside the TAMU scraper before publish.

**Challenge Entertainment Rx Pizza.** Not a clone. `venue-1419` is Bryan, `venue-1421` is south College Station. The public list looks duplicated because both place names are `Rx Pizza`. Disambiguate the **name** (`Rx Pizza, Bryan` / `Rx Pizza, College Station`); do not merge the series.

**TAMU Made Well place blob.** Location string `Made Well House 600 E. 27th Bryan` has no comma, so `_place_dict` uses the whole string as the name and `_street_from_location` uses the whole string as the street. That is why the site venue signal fails. Fix the scraper so name is `Made Well House` and street starts at the house number. The site matcher still has to tolerate leftover messy rows.

---

## 3. Site matcher — target behaviour

Keep the current architecture: score on ingest of an occurrence, auto-merge or queue, `merged_into_id` is reversible, `distinct` is permanent, same-source pairs are never candidates.

### 3.1 Title

After existing `normalize_title` (register suffix, trailing `(venue)`, non-word → space):

1. `SequenceMatcher` ratio as today.
2. **Containment boost.** Let `shorter` / `longer` be the two normalized titles by length. If `shorter` is a substring of `longer`, and (`shorter` has ≥ 2 tokens **or** `len(shorter) ≥ 12`), title score is `max(ratio, 0.92)`.

`Tyler Halverson` ⊂ `live music at the tap tyler halverson`. `first friday` ⊂ `first friday in historic downtown bryan`. Single short tokens (`concert`, `trivia`) do not boost.

### 3.2 Start

Unchanged: 1.0 exact, 0.8 within 30 minutes, 0.5 within 90 minutes, else 0.0 and critical `start` conflict.

### 3.3 Venue

Add `p.name AS place_name` to the occurrence select. Score 1.0 if any of:

1. `place_id` equal.
2. `external_place_id` equal and non-empty.
3. `normalize_street(street)` equal and non-empty.
4. Extracted address keys equal: from each street (and from `place_name` if street is empty or is the name blob), take from the first house number onward, expand abbreviations, drop trailing city/state/zip tokens (`bryan`, `college station`, `tx`, `texas`, 5-digit ZIP). Compare `(housenum, remaining tokens with st/street/rd/road/dr/drive/ave/avenue stripped)`. `600 East 27th Street` and `Made Well House 600 E. 27th Bryan` both become `('600', ('e', '27th'))`.
5. Normalized place-name containment, same token/length rule as titles: `made well house` ⊂ `made well house 600 e 27th bryan`.

`normalize_street`: lower, strip punctuation to space, map `street→st`, `avenue→ave`, `drive→dr`, `road→rd`, `boulevard→blvd`, `lane→ln`, `north→n`, `south→s`, `east→e`, `west→w`, `suite→ste`, collapse whitespace.

Critical `venue` conflict **only** when venue is applicable (both sides have some venue identity) **and** none of 1–5 matched. Formatting is not disagreement.

### 3.4 Organization

Include the organization signal only when both `organization_id`s are set **and they are equal** (score 1.0). If they differ, omit the signal (same as “both missing”). Aggregator self-names must not pull a 1.0 title/start/venue pair down to 0.944.

### 3.5 Registration URL

Unchanged: include only when both are non-empty; 1.0 if equal else 0.0. Not a critical conflict.

### 3.6 Auto-merge and review

Keep defaults `DEDUPE_AUTO_MERGE=0.95` and `DEDUPE_REVIEW=0.80`.

Additional gates:

- **Title floor for auto-merge.** Even if blended score ≥ 0.95 and there are no critical conflicts, do **not** auto-merge when `parts["title"] < 0.70`. Queue as `open` instead. Prevents two different events at Kyle Field the same hour from merging on venue+start.
- **Co-located review.** If start score is 1.0, venue score is 1.0, and title score ≥ 0.40, queue as `open` even when blended score is below 0.80. This is how `BVCA September Swiss` vs `Brazos Valley Chess Association Tournament at First Baptist Church` (same time, same church, title 0.41) reaches a human. It does **not** auto-merge.

`status` mismatch remains a critical conflict.

### 3.7 Backfill

`reconcile` only runs on the occurrence just ingested. After a scoring change, existing open pairs and never-queued pairs stay visible until something re-scores them.

Add `dedupe.backfill(conn) -> int` that selects every `event_occurrence` with `merged_into_id IS NULL` and `status='scheduled'` and calls `reconcile` on each id. Return the number of pairs that newly auto-merged. The worker (or a one-shot `python -c` in the web container) runs this once after deploy. `open` rows may become `merged`; `distinct` and `merged` stay skipped.

### 3.8 Repair of already-cloned same-series rows

FOX 44 clones already in SQLite will not vanish when CitySpark starts sending stable tids — that is a *new* tid, a third row, unless we collapse what is there.

Add `dedupe.collapse_intra_series_clones(conn) -> int`: for each `(series_id, start_utc)` with more than one unmerged scheduled occurrence, keep the lowest `id` and `merge(..., decided_by='repair')` the rest. This is data repair, not a change to “same-source pairs are never *candidates*.” Test it. Run it once as part of backfill.

Do **not** collapse across different `series_id`s of the same source (Garden Club). That remains the TAMU injector’s job.

---

## 4. Injector identity — target behaviour

Repo: `/srv/docker/cortex/modules/event_watch`. Tests live with the module (fixtures under `tests/fixtures/event_watch/` where that pattern already exists).

### 4.1 CitySpark / `mycentx`

`to_raw` today:

```python
series_uid=str(record.get("PId") or "")
occurrence_tid=str(record.get("Id") or "")
```

Change `occurrence_tid` to a function of the stable series id and the wall-clock start, e.g. `f"{pid}:{wall_clock_local(DateStart)}"`. `PId` is already the series uid. Do not use `Id`.

State keys in `engine.reconcile` will change. Old non-numeric tids are never cancelled by `_tid_in_window`. After the formula change:

- New upserts use the stable tid (site `UNIQUE (series_id, source_occurrence_tid)` creates a **new** occurrence unless we repair first).
- Run site `collapse_intra_series_clones` **before** the first post-fix CitySpark inject, or the calendar grows a third Halverson.
- Optional later: teach `_tid_in_window` nothing about CitySpark. Do not special-case the engine.

Pin with a unit test: two widget records that differ only in `Id` produce the same `occurrence_tid`.

### 4.2 TAMU intra-run collapse

After `normalize` builds the payload list, drop extras that share `(start_local, place slug or normalized location, status)` when title containment/similarity would pass the site’s title rule (reuse the same 0.92 containment idea, or `SequenceMatcher >= 0.6` plus containment). Winner: prefer the payload whose `source_url` is on `calendar.tamu.edu` over `agriliferegister.tamu.edu`, then the longer description, then stable uid sort.

Garden Club 387508 vs 390935 is the fixture. Do not collapse two sessions at different times.

### 4.3 TAMU place strings

`_street_from_location` / `_place_dict`: if the location is `Name <house-number> …` with no comma, name is the text before the first house number, street is from the house number with trailing city stripped. `Made Well House 600 E. 27th Bryan` → name `Made Well House`, street `600 E. 27th`. Do not invent a Google place id.

### 4.4 Challenge Entertainment place names

When emitting `place.name`, if `VENUES[key]["note"]` starts with the venue name plus a comma city, or add an explicit `place_name` on the two Rx Pizza rows: `Rx Pizza, Bryan` and `Rx Pizza, College Station`. Titles stay `Live Trivia at Rx Pizza` **or** include the city — prefer unique `place.name` so the card’s venue line distinguishes them. Do not merge `venue-1419` and `venue-1421`.

---

## 5. What we are not doing

- No same-source candidate generation in `dedupe._nearby`.
- No change to `distinct` permanence.
- No deletion of merged losers.
- No `event.sync`.
- No topic vocabulary edits, no nightlife/TAMU drop lists, no library LLM tagging work.
- No relaxation of the 90-minute start gap (all-day vs kickoff stays a conflict).
- No admin UI redesign. `/admin/duplicates` already exists; backfill should empty most of the 0.944 pile by auto-merging.

---

## 6. Testing

**discoverbcs** (`./run_tests.sh`, cwd `app/`):

- Extend `app/tests/test_dedupe_scoring.py` for street aliases, address-key extraction, title containment, org omission, title floor.
- Extend `app/tests/test_dedupe_merge.py` so a Made Well–shaped pair and a Halverson-prefix pair auto-merge; Amanda Churchill–shaped pair does not; same-source still returns `[]`; intra-series clone collapse keeps one row.
- Existing tests that encode 0.944-with-org-mismatch as “queue not merge” must be updated to the new org rule (they should auto-merge once org is omitted).

**event_watch:** CitySpark tid stability; TAMU collapse of Garden Club-shaped payloads; TAMU place split; Challenge Rx Pizza names remain two series.

Do not hit production Resend, Turnstile, or the live widget from unit tests.

---

## 7. Deploy

discoverbcs: `.py` only → `docker compose restart discoverbcs discoverbcs-worker`. Then run backfill + intra-series collapse once in the web container against `data/discoverbcs.db`.

event_watch: deploy Cortex per that module’s usual runner. **Order:** site collapse of intra-series clones, then CitySpark tid change, so the first new stable tid updates the surviving row (if the series uid + start still match via collapse) rather than inserting beside two live clones. Collapse keys on `start_utc` not tid, so it is safe either immediately before or after the tid change; do it before the next FOX 44 run.

Verify on `https://discoverbcs.org/events` for Friday 2026-09-04: one Made Well Open House, one Tyler Halverson, one First Friday downtown (satellite events such as Physics Demos and Made Well remain separate). Two Rx Pizza trivias remain, with distinguishable places.

---

## Key Decisions

1. **Two repos, two jobs.** Cross-source merge is the site. Intra-source identity is the injector. Mixing them would fight the existing tests and the intake contract §8.
2. **Fix signals, keep 0.95.** The 0.944 pile is aggregator `organization_id`, not a threshold that was too proud. Lowering 0.95 without fixing venue/title would still miss Made Well (0.722).
3. **Organization disagreement is not disagreement.** Omit unequal org ids from the denominator.
4. **Venue formatting is not a critical conflict.** Street aliases, address-in-name blobs, and name containment are matches. True different addresses still veto auto-merge.
5. **Title containment, not stopword soup.** Prefixes like `Live Music at The Tap:` are handled by substring of the shorter normalized title. One-token titles do not boost.
6. **Title floor 0.70 on auto-merge.** Venue+start is not enough to merge silently.
7. **Co-located review at title ≥ 0.40.** Chess-tournament naming is a human call, not an auto-merge.
8. **FOX 44 `Id` is not an occurrence id.** Stable tid = `PId` + start. Existing clones are repaired on the site by `(series_id, start_utc)`.
9. **Rx Pizza is two places.** Fix the label, do not merge.
10. **Nightlife and TAMU stay.** This work does not change what is in the calendar, only whether it appears once.

---

## Open Questions

None. Product calls were made 2026-08-30. All-day vs timed kickoff is a documented limitation, not an open question.

---

## PR Plan

### PR 1 — discoverbcs matcher + repair (this repo)

- **Title:** Auto-merge cross-source duplicates that humans already treat as the same event
- **Files:** `app/dedupe.py`, `app/tests/test_dedupe_scoring.py`, `app/tests/test_dedupe_merge.py`, `docs/decisions.md`
- **Depends on:** nothing
- **Changes:** title containment, street/address-key/name venue matching, omit unequal orgs, title floor, co-located review, `backfill`, `collapse_intra_series_clones`. No threshold default change.

### PR 2 — CitySpark stable occurrence tid (cortex)

- **Title:** Stop CitySpark from minting a new occurrence id every scrape
- **Files:** `modules/event_watch/lib/scrapers/cityspark.py`, its tests
- **Depends on:** PR 1 deployed and collapse run, or collapse run in the same maintenance window
- **Changes:** `occurrence_tid = f"{PId}:{start_local}"`

### PR 3 — TAMU intra-source collapse + place split (cortex)

- **Title:** Collapse TAMU double listings and split venue name from street
- **Files:** `modules/event_watch/lib/scrapers/tamu.py`, tests
- **Depends on:** nothing (independent of PR 2)
- **Changes:** post-normalize collapse on start+place+similar title; `_place_dict` name/street split on house number

### PR 4 — Challenge Rx Pizza display names (cortex)

- **Title:** Distinguish the two Rx Pizza trivia venues on the calendar
- **Files:** `modules/event_watch/lib/scrapers/challenge.py`, tests/fixtures
- **Depends on:** nothing
- **Changes:** unique `place.name` (and optionally title) per venue key; still two series
