from __future__ import annotations

import os

from modules._shared import vpn_client

from .lib import logging_bridge

#: Attempts before giving up on getting a different exit.
#:
#: Deliberately more generous than a scrape's 3. Nothing waits on this job: it
#: runs in the 90-minute dormant window between scrapes, where one rotation
#: costs about 28 s expected. Five attempts is roughly two and a half minutes
#: against ninety, so there is no reason to be stingy — and each extra attempt
#: is another chance to dodge the 6.36% of restarts that produce no IP at all.
DEFAULT_ATTEMPTS = 5

#: Answers HEAD with 404 and GET with 200, which is why usable() must not trust
#: a failed HEAD. Same target the scrapers verify against.
DEFAULT_VERIFY_URL = "https://www.cloudflare.com/cdn-cgi/trace"


class VPNCycleError(RuntimeError):
    """Raised when the tunnel could not be moved to a different exit."""


def _first_set(*values: str | None) -> str:
    for v in values:
        if v and str(v).strip():
            return str(v).strip()
    return ""


def run(**kwargs) -> str | None:
    """Rotate the VPN to a different exit while nothing is scraping.

    Rotation used to happen at the front of every scrape, where it had no room
    to fail — 28 s expected, a 6.36% chance of producing no IP at all, and the
    scrape blocked behind it. Worse, the switch accepts an unchanged IP as
    success, so 1.54% of runs presented every source the address the previous
    run had just used.

    Doing it here inverts both problems. The scrape keeps
    ``rotate_vpn_per_run: false`` and merely verifies the exit it finds, which
    already fails closed if the tunnel is broken; this job owns the rotating,
    with hours of budget and permission to insist on a genuinely new exit.

    kwargs (all optional, env fallbacks in brackets):
      proxy_url      [VPN_PROXY_URL, CAREER_WATCH_PROXY_URL]
      control_url    [VPN_CONTROL_URL] default http://vpn:8000
      verify_url     [VPN_VERIFY_URL]  default the Cloudflare trace endpoint
      attempts       [VPN_CYCLE_ATTEMPTS] default 5
      rotate_timeout [VPN_ROTATE_TIMEOUT] default the client's own

    Returns None on success — it runs ~10x a day and success is not news.
    Raises VPNCycleError on failure, so cortex's job-failure path surfaces it.
    """
    proxy_url = _first_set(kwargs.get("proxy_url"),
                           os.getenv("VPN_PROXY_URL"),
                           os.getenv("CAREER_WATCH_PROXY_URL"))
    if not proxy_url:
        # Matches the engines: an unconfigured VPN is not a failure, and this
        # job should be harmless to schedule on a host that has no tunnel.
        logging_bridge.activity({
            "component": "vpn_cycle", "op": "vpn_cycle_skipped",
            "reason": "no proxy configured",
        })
        return None

    control_url = _first_set(kwargs.get("control_url"),
                             os.getenv("VPN_CONTROL_URL")) or "http://vpn:8000"
    verify_url = _first_set(kwargs.get("verify_url"),
                            os.getenv("VPN_VERIFY_URL")) or DEFAULT_VERIFY_URL
    try:
        attempts = int(_first_set(str(kwargs.get("attempts") or ""),
                                  os.getenv("VPN_CYCLE_ATTEMPTS"))
                       or DEFAULT_ATTEMPTS)
    except ValueError:
        attempts = DEFAULT_ATTEMPTS
    try:
        rotate_timeout = float(_first_set(str(kwargs.get("rotate_timeout") or ""),
                                          os.getenv("VPN_ROTATE_TIMEOUT"))
                               or vpn_client.DEFAULT_ROTATE_TIMEOUT)
    except ValueError:
        rotate_timeout = vpn_client.DEFAULT_ROTATE_TIMEOUT

    gluetun = vpn_client.GluetunClient(control_url=control_url,
                                       rotate_timeout=rotate_timeout)
    outcome = gluetun.switch_until_usable(
        proxy_url=proxy_url,
        verify_url=verify_url,
        attempts=attempts,
        prefer_new_ip=True,
        require_new_ip=True,
    )

    logging_bridge.activity({
        "component": "vpn_cycle", "op": "vpn_cycle",
        "ok": outcome.ok,
        "previous_ip": outcome.previous_ip,
        "ip": outcome.ip,
        "changed": outcome.changed,
        "attempts": outcome.attempts,
        "seconds": round(outcome.seconds, 2),
        "reason": outcome.reason,
        "tried": [{"ip": ip, "ok": ok} for ip, ok in outcome.tried],
        "restarts": outcome.restarts,
    })

    if not outcome.ok:
        # Loud on purpose. The scrape cannot tell a stale exit from a fresh one
        # — it only checks that the tunnel works — so this event is the only
        # signal that the next run may reuse an address.
        raise VPNCycleError(
            f"could not move to a new exit after {outcome.attempts} attempt(s): "
            f"{outcome.reason}"
        )
    return None
