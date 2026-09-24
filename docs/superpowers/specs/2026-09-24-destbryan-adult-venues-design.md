# Destination Bryan: band bar and brewery listings adult

**Date:** 2026-09-24 · **Repo:** `/srv/docker/cortex` · **Scraper:** `modules/event_watch/lib/scrapers/destbryan.py`

## 1. Why

On 2026-09-24 discoverbcs.org moved Destination Bryan back onto its family
calendar (it had been hidden wholesale). Its ~55 family listings (farmers
markets, pumpkin patches, concerts, crafts) now show by default. So do ~20
bar and brewery listings, and they arrive with **no audience**, so a parent
filtering by age sees "Ages not listed" on karaoke at a pub.

The site's policy for mixed-audience sources (decided 2026-08-12 for
Challenge Entertainment, see `challenge.py` `VENUES`) is **publish and band
`["adult"]`, never drop**. `destbryan` already bands `nightlife`-categorised
cards adult (`audiences_from_categories`), but these cards are not
categorised `nightlife`.

## 2. Already on the branch

| Name | Where | What it does today |
|---|---|---|
| `audiences_from_categories(labels)` | `destbryan.py` | `nightlife` → `adult`, `family-friendly` → `all-ages`, else `[]` |
| `DestBryanScraper._series(item)` | `destbryan.py` | builds `series`; `"audiences": audiences_from_categories(cats)` |
| `_place_for(item)` | `destbryan.py` | `place["name"]` = JSON-LD `location.name`, else card address, else city |
| `VENUES` | `challenge.py` | the precedent: per-venue audience band. Not imported here. |

## 3. Design

- `ADULT_VENUES`: a hand-kept set of **normalized place names** (casefold,
  collapse whitespace, strip a leading "the "). A card whose `place["name"]`
  normalizes into it gets `audiences == ["adult"]`, replacing whatever the
  categories gave (a `family-friendly` tag at a pub does not make it one).
- `ADULT_TITLES`: a short regex of title words that mean a bar night wherever
  it is held: `karaoke`, `college night`, `happy hour`, `pub crawl`,
  `bar crawl`, `tequila`. Same effect.
- **Unknown venues are not rejected.** Unlike Challenge Entertainment, most
  of this feed is family programming at shops, parks and churches; rejecting
  every unlisted venue would reject most of the feed. The table only adds
  a band. Say so in the module docstring, citing the 2026-08-12 policy.
- Initial `ADULT_VENUES`, as the names appear in the feed on 2026-09-24:
  `Murphy's Law Pub`, `Angry Elephant`, `Cadillac Ranch Bar & Grill`,
  `Yesterdays Bar and Grill`, `Tilted Pint Pub & Grill`, `The Owl`,
  `KinderHill Brew Lab`, `Blackwater Draw Brewing Co.`
- Deliberately **not** listed (family venues that host an adult night now
  and then; the title list catches those nights): Zoomz Entertainment,
  Grand Stafford Theater, La Bougainvillea Kitchen & Lounge, Ronin Farm.

## 4. Out of scope

- discoverbcs is not touched; the intake contract is unchanged (`adult` is
  already in the audience vocabulary).
- `visitcstx`, `kbtx`, `mycentx`: not in this ticket.
- Rows already in discoverbcs update when the feed re-sends them; no backfill.
