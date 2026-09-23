# event_watch

Scrapes public event calendars and publishes them onto `events:<site>` as
`cortex.discoverbcs-ingest`.

| Kind | Source | Window | Notes |
|---|---|---|---|
| `tockify` | Bryan + College Station Public Library System | run default (270d) | JSON + ICS feeds |
| `challenge` | Challenge Entertainment — pub trivia, Singo, bingo | **35d** (own cap) | AJAX API, one request per day |
| `kbtx` | KBTX Community Calendar (Tockify `kbtx.calendar`) | run default (270d) | JSON + ICS; BCS only; address-kit fallback |
| `kbtxdeals` | KBTX Marketplace (incentRev coupons) | first-seen day | all-day on first appearance; not the Tockify calendar |
| `tamu` | Texas A&M LiveWhale calendar | run default (270d) | JSON; public-interest filter; BCS only |
| `tamumusic` | TAMU Music Activities (LiveWhale group) | run default (270d) | group JSON; same concerts also appear in `tamu` |
| `cityspark` | FOX 44 / MyCenTX (CitySpark widget API) | run default (270d) | Bryan 15mi; direct, not fox44news.com |
| `bryantx` | City of Bryan GOVstack calendar | run default (270d) | HTML `_List` pages; direct |
| `lakewalk` | Lake Walk (The Events Calendar) | run default (270d) | tribe REST; dedupe TEC ghosts; direct |
| `destbryan` | Destination Bryan Craft directory | run default (270d) | HTML list + JSON-LD; all categories; nearby ZIPs |
| `bvmuseum` | Brazos Valley Museum of Natural History | run default (270d) | Homepage Upcoming Events repeater; year from the run; direct |
| `visitcstx` | Visit College Station (Algolia Events) | run default (270d) | InstantSearch index; all categories; not destbryan |
| `bush41` | George H.W. Bush Presidential Library | upcoming list | Drupal Views; date + optional body clock |
| `bvso` | Brazos Valley Symphony Orchestra | run default (270d) | `/concerts/` + Tickera; year fail-closed |
| `hyperbole` | Hyperbole Bookstore (Bookmanager) | run default (270d) | `event/getList`; LA wall-clock → Chicago |
| `bcschamber` | BCS Chamber of Commerce (GrowthZone) | run default (270d) | `/api/events` XML; LocationDesc over Map*; direct |
| `ttc` | The Theater Company of Bryan / College Station | run default (270d) | Squarespace `/calendar?format=json`; one night per occurrence |
| `stage12` | Stage 12 (Brookshire Brothers, College Station) | run default (270d) | Drupal month calendar + node details; direct |
| `rei` | REI Co-op College Station | run default (270d) | `#modelData` JSON; store 214 only; **RETIRED 2026-09-04 — Akamai** |
| `wonderfulwords` | Wonderful Words Bookshoppe (Wix Events) | run default (270d) | `_api/wix-events-web`; instance from dynamicmodel |
| `pwat` | Painting with a Twist College Station | run default (270d) | SSR calendar HTML; 12-hour `datetime` trap |
| `sewmuchclass` | Sew Much Class | run default (270d) | Rain POS month grid; drop PHD / closed / training |
| `thepalace` | The Palace (downtown Bryan concerts) | run default (270d) | Theater Toolkit JSON-LD; live shows only |
| `lowes` | Lowe's Kids Club | run default (270d) | `/workshopdata` JSON; CS store 3032; VPN |
| `homedepot` | Home Depot Kids Workshop | run default (270d) | first Saturday + Canada kit names; CS #6559; VPN |
| `americangi` | Museum of the American G.I. | run default (270d) | TEC REST; photo view is a shell; direct |
| `cstxparks` | College Station Parks & Rec (RecTrac Events) | run default (270d) | WebTrac HTML; Events category only |
| `cstxcal` | City of College Station (Timely) | run default (270d) | Public Type JSON; meetings in; office-closure Notices out |
| `youthcamps` | TAMU Youth Camps (Localist) | run default (270d) | `/api/2/events`; BCS only; campus pins |
| `bvchess` | Brazos Valley Chess Association (Google Calendar) | run default (270d) | public ICS; one VEVENT per tournament; venue gate; direct |

Design: `/srv/docker/websites/discoverbcs/docs/superpowers/specs/2026-08-12-bcs-library-event-injector-design.md`
Contract: `/srv/docker/websites/discoverbcs/docs/intake-contract.md`

## Status

**Live.** All 30 kinds are scheduled in `local/config.json` (gitignored — the
entries below are the record of what is there). `vpn-cycle-events` fires at
01:00 on the same days, ahead of the 01:10 first scrape. `event-watch-done`
fires at 02:00.

