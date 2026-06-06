# service/email_outbound.py
"""Outbound email worker: consume the `email.send` stream and deliver via Proton.

Mirrors service.imap_listener's thread/controller pattern. Runs as a background
thread started by `service.cli` during `serve`. At-least-once: a failed message
stays pending and is retried via XAUTOCLAIM; after MAX_ATTEMPTS it is moved to
the `email.send.dead` stream and acked so it cannot block the group.
"""
from __future__ import annotations

import logging
import os
import socket
import threading
import time
from typing import Any

from eventbus import EMAIL_SEND, EMAIL_SEND_DEAD, EventBus, Message

from service import emailer
from service.emailer import EmailSendError

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.addHandler(logging.NullHandler())

GROUP = "cortex-emailer"
MAX_ATTEMPTS = 3
_MIN_IDLE_MS = int(os.getenv("EMAIL_OUTBOUND_RETRY_IDLE_MS", "10000"))


class InvalidMessage(Exception):  # noqa: N818
    """The message payload is missing required fields (never retried)."""


def _dry_run() -> bool:
    return os.getenv("CORTEX_DRY_RUN") == "1" or os.getenv("SEND_EMAIL") == "0"


def handle_message(msg: Message) -> None:
    """Send one email. Raises InvalidMessage (permanent) or EmailSendError (transient)."""
    p: dict[str, Any] = msg.payload or {}
    to = p.get("to") or []
    subject = p.get("subject")
    html = p.get("html")
    if not to or not subject or not html:
        raise InvalidMessage(f"missing to/subject/html in {msg.id}")

    if _dry_run():
        logger.info("[email-out] DRY-RUN would send to=%s subject=%r", to, subject)
        return

    emailer.send_html(subject=subject, html=html, to=to, cc=p.get("cc"), bcc=p.get("bcc"))
    logger.info("[email-out] sent to=%s subject=%r corr=%s", to, subject, msg.correlation_id)


def _dispatch(bus: EventBus, msg: Message) -> None:
    """Run the handler for one message; ack on success, dead-letter on terminal failure."""
    try:
        handle_message(msg)
        # at-least-once: a crash here (after send, before ack) re-delivers the message
        # on the next claim_stale pass, so a recipient could receive a duplicate email.
        bus.ack(EMAIL_SEND, GROUP, msg.id)
    except InvalidMessage:
        logger.error("[email-out] invalid message %s -> dead", msg.id, exc_info=True)
        bus.to_dead(EMAIL_SEND_DEAD, msg)
        bus.ack(EMAIL_SEND, GROUP, msg.id)
    except EmailSendError:
        attempts = bus.delivery_count(EMAIL_SEND, GROUP, msg.id)
        if attempts >= MAX_ATTEMPTS:
            logger.error("[email-out] message %s failed %d attempts -> dead", msg.id, attempts)
            bus.to_dead(EMAIL_SEND_DEAD, msg)
            bus.ack(EMAIL_SEND, GROUP, msg.id)
        else:
            logger.warning("[email-out] send failed (attempt %d) for %s; will retry",
                           attempts, msg.id)
            # leave unacked: a later claim_stale pass redelivers it


def process_once(bus: EventBus, consumer: str, *, block_ms: int = 0) -> None:
    """One reclaim+read cycle. Used by the loop (block_ms>0 to pace) and by tests
    (block_ms=0 for a non-blocking single pass)."""
    bus.ensure_group(EMAIL_SEND, GROUP)
    for msg in bus.claim_stale(EMAIL_SEND, GROUP, consumer, min_idle_ms=_MIN_IDLE_MS):
        _dispatch(bus, msg)
    for msg in bus.read(EMAIL_SEND, GROUP, consumer, count=10, block_ms=block_ms):
        _dispatch(bus, msg)


# --------------------------------------------------------------------------- #
# Thread controller (mirrors imap_listener)
# --------------------------------------------------------------------------- #
_thread: threading.Thread | None = None
_stop_event = threading.Event()


class WorkerController:
    def __init__(self, thread: threading.Thread, stop_event: threading.Event):
        self._thread = thread
        self._stop = stop_event

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)


def start(bus: EventBus | None = None) -> WorkerController:
    global _thread
    if _thread and _thread.is_alive():
        logger.info("[email-out] worker already running")
        return WorkerController(_thread, _stop_event)
    _stop_event.clear()
    consumer = os.getenv("HOSTNAME", socket.gethostname() or "cortex")
    t = threading.Thread(
        target=_loop, name="email-outbound", args=(bus, consumer, _stop_event), daemon=True,
    )
    t.start()
    _thread = t
    return WorkerController(t, _stop_event)


def stop() -> None:
    """Module-level convenience to stop the worker loop (mirrors imap_listener)."""
    _stop_event.set()


def _loop(bus: EventBus | None, consumer: str, stop_event: threading.Event) -> None:
    backoff = 5
    while not stop_event.is_set():
        try:
            if bus is None:
                bus = EventBus.from_env(source="cortex")
            backoff = 5
            while not stop_event.is_set():
                # block_ms>0 makes the read wake periodically so we can observe stop_event.
                process_once(bus, consumer, block_ms=5000)
        except Exception as e:
            if stop_event.is_set():
                break
            logger.error("[email-out] loop error: %r; retrying in %ds", e, backoff)
            bus = None  # force reconnect
            slept = 0
            while slept < backoff and not stop_event.is_set():
                time.sleep(1)
                slept += 1
            backoff = min(backoff * 2, 120)
    logger.info("[email-out] worker stopped")
