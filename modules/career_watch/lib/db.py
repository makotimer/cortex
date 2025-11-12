from __future__ import annotations

import contextlib
import os
import sqlite3
from collections.abc import Iterable

from .logging_bridge import error as log_error
from .models import Posting
from .utils import now_iso

# ---- Public API -------------------------------------------------------------


def init_db(sqlite_path: str) -> None:
    """
    Ensure the SQLite database and schema exist.
    Safe to call multiple times.
    """
    _ensure_dir(sqlite_path)
    with _connect(sqlite_path) as conn:
        _apply_pragmas(conn)
        _ensure_schema(conn)


def filter_new(sqlite_path: str, person_env: str, postings: Iterable[Posting]) -> list[Posting]:
    """
    Insert unseen postings and return the subset that are NEW.

    Dedupe key: (source, person_env, title, url)

    Args:
        sqlite_path: path to sqlite file
        person_env: person name for this run (must match posting.person)
        postings: all items returned by scrapers (NOT filtered)

    Returns:
        A list of postings that were not previously recorded.
    """
    init_db(sqlite_path)

    new_items: list[Posting] = []
    ts = now_iso()

    try:
        with _connect(sqlite_path) as conn:
            _apply_pragmas(conn)
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            for p in postings:
                # Enforce person consistency; prefer the run's person.
                source = p.source.strip()
                title = p.title.strip()
                url = p.url.strip()
                person_norm = person_env.strip()

                # INSERT OR IGNORE to dedupe
                cur.execute(
                    """
                    INSERT OR IGNORE INTO postings (source, person, title, url, first_seen_utc)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (source, person_norm, title, url, ts),
                )
                # If the row was inserted, it's NEW
                if cur.rowcount == 1:
                    # Preserve the original Posting object but with normalized person if needed
                    if p.person_env != person_norm:
                        new_items.append(Posting(source=source, person_env=person_norm, title=title, url=url))
                    else:
                        new_items.append(p)
            conn.commit()
    except Exception as e:
        # Surface to caller, but also log a structured error.
        log_error({
            "component": "career_watch.db",
            "op": "filter_new",
            "sqlite_path": sqlite_path,
            "error": repr(e),
        })
        raise

    return new_items


# ---- Nice-to-have helpers for tests & diagnostics --------------------------


def count_rows(sqlite_path: str) -> int:
    """Return total rows in postings table; 0 if DB missing/empty."""
    if not os.path.exists(sqlite_path):
        return 0
    with _connect(sqlite_path) as conn:
        _apply_pragmas(conn)
        _ensure_schema(conn)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM postings")
        (n,) = cur.fetchone()
    return int(n or 0)


def reset_db(sqlite_path: str) -> None:
    """
    Remove the DB file entirely (for pytest fixtures).
    Safe if it doesn't exist.
    """
    with contextlib.suppress(FileNotFoundError):
        os.remove(sqlite_path)


# ---- Internal utilities -----------------------------------------------------


def _ensure_dir(sqlite_path: str) -> None:
    d = os.path.dirname(os.path.abspath(sqlite_path)) or "."
    os.makedirs(d, exist_ok=True)


def _connect(sqlite_path: str) -> sqlite3.Connection:
    # isolation_level=None gives autocommit mode; we'll manage transactions explicitly.
    conn = sqlite3.connect(sqlite_path, timeout=30.0, isolation_level=None)
    # Return rows as tuples; we don't need row factories here.
    return conn


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    # Reasonable defaults for small append-only table
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA cache_size=-8000;")  # approx 8MB cache


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS postings (
          id INTEGER PRIMARY KEY,
          source TEXT NOT NULL,
          person TEXT NOT NULL,
          title  TEXT NOT NULL,
          url    TEXT NOT NULL,
          first_seen_utc TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS ux_postings_dedupe
          ON postings (source, person, title, url);
        """
    )
