---
repo: /srv/docker/cortex
spec: docs/superpowers/specs/2026-09-24-destbryan-adult-venues-design.md
plan: docs/superpowers/plans/2026-09-24-destbryan-adult-venues.md
verify: ./run_tests.sh
service: cortex
---

# cortex: Band Destination Bryan bar and brewery listings adult

discoverbcs.org put Destination Bryan back on its family calendar on 2026-09-24. About 20 of its listings are karaoke, open mics, trivia and brewery Oktoberfests at pubs, and they arrive with no audience, so they read "Ages not listed" to a parent filtering by age. Band them `["adult"]` by venue (a hand-kept table, like `challenge.py`'s `VENUES`) and by a few bar-night title words. Unlike Challenge Entertainment, an unknown venue is **not** rejected: most of this feed is family programming.

## Start here

1. Read `docs/superpowers/specs/2026-09-24-destbryan-adult-venues-design.md` (short; §3 is the design and the initial venue list).
2. Read `docs/superpowers/plans/2026-09-24-destbryan-adult-venues.md`, then do Task 1.

## Constraints

- `normalize()` stays pure. Never reject for an unknown venue.
- Payloads for non-adult venues and titles must be byte-identical.
- Do not re-capture the `destbryan_*` fixtures. Do not import `challenge.VENUES`.
- discoverbcs is not touched; the intake contract is unchanged.
- Commit per task with plain messages. Do not push, do not `docker compose`, do not raise `timeout:`.

## Done when

- [ ] A card at `Murphy's Law Pub` (any case, with or without a leading "The") publishes `audiences == ["adult"]`, even if categorised `family-friendly`
- [ ] A "College Night" or "Karaoke" title publishes `["adult"]` at an unlisted venue
- [ ] A card at an unlisted venue with no bar-night title publishes exactly as before and is not rejected
- [ ] `./run_tests.sh` passes
- [ ] `modules/event_watch/README.md` Destination Bryan section mentions the band and the table