| Job | Kind | When | kwargs |
|---|---|---|---|
| `event-watch-destbryan` | `destbryan` | Tue/Fri 01:10 | `rotate_vpn_per_run: false` |
| `event-watch-challenge` | `challenge` | Tue/Fri 01:10 | `proxy_url: ""` |
| `event-watch-bvso` | `bvso` | Tue/Fri 01:10 | `proxy_url: ""` |
| `event-watch-lowes` | `lowes` | Tue/Fri 01:10 | `proxy_url: ""` |
| `event-watch-homedepot` | `homedepot` | Tue/Fri 01:10 | `proxy_url: ""` |
| `event-watch-americangi` | `americangi` | Tue/Fri 01:10 | `proxy_url: ""` |
| `event-watch-cityspark` | `cityspark` | Tue/Fri 01:22 | `proxy_url: ""` |
| `event-watch-bryantx` | `bryantx` | Tue/Fri 01:22 | `proxy_url: ""` — sends a browser User-Agent; events.bryantx.gov 403s bot UAs since 2026-09-18 |
| `event-watch-lakewalk` | `lakewalk` | Tue/Fri 01:22 | `proxy_url: ""` |
| `event-watch-bvmuseum` | `bvmuseum` | Tue/Fri 01:22 | `proxy_url: ""` |
| `event-watch-bcschamber` | `bcschamber` | Tue/Fri 01:22 | `proxy_url: ""` |
| `event-watch-ttc` | `ttc` | Tue/Fri 01:22 | `proxy_url: ""` |
| `event-watch-stage12` | `stage12` | Tue/Fri 01:24 | `proxy_url: ""` |
| `event-watch-sewmuchclass` | `sewmuchclass` | Tue/Fri 01:24 | `proxy_url: ""` |
| `event-watch-bvchess` | `bvchess` | Tue/Fri 01:24 | `proxy_url: ""` |
| `event-watch-bcslibrary` | `tockify` | Tue/Fri 01:28 | `rotate_vpn_per_run: false` |
| `event-watch-kbtx` | `kbtx` | Tue/Fri 01:28 | `rotate_vpn_per_run: false` |
| `event-watch-tamu` | `tamu` | Tue/Fri 01:28 | `rotate_vpn_per_run: false` |
| `event-watch-visitcstx` | `visitcstx` | Tue/Fri 01:28 | `rotate_vpn_per_run: false` |
| `event-watch-bush41` | `bush41` | Tue/Fri 01:28 | `rotate_vpn_per_run: false` |
| `event-watch-hyperbole` | `hyperbole` | Tue/Fri 01:28 | `rotate_vpn_per_run: false` |
| `event-watch-kbtxdeals` | `kbtxdeals` | Tue/Fri 01:32 | `rotate_vpn_per_run: false` |
| `event-watch-tamumusic` | `tamumusic` | Tue/Fri 01:32 | `rotate_vpn_per_run: false` |
| `event-watch-wonderfulwords` | `wonderfulwords` | Tue/Fri 01:32 | `rotate_vpn_per_run: false` |
| `event-watch-pwat` | `pwat` | Tue/Fri 01:32 | `rotate_vpn_per_run: false` |
| `event-watch-thepalace` | `thepalace` | Tue/Fri 01:32 | `rotate_vpn_per_run: false` |
| `event-watch-cstxparks` | `cstxparks` | Tue/Fri 01:32 | `rotate_vpn_per_run: false` |
| `event-watch-cstxcal` | `cstxcal` | Tue/Fri 01:36 | `rotate_vpn_per_run: false` |
| `event-watch-youthcamps` | `youthcamps` | Tue/Fri 01:36 | `rotate_vpn_per_run: false` |
| ~~`event-watch-rei`~~ | `rei` | ~~Tue/Fri 01:40~~ | `enabled: false` since 2026-09-04 |
| `event-watch-done` | — (`modules.event_watch_done`) | Tue/Fri 02:00 | `site: discoverbcs` — publishes `watch.complete` so the site reviews the morning's intake as one batch |

First real injection of `challenge`: 2026-08-12, window `2026-08-13 → 2026-09-17`,
**48 upserted / 0 cancelled / 0 rejected**, 12 series, no unmapped venue.

### Why two jobs and not one

They need different network paths, and one job carries one `proxy_url`.
`challengeentertainment.com` refuses every gluetun exit tried so far, so that kind
runs direct; `tockify` keeps its proxy. Folding them together would mean either
sending the library feed out un-proxied or letting the Challenge fetch fail on
every run.

That is also why **every job pins `kinds` explicitly**. The default is
`["tockify", "challenge"]` — `kbtx`, `kbtxdeals`, `cityspark`, `tamu`, `bryantx`,
`lakewalk`, `bvmuseum`, `bvso`, `hyperbole`, `bcschamber`, `ttc`, `stage12`, `rei`, `wonderfulwords`, `pwat`, `sewmuchclass`, `thepalace`, `lowes`, `homedepot`, `americangi`, `cstxparks`, `cstxcal`, `youthcamps` and `bvchess` are opt-in so a bare run does not silently add them. A job that omits `kinds` would pick up Challenge
through whatever proxy that job has, and the library job would then email a
fetch failure twice a week. `cityspark` talks to portal.cityspark.com, not
fox44news.com; it goes direct like Challenge.

