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
#:
#: 90 → 120 on 2026-08-13. Surveying 215 exits put the clean switch distribution
#: at a ~12 s median and a 72-78 s maximum once restart-corrupted samples were
#: excluded, so 90 s left only about 15% headroom over the observed worst case.
DEFAULT_ROTATE_TIMEOUT = 120.0

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

#: Gap between verification probes.
#:
#: gluetun publishes a public IP before the tunnel reliably carries traffic, so
#: a probe fired the instant an IP appears can fail on a perfectly good server.
#: Without any pause the first version of this code quarantined 8 exits in a few
#: minutes, 5 of which had already served production successfully — one of them
#: 7 times.
VERIFY_SETTLE_SECONDS = 2.0

#: Total budget for deciding whether one exit carries traffic.
#:
#: This replaced a single fixed 6 s sleep, which measured the wrong thing. A
#: probe ladder across 215 exits found ~97% carrying traffic on the very first
#: probe (median 0.34 s), so 6 s was generous for almost every exit — but the
#: tail ran to 7.5 s, 15.1 s, 20.7 s and 36.1 s, and each of those was a healthy
#: exit that the fixed sleep condemned and quarantined for 30 minutes. One of
#: them was watched directly: tun0's byte counters kept resetting for ~24 s
#: after gluetun had already published the IP, i.e. WireGuard was still
#: rebuilding the interface.
#:
#: A budget costs nothing in the 97% case and rescues the tail.
VERIFY_DEADLINE_SECONDS = 45.0

#: Hard cap on probes within that budget, so a zero gap cannot spin.
VERIFY_MAX_PROBES = 6


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
    #: Seconds each tunnel restart spent waiting for a public IP, in order.
    #:
    #: This is the number the overnight survey had to be built to learn, and
    #: production discarded it entirely: only the unused rotate() ever recorded
    #: switch latency. Reporting it here means a slow-switch problem shows up in
    #: the vpn_switch activity event instead of needing a live survey to find.
    #: A restart that timed out contributes its full wait, so three fast
    #: failures stay distinguishable from one slow one.
    restarts: list[float] = field(default_factory=list)


class GluetunClient:
    def __init__(
        self,
        control_url: str = "http://vpn:8000",
        timeout: float = 5.0,
        rotate_timeout: float = DEFAULT_ROTATE_TIMEOUT,
        quarantine_path: str | None = None,
        quarantine_ttl: float = DEFAULT_QUARANTINE_TTL,
        verify_settle: float = VERIFY_SETTLE_SECONDS,
        verify_deadline: float = VERIFY_DEADLINE_SECONDS,
    ) -> None:
        self._verify_settle = verify_settle
        self._verify_deadline = verify_deadline
        self._base = control_url.rstrip("/")
        self._timeout = timeout
        self._rotate_timeout = rotate_timeout
        self._quarantine_path = Path(quarantine_path) if quarantine_path else None
        self._quarantine_ttl = quarantine_ttl
        #: Wall-clock seconds the last tunnel restart spent waiting for an IP.
        #: Set on both success and timeout so reconnect latency can be tracked
        #: over time; stays None until a restart has been attempted.
        self.last_restart_seconds: float | None = None
        #: Number of public-IP polls the last restart made.
        self.last_restart_polls: int = 0

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
        nothing. But HEAD is an *optimisation, not the test*: only a successful
        HEAD is trusted, and anything else — a bad status or an exception — hands
        the question to GET, which decides.

        Falling through on 405 alone was not enough, and the cost was total.
        ``https://www.cloudflare.com/cdn-cgi/trace``, career_watch's default
        verify URL, answers HEAD with **404** and GET with 200. So this returned
        False for every exit on every attempt from 2026-08-12 15:02 until the
        fix, quarantining healthy servers wholesale and failing every run with
        "no usable VPN exit" while the tunnel was fine.
        """
        proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None
        for method in (requests.head, requests.get):
            try:
                r = method(verify_url, proxies=proxies, timeout=VERIFY_TIMEOUT,
                           allow_redirects=True)
            except Exception as exc:
                LOG.warning("gluetun: verify %s (%s) failed: %s",
                            verify_url, method.__name__.upper(), exc)
                continue
            if r.status_code < 400:
                return True
            LOG.info("gluetun: verify %s answered %s with %d",
                     verify_url, method.__name__.upper(), r.status_code)
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
                if self.last_restart_seconds is not None:
                    out.restarts.append(round(self.last_restart_seconds, 2))

            ip = self.current_ip()
            if not ip:
                out.tried.append((None, False))
                out.reason = "tunnel reported no public IP"
                continue

            if self._is_quarantined(ip):
                out.tried.append((ip, False))
                out.reason = f"exit {ip} is quarantined"
                continue

            if self._verify_until_ready(proxy_url, verify_url):
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

    def _verify_until_ready(self, proxy_url: str, verify_url: str) -> bool:
        """Probe until the exit carries traffic, or the budget runs out.

        This is what separates "this exit cannot reach the target" from "the
        tunnel was not ready yet" — a distinction worth spending seconds on,
        because getting it wrong quarantines working servers and shrinks the
        pool the next attempt draws from.

        Retrying to a deadline rather than once after a fixed sleep is the
        difference between covering the median exit and covering the tail: 97%
        answer the first probe, but the slowest healthy exit measured took
        36 s, and a single 6 s sleep condemned it.
        """
        started = time.monotonic()
        for probe_n in range(1, VERIFY_MAX_PROBES + 1):
            if self.usable(proxy_url, verify_url):
                if probe_n > 1:
                    LOG.info("gluetun: exit became usable on probe %d after "
                             "%.1fs", probe_n, time.monotonic() - started)
                return True
            elapsed = time.monotonic() - started
            # Stop when the *next* probe could not finish inside the budget,
            # rather than starting one that will overrun it.
            if elapsed + self._verify_settle >= self._verify_deadline:
                LOG.info("gluetun: exit still unusable after %d probe(s) in "
                         "%.1fs", probe_n, elapsed)
                break
            time.sleep(self._verify_settle)
        return False

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

        This deliberately does not require the IP to change: a working tunnel on
        the same exit is a perfectly good outcome, and demanding a change is
        what produced the old false failures.

        Records the wait on ``last_restart_seconds`` (and the poll count on
        ``last_restart_polls``) whether or not an IP appeared, so callers can
        report reconnect latency and tune ``rotate_timeout`` from production
        data rather than from a separate survey.
        """
        self.last_restart_seconds = None
        self.last_restart_polls = 0

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

        # Wall-clock timed: a slow current_ip() call can overshoot the deadline
        # by at most one poll, so the worst case stays near rotate_timeout.
        started = time.monotonic()
        deadline = started + self._rotate_timeout
        while time.monotonic() < deadline:
            self.last_restart_polls += 1
            ip = self.current_ip()
            if ip:
                self.last_restart_seconds = time.monotonic() - started
                LOG.info("gluetun: tunnel back up on %s in %.1fs (%d polls)",
                         ip, self.last_restart_seconds, self.last_restart_polls)
                return ip
            time.sleep(ROTATE_POLL_INTERVAL)

        self.last_restart_seconds = time.monotonic() - started
        LOG.warning("gluetun: tunnel did not come up within %.1fs (%d polls)",
                    self.last_restart_seconds, self.last_restart_polls)
        return None
