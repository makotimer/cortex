# Event Watch Stable Identity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Each injector emits one stable `(source_series_uid, source_occurrence_tid)` per real happening, and two Rx Pizza locations stop sharing a display name.

**Architecture:** Changes stay inside Cortex scrapers. Discover BCS continues to treat same-source rows as non-candidates. Site-side repair of rows already cloned by CitySpark is the other plan (`docs/superpowers/plans/2026-08-30-aggressive-cross-source-dedup.md` Task 5) and must run before or in the same window as the first CitySpark scrape with the new tid formula.

**Tech Stack:** Python 3.12, existing `modules/event_watch` scrapers, pytest in `/srv/docker/cortex`.

**Spec:** `/srv/docker/websites/discoverbcs/docs/superpowers/specs/2026-08-30-dedup-and-source-identity-design.md` §4

**Repo:** `/srv/docker/cortex` — do not implement this in the discoverbcs tree.

## Global Constraints

- Intake contract is unchanged. Still one `event.upsert` per occurrence. Still no cross-source dedupe in the injector.
- Do not merge Challenge `venue-1419` and `venue-1421`. They are two restaurants.
- Do not drop nightlife or TAMU academic events.
- Do not special-case `engine._tid_in_window` for CitySpark.
- `source_occurrence_tid` must remain a string. CitySpark’s new tid is non-numeric (`PId:start_local`); that is acceptable — numeric tids are only required when engine cancellation-by-window must apply. Stability matters more than cancellability here because the same key will upsert in place.
- Tests: from `/srv/docker/cortex`, run the event_watch slice (see that repo’s usual pytest invocation; tests live in `tests/test_event_watch.py`, fixtures in `tests/fixtures/event_watch/`).
- Commit after every task. Do not push.

## File Structure

| File | Responsibility |
|---|---|
| `modules/event_watch/lib/scrapers/cityspark.py` | Stable `occurrence_tid` |
| `modules/event_watch/lib/scrapers/tamu.py` | Place name/street split; intra-run collapse |
| `modules/event_watch/lib/scrapers/challenge.py` | Distinct `place.name` for chained venues |
| `tests/test_event_watch.py` | Pin all three |

---

### Task 1: CitySpark occurrence tid = PId + start

**Files:**
- Modify: `modules/event_watch/lib/scrapers/cityspark.py` (`to_raw`)
- Test: `tests/test_event_watch.py`

**Interfaces:**
- Consumes: `wall_clock_local(record["DateStart"])`, `record["PId"]`
- Produces: `to_raw(record).occurrence_tid == f"{PId}:{start_local}"`

- [ ] **Step 1: Write the failing test** (near the other CitySpark tests ~line 1202)

```python
def test_cityspark_occurrence_tid_is_stable_when_widget_id_rotates():
    base = {
        "PId": 19499473,
        "Id": "260729ihPn2Ebfc0KhjjZhv-OtzQ",
        "Name": "Tyler Halverson",
        "DateStart": "2026-09-04T20:00:00Z",
        "CityState": "College Station, TX",
        "Venue": "The Tap",
        "Address": "815 Harvey Road",
        "AllDay": False,
        "Tags": [],
    }
    a = cityspark.to_raw(base)
    b = cityspark.to_raw({**base, "Id": "260830oaM0hRHGJU2AzyWRoAx3og"})
    assert a.series_uid == "19499473"
    assert a.occurrence_tid == b.occurrence_tid
    assert a.occurrence_tid == "19499473:2026-09-04T20:00:00"
    assert "260729" not in a.occurrence_tid
    assert "260830" not in b.occurrence_tid
```

Use whatever `DateStart` shape the fixture actually has (the scraper strips a fake `Z`). If the fixture uses a different time format, match `wall_clock_local`’s output, not the raw field.

- [ ] **Step 2: Run it — expect FAIL** (today tid equals `Id`)

```bash
cd /srv/docker/cortex && python -m pytest tests/test_event_watch.py::test_cityspark_occurrence_tid_is_stable_when_widget_id_rotates -v
```

- [ ] **Step 3: Change `to_raw`**

