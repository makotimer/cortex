# tests/test_career_watch.py
from unittest import mock

from modules.career_watch.lib import config as cw_config  # already added
from modules.career_watch.lib import db, engine, models, render, vpn_client


# ----------------------------------------------------------------------
# 1. No new postings → engine returns None
# ----------------------------------------------------------------------
def test_no_new_postings_returns_none(fresh_settings, stub_scraper):
    # Seed DB with *both* postings → nothing new
    seed = [
        models.Posting(
            source="lever:acme",
            person_env="Test User",
            title="lever:acme - Engineer",
            url="https://example.com/lever-acme/1",
        ),
        models.Posting(
            source="greenhouse:acme",
            person_env="Test User",
            title="greenhouse:acme - Engineer",
            url="https://example.com/greenhouse-acme/1",
        ),
    ]
    db.filter_new(fresh_settings.sqlite_path, "Test User", seed)

    with mock.patch("modules.career_watch.lib.scrapers.registry.get", return_value=stub_scraper):
        result = engine.run_once(fresh_settings)

    assert result is None


# ----------------------------------------------------------------------
# 2. One new posting → HTML + meta returned
# ----------------------------------------------------------------------
def test_one_new_posting_returns_html_and_meta(fresh_settings, stub_scraper):
    db.filter_new(
        fresh_settings.sqlite_path,
        "Test User",
        [
            models.Posting(
                source="lever:acme",
                person_env="Test User",
                title="lever:acme - Engineer",
                url="https://example.com/lever-acme/1",
            )
        ],
    )

    html, meta = engine.run_once(fresh_settings, get_scraper=lambda kind: stub_scraper)  # ← INJECTED

    assert html is not None
    assert "greenhouse:acme - Engineer" in html
    assert meta["new_total"] == 1


# ----------------------------------------------------------------------
# 3. email_all_even_if_seen=True → render everything
# ----------------------------------------------------------------------
def test_email_all_even_if_seen_renders_all(fresh_settings, stub_scraper):
    settings = cw_config.Settings.from_env_and_kwargs({
        "person_env": "Test User",
        "groups_path": fresh_settings.groups_path,
        "sqlite_path": fresh_settings.sqlite_path,
        "max_threads": 2,
        "email_all_even_if_seen": True,
        "proxy_url": "",
    })

    html, meta = engine.run_once(settings, get_scraper=lambda kind: stub_scraper)

    assert html is not None
    assert "lever:acme - Engineer" in html
    assert "greenhouse:acme - Engineer" in html
    assert meta["new_total"] == 2


# ----------------------------------------------------------------------
# 4. ingest_only_no_email=True → DB updated, no return value
# ----------------------------------------------------------------------
def test_ingest_only_no_email_returns_none(fresh_settings, stub_scraper):
    settings = cw_config.Settings.from_env_and_kwargs({
        "person_env": "Test User",
        "groups_path": fresh_settings.groups_path,
        "sqlite_path": fresh_settings.sqlite_path,
        "ingest_only_no_email": True,
        "proxy_url": "",
    })

    result = engine.run_once(settings, get_scraper=lambda kind: stub_scraper)

    assert result is None
    assert db.count_rows(fresh_settings.sqlite_path) == 2


# ----------------------------------------------------------------------
# 5. skip_network=True → scrapers are never instantiated
# ----------------------------------------------------------------------
def test_skip_network_skips_scrapers(fresh_settings):
    settings = cw_config.Settings.from_env_and_kwargs({
        "person_env": "Test User",
        "groups_path": fresh_settings.groups_path,
        "sqlite_path": fresh_settings.sqlite_path,
        "skip_network": True,
        "proxy_url": "",
    })

    # No patch needed - engine short-circuits before registry lookup
    result = engine.run_once(settings)
    assert result is None


# ----------------------------------------------------------------------
# 6. VPN health check: unhealthy → engine returns None (fail-closed)
# ----------------------------------------------------------------------
def test_vpn_health_fail_returns_none(fresh_settings, stub_scraper, monkeypatch):
    settings = cw_config.Settings.from_env_and_kwargs({
        "person_env": "Test User",
        "groups_path": fresh_settings.groups_path,
        "sqlite_path": fresh_settings.sqlite_path,
        "proxy_url": "http://vpn:8888",
    })
    monkeypatch.setattr(vpn_client.GluetunClient, "health", lambda self: False)
    result = engine.run_once(settings, get_scraper=lambda kind: stub_scraper)
    assert result is None


