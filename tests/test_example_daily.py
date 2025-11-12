from modules.example_daily import run


def test_example_daily_happy_path(frozen_utc):
    res = run(name="ben", items=["x", "y"])
    assert "<html" in res.lower()
    # echoes kwargs
    assert "&quot;name&quot;: &quot;ben&quot;" in res.lower()


def test_example_daily_raises_on_fail_flag():
    import pytest

    with pytest.raises(RuntimeError):
        run(fail=True)
