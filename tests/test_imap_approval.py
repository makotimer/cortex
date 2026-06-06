import fakeredis
import pytest

from eventbus import EventBus, events_stream
from service.imap_commands import handlers


@pytest.fixture
def bus():
    return EventBus(fakeredis.FakeStrictRedis(decode_responses=True), source="cortex")


def _email(subject: str) -> bytes:
    return (f"Subject: {subject}\r\nFrom: admin@example.com\r\n"
            f"Content-Type: text/plain\r\n\r\n{subject}\r\n").encode()


def test_approve_from_allowlisted_sender_publishes_decision(bus, monkeypatch):
    monkeypatch.setattr("service.site_events.get_bus", lambda: bus)
    cfg = {"approval_allowlist": ["admin@example.com"]}
    bus.ensure_group(events_stream("hs"), "hs")

    to, subj, html = handlers.handle_command(
        _email("APPROVE hs-abc123"), cfg, None, from_addr="admin@example.com")

    msgs = bus.read(events_stream("hs"), "hs", "c1", block_ms=10)
    assert len(msgs) == 1
    assert msgs[0].type == "registration.decision"
    assert msgs[0].payload == {"decision": "approve", "token": "hs-abc123",
                               "approver": "admin@example.com"}
    assert "approve" in (html or "").lower()


def test_decision_from_non_allowlisted_sender_is_rejected(bus, monkeypatch):
    monkeypatch.setattr("service.site_events.get_bus", lambda: bus)
    cfg = {"approval_allowlist": ["admin@example.com"]}
    bus.ensure_group(events_stream("hs"), "hs")

    to, subj, html = handlers.handle_command(
        _email("APPROVE hs-abc123"), cfg, None, from_addr="stranger@evil.com")

    assert bus.read(events_stream("hs"), "hs", "c1", block_ms=10) == []
    assert "not authorized" in (html or "").lower()


def test_deny_publishes_deny_decision(bus, monkeypatch):
    monkeypatch.setattr("service.site_events.get_bus", lambda: bus)
    cfg = {"approval_allowlist": ["admin@example.com"]}
    bus.ensure_group(events_stream("hs"), "hs")

    handlers.handle_command(_email("DENY hs-zzz"), cfg, None, from_addr="admin@example.com")
    msgs = bus.read(events_stream("hs"), "hs", "c1", block_ms=10)
    assert msgs[0].payload["decision"] == "deny"
