---
repo: /srv/docker/cortex
spec: docs/superpowers/specs/2026-08-30-dedup-and-source-identity-design.md
plan: docs/superpowers/plans/2026-08-30-event-watch-stable-identity.md
verify: ./run_tests.sh
service: cortex
---

# Stabilize event_watch identity keys

FOX 44 / CitySpark mints a new `source_occurrence_tid` every scrape (`Id` is a batch token), so Tyler Halverson appears twice from one series. TAMU sends Garden Club twice (calendar vs AgriLife register) and stuffs `Made Well House 600 E. 27th Bryan` into both name and street. Challenge Entertainment's two Rx Pizza restaurants share the display name `Rx Pizza`. Fix those in the scrapers. Do not teach discoverbcs to match same-source rows.

## Start here

1. Read `docs/superpowers/plans/2026-08-30-event-watch-stable-identity.md` (header, Global Constraints, then tasks in order).
2. Read spec §2.2 and §4 in `docs/superpowers/specs/2026-08-30-dedup-and-source-identity-design.md`.
3. Work every task in the plan, TDD, commit per task.

**Do not start until** the discoverbcs ticket has run `collapse_intra_series_clones` on production SQLite. Otherwise the first CitySpark scrape with a stable tid inserts a *third* Halverson beside two live clones.

## Constraints

- Intake contract is unchanged. No cross-source dedupe in the injector.
- Do not merge Challenge `venue-1419` and `venue-1421` (two restaurants).
- Do not drop nightlife or TAMU academic events.
- Do not special-case `engine._tid_in_window` for CitySpark.
- Verify with `./run_tests.sh` and `python -m pytest tests/test_event_watch.py -k "cityspark or tamu or challenge" -v`.
- Update `test_challenge_two_locations_of_one_chain_are_two_places`; it currently requires both names equal `Rx Pizza`.

## Done when

- [ ] Two CitySpark records that differ only in `Id` produce the same `source_occurrence_tid` (`{PId}:{start_local}`)
- [ ] TAMU `Made Well House 600 E. 27th Bryan` splits to name `Made Well House` and a street starting at `600`
- [ ] Two Garden Club payloads at the same Gardens slot collapse to the `calendar.tamu.edu` uid
- [ ] Challenge still has slugs `rx-pizza-bryan` and `rx-pizza-south-college-station`, but `place.name` is no longer identical `Rx Pizza` for both
