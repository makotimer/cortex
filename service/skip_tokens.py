# service/skip_tokens.py
"""Cross-process "skip the next scheduled fire" tokens.

A token is a small JSON file under ``<state_dir>/skip/<job-id>.json`` written by an
out-of-process trigger (e.g. the ``trigger-reading`` CLI command) and consumed by the
running scheduler's job wrapper. It lets a separate process suppress one imminent
scheduled run without sharing the scheduler's in-memory job store.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

_SKIP_SUBDIR = "skip"


def default_state_dir() -> Path:
    """Writable state directory (the bind-mounted local/state), derived from CONFIG_PATH."""
    cfg_path = os.getenv("CONFIG_PATH") or "local/config.json"
    return Path(cfg_path).parent / "state"


def _safe(job_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", job_id)


def _token_path(state_dir: Path, job_id: str) -> Path:
    return Path(state_dir) / _SKIP_SUBDIR / f"{_safe(job_id)}.json"


def write_skip_token(state_dir: Path, job_id: str, slot: datetime, grace_sec: int = 600) -> Path:
    """Write a skip token for ``job_id`` targeting the scheduled fire at ``slot``.

    The token is honored by :func:`consume_skip_token` until ``slot + grace_sec``.
    Returns the path written.
    """
    path = _token_path(state_dir, job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "slot": slot.isoformat(),
        "expires": (slot + timedelta(seconds=grace_sec)).isoformat(),
    }
    path.write_text(json.dumps(payload))
    return path


def consume_skip_token(state_dir: Path, job_id: str, now: datetime) -> bool:
    """Return True (and delete the token) if a non-stale token exists for ``job_id``.

    - No token        -> False.
    - now <= expires  -> delete, return True (caller should skip the run).
    - now >  expires  -> stale; delete, return False.
    - corrupt token   -> delete, return False.
    """
    path = _token_path(state_dir, job_id)
    if not path.exists():
        return False
    try:
        data = json.loads(path.read_text())
        expires = datetime.fromisoformat(data["expires"])
    except Exception:
        logger.warning("Corrupt skip token %s; removing", path, exc_info=True)
        path.unlink(missing_ok=True)
        return False

    path.unlink(missing_ok=True)
    if now <= expires:
        return True
    logger.info("Stale skip token for %s (expired %s); not skipping", job_id, expires)
    return False
