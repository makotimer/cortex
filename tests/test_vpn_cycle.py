# tests/test_vpn_cycle.py
"""modules.vpn_cycle -- rotate the tunnel while nothing is scraping.

Rotation used to sit at the front of every scrape, where it had no time to
fail: 28 s expected cost, a 6.36% chance of producing no IP at all, and the
scrape blocked behind it. Worse, ``_restart_and_wait()`` accepts an unchanged
IP as success -- demanding a change is what caused the old false failures -- so
1.54% of restarts landed on the exit the previous run had just used, presenting
every source the same address twice running.

Moving rotation into the 90-minute dormant window between scrapes inverts both
problems. With hours of budget instead of seconds, the cycler can *require* a
different IP and retry until it gets one, and the scrape starts on an exit that
is already settled and verified.

So the client grows ``require_new_ip``, off by default: scrapers keep today's
forgiving behaviour, and only the cycler -- which can afford it -- demands a
change.
"""
import pytest

from modules._shared import vpn_client


class _StubResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None


@pytest.fixture
def quiet_tunnel(monkeypatch):
    monkeypatch.setattr(vpn_client.requests, "put", lambda *a, **kw: _StubResponse())
    monkeypatch.setattr(vpn_client.time, "sleep", lambda _s: None)
    monkeypatch.setattr(vpn_client.GluetunClient, "usable", lambda s, p, v: True)


def _client(**kw):
    return vpn_client.GluetunClient(control_url="http://vpn:8000", **kw)


# ----------------------------------------------------------------------
# require_new_ip on the client
# ----------------------------------------------------------------------
def test_require_new_ip_rejects_an_unchanged_exit(quiet_tunnel, monkeypatch):
    """The whole point: a reconnect to the same exit is not a rotation."""
    monkeypatch.setattr(vpn_client.GluetunClient, "current_ip", lambda self: "1.1.1.1")

    out = _client().switch_until_usable(
        proxy_url="http://vpn:8888", verify_url="u", attempts=3,
        prefer_new_ip=True, require_new_ip=True)

    assert out.ok is False
    assert out.attempts == 3, "it must keep trying rather than accept the same exit"
    assert "same exit" in out.reason


def test_require_new_ip_accepts_the_first_genuinely_new_exit(quiet_tunnel, monkeypatch):
    ips = iter(["1.1.1.1", "1.1.1.1", "1.1.1.1", "1.1.1.1", "2.2.2.2", "2.2.2.2"])
    monkeypatch.setattr(vpn_client.GluetunClient, "current_ip", lambda self: next(ips))

    out = _client().switch_until_usable(
        proxy_url="http://vpn:8888", verify_url="u", attempts=3,
        prefer_new_ip=True, require_new_ip=True)

    assert out.ok is True
    assert out.ip == "2.2.2.2"
    assert out.changed is True
    assert out.previous_ip == "1.1.1.1"


def test_require_new_ip_is_off_by_default(quiet_tunnel, monkeypatch):
    """Scrapers must be provably unaffected.

    Requiring a change under time pressure is what produced the old false
    failures, so the scrape path keeps accepting a working tunnel on the same
    exit. Only the cycler, which has 90 minutes, asks for more.
    """
    monkeypatch.setattr(vpn_client.GluetunClient, "current_ip", lambda self: "1.1.1.1")

    out = _client().switch_until_usable(
        proxy_url="http://vpn:8888", verify_url="u", attempts=3, prefer_new_ip=True)

    assert out.ok is True, "an unchanged but working exit is still a success"
    assert out.ip == "1.1.1.1"


# ----------------------------------------------------------------------
# the module itself
# ----------------------------------------------------------------------
def _patch_switch(monkeypatch, outcome, seen=None):
    def _switch(self, **kwargs):
        if seen is not None:
            seen.append(kwargs)
        return outcome
    monkeypatch.setattr(vpn_client.GluetunClient, "switch_until_usable", _switch)


def test_cycle_asks_for_a_new_ip_and_stays_silent_on_success(monkeypatch):
    """Runs ~10x a day: success must not generate mail."""
    from modules import vpn_cycle
    seen: list[dict] = []
    _patch_switch(monkeypatch, vpn_client.SwitchOutcome(
        ok=True, ip="2.2.2.2", changed=True, attempts=1, reason="verified"), seen)

    assert vpn_cycle.run(proxy_url="http://vpn:8888") is None
    assert seen and seen[0]["require_new_ip"] is True
    assert seen[0]["prefer_new_ip"] is True


def test_cycle_raises_when_it_cannot_get_a_new_exit(monkeypatch):
    """A failed cycle must be loud -- it is the only signal the scrape has."""
    from modules import vpn_cycle
    _patch_switch(monkeypatch, vpn_client.SwitchOutcome(
        ok=False, attempts=5, reason="every attempt returned the same exit"))

    with pytest.raises(vpn_cycle.VPNCycleError, match="same exit"):
        vpn_cycle.run(proxy_url="http://vpn:8888")


def test_cycle_does_nothing_when_no_proxy_is_configured(monkeypatch):
    """Matches the engines: an unconfigured VPN is not a failure."""
    from modules import vpn_cycle

    def _boom(self, **kwargs):
        raise AssertionError("must not touch the tunnel with no proxy configured")

    monkeypatch.setattr(vpn_client.GluetunClient, "switch_until_usable", _boom)
    monkeypatch.delenv("CAREER_WATCH_PROXY_URL", raising=False)
    monkeypatch.delenv("VPN_PROXY_URL", raising=False)

    assert vpn_cycle.run() is None


def test_cycle_logs_what_it_did(monkeypatch):
    from modules import vpn_cycle
    events: list[dict] = []
    _patch_switch(monkeypatch, vpn_client.SwitchOutcome(
        ok=True, ip="2.2.2.2", previous_ip="1.1.1.1", changed=True, attempts=2,
        seconds=31.4, reason="verified", restarts=[14.3, 16.1]))
    monkeypatch.setattr(vpn_cycle.logging_bridge, "activity", events.append)

    vpn_cycle.run(proxy_url="http://vpn:8888")

    cycles = [e for e in events if e.get("op") == "vpn_cycle"]
    assert cycles, "the cycle must be logged"
    assert cycles[0]["ok"] is True
    assert cycles[0]["ip"] == "2.2.2.2"
    assert cycles[0]["restarts"] == [14.3, 16.1]
    # The pair is the whole point of the job: it is the evidence that the next
    # scrape will not present the address the last one used.
    assert cycles[0]["previous_ip"] == "1.1.1.1"


def test_cycle_attempts_default_is_generous(monkeypatch):
    """Nothing waits on this, so it should try harder than a scrape would."""
    from modules import vpn_cycle
    seen: list[dict] = []
    _patch_switch(monkeypatch, vpn_client.SwitchOutcome(
        ok=True, ip="2.2.2.2", changed=True, attempts=1, reason="verified"), seen)

    vpn_cycle.run(proxy_url="http://vpn:8888")

    assert seen[0]["attempts"] >= 5
