import datetime as dt
from pathlib import Path

import pytest

from service import scheduler as sch
from service import skip_tokens


@pytest.fixture
def cfg(tmp_path: Path):
    return {
        "timezone": "UTC",
        "jobs": [
            {
                "id": "demo-job",
                "module": "modules.example_daily",
                "trigger": {"daily_time": {"time": "05:25", "day_of_week": "mon-sun"}},
            }
        ],
    }


def test_job_wrapper_skips_when_token_present(monkeypatch, tmp_path: Path, cfg):
    monkeypatch.setattr(sch, "_state_dir", lambda: tmp_path)

    calls = []
    monkeypatch.setattr(
        sch.runner, "run_module_once",
        lambda *a, **k: calls.append((a, k)) or (None, "rid"),
    )

    from zoneinfo import ZoneInfo
    tz = ZoneInfo("UTC")
    spec = sch._make_job_spec(
        cfg["jobs"][0],
        default_job_defaults={"coalesce": True, "max_instances": 1},
        tz=tz,
    )

    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler(timezone=tz)
    sch._add_job(scheduler, spec)
    wrapper = scheduler.get_job("demo-job").func

    # No token -> runs
    wrapper()
    assert len(calls) == 1

    # Token present for "now" -> skipped
    now = dt.datetime.now(tz)
    skip_tokens.write_skip_token(tmp_path, "demo-job", now)
    wrapper()
    assert len(calls) == 1  # unchanged -> skipped
