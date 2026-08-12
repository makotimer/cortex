from __future__ import annotations

from modules._shared.logging_bridge import make

activity, error = make("modules.event_watch", "event_watch")

__all__ = ["activity", "error"]
