# tests/test_career_watch.py
from unittest import mock

import pytest

from modules._shared import vpn_client
from modules.career_watch.lib import config as cw_config  # already added
from modules.career_watch.lib import db, engine, models, render


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
# 6-10. The VPN gate: "can this exit reach anything?", not "does it have an IP?"
#
# These moved off health()/rotate() and onto switch_until_usable(). The old
# gate passed whenever gluetun reported a public IP, which it does while the
# tunnel is mid-reconnect -- 22% of runs failed to rotate and 54% of those then
# scraped zero results from every source while still logging ok=true.
# ----------------------------------------------------------------------
def _outcome(**kw):
    base = {"ok": True, "ip": "1.2.3.4", "attempts": 1, "reason": "verified"}
    base.update(kw)
    return vpn_client.SwitchOutcome(**base)


def _patch_switch(monkeypatch, outcome, calls=None):
    def _switch(self, **kwargs):
        if calls is not None:
            calls.append(kwargs)
        return outcome
    monkeypatch.setattr(vpn_client.GluetunClient, "switch_until_usable", _switch)


def _vpn_settings(fresh_settings, **kw):
    return cw_config.Settings.from_env_and_kwargs({
        "person_env": "Test User",
        "groups_path": fresh_settings.groups_path,
        "sqlite_path": fresh_settings.sqlite_path,
        "proxy_url": "http://vpn:8888",
        **kw,
    })


def test_no_usable_exit_raises(fresh_settings, stub_scraper, monkeypatch):
    """Fail-closed. Raising (not returning None) makes the runner record
    ok=False, so a missed scrape lands in FAILED RUNS instead of hiding."""
    _patch_switch(monkeypatch, _outcome(ok=False, ip=None, attempts=3,
                                        reason="exit 9.9.9.9 could not reach target"))
    with pytest.raises(engine.VPNUnavailableError):
        engine.run_once(_vpn_settings(fresh_settings),
                        get_scraper=lambda kind: stub_scraper)


def test_usable_exit_proceeds(fresh_settings, stub_scraper, monkeypatch):
    _patch_switch(monkeypatch, _outcome())
    result = engine.run_once(_vpn_settings(fresh_settings),
                             get_scraper=lambda kind: stub_scraper)
    assert result is not None
    _html, meta = result
    assert meta["new_total"] == 2


def test_usable_exit_that_did_not_change_still_proceeds(
        fresh_settings, stub_scraper, monkeypatch):
    """The old gate called an unchanged IP a failed rotation even when the
    tunnel was perfectly usable. Rotation is a nicety; reachability is the
    requirement."""
    _patch_switch(monkeypatch, _outcome(changed=False))
    assert engine.run_once(_vpn_settings(fresh_settings),
                           get_scraper=lambda kind: stub_scraper) is not None


def test_no_proxy_url_skips_the_vpn_gate_entirely(
        fresh_settings, stub_scraper, monkeypatch):
    assert fresh_settings.proxy_url is None

    def _boom(self, **kwargs):
        raise AssertionError("should not switch when no proxy is configured")

    monkeypatch.setattr(vpn_client.GluetunClient, "switch_until_usable", _boom)
    assert engine.run_once(fresh_settings,
                           get_scraper=lambda kind: stub_scraper) is not None


def test_rotate_vpn_per_run_false_verifies_without_switching(
        fresh_settings, stub_scraper, monkeypatch):
    """prefer_new_ip=False verifies the current exit first and only switches if
    it is unusable -- restarting the tunnel disturbs every other consumer."""
    calls: list[dict] = []
    _patch_switch(monkeypatch, _outcome(), calls)
    engine.run_once(_vpn_settings(fresh_settings, rotate_vpn_per_run=False),
                    get_scraper=lambda kind: stub_scraper)
    assert calls and calls[0]["prefer_new_ip"] is False


def test_rotate_vpn_per_run_true_asks_for_a_new_ip(
        fresh_settings, stub_scraper, monkeypatch):
    calls: list[dict] = []
    _patch_switch(monkeypatch, _outcome(), calls)
    engine.run_once(_vpn_settings(fresh_settings, rotate_vpn_per_run=True),
                    get_scraper=lambda kind: stub_scraper)
    assert calls and calls[0]["prefer_new_ip"] is True


# ----------------------------------------------------------------------
# 10b. Rotation waits far longer than the old 20 s and reports how long
#      the reconnect actually took, so latency can be tracked over time.
# ----------------------------------------------------------------------
def test_rotate_timeout_default_is_generous():
    assert vpn_client.DEFAULT_ROTATE_TIMEOUT >= 60


