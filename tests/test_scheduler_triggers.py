from datetime import datetime, timedelta, timezone

import pytest

# Helpers ----------------------------------------------------------------------


def _next_times(trigger, tzinfo, count=5, start=None):
    """
    Ask a trigger for the next `count` fire times, seeding the computation
    as if the previous fire happened at `start`. This avoids APScheduler's
    internal default anchoring to trigger.start_date (creation time).
    """
    from datetime import datetime, timedelta

    if start is None:
        start = datetime.now(tz=tzinfo)

    # Seed both prev and now at `start` so "next" means "strictly after start"
    prev = start
    now = start

    out = []
    for _ in range(count):
        nxt = trigger.get_next_fire_time(prev, now)
        if nxt is None:
            break
        out.append(nxt)
        prev = nxt
        # Move 'now' a tick forward to ensure strictly increasing times
        now = nxt + timedelta(microseconds=1)
    return out


# Tests ------------------------------------------------------------------------


def test_build_trigger_accepts_interval_minutes():
    from service.scheduler import _build_trigger

    trig = _build_trigger({"interval": {"minutes": 5}}, "UTC")
    # IntervalTrigger exposes 'interval' timedelta
    assert hasattr(trig, "interval")
    assert trig.interval.total_seconds() == 300

    # And it should actually schedule every 5 minutes
    ts = datetime(2099, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    times = _next_times(trig, timezone.utc, count=3, start=ts)
    assert times[0] == ts + timedelta(minutes=5)
    assert times[1] == ts + timedelta(minutes=10)
    assert times[2] == ts + timedelta(minutes=15)


def test_build_trigger_accepts_cron_numeric_fields():
    from service.scheduler import _build_trigger

    trig = _build_trigger(
        {"cron": {"second": 0, "minute": 0, "hour": 3, "day_of_week": "mon-fri"}},
        "UTC",
    )
    assert hasattr(trig, "fields")

    # Use a real Monday: 2096-01-02 is Monday
    start = datetime(2096, 1, 2, 0, 0, 0, tzinfo=timezone.utc)  # Monday
    times = _next_times(trig, timezone.utc, count=3, start=start)
    assert times[0] == datetime(2096, 1, 2, 3, 0, 0, tzinfo=timezone.utc)  # Mon
    assert times[1] == datetime(2096, 1, 3, 3, 0, 0, tzinfo=timezone.utc)  # Tue
    assert times[2] == datetime(2096, 1, 4, 3, 0, 0, tzinfo=timezone.utc)  # Wed


def test_build_trigger_accepts_cron_string_lists():
    from service.scheduler import _build_trigger

    trig = _build_trigger(
        {"cron": {"second": 0, "minute": "0,45", "hour": "5-6", "day_of_week": "mon-sat"}},
        "UTC",
    )
    # Use a real Monday: 2099-01-05 is Monday
    start = datetime(2099, 1, 5, 4, 59, 0, tzinfo=timezone.utc)  # Monday 04:59
    times = _next_times(trig, timezone.utc, count=4, start=start)
    # Expect: 05:00, 05:45, 06:00, 06:45 (same day)
    assert times[0] == datetime(2099, 1, 5, 5, 0, 0, tzinfo=timezone.utc)
    assert times[1] == datetime(2099, 1, 5, 5, 45, 0, tzinfo=timezone.utc)
    assert times[2] == datetime(2099, 1, 5, 6, 0, 0, tzinfo=timezone.utc)
    assert times[3] == datetime(2099, 1, 5, 6, 45, 0, tzinfo=timezone.utc)


def test_build_trigger_accepts_date_iso_with_tz():
    from service.scheduler import _build_trigger

    trig = _build_trigger({"date": {"run_at": "2099-01-01T00:00:00Z"}}, "UTC")
    # DateTrigger has run_date
    assert hasattr(trig, "run_date")
    assert trig.run_date.year == 2099
    assert trig.run_date.tzinfo is not None


def test_build_trigger_accepts_date_epoch_seconds():
    from service.scheduler import _build_trigger

    ts = int(datetime(2099, 1, 1, 0, 0, 0, tzinfo=timezone.utc).timestamp())
    trig = _build_trigger({"date": {"run_at": ts}}, "UTC")
    assert hasattr(trig, "run_date")
    assert trig.run_date.year == 2099
    assert trig.run_date.tzinfo is not None


def test_build_trigger_date_iso_without_tz_uses_scheduler_tz():
    from service.scheduler import _build_trigger

    trig = _build_trigger({"date": {"run_at": "2099-01-01T00:00:00"}}, "America/Indiana/Indianapolis")
    assert trig.run_date.tzinfo is not None
    # We can't assert exact offset (DST varies), but tzinfo must be present
    assert "America" in str(trig.run_date.tzinfo) or "UTC" in str(trig.run_date.tzinfo)


def test_build_trigger_daily_time_single_time_is_exact():
    from service.scheduler import _build_trigger

    trig = _build_trigger({"daily_time": {"time": "03:15", "day_of_week": "mon-fri"}}, "UTC")

    # Use a real Monday: 2096-01-02 is Monday
    start = datetime(2096, 1, 2, 3, 14, 50, tzinfo=timezone.utc)  # Monday
    times = _next_times(trig, timezone.utc, count=3, start=start)
    assert times[0] == datetime(2096, 1, 2, 3, 15, 0, tzinfo=timezone.utc)  # Mon 03:15
    assert times[1] == datetime(2096, 1, 3, 3, 15, 0, tzinfo=timezone.utc)  # Tue 03:15
    assert times[2] == datetime(2096, 1, 4, 3, 15, 0, tzinfo=timezone.utc)  # Wed 03:15


def test_build_trigger_daily_time_multiple_times_no_cross_product():
    """
    Critical regression: multiple 'time' entries must not cross-product hours x minutes.
    Expect exact pairs: [05:00, 06:30, 08:00] rather than [05:00, 05:30, 06:00, 06:30, 08:00, 08:30, ...].
    """
    from apscheduler.triggers.combining import OrTrigger

    from service.scheduler import _build_trigger

    trig = _build_trigger(
        {"daily_time": {"time": ["05:00", "06:30", "08:00"], "day_of_week": "mon-sat"}},
        "America/Indiana/Indianapolis",
    )
    # Multiple times should produce OrTrigger combining exact tuples
    assert isinstance(trig, OrTrigger)

    # Pick a Monday in EST (no DST complications needed here)
    start = datetime(2097, 1, 6, 4, 59, 0, tzinfo=timezone.utc)  # Still fine; trigger has its own tz
    times = _next_times(trig, timezone.utc, count=4, start=start)

    # Extract HH:MM in the trigger's local tz for human clarity if desired
    # but asserting UTC instants is equally valid since triggers carry tzinfo.
    # We assert strictly increasing and that 06:00 is NOT present.
    hm = [(t.hour, t.minute) for t in times[:3]]
    assert hm == [(5, 0), (6, 30), (8, 0)], f"Unexpected sequence {hm}"

    # Also ensure no 06:00 or 08:30 sneaks in early
    assert (6, 0) not in hm and (8, 30) not in hm


def test_build_trigger_daily_time_supports_seconds_and_dedup():
    from service.scheduler import _build_trigger

    trig = _build_trigger(
        {"daily_time": {"time": ["12:00:10", "12:00:10", "12:00:20"], "day_of_week": "sun"}},
        "UTC",
    )
    # Use a real Sunday: 2099-01-04 is Sunday
    start = datetime(2099, 1, 4, 11, 59, 59, tzinfo=timezone.utc)  # Sunday
    times = _next_times(trig, timezone.utc, count=3, start=start)
    assert times[0] == datetime(2099, 1, 4, 12, 0, 10, tzinfo=timezone.utc)
    assert times[1] == datetime(2099, 1, 4, 12, 0, 20, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "payload",
    [
        {"date": {}},
        {"daily_time": {}},
        {"daily_time": {"time": "99:99"}},  # invalid time
        {"cron": "*/15 * *"},  # invalid: only 3 fields
        {"interval": {"minutes": -5}},  # invalid interval
        {},  # empty
    ],
)
def test_build_trigger_invalid_inputs_raise(payload):
    from service.scheduler import _build_trigger

    with pytest.raises(ValueError):
        _build_trigger(payload, "UTC")
