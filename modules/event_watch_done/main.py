"""event_watch_done — tell the site the morning's event_watch runs are over.

The 29 event_watch jobs are separate scheduled runs (Tue/Fri 01:10 to 01:36) and
nothing marks the last one; the site wants a single "all done" so it can
review what came in as one batch. This job publishes ``watch.complete`` on
``events:<site>`` once, from its own schedule slot after the last scrape.
The worker does the reviewing; this decides nothing.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

WATCH_COMPLETE = "watch.complete"
SCHEMA_VERSION = "1"
SOURCE = "cortex.discoverbcs-ingest"
DEFAULT_SITE = "discoverbcs"
LOCAL_TZ = ZoneInfo("America/Chicago")


def payload(site: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(LOCAL_TZ)
    local = now.astimezone(LOCAL_TZ)
    return {
        "schema_version": SCHEMA_VERSION,
        "site": site,
        "run_date": local.date().isoformat(),
        "completed_at": local.isoformat(timespec="seconds"),
    }


def emit(site: str, *, bus: Any = None) -> str:
    """Publish one watch.complete. `bus` is injectable for tests; the real
    one is imported lazily so the module imports on a host without the kit."""
    if bus is None:
        from eventbus import EventBus

        bus = EventBus.from_env(source=SOURCE)
    from eventbus import events_stream

    stream = events_stream(site)
    msg_id: str = bus.publish(stream, WATCH_COMPLETE, payload=payload(site))
    return msg_id


def run(**kwargs: Any) -> str | None:
    """Cortex module entry point. Returns None: nothing here wants a human."""
    site = str(kwargs.get("site") or DEFAULT_SITE)
    emit(site)
    return None