def test_rotate_records_duration_on_success(monkeypatch):
    client = vpn_client.GluetunClient(control_url="http://vpn:8000")
    monkeypatch.setattr(vpn_client.requests, "put", lambda *a, **kw: _StubResponse())
    monkeypatch.setattr(vpn_client.time, "sleep", lambda _s: None)
    # First call = "before" IP, later calls = the new IP after a few polls.
    ips = iter(["1.1.1.1", "1.1.1.1", "1.1.1.1", "2.2.2.2"])
    monkeypatch.setattr(vpn_client.GluetunClient, "current_ip", lambda self: next(ips))

    assert client.rotate() == "2.2.2.2"
    assert client.last_rotate_polls == 3
    assert client.last_rotate_seconds is not None
    assert client.last_rotate_seconds >= 0


def test_rotate_records_duration_on_timeout(monkeypatch):
    # A tiny timeout keeps the test fast while exercising the timeout path.
    client = vpn_client.GluetunClient(control_url="http://vpn:8000", rotate_timeout=0.05)
    monkeypatch.setattr(vpn_client.requests, "put", lambda *a, **kw: _StubResponse())
    monkeypatch.setattr(vpn_client.time, "sleep", lambda _s: None)
    monkeypatch.setattr(vpn_client.GluetunClient, "current_ip", lambda self: "1.1.1.1")

    assert client.rotate() is None
    # Duration is recorded on failure too — that's the number worth tracking.
    assert client.last_rotate_seconds is not None
    assert client.last_rotate_polls >= 1


def test_rotate_timeout_env_override(fresh_settings, stub_scraper, monkeypatch):
    """VPN_ROTATE_TIMEOUT still reaches the client that does the switching."""
    monkeypatch.setenv("VPN_ROTATE_TIMEOUT", "150")
    seen: list[float] = []

    def _switch(self, **kwargs):
        seen.append(self._rotate_timeout)
        return _outcome()

    monkeypatch.setattr(vpn_client.GluetunClient, "switch_until_usable", _switch)
    engine.run_once(_vpn_settings(fresh_settings),
                    get_scraper=lambda kind: stub_scraper)
    assert seen == [150.0]


class _StubResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None


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


# ----------------------------------------------------------------------
# switch_until_usable: the switching logic itself
# ----------------------------------------------------------------------
def _client(tmp_path, **kw):
    return vpn_client.GluetunClient(
        control_url="http://vpn:8000",
        quarantine_path=str(tmp_path / "q.json"), **kw)


def test_usable_is_true_on_a_2xx_through_the_proxy(monkeypatch, tmp_path):
    monkeypatch.setattr(vpn_client.requests, "head",
                        lambda *a, **kw: _StubResponse(200))
    assert _client(tmp_path).usable("http://vpn:8888", "https://example.invalid")


def test_usable_is_false_when_the_request_cannot_complete(monkeypatch, tmp_path):
    """The exact shape of the failure that passed the old gate: gluetun holds a
    public IP, and nothing can be reached through it."""
    def _boom(*a, **kw):
        raise RuntimeError("ProxyError")
    monkeypatch.setattr(vpn_client.requests, "head", _boom)
    assert not _client(tmp_path).usable("http://vpn:8888", "https://example.invalid")


def test_usable_falls_back_to_get_when_head_is_refused(monkeypatch, tmp_path):
    monkeypatch.setattr(vpn_client.requests, "head",
                        lambda *a, **kw: _StubResponse(405))
    monkeypatch.setattr(vpn_client.requests, "get",
                        lambda *a, **kw: _StubResponse(200))
    assert _client(tmp_path).usable("http://vpn:8888", "https://example.invalid")


def test_current_exit_is_verified_before_any_restart(monkeypatch, tmp_path):
    """prefer_new_ip=False must not restart a working tunnel."""
    c = _client(tmp_path)
    restarts = []
    monkeypatch.setattr(vpn_client.GluetunClient, "current_ip", lambda self: "1.1.1.1")
    monkeypatch.setattr(vpn_client.GluetunClient, "_restart_and_wait",
                        lambda self: restarts.append(True))
    monkeypatch.setattr(vpn_client.GluetunClient, "usable", lambda self, p, v: True)

    out = c.switch_until_usable(proxy_url="http://vpn:8888", verify_url="u",
                                prefer_new_ip=False)
    assert out.ok and out.attempts == 1 and restarts == []


