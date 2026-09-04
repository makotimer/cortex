---
repo: /srv/docker/cortex
spec: docs/superpowers/specs/2026-09-04-bcschamber-place-resolution-design.md
plan: docs/superpowers/plans/2026-09-04-bcschamber-place-resolution.md
verify: ./run_tests.sh
service: cortex
---

# cortex: Resolve BCS Chamber places when the feed omits the city

`bcschamber` rejects 6 of 34 events (~18%) with `missing city` and emails about it every run — the same six ids on 08-30, 09-01 and 09-04. Four are recoverable from data the feed already carries: 3849 and 3898 publish a real street line (`3991 East 29th Street`, `1929 Country Club Drive`) with no city, and 3862/3896 both name `Brazos County Expo`, a standing venue that already appears twice in one 34-event snapshot. Add a resolution ladder — pinned venue map, then `kbtx.resolve_address` — behind the existing text paths. 3855 and 3889 have no location at all and stay rejected.

## Start here

1. Read `docs/superpowers/specs/2026-09-04-bcschamber-place-resolution-design.md` — §2 is why this is a bounded job and not an endless one, §3 is the ladder.
2. Read `docs/superpowers/plans/2026-09-04-bcschamber-place-resolution.md` (header, Global Constraints, then tasks in order).
3. Work every task, TDD, commit per task.

## Constraints

- **Every payload accepted today must stay byte-identical.** The new steps run only after both existing `_place_from_text` calls have failed. Task 5 pins this.
- `BaseEventScraper.normalize` is **pure** — no network, no clock, no state. Resolution happens in `fetch()` and lands on `RawEvent.supplement`, exactly as `kbtx` does it. `BcsChamberScraper` gains `state_dir`, and `engine._build_scrapers` must add it to the set constructed with `state_dir=`.
- Do not write a second address resolver. Import `kbtx.resolve_address` — `visitcstx`, `destbryan`, `tamu` and `tamumusic` already do.
- Do not pin a venue address you have not confirmed. BVCOG and Phillips Event Center are confirmed by the feed's own `LocationDesc`; Brazos County Expo and Brazos Center are not — confirm or drop them. A wrong city is worse than a reject because it is silent.
- Do not relax `allow_bare_name=False` on descriptions. It would be right on 3898 ("Sponsored by the City of Bryan") and wrong on 3855 ("Bryan/College Station Chamber of Commerce").
- `test_bcschamber_rejects_events_with_no_city` currently requires 3849 and 3862 to be rejected. Task 4 updates it.
- Do not re-capture `tests/fixtures/event_watch/bcschamber_events.xml`; 3896 and 3898 belong in record-dict tests.
- The intake contract is unchanged. discoverbcs needs no change and is not touched. `bcschamber` stays out of `DEFAULT_KINDS`.

## Done when

- [ ] A record whose only location is `Brazos County Expo` publishes with `place.city == "Bryan"` and `place.area == "bryan"`
- [ ] A record whose location is a street with no city publishes using the injected resolver's city, with no network call in any test
- [ ] `out_of_area` drops silently; any other non-`matched` status rejects
- [ ] Fixture ids 3855 and 3889 still reject `missing city`; 3849 and 3862 publish
- [ ] Every fixture payload that publishes today still publishes with an identical `series.place`
- [ ] `./run_tests.sh` passes
- [ ] A live dry run (`--kwargs dry_run=true kinds=bcschamber proxy_url= --no-email`) reports `rejected: 2`
- [ ] `modules/event_watch/README.md` BCS Chamber section and the 2026-08-30 "six `missing city`" note reflect the ladder
