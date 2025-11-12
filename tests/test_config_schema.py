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
