import datetime as dt
from types import SimpleNamespace
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

from service import cli


@pytest.mark.parametrize(
    "spec,weekday,expected",
    [
        ("mon-thu", 0, True),
        ("mon-thu", 3, True),
        ("mon-thu", 4, False),
        ("fri,sat,sun", 5, True),
        ("fri,sat,sun", 0, False),
        ("mon-sun", 6, True),
    ],
)
def test_weekday_matches(spec, weekday, expected):
    assert cli._weekday_matches(spec, weekday) is expected


def _bible_cfg():
    return {
        "timezone": "America/Chicago",
        "jobs": [
            {"id": "bible-plan-mon-thu-0455", "module": "modules.bible_plan",
             "trigger": {"daily_time": {"time": "05:25", "day_of_week": "mon-thu"}}},
            {"id": "bible-plan-fri-sun-0555", "module": "modules.bible_plan",
             "trigger": {"daily_time": {"time": "06:55", "day_of_week": "fri,sat,sun"}}},
        ],
    }


def test_select_bible_job_weekday():
    job = cli._select_bible_job(_bible_cfg(), dt.date(2026, 6, 2))  # Tuesday
    assert job["id"] == "bible-plan-mon-thu-0455"


def test_select_bible_job_weekend():
    job = cli._select_bible_job(_bible_cfg(), dt.date(2026, 6, 6))  # Saturday
    assert job["id"] == "bible-plan-fri-sun-0555"


def test_select_bible_job_no_match_raises():
    cfg = {"jobs": [{"id": "x", "module": "modules.other",
                     "trigger": {"daily_time": {"time": "05:25", "day_of_week": "mon-sun"}}}]}
    with pytest.raises(ValueError):
        cli._select_bible_job(cfg, dt.date(2026, 6, 2))


def test_next_fire_within_true_for_imminent_slot():
    tz = ZoneInfo("America/Chicago")
    job = {"id": "bible-plan-mon-thu-0455", "module": "modules.bible_plan",
           "trigger": {"daily_time": {"time": "05:25", "day_of_week": "mon-thu"}}}
    now = dt.datetime(2026, 6, 2, 5, 0, tzinfo=tz)  # Tue 05:00, fire 05:25 same day
    fire = cli._next_fire_within(job, tz, now, hours=6)
    assert fire is not None
    assert fire.hour == 5 and fire.minute == 25


def test_next_fire_within_none_when_far():
    tz = ZoneInfo("America/Chicago")
    job = {"id": "bible-plan-mon-thu-0455", "module": "modules.bible_plan",
           "trigger": {"daily_time": {"time": "05:25", "day_of_week": "mon-thu"}}}
    now = dt.datetime(2026, 6, 2, 12, 0, tzinfo=tz)  # Tue noon, next fire Wed 05:25 (>6h)
    assert cli._next_fire_within(job, tz, now, hours=6) is None


def test_cmd_trigger_reading_runs_and_writes_token(monkeypatch, tmp_path):
    cfg = {
        "timezone": "America/Chicago",
        "jobs": [
            {"id": "bible-plan-mon-thu-0455", "module": "modules.bible_plan",
             "email_to": ["a@example.com"], "subject": "Daily Reading",
             "trigger": {"daily_time": {"time": "05:25", "day_of_week": "mon-thu"}}},
            {"id": "bible-plan-fri-sun-0555", "module": "modules.bible_plan",
             "email_to": ["a@example.com"],
             "trigger": {"daily_time": {"time": "06:55", "day_of_week": "fri,sat,sun"}}},
        ],
    }
    monkeypatch.setattr(cli, "_load_config_with_optional_path", lambda path: cfg)
    monkeypatch.setattr(cli.skip_tokens, "_token_path",
                        lambda sd, jid: tmp_path / "skip" / f"{jid}.json")
    fixed_now = dt.datetime(2026, 6, 2, 5, 0, tzinfo=ZoneInfo("America/Chicago"))
    monkeypatch.setattr(cli, "_now_local", lambda tz: fixed_now)

    run_mock = mock.Mock(return_value=(None, "run-123"))
    monkeypatch.setattr(cli._runner, "run_module_once", run_mock)

    args = SimpleNamespace(config=None, date=None, no_dedup=False)
    rc = cli.cmd_trigger_reading(args)
    assert rc == 0

    _, kwargs = run_mock.call_args
    assert kwargs["email_to"] == ["a@example.com"]
    assert kwargs["trigger_type"] == "command"
    assert kwargs["send_email"] is True

    assert (tmp_path / "skip" / "bible-plan-mon-thu-0455.json").exists()


def test_cmd_trigger_reading_no_dedup_skips_token(monkeypatch, tmp_path):
    cfg = {
        "timezone": "America/Chicago",
        "jobs": [
            {"id": "bible-plan-mon-thu-0455", "module": "modules.bible_plan",
             "email_to": ["a@example.com"],
             "trigger": {"daily_time": {"time": "05:25", "day_of_week": "mon-thu"}}},
        ],
    }
    monkeypatch.setattr(cli, "_load_config_with_optional_path", lambda path: cfg)
    monkeypatch.setattr(cli, "_state_dir", lambda: tmp_path)
    monkeypatch.setattr(cli, "_now_local",
                        lambda tz: dt.datetime(2026, 6, 2, 5, 0, tzinfo=ZoneInfo("America/Chicago")))
    monkeypatch.setattr(cli._runner, "run_module_once", mock.Mock(return_value=(None, "r")))

    args = SimpleNamespace(config=None, date=None, no_dedup=True)
    assert cli.cmd_trigger_reading(args) == 0
    assert not (tmp_path / "skip").exists()
