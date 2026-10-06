# Plan: autoheal is the fleet's one autoheal, with no network

Spec: `docs/superpowers/specs/2026-10-06-autoheal-fleet-owner-design.md`

### Task 1: compose + test

- [ ] Add a test (new file `tests/test_compose_autoheal.py`, or the existing compose
      test module if one exists — check with `grep -rln docker-compose.yaml tests/`)
      that loads `docker-compose.yaml` with `yaml.safe_load` and asserts:
      `services.autoheal.network_mode == "none"`, `"networks" not in services.autoheal`,
      env still has `AUTOHEAL_CONTAINER_LABEL=autoheal`, and the docker.sock mount
      is present. Run it; it fails.
- [ ] Edit `docker-compose.yaml`: `network_mode: none` on `autoheal`, plus the
      fleet-owner comment from the spec. Run `./run_tests.sh`; all pass. Commit.

### Task 2: docs

- [ ] `docs/architecture.md`: service row + "Only `cortex_bridge` and `vpn`" paragraph
      (fleet-wide, list the label holders as of 2026-10-06 from the spec); network
      table drops the `default` row, adds a line that autoheal is `network_mode: none`.
- [ ] `docs/operations.md` line ~44 and `README.md` (if it repeats the claim): same
      correction. `docs/decisions.md`: dated 2026-10-06 entry — why no network, why
      cortex owns the fleet autoheal, that lwrpc's duplicate is being removed.
- [ ] `./run_tests.sh` passes. Commit.
