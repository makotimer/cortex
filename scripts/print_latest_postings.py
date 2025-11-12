#!/usr/bin/env python3

import glob
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Path to search for .db files
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # ../scripts → project root
DB_DIR = PROJECT_ROOT / "local" / "state"
DB_PATTERN = str(DB_DIR / "*.db")  # glob needs a string


def get_db_files() -> list[str]:
    """Return list of .db files in the target directory."""
    return sorted(glob.glob(DB_PATTERN))


def get_latest_entries(db_path: str, limit: int = 15) -> list[tuple[str, str, str]]:
    """
    Fetch the latest `limit` entries from the postings table, sorted by first_seen_utc DESC.
    Returns list of (title, url, first_seen_utc)
    """
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        conn.execute("PRAGMA foreign_keys=ON;")

        cur = conn.cursor()
        cur.execute(
            """
            SELECT title, url, first_seen_utc
            FROM postings
            ORDER BY first_seen_utc DESC
            LIMIT ?
        """,
            (limit,),
        )

        rows = cur.fetchall()
        conn.close()
        return rows
    except Exception as e:
        print(f"Error reading {db_path}: {e}", file=sys.stderr)
        return []


def format_timestamp(iso_str: str) -> str:
    """Convert ISO timestamp to readable local format."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    except ValueError:
        return iso_str
    except Exception:
        return iso_str


def main():
    if not os.path.exists(DB_DIR):
        print(f"Directory not found: {DB_DIR}")
        sys.exit(1)

    db_files = get_db_files()
    if not db_files:
        print(f"No .db files found in {DB_DIR}")
        return

    # Parse optional limit
    limit = 15
    if len(sys.argv) > 1:
        try:
            limit = int(sys.argv[1])
            if limit <= 0:
                raise ValueError
        except ValueError:
            print(f"Invalid limit: {sys.argv[1]}. Using default (15).", file=sys.stderr)
            limit = 15

    print(f"Found {len(db_files)} database(s). Showing last {limit} entries per DB.\n")

    for db_path in db_files:
        print("=" * 80)
        print(f"DATABASE: {os.path.basename(db_path)}")
        print(f"PATH: {db_path}")
        print("-" * 80)

        entries = get_latest_entries(db_path, limit)
        if not entries:
            print("  No entries found or error accessing database.")
            continue

        for i, (title, url, ts) in enumerate(entries, 1):
            formatted_ts = format_timestamp(ts)
            print(f"{i:2d}. [{formatted_ts}]")
            print(f"     Title: {title}")
            print(f"     URL:   {url}")
            print()

        print()


if __name__ == "__main__":
    main()
