# tests/test_skip_tokens.py
import datetime as dt
from pathlib import Path

from service import skip_tokens


def _now(h=5, m=25):
    return dt.datetime(2026, 6, 2, h, m, tzinfo=dt.UTC)


def test_write_then_consume_within_grace_returns_true_and_removes(tmp_path: Path):
    slot = _now()
    skip_tokens.write_skip_token(tmp_path, "bible-plan-mon-thu-0455", slot, grace_sec=600)
    token_file = tmp_path / "skip" / "bible-plan-mon-thu-0455.json"
    assert token_file.exists()

    skipped = skip_tokens.consume_skip_token(tmp_path, "bible-plan-mon-thu-0455", slot)
    assert skipped is True
    assert not token_file.exists()


def test_consume_absent_returns_false(tmp_path: Path):
    assert skip_tokens.consume_skip_token(tmp_path, "nope", _now()) is False


def test_consume_after_expiry_returns_false_and_removes(tmp_path: Path):
    slot = _now()
    skip_tokens.write_skip_token(tmp_path, "job", slot, grace_sec=600)
    token_file = tmp_path / "skip" / "job.json"

    late = slot + dt.timedelta(minutes=11)
    assert skip_tokens.consume_skip_token(tmp_path, "job", late) is False
    assert not token_file.exists()


def test_consume_corrupt_token_returns_false_and_removes(tmp_path: Path):
    token_file = tmp_path / "skip" / "job.json"
    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text("{not json")
    assert skip_tokens.consume_skip_token(tmp_path, "job", _now()) is False
    assert not token_file.exists()
