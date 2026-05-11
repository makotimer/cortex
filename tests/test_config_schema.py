import pytest

from service.config_schema import ConfigError, validate

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg(trigger: dict, **job_extras) -> dict:
    """Minimal valid config with one job using the given nested trigger."""
    job = {"id": "test-job", "module": "modules.example_daily", "trigger": trigger, **job_extras}
    return {"jobs": [job]}


# ---------------------------------------------------------------------------
# daily_time — nested dict form (regression: was incorrectly rejected)
# ---------------------------------------------------------------------------

def test_validate_daily_time_dict_single_time():
    validate(_cfg({"daily_time": {"time": "08:00"}}))


def test_validate_daily_time_dict_with_day_of_week():
    validate(_cfg({"daily_time": {"time": "08:00", "day_of_week": "mon-fri"}}))


def test_validate_daily_time_dict_multiple_times():
    validate(_cfg({"daily_time": {"time": ["06:00", "18:00"]}}))


def test_validate_daily_time_dict_missing_time_raises():
    with pytest.raises(ConfigError, match="'time'"):
        validate(_cfg({"daily_time": {}}))


def test_validate_daily_time_dict_bad_time_raises():
    with pytest.raises(ConfigError, match=r"out of range|HH:MM"):
        validate(_cfg({"daily_time": {"time": "99:00"}}))


def test_validate_daily_time_dict_unknown_field_raises():
    with pytest.raises(ConfigError, match="unknown field"):
        validate(_cfg({"daily_time": {"time": "08:00", "typo_field": True}}))


# ---------------------------------------------------------------------------
# interval — deep int validation now fires for nested triggers
# ---------------------------------------------------------------------------

def test_validate_interval_valid():
    validate(_cfg({"interval": {"hours": 1}}))


def test_validate_interval_negative_raises():
    with pytest.raises(ConfigError):
        validate(_cfg({"interval": {"hours": -1}}))


# ---------------------------------------------------------------------------
# date — empty string now caught for nested triggers
# ---------------------------------------------------------------------------

def test_validate_date_empty_raises():
    with pytest.raises(ConfigError, match="non-empty"):
        validate(_cfg({"date": ""}))


# ---------------------------------------------------------------------------
# Existing smoke test
# ---------------------------------------------------------------------------

def test_load_and_validate_min_config(write_min_config):
    # Ensure load_config returns a structure with .jobs or ["jobs"]
    from service import config_schema

    cfg = config_schema.load_config()  # CONFIG_PATH set by fixture
    # emulator: accept dict or object with .jobs
    jobs = getattr(cfg, "jobs", None) or cfg.get("jobs", [])
    assert isinstance(jobs, list) and jobs, "expected at least one job"
    # optional validate()
    if hasattr(config_schema, "validate"):
        config_schema.validate(cfg)