The jobs share no state and no source, so they overlap on purpose. The
container is capped at 512 MB and the scheduler has 10 workers, so each
minute starts at most six scrapes. Destination Bryan is the long proxied
run and has 01:10 to itself among proxied jobs; five direct scrapes share
that minute. The other direct scrapes are 01:22 and 01:24. The other
proxied scrapes are 01:28, 01:32, and 01:36. `event-watch-done` is 02:00,
after the last scrape and before career-watch rotates the VPN at 04:45.
Friday's digest goes out with that 02:00 signal.

Window length and schedule are deliberately unscoped by the design (§3, §11.3).
`window_days` defaults to 270; a scraper may narrow it (see below).

### 2026-08-30 notes

Schedule moved **Wed/Sun → Tue/Fri**. All kinds are now jobs (the previous 14
opt-ins were added on the same 15-minute grid after 06:20). `vpn-cycle-events`
moved with them (Tue/Fri 03:25). Live `local/config.json` is gitignored; the
table above is the record.

TAMU `no_match` attention mail was Traveling Aggies country names, KAMU
`multiple locations`, and other-county AgriLife offices — not a geocoder
outage. Unplaceable locations are now `drop_geo`. `Peru` had been matching
Fusion Peru. Rejected records log title + location.

First inject of the 14 newly scheduled kinds (same day): most upserted or were
already current. **REI failed** (direct read-timeout; VPN verify 403) and kept
failing every run until it was retired on 2026-09-04. Six older REI occurrences
remain in state. BCS Chamber emailed six `missing city` — four of them now
resolve (venue map + street resolution, see the BCS Chamber section), leaving
two: Lobsterfest and a Business After Hours with no address at source.

## A source may see less far ahead than the run asks for

`BaseEventScraper.max_window_days` caps one source's horizon, and the **engine**
applies it — narrowing `fetch` *and* the window `reconcile()` measures
disappearance against.

Those must be the same window. A scraper that quietly clamped its own fetch would
leave the engine believing it had looked eight months ahead, find nothing there,
and cancel a calendar it never actually asked about.

`challenge` sets 35 days because it costs one HTTP request per day, so the window
is the request budget. Five weeks is past every frequency the source offers
(weekly, bi-weekly, 2x/month, monthly), so each series is observed at least once.

## Shape

```
main.py            run(**kwargs) -> None | (html, meta)
lib/config.py      settings from kwargs then env
lib/engine.py      sequencing + the failure ladder; reconcile() is pure
lib/state.py       what was sent last time (digests), topic cache. Storage only
lib/classify.py    topic assignment — labels only, see below
lib/publish.py     event.upsert / event.cancel / ingest.report
lib/normalize.py   pure helpers common to every source
lib/scrapers/      one file per source; fetch does I/O, normalize is pure
```

The load-bearing boundary is I/O versus pure, not file-by-file: `normalize` never
touches the network, the clock or state, so every per-source rule is testable
against saved fixtures.

## Only genuine changes are published

Each run records a digest of the payload it published per occurrence. The next run
re-sends only what actually changed, and always emits one `ingest.report` — including
runs that changed nothing.

That last part is not decoration. If the injector simply stopped sending unchanged
events, a quiet source and a dead injector would look identical at `/admin/intake`.
The report is what keeps "nothing changed" distinguishable from "nothing ran".

## Topics are labels-only for now

Design §6 wants an LLM pass once per series. Cortex has **no route to any LLM** —
every `llm-proxy` is a per-site sidecar and cortex joins only `mailnet` and
`eventbus`. §11 open decision 1 anticipated this: ship labels-only (`SRP` →
`reading`, `Community-Events` → `community`) until it is resolved. `classify.classify_series`
is the seam; pass it a callable and the cache and nothing else changes.

Untagged series sort first in `/admin/events`, which is exactly the editorial backlog.

## Challenge Entertainment: two decisions worth knowing

**It is asked for one date at a time.** With no `selected_date` the endpoint
returns one card per show carrying only its *next* occurrence and a date pill with
no year. Expanding `"Wednesdays, 8:00 pm"` locally would invent dates the source
never asserted — including the ones it cancelled. Asking per date instead gets
occurrences the source stands behind, with per-date cancellation, and removes year
inference entirely because we supplied the date.

**`VENUES` is an editorial gate, not a lookup.** These shows run in family
restaurants *and* in 21+ bars, and "Live Trivia" reads identically at both. The
table records the audience band per venue — `all-ages` or `adult` — and a venue
that is not listed is rejected loudly and emailed rather than guessed at. That is
the same failure mode as an unmapped Tockify venue, for the same reason: the cost
of guessing lands on a family, not on us.

