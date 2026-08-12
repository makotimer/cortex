# career_watch/lib/vpn_client.py
"""
Thin client for the gluetun HTTP control server.

Errors are always caught and logged — callers decide whether a failure is fatal.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import requests

LOG = logging.getLogger(__name__)


#: Seconds to wait for the tunnel to come back up after a rotation restart.
#: Was 20 s, which timed out on ~22% of runs and left the scrape pointed at a
#: still-reconnecting tunnel (see rotate() below).
DEFAULT_ROTATE_TIMEOUT = 90.0

#: Seconds between public-IP polls while waiting for the tunnel.
ROTATE_POLL_INTERVAL = 2.0

#: How long a failed exit stays quarantined.
#:
#: Deliberately short. Across 750 recorded rotations and 220 distinct exit IPs,
#: not one server was ever persistently bad — every failed run came from a
#: switch that did not complete, not from a bad server. Exits do fail (a
#: ProtonVPN address that was also a Tor exit relay could not reach the target
#: at all), but treating that as a lasting property would shrink the usable pool
#: on no evidence. This is a "do not immediately retry" note, not a reputation.
DEFAULT_QUARANTINE_TTL = 1800.0

#: Seconds allowed for the through-proxy verification request.
VERIFY_TIMEOUT = 12.0

#: Pause before re-verifying an exit that failed its first probe.
#:
#: gluetun publishes a public IP before the tunnel reliably carries traffic, so
#: a probe fired the instant an IP appears can fail on a perfectly good server.
#: Without this pause the first version of this code quarantined 8 exits in a
#: few minutes, 5 of which had already served production successfully — one of
#: them 7 times. An exit is only condemned if it fails twice with a gap.
VERIFY_SETTLE_SECONDS = 6.0


@dataclass
class SwitchOutcome:
    """What one switch_until_usable() call achieved. Log this verbatim."""

    ok: bool
    ip: str | None = None
    changed: bool = False
    attempts: int = 0
    seconds: float = 0.0
    reason: str = ""
    #: Exits parked during this call, with why. Feeds the quarantine file and
    #: is the raw material any future server reputation would be built from.
    quarantined: list[str] = field(default_factory=list)
    #: (ip, ok) per attempt, so a run's log shows what was actually tried.
    tried: list[tuple[str | None, bool]] = field(default_factory=list)


class GluetunClient:
    def __init__(
        self,
        control_url: str = "http://vpn:8000",
        timeout: float = 5.0,
        rotate_timeout: float = DEFAULT_ROTATE_TIMEOUT,
        quarantine_path: str | None = None,
        quarantine_ttl: float = DEFAULT_QUARANTINE_TTL,
        verify_settle: float = VERIFY_SETTLE_SECONDS,
    ) -> None:
        self._verify_settle = verify_settle
        self._base = control_url.rstrip("/")
        self._timeout = timeout
        self._rotate_timeout = rotate_timeout
        self._quarantine_path = Path(quarantine_path) if quarantine_path else None
        self._quarantine_ttl = quarantine_ttl
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

    # ------------------------------------------------------------------
    # Usability: the question that actually matters
    # ------------------------------------------------------------------
    def usable(self, proxy_url: str, verify_url: str) -> bool:
        """Can we actually reach ``verify_url`` through the tunnel?

        ``health()`` only asks whether gluetun reports a public IP, which is a
        different question and has been answered "yes" while nothing worked —
        an exit that was also a Tor relay held a perfectly good IP and could
        not reach the target at all. This asks the real question.

        HEAD first, since most of these endpoints answer it and it costs almost
        nothing; a server that rejects HEAD gets a GET.
        """
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        for method in (requests.head, requests.get):
            try:
                r = method(verify_url, proxies=proxies, timeout=VERIFY_TIMEOUT,
                           allow_redirects=True)
            except Exception as exc:
                LOG.warning("gluetun: verify %s failed: %s", verify_url, exc)
                return False
            if r.status_code == 405:      # HEAD not allowed here; try GET
                continue
            return bool(r.status_code < 400)
        return False

    def switch_until_usable(
        self,
        *,
        proxy_url: str,
        verify_url: str,
        attempts: int = 3,
        prefer_new_ip: bool = True,
    ) -> SwitchOutcome:
        """Switch exits until one can actually reach ``verify_url``.

        Replaces "did the IP change within N seconds?" with "does it work?",
        which fixes both directions of the old failure:

        * a reconnect to the same server used to count as failure even though
          the tunnel was fine, and
        * a brand new IP used to count as success even when nothing could be
          fetched through it.

        ``prefer_new_ip=False`` verifies the current exit first and only
        switches if it is unusable — right for a public feed, where rotating
        buys nothing and restarting the tunnel disturbs other consumers.
        """
        started = time.monotonic()
        before = self.current_ip()
        out = SwitchOutcome(ok=False, reason="no attempts made")

        for attempt in range(1, max(1, attempts) + 1):
            out.attempts = attempt
            if attempt > 1 or prefer_new_ip:
                self._restart_and_wait()

            ip = self.current_ip()
            if not ip:
                out.tried.append((None, False))
                out.reason = "tunnel reported no public IP"
                continue

            if self._is_quarantined(ip):
                out.tried.append((ip, False))
                out.reason = f"exit {ip} is quarantined"
                continue

            if self._verify_with_settle(proxy_url, verify_url):
                out.tried.append((ip, True))
                out.ok, out.ip = True, ip
                out.changed = bool(before and ip != before)
                out.reason = "verified"
                break

            # Failed twice with a pause between, so this is the exit and not
            # the tunnel still coming up. Park it so the next attempt does not
            # land straight back on it.
            out.tried.append((ip, False))
            self._quarantine(ip)
            out.quarantined.append(ip)
            out.reason = f"exit {ip} could not reach {verify_url}"

        out.seconds = time.monotonic() - started
        if not out.ok:
            LOG.warning("gluetun: no usable exit after %d attempt(s) in %.1fs (%s)",
                        out.attempts, out.seconds, out.reason)
        return out

    def _verify_with_settle(self, proxy_url: str, verify_url: str) -> bool:
        """Probe, and on failure wait and probe once more.

        The retry is what separates "this exit cannot reach the target" from
        "the tunnel was not ready yet" — a distinction worth the few seconds,
        because getting it wrong quarantines working servers.
        """
        if self.usable(proxy_url, verify_url):
            return True
        LOG.info("gluetun: first probe failed, settling %.0fs before retry",
                 self._verify_settle)
        time.sleep(self._verify_settle)
        return self.usable(proxy_url, verify_url)

    # ------------------------------------------------------------------
    # Quarantine: a short memory of exits that just failed
    # ------------------------------------------------------------------
    def _load_quarantine(self) -> dict[str, float]:
        if not self._quarantine_path:
            return {}
        try:
            data = json.loads(self._quarantine_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        now = time.time()
        return {ip: exp for ip, exp in data.items()
                if isinstance(exp, (int, float)) and exp > now}

    def _is_quarantined(self, ip: str) -> bool:
        return ip in self._load_quarantine()

    def _quarantine(self, ip: str) -> None:
        if not self._quarantine_path:
            return
        entries = self._load_quarantine()
        entries[ip] = time.time() + self._quarantine_ttl
        path = self._quarantine_path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name,
                                       suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(entries, fh, sort_keys=True)
            os.replace(tmp, path)
        except OSError as exc:
            # Losing the note costs one wasted retry later, never the run.
            LOG.warning("gluetun: could not persist quarantine: %s", exc)

    def _restart_and_wait(self) -> str | None:
        """Stop and start the tunnel, then wait for *any* public IP.

        Unlike rotate(), this does not require the IP to change: a working
        tunnel on the same exit is a perfectly good outcome, and demanding a
        change is what produced false failures.
        """
        for status in ("stopped", "running"):
            try:
                r = requests.put(f"{self._base}/v1/vpn/status",
                                 json={"status": status}, timeout=self._timeout)
                r.raise_for_status()
            except Exception as exc:
                LOG.warning("gluetun: restart PUT status=%s failed: %s", status, exc)
                return None
            if status == "stopped":
                time.sleep(2)

        deadline = time.monotonic() + self._rotate_timeout
        while time.monotonic() < deadline:
            ip = self.current_ip()
            if ip:
                return ip
            time.sleep(ROTATE_POLL_INTERVAL)
        return None

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
