# event_watch

Scrapes public event calendars and publishes them onto `events:<site>` as
`cortex.discoverbcs-ingest`.

| Kind | Source | Window | Notes |
|---|---|---|---|
| `tockify` | Bryan + College Station Public Library System | run default (270d) | JSON + ICS feeds |
| `challenge` | Challenge Entertainment — pub trivia, Singo, bingo | **35d** (own cap) | AJAX API, one request per day |
| `kbtx` | KBTX Community Calendar (Tockify `kbtx.calendar`) | run default (270d) | JSON + ICS; BCS only; address-kit fallback |
| `tamu` | Texas A&M LiveWhale calendar | run default (270d) | JSON; public-interest filter; BCS only |
| `cityspark` | FOX 44 / MyCenTX (CitySpark widget API) | run default (270d) | Bryan 15mi; direct, not fox44news.com |
| `bryantx` | City of Bryan GOVstack calendar | run default (270d) | HTML `_List` pages; direct |

Design: `/srv/docker/websites/discoverbcs/docs/superpowers/specs/2026-08-12-bcs-library-event-injector-design.md`
Contract: `/srv/docker/websites/discoverbcs/docs/intake-contract.md`

## Status

**Live.** Both sources are scheduled in `local/config.json` (gitignored — the
entries below are the record of what is there) and both have injected for real.

| Job | Kind | When | kwargs |
|---|---|---|---|
| `event-watch-bcslibrary` | `tockify` | Wed/Sun 03:40 | `rotate_vpn_per_run: false` |
| `event-watch-challenge` | `challenge` | Wed/Sun 03:55 | `proxy_url: ""` |
| `event-watch-kbtx` | `kbtx` | Wed/Sun 04:10 | `rotate_vpn_per_run: false` |
| `event-watch-tamu` | `tamu` | Wed/Sun 04:25 | `rotate_vpn_per_run: false` |
| `event-watch-cityspark` | `cityspark` | *not scheduled yet* | `proxy_url: ""` |
| `event-watch-bryantx` | `bryantx` | *not scheduled yet* | `proxy_url: ""` |

First real injection of `challenge`: 2026-08-12, window `2026-08-13 → 2026-09-17`,
**48 upserted / 0 cancelled / 0 rejected**, 12 series, no unmapped venue.

### Why two jobs and not one

They need different network paths, and one job carries one `proxy_url`.
`challengeentertainment.com` refuses every gluetun exit tried so far, so that kind
runs direct; `tockify` keeps its proxy. Folding them together would mean either
sending the library feed out un-proxied or letting the Challenge fetch fail on
every run.

That is also why **every job pins `kinds` explicitly**. The default is
`["tockify", "challenge"]` — `kbtx`, `cityspark`, `tamu` and `bryantx` are
opt-in so a bare run does not silently add them. A job that omits `kinds` would pick up Challenge
through whatever proxy that job has, and the library job would then email a
fetch failure twice a week. `cityspark` talks to portal.cityspark.com, not
fox44news.com; it goes direct like Challenge.

The 15-minute offset is only politeness: the two runs share no state and no
source, so overlapping would be harmless.

Window length and schedule are deliberately unscoped by the design (§3, §11.3).
`window_days` defaults to 270; a scraper may narrow it (see below).

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

**Venues are not a maintained table.** Most records already have `c_locality`
and a Google `place_id`; those get `area` from the city. When the city is
missing, fetch calls `address-kit` (Census → Geoapify → Google, BCS ZIPs only)
and caches the result on `place_id` or the cleaned address, so a venue is
looked up at most once. `out_of_area` is a quiet drop; `no_match` is an
attention email. `normalize` never calls the kit.

`kbtx` is not in `DEFAULT_KINDS`. Pin it.

## CitySpark / MyCenTX

FOX 44 embeds CitySpark. The page on fox44news.com is PerimeterX; the feed is
`POST https://portal.cityspark.com/api/events/GetEvents/MyCenTX` with no auth.
The filter is Bryan + 15 miles — the same one as the station's public URL.

`DateStart` is wall-clock Central wearing a `Z`. Believing the suffix would
shift every timed listing five or six hours. `Free` is false on the whole
captured window, so `is_free` is omitted rather than sent as a lie.

`cityspark` is not in `DEFAULT_KINDS`. Pin it, and run it un-proxied.

## TAMU LiveWhale

Public `/live/json/events`. The listing HTML is just a LiveWhale shell.

**Public-interest filter, not an audience gate.** Drop title matches
(`training`, `orientation`, `office hours`, `retreat`, `new student`), groups
Howdy Week / CTE / Faculty Affairs, and Students-only unless Visitors,
Residents, or Youth (K-12) is also tagged. Untagged talks stay.

**Bryan–College Station only.** The campus filter leaks. Virtual/Zoom without a
BCS campus tag is dropped. Named campus buildings become `college_station`
without a geocoder; street addresses that need a city go through address-kit.

The JSON endpoint silently caps around 400, so fetch walks the window a week at
a time. Occurrence tid is start-time millis so disappearance reconciliation
still works. `tamu` is not in `DEFAULT_KINDS`. Pin it.

## City of Bryan

GOVstack / CivicPlus HTML. `GET /default/_List?StartDate=&EndDate=&Page=`
(0-based) until a page is empty. Times come from the detail path
(`2026-09-04-1700`), not the `5:00 PM` string. `0000` is all-day.

First Friday is one series with one occurrence per published month. Public
meetings stay in. `bryantx` is not in `DEFAULT_KINDS`. Pin it and run direct.

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

# TAMU LiveWhale
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=tamu --no-email

# City of Bryan (HTML list; un-proxied)
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true kinds=bryantx proxy_url= --no-email

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
| >25% of the window missing | Cancel nothing, email, keep previous state |
| Unknown venue | Reject that event, email, publish the rest |
| `challenge` finds no shows on **any** day | Treated as a fetch failure, not an empty calendar |
| Bus publish fails | eventbus-kit retries, then dead-letters |

That second-to-last row matters because `challenge` has no envelope to check: an
HTML fragment saying "0 shows found" is a valid response. Five straight weeks of
genuine silence is not something this source does, so it reads as a renamed
parameter or a moved endpoint — and aborting is what stops the disappearance guard
from being handed an empty set.

A run that bails writes no state, so nothing is ever falsely cancelled after an outage.