Everything factual still comes from the live feed. `area` is derived from the city
the source states, and latitude, longitude and postcode come from `filter_map` —
so the table holds only judgement, and only judgement needs maintaining. The
current banding (6 family, 6 bars) is a first pass; flip any entry that reads
wrong.

## KBTX: a second Tockify calendar, not a second library

Fetch is the same JSON+ICS pair. Everything else is not.

**Months-long all-day listings are dropped.** The feed uses all-day spans of
months for PSAs and ongoing programmes (virtual school, Head Start, a pantry).
Anything all-day and longer than 14 days is skipped. A timed play or exhibit
that happens to last two weeks is kept.

**Bryan and College Station only.** Brenham, Hearne, Leon County and Waco-area
listings are dropped, not filed as `nearby`.

**Venues are not a maintained table.** A Bryan/College Station `c_locality`
with a street is structured: the place is the venue name, or the street when
Tockify omitted a name — never the city itself. City-only records omit
`series.place`. A named venue with no street goes through `address-kit`
(Census → Geoapify → Google, BCS ZIPs only) and caches on `place_id` or the
cleaned address, so a venue is looked up at most once. `out_of_area` is a
quiet drop; `no_match` keeps the named venue without a street when the city
is already BCS, and is an attention email when there is no city.
`normalize` never calls the kit.

`kbtx` is not in `DEFAULT_KINDS`. Pin it.

## KBTX Marketplace

incentRev coupons at `kbtx.incentrev.com/bryan/deals`. Not the Tockify
community calendar. Each live voucher is one all-day occurrence dated
the first run that saw it (`first_seen` in state). A later run does
not move that date. `kbtxdeals` is not in `DEFAULT_KINDS`. Pin it.

## CitySpark / MyCenTX

FOX 44 embeds CitySpark. The page on fox44news.com is PerimeterX; the feed is
`POST https://portal.cityspark.com/api/events/GetEvents/MyCenTX` with no auth.
The filter is Bryan + 15 miles — the same one as the station's public URL.

`DateStart` is wall-clock Central wearing a `Z`. Believing the suffix would
shift every timed listing five or six hours. `Free` is false on the whole
captured window, so `is_free` is omitted rather than sent as a lie.

A CitySpark row with neither `Venue` nor `Address` is still published, with
no `series.place` — the widget's city centroid is not a venue. A street
without a venue name becomes the place name. A named venue without a street
is looked up through `address-kit` (cached like KBTX); a miss keeps the
name and drops the city pin.

`cityspark` is not in `DEFAULT_KINDS`. Pin it, and run it un-proxied.

## TAMU LiveWhale

Public `/live/json/events`. The listing HTML is just a LiveWhale shell.

**Public-interest filter, not an audience gate.** Drop title matches
(`training`, `orientation`, `office hours`, `retreat`, `new student`), groups
Howdy Week / CTE / Faculty Affairs, and Students-only unless Visitors,
Residents, or Youth (K-12) is also tagged. Untagged talks stay.

**Bryan–College Station only.** The campus filter leaks. Virtual/Zoom without a
BCS campus tag is dropped. Named campus buildings become `college_station`
without a geocoder; street addresses and named local venues go through
address-kit. Country names, `multiple locations`, and other-county offices are
dropped quietly — geocoding them emailed `no_match` twice a week, and `Peru`
matched Fusion Peru. A local venue that still cannot be resolved is rejected
with the location in the log and the attention email.

The JSON endpoint silently caps around 400, so fetch walks the window a week at
a time. Occurrence tid is start-time millis so disappearance reconciliation
still works. `tamu` is not in `DEFAULT_KINDS`. Pin it.

## TAMU Music Activities

Same LiveWhale JSON, group `Music Activities` (`/music-activities/all`).
Native ids (385689…) differ from the copies on the campus calendar
(386730…, `parent` = native id). `tamu` already sees those copies; this
kind publishes the native rows with source `tamu-music` and a `music`
topic. Downstream duplicate detection owns the overlap. `tamumusic` is
not in `DEFAULT_KINDS`. Pin it.

## City of Bryan

GOVstack / CivicPlus HTML. `GET /default/_List?StartDate=&EndDate=&Page=`
(0-based) until a page is empty. Times come from the detail path
(`2026-09-04-1700`), not the `5:00 PM` string. `0000` is all-day.

First Friday is one series with one occurrence per published month. Public
meetings stay in. `bryantx` is not in `DEFAULT_KINDS`. Pin it and run direct.

## Lake Walk

The Events Calendar REST API
(`GET /wp-json/tribe/events/v1/events`). The public grid is one card per
series and the permalink date is often not the next occurrence.

TEC expands the window but **repeats each real date ~25 times** with
`-YYYY-MM-DD` slug suffixes. Fetch walks 50-per-page; normalize keeps one
row per `(slug-without-date, start)`. Yoga every Saturday is one series,
one occurrence per Saturday.

