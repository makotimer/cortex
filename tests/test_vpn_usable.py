# tests/test_vpn_usable.py
"""Regression tests for GluetunClient.usable().

The bug these exist for: HEAD was treated as authoritative for every status
except 405, and career_watch's default verify URL
(https://www.cloudflare.com/cdn-cgi/trace) answers HEAD with 404. So usable()
returned False for every exit on every attempt, quarantining healthy servers
and failing every run with "no usable VPN exit" while the tunnel was fine.
"""
import pytest

from modules._shared import vpn_client


class _Resp:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


@pytest.fixture
def client():
    return vpn_client.GluetunClient(control_url="http://vpn:8000")


def _patch(monkeypatch, head, get):
    """Wire head/get to return a status code, or raise if given an Exception."""
    def _call(outcome):
        def _inner(*a, **kw):
            if isinstance(outcome, Exception):
                raise outcome
            return _Resp(outcome)
        return _inner
    monkeypatch.setattr(vpn_client.requests, "head", _call(head))
    monkeypatch.setattr(vpn_client.requests, "get", _call(get))


@pytest.mark.parametrize("head_status", [404, 403, 405, 400, 500])
def test_bad_head_falls_through_to_get(client, monkeypatch, head_status):
    """Any unsuccessful HEAD must hand the verdict to GET, not just a 405.

    404 is the case that broke production: Cloudflare's trace endpoint.
    """
    _patch(monkeypatch, head=head_status, get=200)
    assert client.usable("http://vpn:8888", "https://example.test/trace") is True


def test_head_exception_still_tries_get(client, monkeypatch):
    """A HEAD that raises is not a verdict either — the proxy may just dislike it."""
    _patch(monkeypatch, head=OSError("proxy hates HEAD"), get=200)
    assert client.usable("http://vpn:8888", "https://example.test/trace") is True


def test_successful_head_short_circuits(client, monkeypatch):
    """The optimisation still has to work: a good HEAD means no GET at all."""
    calls = []
    monkeypatch.setattr(vpn_client.requests, "head",
                        lambda *a, **kw: _Resp(200))
    monkeypatch.setattr(vpn_client.requests, "get",
                        lambda *a, **kw: calls.append(1) or _Resp(200))
    assert client.usable("http://vpn:8888", "https://example.test/") is True
    assert calls == [], "GET should not run when HEAD already succeeded"


def test_both_failing_is_unusable(client, monkeypatch):
    """A genuinely unreachable target must still read as unusable."""
    _patch(monkeypatch, head=502, get=502)
    assert client.usable("http://vpn:8888", "https://example.test/") is False


def test_both_raising_is_unusable(client, monkeypatch):
    _patch(monkeypatch, head=OSError("no route"), get=OSError("no route"))
    assert client.usable("http://vpn:8888", "https://example.test/") is False


def test_redirect_counts_as_usable(client, monkeypatch):
    """allow_redirects means a 3xx should not normally surface, but if it does
    it is still a reachable server, not a broken exit."""
    _patch(monkeypatch, head=301, get=200)
    assert client.usable("http://vpn:8888", "https://example.test/") is True