def test_a_bad_exit_is_quarantined_and_the_next_one_is_used(monkeypatch, tmp_path):
    c = _client(tmp_path)
    current = {"ip": "9.9.9.9"}
    monkeypatch.setattr(vpn_client.GluetunClient, "current_ip",
                        lambda self: current["ip"])
    monkeypatch.setattr(vpn_client.GluetunClient, "_restart_and_wait",
                        lambda self: current.__setitem__("ip", "2.2.2.2"))
    monkeypatch.setattr(vpn_client.GluetunClient, "usable",
                        lambda self, p, v: current["ip"] == "2.2.2.2")

    out = c.switch_until_usable(proxy_url="http://vpn:8888", verify_url="u",
                                prefer_new_ip=False, attempts=3)
    assert out.ok and out.ip == "2.2.2.2"
    assert out.quarantined == ["9.9.9.9"]
    assert c._is_quarantined("9.9.9.9")


def test_a_quarantined_exit_is_skipped_without_being_probed(monkeypatch, tmp_path):
    c = _client(tmp_path)
    c._quarantine("9.9.9.9")
    probed = []
    monkeypatch.setattr(vpn_client.GluetunClient, "current_ip", lambda self: "9.9.9.9")
    monkeypatch.setattr(vpn_client.GluetunClient, "_restart_and_wait", lambda self: None)
    monkeypatch.setattr(vpn_client.GluetunClient, "usable",
                        lambda self, p, v: probed.append(True) or True)

    out = c.switch_until_usable(proxy_url="http://vpn:8888", verify_url="u",
                                prefer_new_ip=False, attempts=2)
    assert not out.ok and probed == []


def test_quarantine_expires(tmp_path):
    c = _client(tmp_path, quarantine_ttl=-1)   # already expired on write
    c._quarantine("9.9.9.9")
    assert not c._is_quarantined("9.9.9.9")


def test_giving_up_reports_every_exit_it_tried(monkeypatch, tmp_path):
    c = _client(tmp_path)
    ips = iter(["1.1.1.1"] * 8)
    monkeypatch.setattr(vpn_client.GluetunClient, "current_ip", lambda self: next(ips))
    monkeypatch.setattr(vpn_client.GluetunClient, "_restart_and_wait", lambda self: None)
    monkeypatch.setattr(vpn_client.GluetunClient, "usable", lambda self, p, v: False)

    out = c.switch_until_usable(proxy_url="http://vpn:8888", verify_url="u",
                                prefer_new_ip=True, attempts=3)
    assert not out.ok and out.attempts == 3
    assert len(out.tried) == 3 and all(ok is False for _ip, ok in out.tried)


def test_a_tunnel_with_no_ip_is_not_usable(monkeypatch, tmp_path):
    c = _client(tmp_path)
    monkeypatch.setattr(vpn_client.GluetunClient, "current_ip", lambda self: None)
    monkeypatch.setattr(vpn_client.GluetunClient, "_restart_and_wait", lambda self: None)
    out = c.switch_until_usable(proxy_url="http://vpn:8888", verify_url="u", attempts=2)
    assert not out.ok and "no public IP" in out.reason


def test_a_slow_exit_is_re_probed_before_being_condemned(monkeypatch, tmp_path):
    """gluetun publishes an IP before the tunnel reliably carries traffic.

    Probing the instant an IP appears condemned 8 exits in minutes, 5 of which
    had already served production — one of them 7 times. One failure is not
    evidence; two, with a gap, is.
    """
    c = _client(tmp_path, verify_settle=0)
    probes = []
    monkeypatch.setattr(vpn_client.GluetunClient, "current_ip", lambda self: "1.1.1.1")
    monkeypatch.setattr(vpn_client.GluetunClient, "_restart_and_wait", lambda self: None)
    monkeypatch.setattr(vpn_client.GluetunClient, "usable",
                        lambda self, p, v: len(probes) > 0 or probes.append(1))

    out = c.switch_until_usable(proxy_url="http://vpn:8888", verify_url="u",
                                prefer_new_ip=False, attempts=1)
    assert out.ok, "second probe succeeded, so the exit must not be condemned"
    assert out.quarantined == []
    assert not c._is_quarantined("1.1.1.1")


def test_an_exit_failing_twice_is_still_quarantined(monkeypatch, tmp_path):
    c = _client(tmp_path, verify_settle=0)
    monkeypatch.setattr(vpn_client.GluetunClient, "current_ip", lambda self: "9.9.9.9")
    monkeypatch.setattr(vpn_client.GluetunClient, "_restart_and_wait", lambda self: None)
    monkeypatch.setattr(vpn_client.GluetunClient, "usable", lambda self, p, v: False)

    out = c.switch_until_usable(proxy_url="http://vpn:8888", verify_url="u",
                                prefer_new_ip=False, attempts=1)
    assert not out.ok and c._is_quarantined("9.9.9.9")
