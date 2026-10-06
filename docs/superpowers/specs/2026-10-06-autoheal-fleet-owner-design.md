# cortex: autoheal is the fleet's one autoheal, with no network

Date: 2026-10-06. Found during the Docker network audit (Docker ran out of
default address pools on 2026-10-05).

## Problem

1. `cortex-autoheal-1` sits alone on the implicit `cortex_default` network. It only
   talks to `/var/run/docker.sock`; it has no webhook, so it needs no network. That
   network burns one of Docker's ~31 default address pools for nothing.
2. `willfarrell/autoheal` with `AUTOHEAL_CONTAINER_LABEL=autoheal` acts on **every
   container on the host** carrying `autoheal=true`, not just this stack's. On
   2026-10-06 that was 13 containers: `cortex_bridge`, `vpn`, and every
   `*-llm-proxy` sidecar (lwrpc, discernandgrow, chimneyhillhoa, build, post,
   brazosclassical, discoverbcs, mandy, spamkit, ops, article). The docs say it
   covers only the bridge and vpn.
3. `lwrpc-autoheal-1` (in `/srv/docker/websites/lwrpc`) watches the same label at
   image defaults (5 s interval, 0 s start period), so a red container can be
   restarted twice. Removing that one is a separate change in the lwrpc repo; this
   ticket makes cortex's the documented fleet owner.

## Design

- `autoheal` service gets `network_mode: none` and no `networks:` key. Nothing else
  in the stack declares the implicit `default` network, so `cortex_default` stops
  being used. Keep its env (30 s interval, 30 s start period) and socket mount.
- Comment on the service: this is the **fleet's** autoheal; it restarts any
  container on the host labelled `autoheal=true`; do not add a second one elsewhere.
- Docs: `docs/architecture.md` service row and the "Only `cortex_bridge` and `vpn`"
  paragraph say it is fleet-wide (the label, not the stack, decides); the network
  table drops the `default` row and notes `network_mode: none`. `docs/decisions.md`
  gets a dated entry. `docs/operations.md:44` sentence corrected the same way.
- A test pins the compose shape (see plan).

## Out of scope

Deploying (`docker compose up -d --force-recreate autoheal`, then
`docker network rm cortex_default`) is done by a human. The lwrpc autoheal removal.
