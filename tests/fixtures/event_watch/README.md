# event_watch fixtures

One set per source. Both were captured live on **2026-08-12**.

- [Tockify — BCS Library](#tockify--bcs-library)
- [Challenge Entertainment](#challenge-entertainment)
- [KBTX Community Calendar](#kbtx-community-calendar)
- [CitySpark — MyCenTX / FOX 44](#cityspark--mycentx--fox-44)
- [TAMU LiveWhale](#tamu-livewhale)
- [City of Bryan](#city-of-bryan)

---

## Tockify — BCS Library

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

---

## Challenge Entertainment

Captured **2026-08-12** from `https://challengeentertainment.com/wp-admin/admin-ajax.php`,
POST, `geo_input=77840&geo_radius=25`, all other filters blank.

| File | `action` | Extra |
|---|---|---|
| `challenge_shows_2026-08-13.html` … `_2026-08-19.html` | `filter_shows` | `selected_date=<that date>` |
| `challenge_map.html` | `filter_map` | — |

Seven consecutive days, Thursday to Wednesday. That is deliberate: every show in
this radius runs weekly, so a seven-day capture contains each series exactly once
and no series twice — which is what makes "12 raw records, 12 series" a meaningful
assertion rather than an accident of the window.

### What this week actually contains

- **12 shows across 12 series** — 11 Live Trivia, 1 Singo. Four of the seven days
  have no shows at all and answer with an `.ntl-empty-state` div.
- **12 venues, 6 of them bars.** Every one is in `challenge.VENUES`; the live
  five-week dry run found no unmapped venue.
- **One cancellation** — Duddley's Draw on 19 Aug carries `ntl-card-cancelled` and
  a `Cancelled This Week` label, and the *same series runs normally on 26 Aug*.
  This is the fixture behind per-date rather than per-series cancellation.
- **Two Rx Pizza locations**, same venue name, different `data-venue-key` and
  different permalink slug. They must not collapse into one place row.
- **The date pill carries no year** — `🎉 Tonight, 7:00 pm` and
  `📅 Wed, Aug 19, 8:00 pm`. Only the clock time is read from it; the date is the
  one we asked for.
- **`filter_map` is the only source of coordinates and postcodes**, and it carries
  no venue key — the join back to a card is on street + city. The postcode lives
  inside a Google Maps directions URL in each pin's popup HTML.

## KBTX Community Calendar

Captured **2026-08-15** from the live Tockify calendar `kbtx.calendar`, window
`2026-08-15 → 2027-05-12` (270 days).

| File | Source |
|---|---|
| `kbtx_ngevent.json` | `https://tockify.com/api/ngevent?calname=kbtx.calendar&startms=…&endms=…&start=0&max=200` — verbatim |
| `kbtx_feed.ics` | `https://tockify.com/api/feeds/ics/kbtx.calendar` — VEVENT blocks verbatim, filtered |

The ICS feed is the whole calendar (96 VEVENTs). Only the 52 blocks whose series
uid appears in the JSON window are kept.

### What this window actually contains

- **52 singletons, 52 series** — no recurrences.
- **4 all-day listings longer than 14 days** — a statewide virtual-school PSA
  (397d), two Head Start enrollment notices (294d), and a mobile food pantry
  (149d). These are dropped, not rejected.
- **3 timed multi-day events that are kept** — a 16-day play, a 33-day exhibit,
  and a 12-week evening program.
- **Cities:** Bryan 22, College Station 8, no `c_locality` 14, then Brenham /
  Buffalo / Hearne / Woodway / Jewett. Non-BCS cities are dropped.
- **13 events after the duration filter still have no city** — garbled
  addresses (`Univery Drive`, `Briarest Dve`). Those reject loudly unless
  `enrich_places` has already attached an address-kit result.

## CitySpark — MyCenTX / FOX 44

Captured **2026-08-15** from the widget API the FOX 44 calendar actually
calls, not from fox44news.com (PerimeterX). Filter is the same as the
station's public URL: Bryan, TX, 15 miles, sort Popularity, start 2026-08-15.

| File | Source |
|---|---|
| `cityspark_getevents.json` | `POST https://portal.cityspark.com/api/events/GetEvents/MyCenTX` — both pages concatenated into one `Value` array |

### What this window actually contains

- **29 occurrences, 26 series.**
- **Cities:** College Station 23, Bryan 6. Nothing else survived the 15-mile filter.
- **8 listings have no venue name** (mostly A&M sports). City + lat/lng are still present; the place name falls back to the city.
- **`DateStart` is wall-clock Central with a fake `Z`.** Mikey B is listed at 8:00pm; the JSON says `2026-08-28T20:00:00Z`, which is 3pm CDT if believed. The injector strips the Z.
- **`Free` is false on every row.** `is_free` is omitted, not sent as false.
- **`18+` arrives escaped** as `18\\+` in Mikey B's description.

## TAMU LiveWhale

Captured **2026-08-15** from
`https://calendar.tamu.edu/live/json/events` for campus
`Bryan-College Station` and categories Arts & Entertainment | General Interest |
Speakers, Forums, Conferences, Training & Workshops, window
`2026-08-15 → 2026-09-16`, `max=400`. Thumbnails and editor fields stripped.

### What this window actually contains

- **400 occurrences.** The endpoint caps; production fetch walks week chunks.
- **Howdy Week 73, CTE 12, Faculty Affairs 4** — all dropped by group.
- **9 titles contain `training`**, 4 contain `orientation`.
- **Audiences:** Students-only is common (career-center and hiring listings).
- **Campus filter leaks:** McAllen / Dallas / Fort Worth still appear and are
  dropped. 170 records have no campus tag.
- **Locations:** MSC, Forsyth, Stark, Virtual, Zoom, plus Socorro / Houston /
  San Antonio addresses.

## City of Bryan

Captured **2026-08-15** from the GOVstack `_List` fragment, window
`2026-08-15 → 2027-05-12`.

| File | Source |
|---|---|
| `bryantx_list_page0.html` | `GET /default/_List?StartDate=08/15/2026&EndDate=05/12/2027&Page=0` |
| `bryantx_list_page1.html` | same, `Page=1` |

Page 2 is empty. 25 + 21 = **46 cards, 17 series**.

### What this window actually contains

- **First Friday** is four monthly cards (Sep/Oct/Nov/Dec), one series.
- **P&Z** is nine cards; **Maroon & White Night** five; cemetery cleanup five
  consecutive days.
- **No City Council** in this window — the Aug 11 meeting is before the start.
- **Two Larry J. Ringer** author-series nights are College Station; everything
  else is Bryan.
- **`0000` + 12:00 AM** is how holiday closures are stored; those are all-day.
- **Senior Citizens Day** is `0530` in the URL (5:30am). The 12-hour line agrees.

## Refreshing

These pin third-party behaviour, so refresh deliberately, not routinely. A refresh
that changes counts means the tests asserting them should change in the same commit.

The Challenge dates are hard-coded in the filenames and in `CHALLENGE_WEEK` in
`tests/test_event_watch.py`; re-capturing a different week means changing both, and
re-checking the cancellation assertion, which is pinned to a specific night.
