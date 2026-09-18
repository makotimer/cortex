#!/usr/bin/env python3
"""Fetch a candidate event page through the same path a scraper will use.

News-site calendars are often a widget behind a bot wall. A host-side curl
from this box is not what event_watch will see — gluetun is. This GETs the
URL from inside the cortex container, through the proxy, after a usable
exit is up, and prints a fingerprint of what the HTML actually is.

A 403 is a finding, not a failed switch. The tunnel is verified against a
neutral URL so PerimeterX cannot burn three exits and leave us with no body.

The fetch itself lives in ``modules/_shared/probe.py`` and is shared with
the ``vpn`` MCP server (``service/mcp_vpn.py``); this file is the CLI.

Run it inside the cortex container::

    docker compose run --rm cortex python scripts/event_probe.py \\
      'https://www.fox44news.com/calendar/'
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app")

from modules._shared.probe import (
    BROWSER_UA,
    DEFAULT_CONTROL,
    DEFAULT_OUT,
    DEFAULT_PROXY,
    DEFAULT_VERIFY,
    VENDOR_MARKERS,
    ProbeResult,
    VPNSwitchFailed,
    capture_stem,
    fingerprint,
    probe,
    write_capture,
)

__all__ = [
    "BROWSER_UA",
    "DEFAULT_CONTROL",
    "DEFAULT_OUT",
    "DEFAULT_PROXY",
    "DEFAULT_VERIFY",
    "VENDOR_MARKERS",
    "capture_stem",
    "fingerprint",
    "main",
    "write_capture",
]


def _resolve_proxy(args: argparse.Namespace) -> str | None:
    if args.no_proxy:
        return None
    if args.proxy_url is not None:
        return args.proxy_url.strip() or None
    return (os.getenv("EVENT_WATCH_PROXY_URL") or DEFAULT_PROXY).strip() or None


def _print_report(result: ProbeResult) -> None:
    report = result.fingerprint
    print(f"url:        {result.url}")
    print(f"final:      {result.final_url}")
    print(f"status:     {result.status}")
    print(f"title:      {report['title'] or '(none)'}")
    print(f"vendors:    {', '.join(report['vendors']) or '(none)'}")
    if result.vpn is not None:
        vpn = result.vpn
        print(f"vpn:        ok={vpn.get('ok')} ip={vpn.get('ip')} reason={vpn.get('reason')}")
    if report["data_attrs"]:
        print("data-attrs:")
        for attr in report["data_attrs"]:
            print(f"  {attr['name']}={attr['value']}")
    if report["script_srcs"]:
        print("scripts:")
        for src in report["script_srcs"]:
            print(f"  {src}")
    if report["iframe_srcs"]:
        print("iframes:")
        for src in report["iframe_srcs"]:
            print(f"  {src}")
    print(f"saved:      {result.body_path}")
    print(f"            {result.meta_path}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("url", help="page to fetch (the calendar URL, not a guess at its API)")
    ap.add_argument("--out", default=DEFAULT_OUT, help=f"directory for body + meta (default {DEFAULT_OUT})")
    ap.add_argument("--proxy-url", default=None, help="gluetun HTTP proxy; empty + --no-proxy goes direct")
    ap.add_argument("--no-proxy", action="store_true", help="skip the VPN entirely")
    ap.add_argument("--no-rotate", action="store_true",
                    help="keep the current exit if it already carries traffic")
    ap.add_argument("--verify-url", default=DEFAULT_VERIFY,
                    help="neutral URL used to decide the exit works (default: cloudflare trace). "
                         "Do not point this at a PerimeterX host or a 403 will look like a dead tunnel")
    ap.add_argument("--control-url", default=os.getenv("VPN_CONTROL_URL") or DEFAULT_CONTROL)
    ap.add_argument("--timeout", type=float, default=20.0)
    ap.add_argument("--user-agent", default=BROWSER_UA)
    args = ap.parse_args(argv)

    try:
        result = probe(
            args.url,
            proxy_url=_resolve_proxy(args),
            control_url=args.control_url,
            verify_url=args.verify_url,
            rotate=not args.no_rotate,
            timeout=args.timeout,
            user_agent=args.user_agent,
            out_dir=Path(args.out),
        )
    except VPNSwitchFailed as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _print_report(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