```python
def to_raw(record: dict) -> RawEvent:
    pid = str(record.get("PId") or "")
    start = wall_clock_local(record.get("DateStart")) or ""
    return RawEvent(
        series_uid=pid,
        occurrence_tid=f"{pid}:{start}" if pid and start else str(record.get("Id") or ""),
        record=record,
    )
```

Fallback to `Id` only when PId or start is missing, so a malformed record still has some tid rather than `":"`.

- [ ] **Step 4: Run CitySpark tests**

```bash
cd /srv/docker/cortex && python -m pytest tests/test_event_watch.py -k cityspark -v
```

Expected: PASS. `test_cityspark_fixture_shape` only asserts tids are non-empty.

- [ ] **Step 5: Commit**

```bash
git add modules/event_watch/lib/scrapers/cityspark.py tests/test_event_watch.py
git commit -m "fix(cityspark): stable occurrence tid from series PId plus start"
```

---

### Task 2: TAMU place split and intra-run collapse

**Files:**
- Modify: `modules/event_watch/lib/scrapers/tamu.py`
- Test: `tests/test_event_watch.py`

**Interfaces:**
- Consumes: `_location`, `_place_dict`, `TamuScraper.normalize`
- Produces: `_split_location_name_street(location: str) -> tuple[str, str]`; `collapse_intra_source(payloads: list[dict]) -> list[dict]`

- [ ] **Step 1: Failing tests**

```python
def test_tamu_splits_name_from_house_number_when_location_has_no_comma():
    name, street = tamu._split_location_name_street(
        "Made Well House 600 E. 27th Bryan")
    assert name == "Made Well House"
    assert street.startswith("600")
    assert "Made Well House" not in street


def test_tamu_collapses_two_listings_of_the_same_slot():
    def payload(uid, title, url, place_slug="the-gardens-at-texas-a-m-university"):
        return {
            "schema_version": "1",
            "source": {"slug": "tamu", "name": "Texas A&M University Events Calendar", "kind": "feed"},
            "series": {
                "source_series_uid": uid,
                "title": title,
                "source_url": url,
                "description": "x" * (80 if "387508" in uid else 20),
                "place": {"slug": place_slug, "name": "The Gardens at Texas A&M University",
                          "city": "College Station", "area": "college_station"},
            },
            "occurrence": {
                "source_occurrence_tid": "1788877800000",
                "start_local": "2026-09-08T09:30:00",
                "timezone": "America/Chicago",
                "all_day": False,
                "status": "scheduled",
            },
        }
    raw = [
        payload("390935", "Garden Club - Fall 2026",
                "https://agriliferegister.tamu.edu/taes-ev-032"),
        payload("387508", "Garden Club at The Gardens",
                "https://calendar.tamu.edu/agrilife/event/387508-garden-club-at-the-gardens"),
        payload("387508", "Garden Club at The Gardens",
                "https://calendar.tamu.edu/agrilife/event/387508-garden-club-at-the-gardens",
                place_slug="other-place"),  # different place: keep
    ]
    # Third payload must also differ in start or it would collide with the
    # first two on start+place. Use a different start:
    raw[2]["occurrence"]["start_local"] = "2026-09-08T09:30:00"
    raw[2]["occurrence"]["source_occurrence_tid"] = "1788877800001"
    out = tamu.collapse_intra_source(raw)
    garden = [p for p in out
              if p["series"]["place"]["slug"] == "the-gardens-at-texas-a-m-university"]
    assert len(garden) == 1
    assert garden[0]["series"]["source_series_uid"] == "387508"
    assert len(out) == 2
```

If the third payload same start + different place is the “keep” case, `len(out) == 2` is correct (one Gardens winner + the other place). Drop the confusing comment; the other-place row is the second kept payload.

- [ ] **Step 2: Run tests — expect FAIL**

- [ ] **Step 3: Implement**

`_split_location_name_street`: if a house number appears, name is the stripped prefix, street is from the house number with trailing `Bryan` / `College Station` / `TX` removed. If no house number, name is `location.split(",")[0]`, street is `_street_from_location` as today.

Use it from `_place_dict` for `name` and `street` (still prefer `reso["matched_address"]` for street when present).

`collapse_intra_source`:

