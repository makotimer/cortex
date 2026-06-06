import pytest
from eventbus import EMAIL_SEND, EMAIL_SEND_DEAD

from service import email_outbound
from service.emailer import EmailSendError

GROUP = "cortex-emailer"


def _publish(bus, payload):
    bus.ensure_group(EMAIL_SEND, GROUP)
    return bus.publish(EMAIL_SEND, "email.send", payload=payload, correlation_id="c-1")


def test_handle_message_sends_email(bus, stub_emailer, monkeypatch):
    monkeypatch.setenv("CORTEX_DRY_RUN", "0")
    monkeypatch.setenv("SEND_EMAIL", "1")
    _publish(bus, {"to": ["a@b.com"], "subject": "Hi", "html": "<p>x</p>"})
    msg = bus.read(EMAIL_SEND, GROUP, "c1", block_ms=10)[0]

    email_outbound.handle_message(msg)

    assert len(stub_emailer.sent["messages"]) == 1
    sent = stub_emailer.sent["messages"][0]
    assert sent["to"] == ["a@b.com"]
    assert sent["subject"] == "Hi"
    assert sent["html"] == "<p>x</p>"


def test_handle_message_dry_run_does_not_send(bus, stub_emailer, monkeypatch):
    monkeypatch.setenv("CORTEX_DRY_RUN", "1")
    _publish(bus, {"to": ["a@b.com"], "subject": "Hi", "html": "<p>x</p>"})
    msg = bus.read(EMAIL_SEND, GROUP, "c1", block_ms=10)[0]

    email_outbound.handle_message(msg)

    assert stub_emailer.sent["messages"] == []   # suppressed


def test_handle_message_missing_fields_raises(bus, monkeypatch):
    monkeypatch.setenv("CORTEX_DRY_RUN", "0")
    monkeypatch.setenv("SEND_EMAIL", "1")
    _publish(bus, {"subject": "no recipients"})   # no 'to'/'html'
    msg = bus.read(EMAIL_SEND, GROUP, "c1", block_ms=10)[0]

    with pytest.raises(email_outbound.InvalidMessage):
        email_outbound.handle_message(msg)


def test_process_once_acks_on_success(bus, stub_emailer, monkeypatch):
    monkeypatch.setenv("CORTEX_DRY_RUN", "0")
    monkeypatch.setenv("SEND_EMAIL", "1")
    mid = _publish(bus, {"to": ["a@b.com"], "subject": "Hi", "html": "<p>x</p>"})

    email_outbound.process_once(bus, "c1")

    assert bus.delivery_count(EMAIL_SEND, GROUP, mid) == 0   # acked


def test_invalid_message_dead_lettered_immediately(bus, monkeypatch):
    monkeypatch.setenv("CORTEX_DRY_RUN", "0")
    monkeypatch.setenv("SEND_EMAIL", "1")
    _publish(bus, {"subject": "no recipients"})   # missing 'to'/'html'

    email_outbound.process_once(bus, "c1")

    assert bus.r.xlen(EMAIL_SEND_DEAD) == 1
    assert bus.r.xpending(EMAIL_SEND, GROUP)["pending"] == 0


def test_process_once_dead_letters_after_three_failed_sends(bus, monkeypatch):
    monkeypatch.setenv("CORTEX_DRY_RUN", "0")
    monkeypatch.setenv("SEND_EMAIL", "1")
    # claim_stale reclaims immediately so the 3 retries happen within the test.
    # _MIN_IDLE_MS is a module constant read at import, so patch the attribute.
    monkeypatch.setattr(email_outbound, "_MIN_IDLE_MS", 0)

    def boom(**kwargs):
        raise EmailSendError("smtp down")
    monkeypatch.setattr("service.emailer.send_html", boom)

    _publish(bus, {"to": ["a@b.com"], "subject": "Hi", "html": "<p>x</p>"})

    for _ in range(3):
        email_outbound.process_once(bus, "c1")

    assert bus.r.xlen(EMAIL_SEND_DEAD) == 1
    pending = bus.r.xpending(EMAIL_SEND, GROUP)
    assert pending["pending"] == 0
