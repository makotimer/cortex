# Destination Bryan Adult Venues — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `destbryan` bands listings at bars and breweries, and bar-night
titles, `audiences: ["adult"]`.

**Spec:** `docs/superpowers/specs/2026-09-24-destbryan-adult-venues-design.md`

**Repo:** `/srv/docker/cortex` · **Tests:** `./run_tests.sh`

## Global Constraints

- `normalize()` stays **pure**: no network, clock or state.
- Every fixture payload whose venue and title are not adult must be
  byte-identical after the change. `test_destbryan_normalizing_twice_is_byte_identical`
  and `test_conformance_destbryan_payloads_pass_the_real_validator` must pass.
- Never reject a card for an unknown venue. The table only adds a band.
- Do not import `challenge.VENUES`; the two tables mean different things.
- Do not re-capture the `destbryan_*` fixtures; use record-dict tests.
- Commit after every task, plain messages. Do not push. Do not `docker compose`.

## File Structure

| File | Responsibility |
|---|---|
| `modules/event_watch/lib/scrapers/destbryan.py` | `ADULT_VENUES`, `ADULT_TITLES`, `_venue_key`, the band in `_series` |
| `tests/test_event_watch_destbryan.py` | pin it |
| `modules/event_watch/README.md` | one line in the Destination Bryan section |

### Task 1: the venue and title band

- [x] Write failing tests in `tests/test_event_watch_destbryan.py`:
  - `test_destbryan_bar_venue_is_banded_adult` (e.g. "Karaoke at Murphy's Law", place `Murphy's Law Pub`)
  - `test_destbryan_venue_match_ignores_case_and_a_leading_the` (`the owl` / `The Owl`)
  - `test_destbryan_adult_venue_overrides_family_friendly_category`
  - `test_destbryan_bar_night_title_is_adult_at_any_venue` ("College Night at Zoomz Entertainment")
  - `test_destbryan_unknown_venue_keeps_category_audiences` (not rejected, unchanged)
- [x] Add `ADULT_VENUES`, `ADULT_TITLES` and `_venue_key(name)` beside `audiences_from_categories`; apply in `DestBryanScraper._series` after `_place_for`.
- [x] Module docstring: why unknown venues are not rejected here, citing the 2026-08-12 policy.
- [x] `./run_tests.sh` passes. Commit.

### Task 2: docs

- [x] `modules/event_watch/README.md`, Destination Bryan: one sentence on the adult band and where the table lives.
- [x] Commit.
