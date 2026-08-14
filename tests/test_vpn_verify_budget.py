# tests/test_vpn_verify_budget.py
"""_verify_until_ready() — retry to a budget instead of one fixed sleep.

A probe ladder across 215 exits found ~97% carrying traffic on the first probe
(median 0.34 s), but the tail ran to 36 s. The old fixed 6 s sleep gave every
exit exactly two chances, which condemned that tail — healthy servers,
quarantined for 30 minutes, shrinking the pool the next attempt draws from.
"""
import time

from modules._shared import vpn_client


def _client(tmp_path, **kw):
    return vpn_client.GluetunClient(control_url="http://vpn:8000", **kw)


def _usable_after(n_failures, calls):
    """usable() that fails n times, then succeeds. Records each call."""
    def _inner(self, proxy, verify):
        calls.append(time.monotonic())
        return len(calls) > n_failures
    return _inner


def test_first_probe_success_costs_nothing(monkeypatch, tmp_path):
    """The 97% case must not pay for the tail's budget."""
    calls = []
    monkeypatch.setattr(vpn_client.GluetunClient, "usable", _usable_after(0, calls))
    c = _client(tmp_path, verify_settle=5, verify_deadline=45)
    started = time.monotonic()
    assert c._verify_until_ready("http://vpn:8888", "u") is True
    assert len(calls) == 1
    assert time.monotonic() - started < 0.5, "must not sleep when the first probe works"


def test_late_exit_is_rescued_not_condemned(monkeypatch, tmp_path):
    """Three failures then success — the case a single retry threw away."""
    calls = []
    monkeypatch.setattr(vpn_client.GluetunClient, "usable", _usable_after(3, calls))
    c = _client(tmp_path, verify_settle=0.01, verify_deadline=45)
    assert c._verify_until_ready("http://vpn:8888", "u") is True
    assert len(calls) == 4


def test_old_fixed_settle_would_have_failed_this(monkeypatch, tmp_path):
    """Pin the regression: two probes is not enough for the measured tail."""
    calls = []
    monkeypatch.setattr(vpn_client.GluetunClient, "usable", _usable_after(3, calls))
    two_probes_only = _client(tmp_path, verify_settle=0.01, verify_deadline=0.02)
    assert two_probes_only._verify_until_ready("http://vpn:8888", "u") is False


def test_budget_is_respected(monkeypatch, tmp_path):
    """A genuinely dead exit must not burn more than the budget."""
    monkeypatch.setattr(vpn_client.GluetunClient, "usable", lambda s, p, v: False)
    c = _client(tmp_path, verify_settle=0.05, verify_deadline=0.2)
    started = time.monotonic()
    assert c._verify_until_ready("http://vpn:8888", "u") is False
    assert time.monotonic() - started < 1.0


def test_probe_cap_stops_a_zero_gap_spinning(monkeypatch, tmp_path):
    """verify_settle=0 with a long deadline must not loop unboundedly."""
    calls = []
    monkeypatch.setattr(vpn_client.GluetunClient, "usable", _usable_after(999, calls))
    c = _client(tmp_path, verify_settle=0, verify_deadline=3600)
    assert c._verify_until_ready("http://vpn:8888", "u") is False
    assert len(calls) == vpn_client.VERIFY_MAX_PROBES


def test_rotate_timeout_covers_the_measured_worst_case():
    """Clean switches maxed at 78.4 s across 215 surveyed exits."""
    assert vpn_client.DEFAULT_ROTATE_TIMEOUT >= 100
