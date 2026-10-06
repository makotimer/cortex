# FLEET-131

Tick every box and commit this file. The next queued ticket will not start
until every box is `[x]`.

- [ ] `docker-compose.yaml` gives `autoheal` `network_mode: none` and a fleet-owner comment, with a passing test pinning it
- [ ] `docs/architecture.md`, `docs/operations.md` and `docs/decisions.md` say autoheal acts on every `autoheal=true` container on the host and has no network
- [ ] `./run_tests.sh` passes
