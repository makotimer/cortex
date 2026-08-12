# career_watch/lib/vpn_client.py
"""
Thin client for the gluetun HTTP control server.

Errors are always caught and logged — callers decide whether a failure is fatal.
"""
from __future__ import annotations

import logging
import time

import requests

LOG = logging.getLogger(__name__)


#: Seconds to wait for the tunnel to come back up after a rotation restart.
#: Was 20 s, which timed out on ~22% of runs and left the scrape pointed at a
#: still-reconnecting tunnel (see rotate() below).
DEFAULT_ROTATE_TIMEOUT = 90.0

#: Seconds between public-IP polls while waiting for the tunnel.
ROTATE_POLL_INTERVAL = 2.0


class GluetunClient:
    def __init__(
        self,
        control_url: str = "http://vpn:8000",
        timeout: float = 5.0,
        rotate_timeout: float = DEFAULT_ROTATE_TIMEOUT,
    ) -> None:
        self._base = control_url.rstrip("/")
        self._timeout = timeout
        self._rotate_timeout = rotate_timeout
        #: Wall-clock seconds the last rotate() spent waiting for a new IP.
        #: Set on both success and timeout so reconnect latency can be tracked
        #: over time; stays None if rotate() was never called.
        self.last_rotate_seconds: float | None = None
        #: Number of public-IP polls the last rotate() made.
        self.last_rotate_polls: int = 0

    def current_ip(self) -> str | None:
        """Return the current VPN public IP, or None on any failure."""
        try:
            r = requests.get(f"{self._base}/v1/publicip/ip", timeout=self._timeout)
            r.raise_for_status()
            data = r.json()
            return str(data.get("public_ip") or "").strip() or None
        except Exception as exc:
            LOG.warning("gluetun: current_ip failed: %s", exc)
            return None

    def health(self) -> bool:
        """Return True if the VPN tunnel is up and has a public IP."""
        return bool(self.current_ip())

    def rotate(self) -> str | None:
        """
        Restart the WireGuard tunnel so gluetun picks a new server from
        SERVER_COUNTRIES.  Returns the new public IP, or None if rotation
        timed out or failed.

        Records the wait duration on ``last_rotate_seconds`` (and poll count on
        ``last_rotate_polls``) whether or not it succeeded, so callers can log
        reconnect latency and tune ``rotate_timeout`` from real data.

        gluetun v3 control API paths:
          - VPN (any type): PUT /v1/vpn/status
          - Confirm: GET /v1/publicip/ip
        """
        self.last_rotate_seconds = None
        self.last_rotate_polls = 0
        before = self.current_ip()

        for status in ("stopped", "running"):
            try:
                r = requests.put(
                    f"{self._base}/v1/vpn/status",
                    json={"status": status},
                    timeout=self._timeout,
                )
                r.raise_for_status()
            except Exception as exc:
                LOG.warning("gluetun: rotate PUT status=%s failed: %s", status, exc)
                return None
            if status == "stopped":
                time.sleep(2)

        # Wall-clock timed: a slow current_ip() call can overshoot the deadline
        # by at most one poll, so the worst case stays near rotate_timeout.
        started = time.monotonic()
        deadline = started + self._rotate_timeout
        while time.monotonic() < deadline:
            self.last_rotate_polls += 1
            ip = self.current_ip()
            if ip and ip != before:
                self.last_rotate_seconds = time.monotonic() - started
                LOG.info(
                    "gluetun: rotated %s → %s in %.1fs (%d polls)",
                    before, ip, self.last_rotate_seconds, self.last_rotate_polls,
                )
                return ip
            time.sleep(ROTATE_POLL_INTERVAL)

        self.last_rotate_seconds = time.monotonic() - started
        LOG.warning(
            "gluetun: rotation timed out after %.1fs (%d polls, still %s)",
            self.last_rotate_seconds, self.last_rotate_polls, before,
        )
        return None
