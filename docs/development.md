# Development

Pure Python 3.12. Two environments are in play and they are not interchangeable —
see the warning below.

## Setup

```bash
cd /srv/docker/cortex
make setup      # install + bootstrap
```

That creates `.venv`, copies `local/config.example.json` → `local/config.json` and
`.env.example` → `.env`. Fill in `.env` before starting anything (`BRIDGE_USERNAME` /
`BRIDGE_PASSWORD` come from `docker exec -it cortex_bridge protonmail-bridge --cli`
→ `info`).

## Host venv vs. container

**`service/`, `modules/` and `tests/` are baked into the image; only `local/` is
bind-mounted.** So:

| Command | Runs where | Sees your edits? |
|---|---|---|
| `make test` / `make live-tests` | container (`scripts/pytest.sh`) | **only after `make reload`** |
| `.venv/bin/python -m pytest tests/` | host | yes, immediately |
| `make lint` / `make format` | host (`.venv/bin/ruff`, `.venv/bin/mypy`) | yes, immediately |

Running `make test` without rebuilding first scores the image's copy of both the code
and the tests, reports a stale pass count, and tells you nothing about your change.
Either `make reload && make test`, or iterate on the host venv.

The host venv cannot import `eventbus` (it is mounted into the container from
`../eventbus/eventbus-kit`), so a handful of `service/` test modules only collect
in the container. Module and scraper tests run fine on the host.

## Loop

```bash
make lint                          # ruff check + mypy
make format                        # ruff format
.venv/bin/python -m pytest tests/  # fast host feedback
make reload && make test           # authoritative, in-container
make logs-f                        # follow cortex logs
```

Adding a job module? Read `modules/README.md` — the Module Author's Guide — and copy
`modules/example_daily/`.
