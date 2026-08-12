"""Persisted run state: what was sent last time, and the topic cache.

Storage only — no business rules (those live in ``engine``). Both files are
caches in the strict sense: deleting one costs a re-classification pass and
disables disappearance detection for exactly one run. The database on the site
is the source of truth.

The ``sent`` map records a digest of the payload actually published for each
occurrence, which is what lets a run publish only genuine changes and report
"no changes" instead of re-sending an unchanged set every time.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

STATE_VERSION = 1


def occurrence_key(series_uid: str, occurrence_tid: str) -> str:
    """Stable key for one occurrence. Mirrors the contract's idempotency pair."""
    return f"{series_uid}|{occurrence_tid}"


def payload_digest(payload: dict[str, Any]) -> str:
    """Content digest of a payload, stable across runs and process restarts.

    Canonical JSON with sorted keys, so key ordering never fakes a change. The
    payload passed here must already exclude per-run volatiles (the envelope's
    id and timestamp are added by the bus, not by us).
    """
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _path(state_dir: str, source_slug: str, name: str) -> Path:
    return Path(state_dir) / f"{source_slug}.{name}.json"


def load(state_dir: str, source_slug: str) -> dict[str, Any]:
    """Return the previous run's record, or an empty one if there is none."""
    path = _path(state_dir, source_slug, "sent")
    data: dict[str, Any]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": STATE_VERSION, "sent": {}, "window": None}
    if data.get("version") != STATE_VERSION:
        # An unreadable old format is treated as absent rather than migrated:
        # the cost is one run without disappearance detection.
        return {"version": STATE_VERSION, "sent": {}, "window": None}
    data.setdefault("sent", {})
    data.setdefault("window", None)
    return data


def save(state_dir: str, source_slug: str, sent: dict[str, str], window: dict[str, int]) -> None:
    """Atomically persist the new record. Only called by a run that got far enough."""
    path = _path(state_dir, source_slug, "sent")
    _write_atomic(path, {"version": STATE_VERSION, "sent": sent, "window": window})


def load_topics(state_dir: str, source_slug: str) -> dict[str, list[str]]:
    path = _path(state_dir, source_slug, "topics")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_topics(state_dir: str, source_slug: str, topics: dict[str, list[str]]) -> None:
    _write_atomic(_path(state_dir, source_slug, "topics"), topics)


def _write_atomic(path: Path, data: Any) -> None:
    """Write via a temp file in the same directory, then rename.

    A half-written state file would silently corrupt the next run's
    disappearance maths, so the file is only ever swapped in whole.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, sort_keys=True, ensure_ascii=False)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
