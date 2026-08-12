# event_watch

Scrapes public event calendars and publishes them onto `events:<site>` as
`cortex.discoverbcs-ingest`. First and only source today is the Bryan + College
Station Public Library System's Tockify calendar.

Design: `/srv/docker/websites/discoverbcs/docs/superpowers/specs/2026-08-12-bcs-library-event-injector-design.md`
Contract: `/srv/docker/websites/discoverbcs/docs/intake-contract.md`

## Status

**Not scheduled.** The module is built and tested but is not in `local/config.json`,
because two things must land first:

1. **The site must learn `ingest.report`.** `discoverbcs/worker.py` dead-letters
   unknown message types permanently, so emitting the run summary against the
   current site would poison `events:discoverbcs.dead` on every run.
2. **A `dry_run` against the live feed should be read by a human**, per design §10.

Window length and schedule are deliberately unscoped by the design (§3, §11.3).
`window_days` defaults to 30.

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

## Where reality differed from the design

Verified against the captured window — see `tests/fixtures/event_watch/README.md`.

- **Six venues, not four.** The extras are an HEB (an outreach event) and the Meyer
  community center. Both are in `VENUES` with an explicit area.
- **One occurrence has no place at all.** The contract requires `series.place.name`,
  so it is rejected loudly rather than published with a guessed area. It is the
  single entry in this run's `rejected` list and it surfaces by email.
- `eid.tid` is an int in the feed; the contract wants a string.
- `status` is an object (`{"name": "scheduled"}`), not a bare string.

## Running it

```bash
# Normalize and log payloads, publish nothing. Do this first.
docker compose run --rm cortex python -m service.cli run modules.event_watch \
  --kwargs dry_run=true --no-email

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
| Bus publish fails | eventbus-kit retries, then dead-letters |

A run that bails writes no state, so nothing is ever falsely cancelled after an outage.
