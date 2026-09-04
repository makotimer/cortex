# BCS Chamber Place Resolution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `bcschamber` stops rejecting events whose city is recoverable — from a
pinned venue or from a street the feed already carries — and keeps rejecting the
ones that genuinely have no location.

**Architecture:** All changes are inside `modules/event_watch`. The intake
contract is unchanged; discoverbcs needs no change and is not touched.

**Tech Stack:** Python 3.12, `modules/event_watch`, pytest in `/srv/docker/cortex`.

**Spec:** `docs/superpowers/specs/2026-09-04-bcschamber-place-resolution-design.md`

**Repo:** `/srv/docker/cortex`

## Global Constraints

- **Every payload accepted today must be byte-identical after this change.** The
  new steps run only after the two existing text paths have failed. There is a
  regression test for this in Task 5; write it first if it helps you.
- `normalize()` is **pure** — no network, no clock, no state. Address resolution
  belongs in `fetch()`. See spec §3.1.
- Do not write a second address resolver. Import `kbtx.resolve_address`, the way
  `visitcstx`, `destbryan`, `tamu` and `tamumusic` already do.
- Do not relax `allow_bare_name=False` on descriptions. See spec §5.
- Do not pin a venue address you have not confirmed. A wrong city is worse than a
  reject — it is silent.
- `bcschamber` stays out of `DEFAULT_KINDS`.
- Commit after every task. Do not push. Do not `docker compose`.

## File Structure

| File | Responsibility |
|---|---|
| `modules/event_watch/lib/scrapers/bcschamber.py` | `CHAMBER_VENUES`, the ladder in `parse_place`, resolution in `fetch` |
| `modules/event_watch/lib/engine.py` | construct `BcsChamberScraper` with `state_dir=` |
| `tests/test_event_watch_bcschamber.py` | pin all of it |

---

### Task 1: Venue map

**Files:** Modify `bcschamber.py`; test `tests/test_event_watch_bcschamber.py`

Add `CHAMBER_VENUES: dict[str, dict[str, Any]]` keyed by slug and
`_CHAMBER_VENUE_PATTERNS: tuple[tuple[re.Pattern, str], ...]`, in the style of
`bvso.VENUES` / `bvso._VENUE_PATTERNS`. Seed it from spec §3.2 — BVCOG and
Phillips Event Center are confirmed by the feed's own `LocationDesc`; confirm
Brazos County Expo and Brazos Center against the chamber listing before you
commit them, and drop any you cannot confirm.

- [ ] **Step 1: Failing test** — `test_bcschamber_bare_venue_name_resolves_from_map`:
      a record with `location_html = "Brazos County Expo"` and an empty
      description normalizes to a payload with `place.city == "Bryan"` and
      `place.area == "bryan"`, not a reject.
- [ ] **Step 2:** Add the map and a `_place_from_venue_map(text) -> dict | None`.
- [ ] **Step 3:** Call it in `parse_place()` as step 3 of the ladder — after both
      `_place_from_text` calls, before the reject.
- [ ] **Step 4:** Test that an unmapped bare name (`"Some New Hall"`, no street,
      no city) still rejects `missing city`.
- [ ] **Step 5:** Test the map does **not** override text — a record naming a
      mapped venue *and* carrying an explicit `City, TX` line keeps the
      text-derived place.
- [ ] **Step 6:** `python -m pytest tests/test_event_watch_bcschamber.py -v`

### Task 2: Resolve a street with no city

**Files:** Modify `bcschamber.py` (`fetch`, `__init__`), `engine.py`

Mirror `kbtx.KbtxScraper.fetch` (lines ~112–140): `state.load_addresses`,
resolve, `state.save_addresses`, result onto
`RawEvent.supplement["place_resolution"]`.

- [ ] **Step 1: Failing test** — an injected fake resolver returning
      `{"status": "matched", "city": "Bryan", "zip_code": "77802", ...}` for a
      record whose location is a street with no city produces a payload with
      `place.city == "Bryan"`. Take the resolver by constructor injection
      (`self._resolve`), as `kbtx` does, so no test touches the network.
- [ ] **Step 2:** `BcsChamberScraper.__init__` accepts `state_dir` and `resolve`.
- [ ] **Step 3:** Enrich in `fetch()`, keyed on a cache key derived from the
      street text. Only look up records that have a street-ish line and no city —
      do not spend an API call on the 21 ribbon cuttings that already resolve.
- [ ] **Step 4:** `engine._build_scrapers` — add `BcsChamberScraper` to the set
      constructed with `state_dir=settings.state_dir`.
- [ ] **Step 5:** Test `skip_network=True` still fetches nothing and calls no
      resolver.
- [ ] **Step 6:** `python -m pytest tests/test_event_watch_bcschamber.py -v`

### Task 3: Consume the resolution in `parse_place`

**Files:** Modify `bcschamber.py`

- [ ] **Step 1: Failing test** — `status == "out_of_area"` drops the record
      (`_Drop`, no reject, no payload); any other non-`matched` status rejects.
- [ ] **Step 2:** Add step 4 of the ladder, reading
      `item.supplement["place_resolution"]`. Note `parse_place(record)` takes a
      record today — thread the supplement through, or move the call site, but
      keep `normalize` pure.
- [ ] **Step 3:** A resolved city outside Bryan / College Station drops, matching
      `test_bcschamber_drops_non_bcs_cities`.
- [ ] **Step 4:** `python -m pytest tests/test_event_watch_bcschamber.py -v`

### Task 4: Update the fixture-based reject test

**Files:** Modify `tests/test_event_watch_bcschamber.py`

`test_bcschamber_rejects_events_with_no_city` currently asserts 3849, 3855, 3862
and 3889 are all rejected. Two of those are now expected to publish.

- [ ] **Step 1:** 3849 (BVCOG) and 3862 (Brazos County Expo) move to the
      published set, with `place.city == "Bryan"`.
- [ ] **Step 2:** 3855 (Lobsterfest, empty location) and 3889 (St. Joseph Health,
      name only) stay rejected. Keep the `rejected_ids.isdisjoint(published)`
      assertion.
- [ ] **Step 3:** Leave `bcschamber_events.xml` alone — do not re-capture it. The
      live-only ids 3896 and 3898 belong in focused record-dict tests, in the
      style of `test_bcschamber_drops_non_bcs_cities`.

### Task 5: Regression — the accepted 28 are unchanged

**Files:** Modify `tests/test_event_watch_bcschamber.py`

- [ ] **Step 1:** Assert every fixture payload that publishes today still
      publishes with an identical `series.place` dict. Snapshot-compare the
      whole normalized output if that is simpler.
- [ ] **Step 2:** `test_bcschamber_normalizing_twice_is_byte_identical` must
      still pass with a resolver stashed in the supplement.
- [ ] **Step 3:** `./run_tests.sh`

### Task 6: Dry run against the live feed

- [ ] **Step 1:**
      ```bash
      docker compose run --rm cortex python -m service.cli run modules.event_watch \
        --kwargs dry_run=true kinds=bcschamber proxy_url= --no-email
      ```
- [ ] **Step 2:** Confirm the summary shows `rejected: 2` (3855, 3889) and that
      no previously-published occurrence changed its `place`.
- [ ] **Step 3:** Update `modules/event_watch/README.md` — the BCS Chamber
      section (the "No city is a loud reject" paragraph now describes a ladder)
      and the 2026-08-30 note that records "six `missing city`".
