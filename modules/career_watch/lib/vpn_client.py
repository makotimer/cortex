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


class GluetunClient:
    def __init__(
        self,
        control_url: str = "http://vpn:8000",
        timeout: float = 5.0,
    ) -> None:
        self._base = control_url.rstrip("/")
        self._timeout = timeout

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

        gluetun v3 control API paths:
          - WireGuard: PUT /v1/openvpn/status (shared path in v3)
          - Confirm: GET /v1/publicip/ip
        """
        before = self.current_ip()

        for status in ("stopped", "running"):
            try:
                r = requests.put(
                    f"{self._base}/v1/openvpn/status",
                    json={"status": status},
                    timeout=self._timeout,
                )
                r.raise_for_status()
            except Exception as exc:
                LOG.warning("gluetun: rotate PUT status=%s failed: %s", status, exc)
                return None
            if status == "stopped":
                time.sleep(2)

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            ip = self.current_ip()
            if ip and ip != before:
                LOG.info("gluetun: rotated %s → %s", before, ip)
                return ip
            time.sleep(2)

        LOG.warning("gluetun: rotation timed out (still %s)", before)
        return None