Venue is almost always The Pavilion at Lake Walk in Bryan. `wellness` /
`yoga` are not in the site vocab and are dropped. `lakewalk` is not in
`DEFAULT_KINDS`. Pin it and run direct.

## Brazos Valley Museum

The `/calendar` page is a Wix Events widget whose API holds only ended
rows (a 2020 camp plus leftover demo shows). What they actually maintain
is the homepage **Upcoming Events** repeater (`#comp-k5cycbh5`).

Cards carry month, day and a clock, but no year. Fetch assigns the run
window's year and drops a card whose whole span is already past. Detail
pages are not a second clock — Learn More is the permalink only. One
card is one series and one occurrence; camp weeks and the 5K / 1-mile
are not expanded.

The museum is at 3232 Briarcrest Dr, Bryan. No topic or audience labels
are on the cards, so those stay empty. `bvmuseum` is not in
`DEFAULT_KINDS`. Pin it and run direct.

## Brazos Valley Symphony Orchestra

`/concerts/` is the 2026–27 season list (day + month; year from that
heading: Sep–Dec = first year, Jan–Aug = second). Tickera
`/wp-json/wp/v2/tc_events` is the same slugs plus leftovers. Dates live
on `/show-item/{slug}/`. A leftover without a 4-digit year on the page
is dropped — last season's "21 September" is not rolled into this one.
404 show pages are skipped. Clock is **Concert Starts**, never the
reception. Rudder Theatre / Auditorium and Christ Church are pinned
College Station halls; any other named venue fails loudly.

`bvso` is not in `DEFAULT_KINDS`. Pin it and run direct — gluetun
exits get 403 from bvso.org.

## Hyperbole Bookstore

Bookmanager, not the HTML. `POST /customer/session/get` then
`event/getList` (`store_id=1110171`). Unix `from`/`to` are shown in
`America/Los_Angeles`; wall-clock 10:30 there is the 10:30 the store
prints, filed as `America/Chicago`. Storytime is one series, one
occurrence per Saturday (`source_occurrence_tid` is the Bookmanager
id). One pinned College Station shop. `hyperbole` is not in
`DEFAULT_KINDS`. Pin it.

## BCS Chamber of Commerce

GrowthZone. The search HTML is a 10-card first paint; do not scroll it.
`GET /api/events` is the upcoming catalog (date query params are ignored).
`StartDate` / `EndDate` are wall-clock Central with no `Z`. `MapCity` is
often a dummy pin at 2700 Earl Rudder Fwy — venue comes from
`LocationDesc`, then the description, then a pinned venue map
(`CHAMBER_VENUES`: BVCOG, Phillips Event Center, Brazos County Expo, Brazos
Center — all Bryan), then `kbtx.resolve_address` on a street line that has no
city. The last two steps run only after the text paths fail, so every payload
that resolved from text before them is byte-identical. A resolved city outside
Bryan / College Station, or a no-match, is still a loud reject, not a guess —
and an event with no location at all (Lobsterfest) stays a reject.
Lucky Goat “Hudson Oaks” is at 3349 University Dr E in Bryan.

Ribbon Cuttings / Business After Hours / Government map to `community`.
`bcschamber` is not in `DEFAULT_KINDS`. Pin it and run direct.

## The Theater Company

Squarespace. `GET /calendar?format=json` is the upcoming catalog (74
nights today). Each listed night is one occurrence of the production
series (cleaned title; `(Copy)` joins the real show). `startDate` is
epoch-ms, wall-clock `America/Chicago`, microseconds dropped. The
Squarespace map pin is empty NYC — venue is 3125 S Texas Ave, Ste 500,
Bryan. `TTC Work Week` is dropped. No category labels. `ttc` is not in
`DEFAULT_KINDS`. Pin it and run direct.

## REI Co-op College Station

