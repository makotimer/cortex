---
repo: /srv/docker/cortex
spec: docs/superpowers/specs/2026-10-06-autoheal-fleet-owner-design.md
plan: docs/superpowers/plans/2026-10-06-autoheal-fleet-owner.md
verify: ./run_tests.sh
service: autoheal
---

# cortex: Autoheal is the fleet's one autoheal and needs no network

`cortex-autoheal-1` sits alone on `cortex_default`, burning one of Docker's exhausted default address pools, though it only talks to the Docker socket. It also restarts every `autoheal=true` container on the host (13 on 2026-10-06, mostly `*-llm-proxy` sidecars), while the docs say it only covers the bridge and vpn. Give it `network_mode: none`, pin that with a test, and document it as the fleet's single autoheal.

## Start here

Read the spec (`docs/superpowers/specs/2026-10-06-autoheal-fleet-owner-design.md`) only. Then work the plan's tasks **in order, one at a time, in this one session**: read Task 1, do it, commit; then immediately read Task 2 and continue. Read a task only when you reach it, and read no other ticket's plan. Commit after each task, but **do not end your turn between tasks**; the ticket is finished only when every box in the checklist file is `[x]` and that file is committed.

## Constraints

- Change only the `autoheal` service in `docker-compose.yaml`. Do not touch `vpn`, `cortex`, `cortex_bridge`, their labels, or `mailnet`.
- Do not edit `/srv/docker/websites/lwrpc`; its autoheal is removed separately.
- Commit per task with plain messages. Do not push, do not `docker compose`, do not `docker network rm`, do not raise `timeout:`.

## Done when

- [ ] `docker-compose.yaml` gives `autoheal` `network_mode: none` and a fleet-owner comment, with a passing test pinning it
- [ ] `docs/architecture.md`, `docs/operations.md` and `docs/decisions.md` say autoheal acts on every `autoheal=true` container on the host and has no network
- [ ] `./run_tests.sh` passes
