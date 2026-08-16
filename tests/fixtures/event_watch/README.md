# event_watch fixtures

One set per source. Both were captured live on **2026-08-12**.

- [Tockify — BCS Library](#tockify--bcs-library)
- [Challenge Entertainment](#challenge-entertainment)
- [KBTX Community Calendar](#kbtx-community-calendar)
- [CitySpark — MyCenTX / FOX 44](#cityspark--mycentx--fox-44)
- [TAMU LiveWhale](#tamu-livewhale)
- [City of Bryan](#city-of-bryan)
- [Lake Walk](#lake-walk)

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

## TAMU Music Activities

Captured **2026-08-15** from
`GET /live/json/events/group/Music%20Activities` for
`2026-08-15 → 2027-05-12`. Thumbnails stripped.

| File | Source |
|---|---|
| `tamumusic_events.json` | 8 concerts, Sep 25–Nov 20 2026 |

- All gid 151, parent empty. Same titles appear on the campus feed as gid 5 copies with ``parent`` set to these ids.
- All at Rudder Theater / Auditorium, campus Bryan–College Station.
- Audiences include Visitors, Residents, and Youth (K-12).

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

## Lake Walk

Captured **2026-08-15**, page 1 of
`GET /wp-json/tribe/events/v1/events?start_date=2026-08-15&end_date=2027-05-12&per_page=50`.
Image blobs and venue HTML stripped.

The endpoint reports **2711 / 55 pages**. Page 1 is 50 rows that are only
**two real occurrences**: Community Yoga on 15 Aug (26 ghost copies) and
Stroller Barre on 18 Aug (24 copies). Dated slugs
(`community-yoga-at-lake-walk-2-2-2026-03-07`) are TEC recurrence debris
all remapped to the same `start_date`. Normalize keeps one per
`(series, start)`.

## BCS Chamber of Commerce

Captured **2026-08-15** from
`GET https://business.bcschamber.org/api/events` (verbatim XML).

| File | Source |
|---|---|
| `bcschamber_events.xml` | 32 ``EventDisplay`` rows, 2026-08-18 → 2026-11-19 |

- ``from`` / ``to`` query params do not change the payload.
- 9 rows carry ``MapAddr1``; several of those pin 2700 Earl Rudder Fwy while ``LocationDesc`` is a different Bryan/CS street.
- Lucky Goat “Hudson Oaks” is 3349 University Dr E, Bryan, TX 77802.
- Four rows have no usable city (Lobsterfest, St. Joseph After Hours, Youth Career Fair / Expo, BVCOG Lunch & Learn).

## The Theater Company

Captured **2026-08-15** from
`GET https://www.theatrecompany.com/calendar?format=json`.
Upcoming items only; bodies and image blobs stripped.

| File | Source |
|---|---|
| `ttc_calendar.json` | 74 upcoming nights, Aug 21 2026 → Drowsy Chaperone 2027 |

- 17 raw titles; ``(Copy)`` and trailing spaces collapse How to Succeed to 12 nights.
- ``location`` is empty with a NYC default pin on every row.
- One ``TTC Work Week`` (Jan 4–9 2027) is dropped as internal.

## Stage 12

Captured **2026-08-16** from
`https://www.brookshirebrothers.com/college-station/stage12events`.
Slimmed to the Drupal Calendar View table plus eight event nodes.

| File | Source |
|---|---|
| `stage12_month_august.html` | default month page, caption August 2026, 15 rows (incl. Jul/Sep spillover) |
| `stage12_month_september.html` | `calendar_timestamp=1788238800`, 11 rows |
| `stage12_month_empty.html` | `calendar_timestamp=1798783200`, January 2027, 0 rows |
| `stage12_node_500451.html` | Movie Night: Monsters University (FREE) |
| `stage12_node_500453.html` | Live Music — Artist: The Fragments |
| `stage12_node_500485.html` | Singo: Back to School (no free wording) |
| `stage12_node_500593.html` | Kids Camp — Ages 6-10, REGISTRATION CLOSED |
| `stage12_node_500630.html` | Craft Night + Live Music — Artist: Keaton Kyzar |
| `stage12_node_500632.html` | Ice Cream Social + Live Music — Artist: Cole Stephens |
| `stage12_node_500636.html` | Live Music — Artist: Peril Suite |
| `stage12_node_500654.html` | Karaoke (admission is FREE) |

- No JSON/ICS. Times are `<time datetime>` on the listing; the node only restates the date.
- Next never stops on empty months, so the walk has to halt itself.
- Visit College Station already lists some of these with the artist in the title.

## Brazos Valley Museum

Captured **2026-08-15** from `https://www.brazosvalleymuseum.org/`.
Slimmed to the Upcoming Events repeater (`#comp-k5cycbh5`) plus the
exhibits repeater so the parser has to pick the right strip.

| File | Source |
|---|---|
| `bvmuseum_home.html` | homepage Upcoming Events + exhibits cards |

- **4 upcoming cards**, yearless: Summer Nature Camp (Jun 2–Aug 8, 9 a.m.–3 p.m.), Wish Upon a Butterfly (Jul 25, 9 a.m.–12 p.m.), Buffalo Stampede (Oct 18, 7:30 a.m. in the body), Boonville Days (Oct 18, 9 a.m.–4 p.m.).
- **3 exhibit cards** (Discovery Room, etc.) must not be parsed as events.
- Camp's Learn More is the homepage; the others have dedicated paths.
- Against a window starting 2026-08-15, camp and butterfly drop as past.

## Destination Bryan

Captured **2026-08-15** from
`https://www.destinationbryan.com/events/?date-from=2026-08-15&date-to=2026-09-15`.
Listing HTML is slimmed to the result counter plus ``article.card`` blocks.

| File | Source |
|---|---|
| `destbryan_list_page1.html` | page 1 (results 1–12 of 219) |
| `destbryan_list_page2.html` | page 2 (results 13–24) |
| `destbryan_details.json` | schema.org Event JSON-LD from three of those cards |

- **219 listings** in that month; 12 per page.
- Cards carry Craft ``data-entry-id``, one primary category, street, lat/lng,
  and a Google Maps query with city/ZIP. Clock times are only on the detail
  JSON-LD.
- Month-long exhibits use ``August 15 to October 24`` (no year) or
  ``August 15 to May 16, 2027``.
- Some JSON-LD start/end values wear a fake ``Z`` (Cadillac Ranch 19:00Z is
  7pm Central, same CitySpark lie).

## Visit College Station

Captured **2026-08-15** from Algolia index
``prod-visit-college-station-listings`` with ``filters=sectionName:Events``.
Highlight/snippet fields stripped.

- **86 hits, 67 series.** Aggieland Farmers Market is one series with 20 Saturday
  occurrences (`isPrimaryEvent` only on the first).
- **Cities:** College Station 82, Bryan 4. No nearby towns in this snapshot.
- **`startDate`/`endDate` are Central wall-clock stored as UTC unix.** Harvest
  is listed 9:00–11:00 a.m. and the stamp is `1786784400` (09:00Z).
- One multi-day listing, Spirit of 150 Week (7 days), is under the 14-day drop.

## Bush 41 Library

Captured **2026-08-15** from
``https://www.bush41library.gov/events/upcoming-events`` plus the three detail
nodes. Listing HTML is the ``.view-id-events`` block; details are
``h1`` + ``.region-content``.

- **3 upcoming programs** (Sep 8, Oct 22, Nov 7). No JSON/ICS; ``/events/all-events`` 404s.
- Date is month/day/year on the listing. Clock time is only in the body when
  they write it (Birdwell: ``at 10 a.m.``). The other two have none.
- Register link is ``#event-registration-form`` on all three.

## Brazos Valley Symphony Orchestra

Captured **2026-08-15** from ``https://bvso.org/concerts/``,
``/wp-json/wp/v2/tc_events``, and five ``/show-item/{slug}/`` pages
(trimmed to the date/title/description blocks).

- **7 season cards** on `/concerts/` under ``2026-2027 Concerts``. Day +
  month only. Read More is the show page; Get Tickets is Tickera.
- **19 published ``tc_events``**. The seven season slugs are in that
  list; the rest are leftovers. REST has no performance date.
- Season show pages: ``Concert Starts: 5:00 PM``, reception at 4:00 if
  any, Rudder Theatre or Christ Church. Holiday Concert has no reception.
- Nutcracker prints ``December 5, 2025 | 7 PM`` and
  ``December 6, 2025 | 2 PM & 6:30 PM`` at Rudder Auditorium.
- Bach to Tchaikovsky is a leftover ``21 September`` with no year.
- Season Release Party is April 14 at Benjamin Knox Gallery, no year.

## Hyperbole Bookstore

Captured **2026-08-15** from Bookmanager
``POST https://api.bookmanager.com/customer/event/getList``
(``store_id=1110171``, ``from=20260815``). Session id stripped by using
the saved ``rows`` only.

- **35 events, 15 series.** Children's Storytime is 21 Saturdays.
- ``from``/``to`` unix stamps display in ``America/Los_Angeles``.
  Storytime ``1786815027`` is 10:30 PDT (the advertised 10:30 AM), not
  12:30 CDT.
- ``ticket`` is empty on every row in this snapshot.
- Author-visit titles overlap the library series; venue is the shop.

## REI Co-op College Station

Captured **2026-08-16** from
``https://www.rei.com/events/p/us-tx-college-station`` (jina reader;
``event_probe`` through gluetun and the container IP both read-timeout
on ``www.rei.com``).

| File | Source |
|---|---|
| `rei_list.html` | list page reduced to the ``#modelData`` script |
| `rei_search.json` | ``pageData.search`` plus capture metadata |
| `rei_list_empty.html` | Access Denied shell, no ``modelData`` |

- **12 courses in the 100-mile window, 6 College Station sessions.**
  Location id ``214``, store 284, 615 University Dr. E #300.
- Austin Gateway / Houston / Houston Willowbrook / Travis Audubon
  rows are present on the list and must not publish.
- ``session.timeZone`` is ``America/Los_Angeles`` on every CS row;
  ``location.timezone`` is ``America/Chicago``.
- Three free (member and non-member $0), three paid (member $15 / $25
  / $40).

## Wonderful Words Bookshoppe

Captured **2026-08-16** from
``GET https://www.wonderfulwordsbookshoppe.com/_api/wix-events-web/v1/events``
with the Events app instance from ``/_api/v2/dynamicmodel``.
``event_probe`` of ``/event-list`` through gluetun is 200 and an
events-viewer shell (no cards).

| File | Source |
|---|---|
| `wonderfulwords_events.json` | scheduled rows ``2026-08-16 → 2027-05-13`` plus one canceled sample |

- **95 scheduled:** Storytime 75, First Friday 9, Bookclub 9, Special Storytime 2.
- Default list order is future-first. ``description`` / ``about`` are empty.
- ``scheduling.config.timeZoneId`` is ``America/Chicago``; start/end are UTC.
- CANCELED ``Story time`` (space) is a different series from ``Storytime``.

## Painting with a Twist

Captured **2026-08-16** through gluetun from
``https://www.paintingwithatwist.com/studio/college-station/calendar/``.

| File | Source |
|---|---|
| `pwat_calendar.html` | studio calendar, August–September 2026 |

- **27 events.** ``time.event-datetime="2026-08-16T03:00"`` is 3pm.
- Family Day owl is ``4368685``, ``$28``, 3:00–4:30pm Sep 5.

## Refreshing

These pin third-party behaviour, so refresh deliberately, not routinely. A refresh
that changes counts means the tests asserting them should change in the same commit.

The Challenge dates are hard-coded in the filenames and in `CHALLENGE_WEEK` in
`tests/test_event_watch.py`; re-capturing a different week means changing both, and
re-checking the cancellation assertion, which is pinned to a specific night.
