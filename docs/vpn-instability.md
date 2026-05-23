# VPN Instability — Ongoing Issue

## Summary

The gluetun VPN sidecar (`cortex-vpn-1`) intermittently fails its health check, causing `career_watch` to bail out on every run during the outage window. This is a recurring problem — 11 of the 13 days between 2026-05-11 and 2026-05-23 had at least one failure, with several days showing a complete blackout (all 14 scheduled runs failed).

## How the failure surfaces

`career_watch` calls `GET http://vpn:8000/v1/publicip/ip` (gluetun's control API) before any scraping. If the request fails or returns no IP, it logs a `vpn_health_fail` trace record and exits immediately (~2ms run). The nightly report flags these as bail-out anomalies.

The JSONL trace record looks like:
```json
{"component": "career_watch.engine", "op": "vpn_health_fail", "person": "Ben Price", "control_url": "http://vpn:8000"}
```

## Historical failure counts (vpn_health_fail per day)

| Date | Failures | Notes |
|------|----------|-------|
| 2026-05-11 |  9 | First observed |
| 2026-05-12 | 14 | Full blackout |
| 2026-05-13 | 14 | Full blackout |
| 2026-05-14 | 14 | Full blackout |
| 2026-05-15 | 11 | |
| 2026-05-16 |  6 | |
| 2026-05-17 |  0 | Recovered |
| 2026-05-18 | 14 | Full blackout — regressed |
| 2026-05-19 |  2 | |
| 2026-05-20 |  0 | Recovered |
| 2026-05-21 |  2 | |
| 2026-05-22 | 14 | Full blackout — regressed |
| 2026-05-23 |  5 | Partial day at time of writing |

14 is the maximum possible (career_watch runs Mon–Sat, every 90 min, 05:00–18:30).

## Known cause (from CLAUDE.md)

> ProtonVPN rotates WireGuard peer public keys periodically. If gluetun's server list is old, the tunnel will appear up (tun0 gets an IP) but pass no traffic. Symptom: gluetun logs show continuous i/o timeout healthcheck failures cycling through many servers.

`SERVER_UPDATE_PERIOD=24h` is set in `docker-compose.yaml` — this is the documented mitigation — but failures are still recurring at this frequency, so either the 24h period is insufficient for ProtonVPN's current rotation cadence, or there is a secondary cause.

## Immediate workaround

```bash
cd /srv/docker/cortex
docker compose up -d vpn
```

This restarts gluetun, forces a server list refresh, and re-establishes the WireGuard tunnel. The vpn container typically recovers within 30–60 seconds.

## What to investigate next

1. **Gluetun logs during a failure window** — run `docker compose logs vpn` after a failure day to confirm whether the symptom matches the known pattern (i/o timeout cycling through servers) or something else is happening.

2. **Update period** — consider reducing `SERVER_UPDATE_PERIOD` to `6h` or `12h` to stay ahead of ProtonVPN's key rotation. Or pin to a specific set of servers that rotate less frequently.

3. **Auto-recovery** — gluetun has a built-in health check; the cortex `docker-compose.yaml` could add a `restart: unless-stopped` policy on the `vpn` service (if not already set) and an `autoheal`-style dependency so cortex restarts automatically if vpn recovers.

4. **Alerting** — the nightly report now catches this, but failures can accumulate silently for 24 hours. Consider adding a check that pages (email or otherwise) if VPN failures exceed a threshold during the day rather than waiting for the overnight report.

## Status

- **2026-05-23**: Active. 5 failures so far today. `cortex-vpn-1` currently shows as `(healthy)` in `docker ps`.
- `SERVER_UPDATE_PERIOD=24h` in place but not sufficient to prevent recurrence.
- No auto-recovery mechanism in place.
