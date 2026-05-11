# scripts/

Helper scripts for setup, container management, and Proton Mail inspection.

---

## `first_run.sh`

One-time bootstrap. Copies example files into place if they don't exist yet.

```bash
./scripts/first_run.sh
```

- `local/config.example.json` → `local/config.json` (if missing)
- `.env.example` → `.env` (if missing)
- Touches `local/command_history` so Docker bind-mount creates a file, not a directory

Called automatically by `make bootstrap` (which is part of `make setup`).

---

## `reload.sh`

Rebuilds and recreates containers without a full `down`/`up` cycle.

```bash
./scripts/reload.sh              # rebuild + restart cortex only
./scripts/reload.sh --bridge     # rebuild + restart cortex_bridge then cortex
./scripts/reload.sh --dry-run    # print commands without running them
./scripts/reload.sh --help       # full usage
```

Called by `make reload` and `make reload-bridge`.

---

## `pytest.sh`

Runs the pytest suite inside the running cortex container.

```bash
./scripts/pytest.sh              # standard suite (skips @pytest.mark.live tests)
./scripts/pytest.sh --live       # include live tests (hit real network/email)
./scripts/pytest.sh -k sonos     # pass any pytest args through
```

Called by `make test` and `make live-tests`.

---

## `career_check.py`

Summarizes new career postings from a local SQLite database.

```bash
python scripts/career_check.py
```

Note: the module docstring refers to `summarize_new_postings.py` — that was the original filename before it was renamed.

Called by `make career-report`.

---

## `print_latest_postings.py`

Prints the most recent postings from any `.db` files found under `local/`.

```bash
python scripts/print_latest_postings.py
```

Useful for a quick sanity-check of what the career scraper collected.

---

## `proton_query.py`

Standalone Proton Mail (Bridge) query tool. Connects to `cortex_bridge` via IMAP.

```bash
# Must be run inside the container (needs network access to cortex_bridge)
docker compose exec cortex python scripts/proton_query.py list-folders
docker compose exec cortex python scripts/proton_query.py subjects [folder] [options]
```

### `list-folders`

Lists all mailboxes/labels visible through the bridge.

```bash
python scripts/proton_query.py list-folders
```

### `subjects [folder]`

Prints email subjects from a folder (default: `INBOX`).

```bash
python scripts/proton_query.py subjects
python scripts/proton_query.py subjects "Labels/Command"
python scripts/proton_query.py subjects INBOX --limit 20 --unseen
python scripts/proton_query.py subjects INBOX --search "SINCE 1-May-2026"
```

| Option | Description |
|---|---|
| `folder` | Mailbox path (default: `INBOX`) |
| `--limit N` | Max messages to show |
| `--unseen` | Only unseen messages |
| `--search TEXT` | Raw IMAP search criteria |
