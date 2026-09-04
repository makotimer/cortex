# BCS Chamber — resolve a place when the feed omits the city

Date: 2026-09-04
Status: approved for implementation
Repo: `/srv/docker/cortex` (`modules/event_watch`)

`bcschamber` rejects ~18% of the chamber's catalog with `missing city` and emails
about it every run. Most of those are recoverable from data the feed already
carries. This closes the recoverable share and leaves the rest rejecting, loudly,
on purpose.

---

## 1. What is happening

`parse_place()` reads `LocationDesc`, then the description, and raises
`ScraperError("missing city")` when neither yields a Brazos-county city. That is
deliberate — README §BCS Chamber: *"No city is a loud reject, not a guess."*
The events never reach `events:discoverbcs`; they are absent from
`bcs-chamber.sent.json`, so there is no half-published or cancel churn either.

Measured on the live catalog 2026-09-04 (34 `EventDisplay`, HTTP 200):

```
STATUS COUNTS: {'ok': 28, 'missing city': 6}
```

Steady, not a regression: 4 rejects on 2026-08-15, then 6 on 08-30, 09-01 and
09-04 — the same six ids each time.

| EventID | Event | Start | `LocationDesc` (text) | Why it fails |
|---|---|---|---|---|
| 3849 | Lunch & Learn "Employee Benefits 101" | 09-14 | `Brazos Valley Council of Governments` / `3991 East 29th Street` / `Brazos Room A&B` | venue + street, no city, no ZIP |
| 3855 | Lobsterfest & Golf Classic 2026 | 09-18 | *(empty — `<p><br/>&nbsp;</p>`)* | no location at source |
| 3862 | Youth Career Fair 2026 | 10-07 | `Brazos County Expo` | bare venue name; description empty |
| 3889 | Business After Hours — St. Joseph Health | 11-19 | *(empty)* | description is the string `St. Joseph Health` |
| 3896 | Ag Breakfast 2026 | 11-24 | `Brazos County Expo` | bare venue name |
| 3898 | Leadership Brazos ALL CLASS REUNION | 10-09 | `Phillips Event Center` / `1929 Country Club Drive` | venue + street, no city |

## 2. Why the fix is bounded

The 28 that pass are two unrelated populations:

- **21 ribbon cuttings.** `LocationDesc` is a street plus a `City, TX` line, so
  they resolve on the existing `_CITY_LINE` path. Every one is a brand-new member
  business (Mojo Tortilla, Kerr Surveying, Haraz Coffee House …). This is the
  source's unbounded tail, it already works, and **nothing in this design touches
  it.**
- **7 named venues** that happen to carry a city somewhere in the block — Brazos
  Center, Benjamin Knox Gallery, Coopers, Hilton Garden Inn, The Kimbell, Frost
  Bank Tower, First Financial.

Every failure is in the second population: a standing civic/event venue typed by
name with no city. That set is small and repeats — **`Brazos County Expo` appears
twice in a single 34-event snapshot** (Youth Career Fair, Ag Breakfast). A hand-
maintained map is viable here precisely because it never has to keep up with the
ribbon-cutting firehose.

Two of the six do not need a map at all: 3849 and 3898 carry a real street line
(`3991 East 29th Street`, `1929 Country Club Drive`) and only lack the city. That
is what `kbtx.resolve_address` already solves for four other scrapers, and it
generalizes to streets nobody has pinned.

## 3. Design

A resolution ladder in `parse_place()`. Steps 1–2 are today's behaviour and run
first, so **every payload that is accepted today must be byte-identical after this
change.**

1. `_place_from_text(location, allow_bare_name=True)` — unchanged.
2. `_place_from_text(description, allow_bare_name=False)` — unchanged.
3. **New — venue map.** Match the location text (then the description text)
   against `CHAMBER_VENUES`. A hit supplies name, street, city, region, area and
   postcode outright.
4. **New — address resolution.** If the location still has a street-ish line and
   no city, use the resolution the scraper stashed during `fetch`.
5. Reject `missing city`.

### 3.1 Purity boundary

`BaseEventScraper.normalize` is **pure** — no network, no clock, no state. The
resolver is I/O, so it runs in `fetch`, exactly as `kbtx` does it: load the cache
with `state.load_addresses`, resolve, stash the result on
`RawEvent.supplement["place_resolution"]`, save with `state.save_addresses`.
`normalize` only reads the supplement. `BcsChamberScraper` therefore gains a
`state_dir` argument, and `engine._build_scrapers` must add it to the set of
classes constructed with `state_dir=`.

Do **not** write a second resolver. `kbtx.resolve_address` is already the shared
one — `visitcstx`, `destbryan`, `tamu` and `tamumusic` all import it. Its
`AddressConfig` sets `city_retries=("College Station, TX", "Bryan, TX")`, which
is the exact shape of a chamber street with no city.

### 3.2 The venue map

`CHAMBER_VENUES`, in the style of `bvso.VENUES` + `_VENUE_PATTERNS`: an explicit
dict keyed by slug, plus a tuple of compiled patterns matched against the text.
An unmatched bare name is not an error on its own — it falls through to step 4
and then to the existing reject.

Seed entries. Two are confirmed by the feed itself; the rest must be confirmed
against the chamber listing before they are committed — **a pinned wrong city is
worse than a reject**, because it sends families to the wrong town with no signal:

| Venue | Street | City | Source of the address |
|---|---|---|---|
| Brazos Valley Council of Governments | 3991 East 29th Street | Bryan | in the feed's own `LocationDesc` (3849) |
| Phillips Event Center | 1929 Country Club Drive | Bryan | in the feed's own `LocationDesc` (3898) |
| Brazos County Expo | — | Bryan | **confirm before committing** |
| Brazos Center | — | Bryan | **confirm**; passes today, pinned for stability |

Steps 3 and 4 overlap for 3849/3898 by design — the map wins because it is
deterministic and costs no API call.

## 4. What stays broken, correctly

3855 and 3889 have no venue at all — 3889's entire description is
`St. Joseph Health`. Nothing downstream can resolve an empty location, and these
stay rejects.

Business After Hours will keep leaking. It rotates monthly through member
businesses (3 in the current feed: Frost Bank ✓, Hilton Garden Inn ✓, St. Joseph
Health ✗), so its venues are as novel as ribbon cuttings, but the chamber often
posts them as a bare business name with no address. That is a source data gap,
not an injector bug.

Expected residual after this work: **2 of 34 (~6%)**, down from 6 of 34 (~18%).

## 5. Out of scope

- Changing any of the 28 payloads accepted today, including enriching a mapped
  venue that already resolves from text.
- Cross-source dedupe, topic vocabulary, or the intake contract. Unchanged.
- Extracting `resolve_address` into a shared module. Four scrapers already import
  it from `kbtx`; a fifth is not the trigger to move it.
- Guessing a city from a sponsor or organization name in the description
  (`allow_bare_name=False` on descriptions stays). It would be right on 3898
  ("Sponsored by the City of Bryan") and wrong on 3855 ("Bryan/College Station
  Chamber of Commerce"). Coincidence is not a rule.
