# modules/sonos/lib/client.py
from __future__ import annotations

import contextlib
from typing import Optional

from soco import SoCo


class SonosClient:
    """
    Minimal wrapper for a Sonos *coordinator* (single room or group master).

    Responsibilities:
      - connect to a coordinator by IP
      - provide a few convenient helpers used by tests and chime code
      - snapshot/restore is handled by SoCo's soco.snapshot.Snapshot directly
        (see your working Pytest)
    """

    def __init__(self, coordinator_ip_env: str, *, debug: bool = False):
        self._ip = str(coordinator_ip_env).strip()
        self.debug = bool(debug)
        self.coord = self._connect(self._ip)

    # --------------------------------------------------------------------- #
    # Connection
    # --------------------------------------------------------------------- #
    def _connect(self, ip: str) -> SoCo:
        try:
            c = SoCo(ip)
            _ = c.player_name  # validate connectivity early
            return c
        except Exception as e:
            raise RuntimeError(f"Cannot reach Sonos coordinator at {ip}: {e}") from e

    # --------------------------------------------------------------------- #
    # Lightweight helpers (safe-fallback style)
    # --------------------------------------------------------------------- #
    @property
    def ip(self) -> str:
        return self._ip

    @property
    def queue_size(self) -> int:
        """
        Return current queue length; 0 on failure.
        """
        try:
            q = self.coord.get_queue()
            return int(len(q) if q is not None else 0)
        except Exception:
            return 0

    def play_uri(self, *, uri: str, title: str | None = None) -> None:
        """
        Fire-and-forget play of a direct URI (does not touch the queue).
        """
        self.coord.play_uri(uri=uri, title=title or "")

    # Transport/track info mirrors (tests use these and tolerate errors)
    def get_current_transport_info(self) -> dict:
        try:
            info = self.coord.get_current_transport_info() or {}
        except Exception:
            info = {}
        return info

    def get_current_track_info(self) -> dict:
        try:
            info = self.coord.get_current_track_info() or {}
        except Exception:
            info = {}
        return info

    # Volume
    def set_volume(self, vol: int) -> None:
        with contextlib.suppress(Exception):
            self.coord.volume = max(0, min(100, int(vol)))

    def get_volume(self) -> int:
        try:
            v = int(self.coord.volume or 0)
        except Exception:
            v = 0
        return v