# ----------------------------------------------------------------------
# 7. VPN health check: healthy → engine proceeds normally
# ----------------------------------------------------------------------
def test_vpn_health_ok_proceeds(fresh_settings, stub_scraper, monkeypatch):
    settings = cw_config.Settings.from_env_and_kwargs({
        "person_env": "Test User",
        "groups_path": fresh_settings.groups_path,
        "sqlite_path": fresh_settings.sqlite_path,
        "proxy_url": "http://vpn:8888",
    })
    monkeypatch.setattr(vpn_client.GluetunClient, "health", lambda self: True)
    result = engine.run_once(settings, get_scraper=lambda kind: stub_scraper)
    # 2 new postings found → (html, meta) returned
    assert result is not None
    _html, meta = result
    assert meta["new_total"] == 2


# ----------------------------------------------------------------------
# 8. No proxy_url set → VPN check skipped entirely
# ----------------------------------------------------------------------
def test_no_proxy_url_skips_vpn_check(fresh_settings, stub_scraper, monkeypatch):
    # proxy_url is None by default (CAREER_WATCH_PROXY_URL not set in tests)
    assert fresh_settings.proxy_url is None

    # If the health check were invoked it would raise; we verify it is not
    monkeypatch.setattr(
        vpn_client.GluetunClient, "health", lambda self: (_ for _ in ()).throw(RuntimeError("should not call"))
    )
    result = engine.run_once(fresh_settings, get_scraper=lambda kind: stub_scraper)
    # 2 new postings → result returned (VPN check was never triggered)
    assert result is not None


# ----------------------------------------------------------------------
# 9. rotate_vpn_per_run=False → rotate() never called
# ----------------------------------------------------------------------
def test_rotate_vpn_per_run_false_skips_rotation(fresh_settings, stub_scraper, monkeypatch):
    settings = cw_config.Settings.from_env_and_kwargs({
        "person_env": "Test User",
        "groups_path": fresh_settings.groups_path,
        "sqlite_path": fresh_settings.sqlite_path,
        "proxy_url": "http://vpn:8888",
        "rotate_vpn_per_run": False,
    })
    monkeypatch.setattr(vpn_client.GluetunClient, "health", lambda self: True)
    rotate_calls: list[bool] = []
    monkeypatch.setattr(vpn_client.GluetunClient, "rotate", lambda self: rotate_calls.append(True) or "1.2.3.4")

    engine.run_once(settings, get_scraper=lambda kind: stub_scraper)
    assert rotate_calls == []


# ----------------------------------------------------------------------
# 10. Rotation failure does not abort the run
# ----------------------------------------------------------------------
def test_rotation_failure_does_not_abort(fresh_settings, stub_scraper, monkeypatch):
    settings = cw_config.Settings.from_env_and_kwargs({
        "person_env": "Test User",
        "groups_path": fresh_settings.groups_path,
        "sqlite_path": fresh_settings.sqlite_path,
        "proxy_url": "http://vpn:8888",
        "rotate_vpn_per_run": True,
    })
    monkeypatch.setattr(vpn_client.GluetunClient, "health", lambda self: True)
    monkeypatch.setattr(vpn_client.GluetunClient, "rotate", lambda self: None)  # rotation fails

    result = engine.run_once(settings, get_scraper=lambda kind: stub_scraper)
    # Run still proceeds because health() passed
    assert result is not None


# ----------------------------------------------------------------------
# 11. Render helper is safe (XSS)
# ----------------------------------------------------------------------
def test_render_build_tables_escapes_html():
    postings = {
        "lever:acme": [
            models.Posting(
                source="lever:acme",
                person_env="Test User",
                title="Senior <script>alert(1)</script>",
                url="https://example.com/lever/1",
            )
        ]
    }
    html = render.build_tables(postings)
    assert "<h3>lever:acme</h3>" in html
    assert "Senior &lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert 'href="https://example.com/lever/1"' in html
    assert "<script>" not in html
