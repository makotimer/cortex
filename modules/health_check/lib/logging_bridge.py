from __future__ import annotations

from modules._shared.logging_bridge import make

activity, error = make("modules.health_check", "health_check")

__all__ = ["activity", "error"]
