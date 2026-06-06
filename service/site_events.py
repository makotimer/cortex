"""Publish inbound IMAP decisions onto a site's event stream."""
from __future__ import annotations

from eventbus import REGISTRATION_DECISION, EventBus, events_stream, site_from_token

_bus: EventBus | None = None


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus.from_env(source="cortex")
    return _bus


def publish_decision(token: str, decision: str, approver: str) -> str:
    """Route a decision (approve|deny) to events:<site> derived from the token prefix."""
    stream = events_stream(site_from_token(token))
    return get_bus().publish(
        stream, REGISTRATION_DECISION,
        payload={"decision": decision, "token": token, "approver": approver},
        correlation_id=token)
