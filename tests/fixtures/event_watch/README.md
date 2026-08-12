# event_watch fixtures

Captured from the live Bryan + College Station Public Library System Tockify
calendar on **2026-08-12**, window `2026-08-12 → 2026-09-11`.

| File | Source |
|---|---|
| `tockify_ngevent.json` | `https://tockify.com/api/ngevent?calname=bcslibrary&startms=1786492800000&endms=1789084800000` — verbatim |
| `tockify_feed.ics` | `https://tockify.com/api/feeds/ics/bcslibrary` — VEVENT blocks verbatim, filtered |

The ICS feed is the whole calendar (258 VEVENTs, 393 KB), not the window. Only the
110 blocks whose series uid appears in the JSON window are kept, so every byte is
still exactly what the server sent while the repo does not carry 200 out-of-window
events. The header and `END:VCALENDAR` are preserved.

## What this window actually contains

Design §5 was written against an earlier sample. Numbers here are what the tests assert:

- 51 occurrences across 36 series — `repeat` 24, `mod` 11, `singleton` 16.
- Audience tags only, no topics: `Adult` 42, `Children` 14, `Teen` 13, `Tween` 7,
  `Community-Events` 4, `All-Ages` 3, `SRP` 1.
- **Six venues, not four** — the design named four. The extras are `HEB` (an outreach
  event in a grocery store) and the `Bob and Wanda Meyer Senior and Community Center`.
- **One occurrence has no place at all** — `place`, `address` and `location` are all
  null. The contract requires `series.place.name`, so it cannot be published; it is
  the fixture behind the unknown-venue test.
- `eid.tid` is an **int** in the JSON; the contract wants a string.
- `status` is an object (`{"name": "scheduled"}`), not a bare string, and every record
  in this window is `scheduled` — the feed-stated cancellation path has no live
  example and is covered by a synthetic fixture instead.
- 3 occurrences have no end time; 1 is all-day.

## Refreshing

These pin third-party behaviour, so refresh deliberately, not routinely. A refresh
that changes counts means the tests asserting them should change in the same commit.
