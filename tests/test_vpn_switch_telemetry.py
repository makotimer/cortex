# tests/test_vpn_switch_telemetry.py
"""Production must report its own switch latency.

Switch latency was only ever recorded by ``rotate()``, which no production code
path calls -- both engines use ``switch_until_usable()``, which restarts via
``_restart_and_wait()``. So ``last_rotate_seconds`` and ``last_rotate_polls``
were permanently None/0 in production, and the only way to learn the real
distribution was to run a separate overnight survey against the live tunnel.

Surveying 457 exits put clean switches at a 14 s median, a 46 s p90, and a
~4.5% rate of restarts that never produced an IP at all. None of that is
visible from the running system. These tests pin the telemetry that makes it
visible, so the next tuning decision reads production instead of a survey.
"""
import pytest

from modules._shared import vpn_client


class _StubResponse:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None


def _client(tmp_path, **kw):
    return vpn_client.GluetunClient(
        control_url="http://vpn:8000",
        quarantine_path=str(tmp_path / "q.json"), **kw)


@pytest.fixture
def quiet_tunnel(monkeypatch):
    """Stub the control server so restarts are instant and always accepted."""
    monkeypatch.setattr(vpn_client.requests, "put", lambda *a, **kw: _StubResponse())
    monkeypatch.setattr(vpn_client.time, "sleep", lambda _s: None)


def test_switch_records_how_long_the_restart_took(quiet_tunnel, monkeypatch, tmp_path):
    """A successful switch must report its restart latency, not discard it."""
    ips = iter(["1.1.1.1", "2.2.2.2", "2.2.2.2"])
    monkeypatch.setattr(vpn_client.GluetunClient, "current_ip", lambda self: next(ips))
    monkeypatch.setattr(vpn_client.GluetunClient, "usable", lambda s, p, v: True)

    out = _client(tmp_path).switch_until_usable(
        proxy_url="http://vpn:8888", verify_url="u", prefer_new_ip=True)

    assert out.ok is True
    assert len(out.restarts) == 1, "one restart was performed, so one should be reported"
    assert out.restarts[0] >= 0


def test_switch_records_a_restart_that_never_came_up(quiet_tunnel, monkeypatch, tmp_path):
    """The ~4.5% dead-rotation case is the one worth measuring -- record it too."""
    monkeypatch.setattr(vpn_client.GluetunClient, "current_ip", lambda self: None)

    out = _client(tmp_path, rotate_timeout=0.05).switch_until_usable(
        proxy_url="http://vpn:8888", verify_url="u", attempts=1, prefer_new_ip=True)

    assert out.ok is False
    assert len(out.restarts) == 1, "a restart that timed out is still a restart"
    assert out.restarts[0] >= 0


def test_every_attempt_contributes_a_restart(quiet_tunnel, monkeypatch, tmp_path):
    """Three failed attempts must be distinguishable from one slow one."""
    monkeypatch.setattr(vpn_client.GluetunClient, "current_ip", lambda self: None)

    out = _client(tmp_path, rotate_timeout=0.01).switch_until_usable(
        proxy_url="http://vpn:8888", verify_url="u", attempts=3, prefer_new_ip=True)

    assert out.attempts == 3
    assert len(out.restarts) == 3


def test_rotate_is_gone(tmp_path):
    """Regression pin: rotate() required the IP to *change* to count as success.

    That is what produced the false failures -- a reconnect to the same server
    is a perfectly good outcome. It had no production caller by 2026-08-13 and
    is removed so it cannot be reintroduced into the hot path by accident.
    """
    assert not hasattr(vpn_client.GluetunClient, "rotate")
