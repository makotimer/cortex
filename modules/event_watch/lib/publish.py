"""Emit messages onto events:<site>.

Decides nothing about *what* to send — the engine does that. This module only
knows how to put a decided message on the bus, and how to not send anything at
all in dry-run.
"""
from __future__ import annotations

from typing import Any

from . import logging_bridge

EVENT_UPSERT = "event.upsert"
EVENT_CANCEL = "event.cancel"
#: Per-run summary so a quiet run is distinguishable from a dead injector.
#: The site rejects unknown message types permanently (worker.py), so this must
#: not be emitted against a site that does not handle it yet.
INGEST_REPORT = "ingest.report"

SCHEMA_VERSION = "1"
SOURCE = "cortex.discoverbcs-ingest"


class Publisher:
    """Thin wrapper over EventBus with a dry-run mode.

    The bus is imported lazily so that normalization and reconciliation stay
    testable on a host with no eventbus-kit on the path.
    """

    def __init__(self, site: str, *, dry_run: bool = False) -> None:
        self.site = site
        self.dry_run = dry_run
        self._bus: Any = None
        self._stream: str | None = None
        self.sent: list[tuple[str, dict]] = []

    def _connect(self) -> tuple[Any, str]:
        if self._bus is None:
            from eventbus import EventBus, events_stream

            self._bus = EventBus.from_env(source=SOURCE)
            self._stream = events_stream(self.site)
        assert self._stream is not None
        return self._bus, self._stream

    def _emit(self, type_: str, payload: dict, correlation_id: str | None = None) -> str | None:
        self.sent.append((type_, payload))
        if self.dry_run:
            logging_bridge.activity({
                "component": "event_watch.publish",
                "op": "dry_run_emit",
                "type": type_,
                "correlation_id": correlation_id,
            })
            return None
        bus, stream = self._connect()
        msg_id: str = bus.publish(stream, type_, payload=payload, correlation_id=correlation_id)
        return msg_id

    def upsert(self, payload: dict) -> str | None:
        return self._emit(EVENT_UPSERT, payload, correlation_id=_corr(payload))

    def cancel(self, payload: dict) -> str | None:
        return self._emit(EVENT_CANCEL, payload, correlation_id=_corr(payload))

    def report(self, source_slug: str, window: dict, counts: dict, ran_at: str) -> str | None:
        """One summary per run — including runs that changed nothing."""
        return self._emit(INGEST_REPORT, {
            "schema_version": SCHEMA_VERSION,
            "source": {"slug": source_slug},
            "window": window,
            "counts": counts,
            "ran_at": ran_at,
        })


def _corr(payload: dict) -> str | None:
    """Correlation id from the idempotency pair, so retries are traceable."""
    series = (payload.get("series") or {}).get("source_series_uid")
    occ = (payload.get("occurrence") or {}).get("source_occurrence_tid")
    if series and occ:
        return f"{series}|{occ}"
    return None