The public list is a 100-mile radius. `#modelData` already carries
every in-radius session. Only location id 214 (College Station REI,
615 University Dr. E #300) is kept; Austin Gateway / Houston rows
are a quiet drop.

`session.timeZone` is `America/Los_Angeles` on every captured CS
row. Times are the UTC start/end converted with `location.timezone`
(`America/Chicago`). Occurrence tid is start-time millis so
disappearance reconciliation still works.

**Retired 2026-09-04. The job is `enabled: false`; do not re-enable it
without a fetch path that actually returns `#modelData`.**

`www.rei.com` is behind Akamai bot management, and it blocks by client
fingerprint, not by IP — so rotating exits does nothing. Measured
2026-09-04 from this host:

| Client | Result |
|---|---|
| curl, default UA | 403 in 85 ms; body is Akamai `errors.edgesuite.net` |
| curl, Chrome UA, HTTP/2 | stream reset `INTERNAL_ERROR`, 69 ms |
| `requests` + `BROWSER_UA` | accepted, never answered, `ReadTimeout` at 30 s |

The last row is what the scraper does, which is why the failure arrives as
a timeout rather than a 403 — and why each dead run burned ~123 s in
retries and emailed. Direct and gluetun (`vpn:8888`, Swiss exit) behave
identically; `https://www.rei.com/robots.txt` is blocked too, so the
crawl policy cannot even be read. Every scheduled run failed after the
last success on 2026-08-16: 08-30, 09-01, 09-04.

Defeating this needs a TLS-impersonating client (`curl_cffi` and the
like). That was declined — an arms race for six events a season against
a site that blocks `robots.txt`. `rei` is not in `DEFAULT_KINDS`.

## Wonderful Words Bookshoppe

The `/event-list` HTML is a Wix events-viewer shell. `event_probe`
through gluetun returns 200 with no cards. Dates live on
`GET /_api/wix-events-web/v1/events` with `Authorization` set to the
Events app instance from `GET /_api/v2/dynamicmodel`
(`140603ad-af8d-84a5-2c80-a0f60cb47351`). Default order is future-first;
fetch walks pages of 50 until the last start is before the window.

Each Wix row is one occurrence. The series uid is the slug with
`-YYYY-MM-DD-HH-MM` stripped (`storytime`, `special-storytime`,
`wonderful-words-bookclub`, `first-friday-open-late`). CANCELED and
ENDED rows are dropped. Descriptions are empty on the list API.
Storytime / bookclub → `reading`; First Friday → `community`. Bookclub
is `adult`; everything else `all-ages`. `wonderfulwords` is not in
`DEFAULT_KINDS`. Pin it.

## Painting with a Twist

SSR calendar at `/studio/college-station/calendar/`. One page is the
whole horizon today (Aug–Sep). `time.event-datetime` is a 12-hour
clock wearing an ISO `T` (`T03:00` + "3:00 pm" = 15:00). Times come
from `.event-time`. Family Day / ALL AGES titles are `all-ages`; the
sip nights are `adult`. `pwat` is not in `DEFAULT_KINDS`. Pin it.

## Sew Much Class

Rain POS month grid
(`pageComponentId=5886934`). Drop titles matching `PHD`, `4-PACK`,
`Studio Closed`, or `Training`. Keep named classes and trips
(Houston quilt-festival bus trip is a `field_trip`). Times are the
bold start on `.calEvent`; end is omitted unless the card prints a
range. Gluetun exits 403 the calendar, so this kind runs
direct. `sewmuchclass` is not in `DEFAULT_KINDS`. Pin it.

## The Palace

Downtown Bryan concerts on a Theater Toolkit "movies" page.
`event_probe` returns the list; each `/movie/ThePalace/{slug}`
page has JSON-LD `screeningEvent` (`Friday, September 18, 2026 7:00 PM`)
and duration minutes. `/weekly-events` is standing copy: Open Mic
every Wednesday 7–10, First Friday 6pm, Maroon & White on the
third Friday 6pm, Gameday on Main Saturdays (college, last Saturday
of August through the second Saturday of January) and Sundays (NFL,
Sunday after Labor Day through the second Sunday of February),
doors 11am. `thepalace` is not in `DEFAULT_KINDS`. Pin it.

## Lowe's Kids Club

`/workshopdata?template=REGISTRATION&types=WORKSHOP&closed=false` is
the list. Keep `subType == KIDS`, drop paid (MrBeast) and
`NO_LOCATION` / Senior Builder rows. Registration is
`/events/register/{url}`.

`start`/`end` are Eastern wall-clock wearing a `Z`. The calendar date
is taken from America/New_York; the published window is 10:00–13:00
America/Chicago, which is what the registration page states. College
Station store 3032 only.

workshopdata 403s the VPN probe's default UA, so this kind runs
direct (`proxy_url: ""`) like Challenge. `lowes` is not in
`DEFAULT_KINDS`. Pin it.

## Home Depot Kids Workshop

US HTML is Akamai 403 through the host and through gluetun. Dates are
generated: first Saturday, 9:00–12:00 America/Chicago, ages 5–12,
College Station #6559. Kit names come from the Canada workshops page
when it answers (same national kits, different Saturday) and are
joined by month. Canada dates are discarded. A captured fallback
table covers Sep–Dec 2026 if Canada times out. A month with no name
publishes as `Kids Workshop`. Registration is the national
`/c/kids-workshop` page. Runs direct (`proxy_url: ""`).
`homedepot` is not in `DEFAULT_KINDS`. Pin it.

## Museum of the American G.I.

The public `/event/` page is a TEC Pro photo grid. Fetch
`/wp-json/tribe/events/v1/events`. Times are wall-clock Chicago
(`2026-10-25 13:00:00`). Multi-day listings (History in Motion,
Living History Weekend) keep the feed's start/end. All current
rows share venue 19124 Highway 6 South. Cost is blank — do not
send `is_free`. Topics: `history`, plus `crafts` / `community`
from the title. Living History School Day is `field_trip`.
`americangi` is not in `DEFAULT_KINDS`. Pin it; run direct.

## College Station Parks & Rec (RecTrac)

Vermont Systems WebTrac at `rectrac.cstx.gov`. Fetch
`/webtrac/web/search.html?module=AR&category=Events` (Detail view).
Session IDs and CSRF tokens are not part of the URL. Events
category only — leagues, aquatics and youth classes stay out.

Each activity code (`LCN-41`, `PKS-17`) is one series; each FMID
row is one occurrence. Times come from `ARItemBeginTime` seconds
when the cart link has them, otherwise the 12-hour display.
Overnight spans stay overnight. Sold-out / Unavailable rows still
publish. `$0.00/$0.00` is `is_free`. Venues are an editorial table
keyed on RecTrac's location cell (including `Larrry J Ringer Lib.`).
Unmapped locations reject loudly. `cstxparks` is not in
`DEFAULT_KINDS`. Pin it.

## City of College Station (Timely)

CivicPlus page `www.cstx.gov/calendar` embeds Timely
`events.timely.fun/yl7pe679`. Fetch
`timelyapp.time.ly/api/calendars/54735353/events` with the public
widget `X-Api-Key` and `filter_groups_659989886=677656162` (Type=
Public, already on the city's default embed). Clean List is a view.
One detail GET per event id fills venue street/city/ZIP; list rows
do not carry them.

Event id is the series; `instance` (local `YYYYMMDDHHMMSS`) becomes
Chicago start millis so recurring nights (Christmas lights) stay
distinct. Meetings stay (same as `bryantx`). Category Notice is
city-office closures with no venue and is dropped. Cancelled rows
are dropped. `Free` without `Paid` is `is_free`. Cross-source
overlap with RecTrac (`cstxparks`) is the site's job. `cstxcal` is
not in `DEFAULT_KINDS`. Pin it.

## TAMU Youth Camps (Localist)

Aggie Camp Finder at `youthcamps.tamu.edu`. Fetch
`/api/2/events?start=&days=&pp=100`. One list row is one instance;
Garden Club repeats the event wrapper. Occurrence tid is start-time
millis so disappearance reconciliation still works.

**Bryan–College Station only.** Galveston and Glen Rose (Fossil Rim)
are a quiet drop. `Texas A&M University - College Station`, Kyle
Field, and the Jack K. Williams Administration Building are pinned
College Station venues. An unmapped location rejects loudly.

`event_cost` `No Cost` is `is_free` — the boolean `free` field is
false on free listings. Day Camp / title `camp` → `camp`; STEM →
`science`; concerts → `music`. Concerts and 150th events also appear
on `tamu`; downstream duplicate detection owns the overlap.
`youthcamps` is not in `DEFAULT_KINDS`. Pin it.

## Brazos Valley Chess Association (Google Calendar)

`brazosvalleychess.com/events` is a Squarespace shell around an embedded
Google Calendar ("BVCA Monthly Event Calendar"). The calendar's public
ICS (`calendar.google.com/calendar/ical/<id>/public/basic.ics`) is the
source: one VEVENT per monthly Swiss tournament, no RRULEs, UTC `Z`
stamps converted to Chicago wall clock. Series uid is the ICS `UID`,
occurrence tid the UTC start stamp. `STATUS:CANCELLED` → `cancelled`.

**One VEVENT is one series with one occurrence.** A VEVENT that grows an
RRULE is rejected loudly rather than expanded. **`VENUES` is an editorial
gate** keyed on the ICS `LOCATION`: only First Baptist Church Bryan is
listed; anything else rejects and emails.

The HTML description is flattened to text. The `Registration Form` link
is `registration_url` (`registration_required: true`); the
`$25 CASH entry fee.` sentence is `cost_note` verbatim, `cost_low_cents`
2500, `per_person`. Topic `sports`; no audiences or ages (the source
states none — "must be a current USCF member" is in the description).
The description's own "Saturday, <month> <day>, 2025" line is stale
copy-paste at the source, not ours. Google is fetched direct.
`bvchess` is not in `DEFAULT_KINDS`. Pin it.

First inject 2026-09-19: **3 upserted / 0 rejected** (Oct, Nov, Dec Swiss;
that day's September tournament fell before the window), occurrences
4156–4158 on the site. 2027 is not on the calendar yet.

## Where reality differed from the design

Verified against the captured window — see `tests/fixtures/event_watch/README.md`.

- **Six venues, not four.** The extras are an HEB (an outreach event) and the Meyer
  community center. Both are in `VENUES` with an explicit area.
- **One occurrence has no place at all.** The contract requires `series.place.name`,
  so it is rejected loudly rather than published with a guessed area. It is the
  single entry in this run's `rejected` list and it surfaces by email.
- `eid.tid` is an int in the feed; the contract wants a string.
- `status` is an object (`{"name": "scheduled"}`), not a bare string.

For `challenge`, against a captured week and a live five-week dry run:

- **The map action carries no venue key**, so coordinates join back to a card on
  street + city. The postcode is only inside a Google Maps URL in the pin's popup.
- **Cancellation is per date, not per series** — Duddley's Draw is cancelled one
  Wednesday and runs the next.
- **Two Rx Pizzas share a venue name** and must not share a place slug; the
  source's own permalink slug already distinguishes them.
- Nothing in the closed 13-topic vocabulary means "quiz night", so every game gets
  `community` and the music games also get `music`.

## Running it

```bash
# Normalize and log payloads, publish nothing. Do this first.
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true --no-email

# One source only (and un-proxied, which challenge and cityspark currently need)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=challenge proxy_url= --no-email

docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=cityspark proxy_url= --no-email

# KBTX (Tockify; uses the same proxy as the library job)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=kbtx --no-email

# KBTX Marketplace coupons
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=kbtxdeals --no-email

# TAMU LiveWhale
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=tamu --no-email

# TAMU Music Activities
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=tamumusic --no-email

# Destination Bryan
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=destbryan --no-email

# Visit College Station (Algolia)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=visitcstx --no-email

# Bush 41 Library
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=bush41 --no-email

# Brazos Valley Symphony Orchestra (direct; the site 403s gluetun)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=bvso proxy_url= --no-email

# Hyperbole Bookstore (Bookmanager)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=hyperbole --no-email

# City of Bryan (HTML list; un-proxied)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=bryantx proxy_url= --no-email

# Lake Walk (TEC REST; un-proxied)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=lakewalk proxy_url= --no-email

# Brazos Valley Museum (homepage repeater; un-proxied)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=bvmuseum proxy_url= --no-email

# BCS Chamber (GrowthZone XML; un-proxied)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=bcschamber proxy_url= --no-email

# Theater Company (Squarespace JSON; un-proxied)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=ttc proxy_url= --no-email

# Stage 12 (Drupal month calendar; un-proxied)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=stage12 proxy_url= --no-email

# REI College Station (RETIRED 2026-09-04 — Akamai tarpits every client; expect a timeout)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=rei proxy_url= --no-email

# Wonderful Words Bookshoppe (Wix Events)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=wonderfulwords --no-email

# Painting with a Twist College Station
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=pwat --no-email

# Sew Much Class (Rain POS month grid; un-proxied — VPN 403s)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=sewmuchclass proxy_url= --no-email

# The Palace live shows
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=thepalace --no-email

# Lowe's Kids Club (workshopdata JSON; un-proxied — VPN probe 403s)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=lowes proxy_url= --no-email

# Home Depot Kids Workshop (Canada kit names + generated first Saturdays; VPN)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=homedepot --no-email

# Museum of the American G.I. (TEC REST; un-proxied)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=americangi proxy_url= --no-email

# College Station Parks RecTrac (Events category)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=cstxparks --no-email

# City of College Station Timely calendar (Type=Public)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=cstxcal --no-email

# TAMU Youth Camps (Localist)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=youthcamps --no-email

# Brazos Valley Chess Association (Google Calendar ICS)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=bvchess proxy_url= --no-email

# Unit tests (hermetic — conformance skips)
make test

# Contract conformance against the site's REAL validator
docker compose run --rm \
  -v /srv/docker/websites/discoverbcs/app:/discoverbcs-app:ro \
  cortex python -m pytest tests/test_event_watch.py -q
```

## Failure ladder

| Failure | Behaviour |
|---|---|
| VPN unhealthy | Bail. Trace `vpn_health_fail`. No state written |
| Fetch fails | Abort that source. Publish nothing, cancel nothing |
| LLM unavailable | Publish without topics |
| >25% of the window missing (source had ≥10 in the window) | Cancel nothing, email, keep previous state |
| Unknown venue | Reject that event, email, publish the rest |
| `challenge` finds no shows on **any** day | Treated as a fetch failure, not an empty calendar |
| Bus publish fails | eventbus-kit retries, then dead-letters |

That second-to-last row matters because `challenge` has no envelope to check: an
HTML fragment saying "0 shows found" is a valid response. Five straight weeks of
genuine silence is not something this source does, so it reads as a renamed
parameter or a moved endpoint — and aborting is what stops the disappearance guard
from being handed an empty set.

A run that bails writes no state, so nothing is ever falsely cancelled after an outage.

**The guard has a floor** (`disappearance_guard_min`, default 10). Below it the
ratio means nothing: `brazos-valley-museum` shows two homepage cards, both moved
date on 2026-09-23, and "2 of 2 vanished" blocked the source from Sep 15 on.
Under the floor a changed date is just a cancel plus an upsert.

**A scraper that re-keys trips the guard on purpose.** `22c9b55` (2026-09-18)
folded TAMU's per-day ids into `program:<venue>:<title>` series; 480 of the 502
"missing" were the same start times under new keys, and every run was blocked
until one supervised run with `disappearance_guard: 1.0` cancelled the old keys
(2026-09-23). Plan that run with any change to `source_series_uid` or
`source_occurrence_tid`.
