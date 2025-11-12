# tests/conftest.py
import contextlib
import json
import os
import pathlib
import re
import tempfile
import types
import warnings
from unittest import mock

import pytest
from freezegun import freeze_time

from modules.career_watch.lib import config as cw_config
from modules.career_watch.lib import models
from modules.career_watch.lib.scrapers.base import BaseScraper

warnings.filterwarnings("error", category=DeprecationWarning)


# ---------------------------------------------------------------------
# Live tests are opt-in: use --live or RUN_LIVE_TESTS=1
# ---------------------------------------------------------------------
def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Run tests marked as 'live' (network calls or external services).",
    )


def pytest_configure(config: pytest.Config) -> None:
    # Marker registration (so pytest --markers shows it)
    config.addinivalue_line(
        "markers",
        "live: marks tests that perform live network calls or hit external services (skipped by default).",
    )
    # ---- ADD: warning filters here (what you'd put in pytest.ini) ----
    config.addinivalue_line(
        "filterwarnings",
        r"ignore:Testing an element's truth value will always return True in future versions.*:"
        r"DeprecationWarning:soco\.services",
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    run_live = config.getoption("--live") or os.getenv("RUN_LIVE_TESTS") == "1"
    if run_live:
        return
    skip_live = pytest.mark.skip(reason="live tests disabled (use --live or RUN_LIVE_TESTS=1)")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


# ---------------------------------------------------------------------
# Test-wide env defaults (autouse, function-scoped)
# Ensures isolation and avoids scope mismatch with 'monkeypatch'.
# ---------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _env_defaults(monkeypatch):
    # Write logs to a throwaway dir so real logs stay clean (per test)
    tmp_logs = tempfile.mkdtemp(prefix="cw-pytest-logs-")
    monkeypatch.setenv("LOG_DIR", tmp_logs)
    monkeypatch.setenv("ACTIVITY_LOG_PREFIX", "activity-test")
    monkeypatch.setenv("ERROR_LOG_PREFIX", "error-test")
    # Optional kill-switch if you added it in logging_utils.py
    # monkeypatch.setenv("LOG_DISABLE", "1")

    # Default person for modules that require it (override per test if desired)
    # Use "The Archivist" explicitly for Pytests.
    monkeypatch.setenv("SCRAPER_USER_1", "The Archivist")

    yield


# ---------------------------------------------------------------------
# Your existing fixtures
# ---------------------------------------------------------------------
@pytest.fixture(autouse=True)
def _llm_default_off_for_unit_tests(monkeypatch, request):
    """Force LLM off unless explicitly running live tests."""
    live_flag = False
    with contextlib.suppress(Exception):
        live_flag = bool(request.config.getoption("--live"))
    env_live = os.getenv("PYTEST_LIVE", "").strip().lower() in {"1", "true", "yes", "on"}

    if not (live_flag or env_live):
        monkeypatch.setenv("BIBLE_PLAN_ENABLE_LLM", "0")


@pytest.fixture(autouse=True)
def no_email_env(monkeypatch):
    monkeypatch.setenv("SEND_EMAIL", "0")
    monkeypatch.setenv("SCHEDULED_MODULES_DRY_RUN", "1")
    monkeypatch.setenv("CONFIG_PATH", "/app/local/config.json")
    yield


@pytest.fixture
def frozen_utc():
    with freeze_time("2025-01-01T00:00:00Z"):
        yield


@pytest.fixture
def write_min_config(tmp_path, monkeypatch):
    cfg = {
        "jobs": [
            {
                "id": "ex-daily-never",
                "name": "Example Daily (test)",
                "module": "modules.example_daily",
                "trigger": {"date": "2099-01-01T00:00:00Z"},
                "kwargs": {"name": "Pytest", "items": ["a", "b"]},
                "send_email": False,
                "summary": "pytest config",
            }
        ]
    }
    import json

    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("CONFIG_PATH", str(p))
    return p


@pytest.fixture
def stub_emailer(monkeypatch):
    class StubErr(Exception):
        pass

    sent = {"messages": []}

    def send_html(**kwargs):
        sent["messages"].append(kwargs)
        return "<fake-message-id@example>"

    ns = types.SimpleNamespace(send_html=send_html, sent=sent, StubErr=StubErr)
    monkeypatch.setattr("service.emailer.send_html", ns.send_html, raising=True)
    monkeypatch.setattr("service.runner.send_html", ns.send_html, raising=False)
    return ns


@pytest.fixture
def minimal_groups_json(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a groups file in a temp directory."""

    def slugify(s: str) -> str:
        s = re.sub(r"[^0-9a-zA-Z]+", "_", s.lower())
        return re.sub(r"_{2,}", "_", s).strip("_")

    person = "Test User"
    filename = f"career_watch_groups.{slugify(person)}.json"
    path = tmp_path / filename

    data = [
        {"kind": "lever", "source": "lever:acme", "params": {}},
        {"kind": "greenhouse", "source": "greenhouse:acme", "params": {}},
    ]
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture
def fresh_settings(minimal_groups_json, tmp_path, monkeypatch):
    """
    Return a **brand-new** Settings instance for *each* test.
    - groups file: the temp file created above
    - DB file: a fresh per-test SQLite file
    """
    db_path = tmp_path / "careerwatch.db"
    monkeypatch.setenv("SQLITE_PATH", str(db_path))

    return cw_config.Settings.from_env_and_kwargs({
        "person_env": "Test User",
        "groups_path": str(minimal_groups_json),  # force load from temp file
        "sqlite_path": str(db_path),
        "max_threads": 2,
    })


@pytest.fixture
def stub_scraper():
    """Return postings with predictable URLs: same URL = already seen."""

    class Stub(BaseScraper):
        def run(self, person_env, specs, skip_network=False):
            results = []
            for spec in specs:
                # Use fixed URL based on source → same every time
                url = f"https://example.com/{spec.source.replace(':', '-')}/1"
                results.append(
                    models.ScrapeResult(
                        source=spec.source,
                        items=[
                            models.Posting(
                                source=spec.source,
                                person_env=person_env,
                                title=f"{spec.source} - Engineer",
                                url=url,
                            )
                        ],
                    )
                )
            return results

    return Stub
