# tests/test_vpn_no_quarantine.py
"""The quarantine is gone: nothing is remembered between attempts.

It guarded a failure mode that does not occur. Across 676 surveyed exits
(2026-08-13/14) the verify target failed on a live exit 0 times in 633 probes,
so the quarantine could never once have fired. Of 387 distinct exits, 148 seen
more than once, not one was bad every time it appeared -- which is what the
DEFAULT_QUARANTINE_TTL comment already said after 750 rotations, without drawing
the conclusion.

What it cost was real: remembering a "bad" exit for 30 minutes shrinks the pool
the next attempt draws from, during exactly the run that is already struggling,
and the pool re-serves servers constantly (148 of 387 exits came back). Every
failure the survey did find came from a switch that never produced an IP
(6.36%), which no amount of remembering helps.

Failing an exit still switches away from it. It just is not written down.

Note on what is RED here: the client only ever had a memory when a caller passed
``quarantine_path``, and the only callers that did were the two engines. So the
test that discriminates is the engine one -- the client-level tests below are
regression guards against the memory being reintroduced.
"""
import pytest

from modules._shared import vpn_client
from modules.career_watch.lib import engine


class _StubResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None


# ----------------------------------------------------------------------
# The production boundary: the engines are what configured the memory.
# ----------------------------------------------------------------------
def test_engine_does_not_configure_a_quarantine(
        fresh_settings, stub_scraper, monkeypatch):
    """career_watch wired VPN_QUARANTINE_PATH into every client it built."""
    built: list[dict] = []
    real_init = vpn_client.GluetunClient.__init__

    def _spy(self, **kwargs):
        built.append(kwargs)
        real_init(self, **kwargs)

    monkeypatch.setattr(vpn_client.GluetunClient, "__init__", _spy)
    monkeypatch.setattr(vpn_client.GluetunClient, "switch_until_usable",
                        lambda self, **kw: vpn_client.SwitchOutcome(
                            ok=True, ip="1.2.3.4", attempts=1, reason="verified"))

    engine.run_once(_vpn_settings(fresh_settings),
                    get_scraper=lambda kind: stub_scraper)

    assert built, "a client must have been constructed"
    for kwargs in built:
        assert "quarantine_path" not in kwargs
        assert "quarantine_ttl" not in kwargs


def _vpn_settings(fresh_settings, **kw):
    from modules.career_watch.lib import config as cw_config
    return cw_config.Settings.from_env_and_kwargs({
        "person_env": "Test User",
        "groups_path": fresh_settings.groups_path,
        "sqlite_path": fresh_settings.sqlite_path,
        "proxy_url": "http://vpn:8888",
        **kw,
    })


# ----------------------------------------------------------------------
# Regression guards: the memory must not come back.
# ----------------------------------------------------------------------
def test_no_quarantine_state_survives_the_client():
    client = vpn_client.GluetunClient(control_url="http://vpn:8000")
    for gone in ("_quarantine", "_is_quarantined", "_load_quarantine"):
        assert not hasattr(client, gone), f"{gone} should be gone"
    assert not hasattr(vpn_client, "DEFAULT_QUARANTINE_TTL")
    assert "quarantined" not in vpn_client.SwitchOutcome.__dataclass_fields__


def test_client_no_longer_accepts_a_quarantine_path():
    """Callers passing one must fail loudly rather than silently get no memory."""
    with pytest.raises(TypeError):
        vpn_client.GluetunClient(control_url="http://vpn:8000",
                                 quarantine_path="/tmp/q.json")


def test_a_failing_exit_still_ends_its_attempt_and_switches_on(monkeypatch):
    """Dropping the memory must not drop the switching-away."""
    monkeypatch.setattr(vpn_client.requests, "put", lambda *a, **kw: _StubResponse())
    monkeypatch.setattr(vpn_client.time, "sleep", lambda _s: None)
    monkeypatch.setattr(vpn_client.GluetunClient, "usable", lambda s, p, v: False)
    monkeypatch.setattr(vpn_client.GluetunClient, "current_ip", lambda self: "9.9.9.9")

    client = vpn_client.GluetunClient(control_url="http://vpn:8000",
                                      verify_settle=0, verify_deadline=0.01)
    out = client.switch_until_usable(proxy_url="http://vpn:8888", verify_url="u",
                                     attempts=3, prefer_new_ip=True)

    assert out.ok is False
    assert out.attempts == 3, "it must keep trying new exits"
    assert len(out.restarts) == 3
    assert "9.9.9.9" in out.reason
