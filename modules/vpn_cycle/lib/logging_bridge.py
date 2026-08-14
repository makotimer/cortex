from __future__ import annotations

from modules._shared.logging_bridge import make

activity, error = make("modules.vpn_cycle", "vpn_cycle")

__all__ = ["activity", "error"]
