from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from modules import event_watch_done


class _Bus:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str, dict]] = []

    def publish(self, stream, type_, *, payload=None, correlation_id=None):
        self.sent.append((stream, type_, payload or {}))
        return "1-0"


def test_payload_is_the_central_run_date():
    at = datetime(2026, 9, 19, 16, 30, tzinfo=ZoneInfo("UTC"))   # 11:30 Central, a Friday
    p = event_watch_done.main.payload("discoverbcs", at)
    assert p == {
        "schema_version": "1", "site": "discoverbcs",
        "run_date": "2026-09-19", "completed_at": "2026-09-19T11:30:00-05:00",
    }


def test_emit_publishes_one_watch_complete_on_the_site_stream():
    bus = _Bus()
    msg_id = event_watch_done.main.emit("discoverbcs", bus=bus)
    assert msg_id == "1-0"
    assert len(bus.sent) == 1
    stream, type_, payload = bus.sent[0]
    assert stream == "events:discoverbcs"
    assert type_ == "watch.complete"
    assert payload["site"] == "discoverbcs" and payload["run_date"]
