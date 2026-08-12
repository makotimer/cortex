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


def _reply_email(body: str, subject: str = "COMMAND",
                 from_addr: str = "admin@example.com") -> bytes:
    """A real-world reply: command lives in the BODY; the subject carries no command."""
    return (f"Subject: {subject}\r\nFrom: {from_addr}\r\n"
            f"Content-Type: text/plain\r\n\r\n{body}\r\n").encode()


def test_approve_in_body_with_glued_proton_quote_publishes(bus, monkeypatch):
    # Reproduces the real Proton reply that broke the chain: the user's typed
    # command is glued directly onto the quoted draft (no separator) and the
    # subject has no command keyword. The decision must still publish, and the
    # quoted "DENY ..." instruction line below must NOT be matched.
    monkeypatch.setattr("service.site_events.get_bus", lambda: bus)
    cfg = {"approval_allowlist": ["admin@example.com"]}
    bus.ensure_group(events_stream("pbd"), "pbd")

    body = (
        "APPROVE pbd-deck-builder-waco-------- Original Message --------"
        "On Thursday, 06/11/26 at 06:16 Ben Price <ben@pricefam.email> wrote:"
        "To publish, send an email with subject: APPROVE pbd-deck-builder-waco\r\n"
        "To reject: DENY pbd-deck-builder-waco\r\n"
        "How to hire a deck builder in Waco, TX ...\r\n"
    )
    to, subj, html = handlers.handle_command(
        _reply_email(body), cfg, None, from_addr="admin@example.com")

    msgs = bus.read(events_stream("pbd"), "pbd", "c1", block_ms=10)
    assert len(msgs) == 1
    assert msgs[0].payload == {"decision": "approve", "token": "pbd-deck-builder-waco",
                               "approver": "admin@example.com"}


def test_command_in_body_overrides_unrelated_subject(bus, monkeypatch):
    # Subject is unreliable on replies ("Re: <draft title>"); the body is the source.
    monkeypatch.setattr("service.site_events.get_bus", lambda: bus)
    cfg = {"approval_allowlist": ["admin@example.com"]}
    bus.ensure_group(events_stream("hs"), "hs")

    body = ("DENY hs-zzz\r\n\r\n"
            "On Thu Ben Price wrote:\r\nplease review this draft\r\n")
    handlers.handle_command(
        _reply_email(body, subject="Re: [hs] Article draft: Something"),
        cfg, None, from_addr="admin@example.com")

    msgs = bus.read(events_stream("hs"), "hs", "c1", block_ms=10)
    assert len(msgs) == 1
    assert msgs[0].payload["decision"] == "deny"
    assert msgs[0].payload["token"] == "hs-zzz"


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
