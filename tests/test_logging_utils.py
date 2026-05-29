import json
from pathlib import Path

from service import logging_utils


def _read_jsonl(path):
    with Path(path).open(encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _read_records():
    return _read_jsonl(logging_utils.get_activity_log_path())


def test_activity_record_gets_ts_when_missing():
    # Trace-style records (from logging_bridge) carry no 'ts' of their own.
    logging_utils.write_activity_log({
        "component": "career_watch.engine",
        "op": "vpn_health_fail",
        "person": "Test User",
    })
    rec = _read_records()[-1]
    assert "ts" in rec
    # ISO-8601 with offset, e.g. 2026-05-28T05:00:00-05:00
    assert "T" in rec["ts"]


def test_activity_record_preserves_explicit_ts():
    logging_utils.write_activity_log({"event": "serve_start", "ts": "2020-01-01T00:00:00+00:00"})
    rec = _read_records()[-1]
    assert rec["ts"] == "2020-01-01T00:00:00+00:00"


def test_error_record_gets_ts_when_missing():
    logging_utils.write_error_log({"component": "career_watch.engine", "op": "scraper_run"})
    rec = _read_jsonl(logging_utils._log_path_for_today(logging_utils._ERROR_PREFIX))[-1]
    assert "ts" in rec