- Group by `(occurrence.start_local, occurrence.status, series.place.slug or "")`.
- Payloads with empty place slug do not collapse against each other.
- Within a group of size > 1, keep one winner: prefer `source_url` containing `calendar.tamu.edu`, then longer `description`, then lexicographically smaller `source_series_uid`.
- Only collapse a pair if titles pass containment (shorter normalized title is a substring with ≥ 2 tokens, or `SequenceMatcher` ratio ≥ 0.6). If two titles in the group fail that, keep both.
- Call it at the end of `TamuScraper.normalize` on `payloads` before return.

Put `collapse_intra_source` at module level so tests import `tamu.collapse_intra_source`.

- [ ] **Step 4: Run TAMU tests**

```bash
cd /srv/docker/cortex && python -m pytest tests/test_event_watch.py -k tamu -v
```

Existing filters (`test_tamu_drops_training_…`, `test_tamu_keeps_public_arts`) must still pass. Collapse must not delete Forsyth / gallery listings that have distinct starts.

- [ ] **Step 5: Commit**

```bash
git add modules/event_watch/lib/scrapers/tamu.py tests/test_event_watch.py
git commit -m "fix(tamu): split venue blobs; collapse duplicate start+place listings"
```

---

### Task 3: Challenge Rx Pizza display names

**Files:**
- Modify: `modules/event_watch/lib/scrapers/challenge.py`
- Test: `tests/test_event_watch.py` (`test_challenge_two_locations_of_one_chain_are_two_places`)

**Interfaces:**
- Consumes: `VENUES` table, `_place`
- Produces: two different `place.name` values; slugs unchanged

- [ ] **Step 1: Change the existing test** (it currently requires both names `== "Rx Pizza"`)

Replace `test_challenge_two_locations_of_one_chain_are_two_places` with:

```python
def test_challenge_two_locations_of_one_chain_are_two_places(challenge_normalized):
    """Both Rx Pizzas run trivia. Sharing a place slug would rewrite one row
    with the other's address (contract §3). Names must also differ so the
    public calendar does not look like a duplicate."""
    payloads, _ = challenge_normalized
    rx = [p["series"]["place"] for p in payloads
          if p["series"]["place"]["slug"].startswith("rx-pizza")]
    slugs = {p["slug"] for p in rx}
    names = {p["name"] for p in rx}
    assert slugs == {"rx-pizza-bryan", "rx-pizza-south-college-station"}
    assert len(names) == 2
    assert "Rx Pizza" not in names
```

- [ ] **Step 2: Run — expect FAIL** (`"Rx Pizza" not in names`)

- [ ] **Step 3: Implement**

Add `place_name` on the two Rx Pizza `VENUES` rows:

```python
"venue-1419": {
    "audiences": ["all-ages"], "indoor": True,
    "place_name": "Rx Pizza, Bryan",
    "note": "Rx Pizza, Bryan",
},
"venue-1421": {
    "audiences": ["all-ages"], "indoor": True,
    "place_name": "Rx Pizza, College Station",
    "note": "Rx Pizza, south College Station",
},
```

In `_series` / `_place`, `name` is `venue.get("place_name") or card["venue"]`. Do **not** change `source_series_uid` (`venue-1419:live-trivia` stays). Do not change slugs (`rx-pizza-bryan` is already unique).

- [ ] **Step 4: Run Challenge tests**

```bash
cd /srv/docker/cortex && python -m pytest tests/test_event_watch.py -k challenge -v
```

- [ ] **Step 5: Commit**

```bash
git add modules/event_watch/lib/scrapers/challenge.py tests/test_event_watch.py
git commit -m "fix(challenge): disambiguate Rx Pizza venue display names"
```

---

## Deploy order

1. Discover BCS Task 5 (`collapse_intra_series_clones` + `backfill`) on production SQLite.
2. This Task 1 (CitySpark tid) then the next `event-watch-cityspark` job (Tue/Fri 07:05).
3. Tasks 2 and 3 can ship in any order relative to 1.

---

## Self-review

- Spec §4.1 CitySpark tid → Task 1
- Spec §4.2 TAMU collapse → Task 2
- Spec §4.3 TAMU place blob → Task 2
- Spec §4.4 Challenge names → Task 3
- Spec “do not merge Rx Pizza series” → Task 3 keeps two slugs / uids
