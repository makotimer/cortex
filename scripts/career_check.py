#!/usr/bin/env python3
"""
summarize_new_postings.py — Robust, timezone-safe summary of new career postings.

Preserves original logic exactly, with all improvements applied.
"""

import argparse
import json
import sys
from collections import namedtuple
from datetime import datetime, timezone
from pathlib import Path

# Try to use ijson for large files; fallback to json
try:
    import ijson  # type: ignore

    USE_IJSON = True
except ImportError:
    USE_IJSON = False


# ----------------------------------------------------------------------
NewPosting = namedtuple(
    "NewPosting",
    ["timestamp", "person", "sources", "count", "run_id", "log_file"],
)


# ----------------------------------------------------------------------
def parse_iso(ts: str | None) -> datetime | None:
    """Parse ISO-8601 string to timezone-aware UTC datetime."""
    if not ts:
        return None
    ts = ts.strip()

    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    if "T" not in ts and " " in ts.split(maxsplit=1)[0]:
        ts = ts.replace(" ", "T", 1)

    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None

    dt = dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)
    return dt


# ----------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize new career postings from activity logs",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--log-dir",
        type=Path,
        help="Directory containing activity-*.log files",
    )
    parser.add_argument(
        "--exclude",
        nargs="+",
        default=["Test User", "The Archivist", "Sidekick"],
        help="Usernames to exclude from summary",
    )
    return parser.parse_args()


# ----------------------------------------------------------------------
def main() -> None:
    args = parse_args()

    # Resolve log directory
    script_dir = Path(__file__).resolve().parent
    log_dir: Path = args.log_dir or (script_dir / ".." / "local" / "logs")
    log_dir = log_dir.resolve()

    if not log_dir.is_dir():
        print(f"Error: Log directory not found: {log_dir}", file=sys.stderr)
        sys.exit(1)

    log_files = sorted(log_dir.glob("activity-*"))
    if not log_files:
        print(f"No activity-* files found in {log_dir}")
        return

    print(f"Scanning {len(log_files)} log file(s) in {log_dir}...")

    results: list[NewPosting] = []
    excluded: set[str] = set(args.exclude)

    for log_file in log_files:
        pending_summary: dict[str, object] | None = None

        try:
            if USE_IJSON:
                with open(log_file, "rb") as f:
                    for item in ijson.items(f, "", multiple_values=True):
                        pending_summary = process_log_line(item, excluded, pending_summary, log_file, results)
            else:
                with open(log_file, encoding="utf-8") as f:
                    for raw_line in f:
                        line = raw_line.strip()
                        if not line:
                            continue
                        try:
                            data = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        pending_summary = process_log_line(data, excluded, pending_summary, log_file, results)
        except Exception as e:
            print(f"Warning: Failed to read {log_file}: {e}", file=sys.stderr)

        # EOF: flush any leftover summary
        if pending_summary:
            results.append(
                NewPosting(
                    timestamp=None,
                    person=pending_summary["person"],
                    sources=pending_summary["sources"],
                    count=pending_summary["count"],
                    run_id=pending_summary.get("run_id"),
                    log_file=log_file.name,
                )
            )

    if not results:
        print("\nNo new postings found (excluding test users).")
        return

    # Sort: known timestamps first
    results.sort(key=lambda x: (x.timestamp is None, x.timestamp))

    # ------------------------------------------------------------------
    print("\n" + "=" * 80)
    print("SUMMARY: NEW POSTINGS FOUND")
    print("=" * 80)

    cur_date: str | None = None
    for e in results:
        if e.timestamp:
            local_dt = e.timestamp.astimezone()
            d_str = local_dt.strftime("%Y-%m-%d")
            t_str = local_dt.strftime("%H:%M:%S")
        else:
            d_str = "Unknown Date"
            t_str = "??:??:??"

        if d_str != cur_date:
            print(f"\n[{d_str}]")
            cur_date = d_str

        # Pretty source list
        src_parts = []
        seen = set()
        for src in e.sources:
            if src in seen:
                continue
            seen.add(src)
            cnt = e.sources.count(src)
            name = src.split(":")[-1]
            src_parts.append(f"{name} ({cnt})" if cnt > 1 else name)
        src_str = ", ".join(src_parts)

        print(f"  {t_str} | {e.person:<20} | +{e.count} new | {src_str}")
        if e.run_id:
            print(f"{' ' * 12}| run_id: {e.run_id}")
        print(f"{' ' * 12}| from: {e.log_file}")

    print("\n" + "=" * 80)
    print(f"Total events with new postings: {len(results)}")
    print("=" * 80)


# ----------------------------------------------------------------------
def process_log_line(
    data: dict,
    excluded: set[str],
    pending_summary: dict[str, object] | None,
    log_file: Path,
    results: list[NewPosting],
) -> dict[str, object] | None:
    """
    Process one JSON log line.
    Returns updated pending_summary (or None).
    """
    # 1. Scheduled run line → attach timestamp to pending summary
    if data.get("module") == "modules.career_watch" and data.get("trigger_type") == "scheduled":
        if pending_summary:
            ts_str = data.get("ts") or data.get("started_at")
            ts = parse_iso(ts_str)
            run_id = data.get("run_id")

            results.append(
                NewPosting(
                    timestamp=ts,
                    person=pending_summary["person"],
                    sources=pending_summary["sources"],
                    count=pending_summary["count"],
                    run_id=run_id or pending_summary.get("run_id"),
                    log_file=log_file.name,
                )
            )
            return None  # clear pending
        return pending_summary

    # 2. Engine summary line → store for later
    if (
        data.get("component") == "career_watch.engine"
        and data.get("op") == "summary"
        and data.get("person") not in excluded
    ):
        new_by_source = data.get("new_by_source", {})
        if not isinstance(new_by_source, dict) or not new_by_source:
            return pending_summary

        return {
            "person": data["person"],
            "sources": list(new_by_source.keys()),
            "count": sum(new_by_source.values()),
            "run_id": data.get("run_id"),
        }

    return pending_summary


# ----------------------------------------------------------------------
if __name__ == "__main__":
    main()
