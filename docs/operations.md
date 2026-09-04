# Operations

The Cortex stack is managed via Docker Compose. Common commands:

- **Start**
  ```bash
  cd /srv/docker/cortex
  make up
  ```
- **Stop**
  ```bash
  cd /srv/docker/cortex
  make down
  ```
- **Logs**
  ```bash
  cd /srv/docker/cortex
  make logs-f
  ```

The ProtonMail Bridge sidecar must be logged in before Cortex can send or receive emails.

## Bridge health: why the check does a real IMAP LOGIN

`cortex_bridge`'s healthcheck runs `scripts/bridge-healthcheck.sh`, which opens
an IMAP `LOGIN` against `127.0.0.1:1143` with the Bridge-local credentials
(`BRIDGE_USERNAME` / `BRIDGE_PASSWORD`, injected as `HEALTH_IMAP_USER` /
`HEALTH_IMAP_PASS`) and requires an `OK` response.

It used to be a bare TCP connect (`socat - TCP:127.0.0.1:1143`). That is not a
health signal: **Bridge keeps the IMAP listener open even when it has loaded
zero users.**

### Incident 2026-08-26 → 08-28 (42h silent outage)

1. Proton's API went down (`503` on `auth/v4/refresh`, `feature/v2/frontend`).
2. `cortex_bridge` restarted at 21:56 UTC, mid-outage — autoheal, on a red
   healthcheck (`RestartCount` stayed 0, so it was not the restart policy).
3. Startup token refresh got four `503`s and Bridge logged
   `Failed to load connected user` — **then never retried**. It ran on with an
   empty user list.
4. Every send after that: SMTP `454 4.7.0 invalid username or password`,
   IMAP `no such user`.
5. The TCP healthcheck stayed green the whole time, so autoheal never restarted
   it again. All outbound mail died silently for 42 hours — cortex scheduler
   jobs were lost outright, and eventbus `email.send` messages dead-lettered.

The vault was never damaged: `docker compose restart cortex_bridge` once Proton
was reachable reloaded the account with no re-login needed.

### Tradeoff

`retries: 4` at `interval: 15s` means ~60s of sustained login failure before
autoheal restarts the bridge, and `start_period: 90s` covers the boot-time user
load and sync. During a genuinely long Proton outage this will restart the
bridge roughly every 150s until the API recovers. That is intentional — a
restart is exactly what recovers this failure — but it is why the failure window
is 60s rather than a few seconds.

### Checking it by hand

```bash
# What the healthcheck sees, plus the last few probe results
docker inspect cortex-cortex_bridge-1 --format '{{json .State.Health}}' | python3 -m json.tool

# Prove SMTP auth works without sending anything
cd /srv/docker/cortex && docker compose exec -T cortex python -c "
import os, smtplib
s = smtplib.SMTP(os.getenv('BRIDGE_HOST'), int(os.getenv('BRIDGE_SMTP_PORT')), timeout=15)
s.ehlo(); s.login(os.getenv('BRIDGE_USERNAME'), os.getenv('BRIDGE_PASSWORD'))
print('SMTP AUTH OK'); s.quit()"
```

### Replaying dead-lettered mail

`email.send.dead` entries keep the full envelope in their `json` field, so a
replay is an `XADD` of that same field back onto `email.send`. Originals stay in
the dead stream as an audit trail — dedupe before replaying (a Copy-button click
plus a Publish legitimately produces identical copies). The historical
`{"ping": 1}` entry is expected and is not a failed delivery.
