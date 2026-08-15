#!/usr/bin/env python3
"""Fetch a candidate event page through the same path a scraper will use.

News-site calendars are often a widget behind a bot wall. A host-side curl
from this box is not what event_watch will see — gluetun is. This GETs the
URL from inside the cortex container, through the proxy, after a usable
exit is up, and prints a fingerprint of what the HTML actually is.

A 403 is a finding, not a failed switch. The tunnel is verified against a
neutral URL so PerimeterX cannot burn three exits and leave us with no body.

Run it inside the cortex container::

    docker compose run --rm cortex python scripts/event_probe.py \\
      'https://www.fox44news.com/calendar/'
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

sys.path.insert(0, "/app")

from modules._shared import vpn_client
from modules._shared.http import HttpClient

DEFAULT_OUT = "/app/local/state/event_probe"
DEFAULT_PROXY = "http://vpn:8888"
DEFAULT_CONTROL = "http://vpn:8000"
DEFAULT_VERIFY = "https://www.cloudflare.com/cdn-cgi/trace"
BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)

#: (vendor, substrings matched against the lowercased page). Order is the
#: report order. Markers are evidence, not guesses — a bare word like
#: "calendar" is not a vendor.
VENDOR_MARKERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("tockify", ("tockify.com", "data-tockify-")),
    ("evvnt", ("evvnt.com", "powered by evvnt")),
    ("localist", ("localist.com", "data-localist")),
    ("eventbrite", ("eventbrite.com",)),
    ("singlespot", ("singlespot.com",)),
    ("calendarwiz", ("calendarwiz.com",)),
    ("perimeterx", (
        "px-captcha",
        "_pxappid",
        "captcha.px-cloud.net",
        "access to this page has been denied",
    )),
)

_CALENDAR_DATA_HINTS = ("tockify", "calendar", "localist", "evvnt", "event")


def fingerprint(html: str) -> dict[str, Any]:
    """Identify what calendar widget a page is actually embedding.

    Pure: no network, no clock. Driven from saved HTML in the tests.
    """
    soup = BeautifulSoup(html or "", "html.parser")
    title_tag = soup.find("title")
    title = title_tag.get_text(strip=True) if title_tag else None
    if not title:
        title = None

    script_srcs = [str(tag["src"]) for tag in soup.find_all("script", src=True)]
    iframe_srcs = [str(tag["src"]) for tag in soup.find_all("iframe", src=True)]

    data_attrs: list[dict[str, str]] = []
    for tag in soup.find_all(True):
        attrs = getattr(tag, "attrs", None) or {}
        for key, value in attrs.items():
            name = str(key)
            if not name.startswith("data-"):
                continue
            low = name.lower()
            if not any(hint in low for hint in _CALENDAR_DATA_HINTS):
                continue
            if isinstance(value, list):
                value = " ".join(str(part) for part in value)
            data_attrs.append({"name": name, "value": str(value)})

    blob = (html or "").lower()
    vendors = [name for name, markers in VENDOR_MARKERS if any(m in blob for m in markers)]

    return {
        "title": title,
        "vendors": vendors,
        "script_srcs": script_srcs,
        "iframe_srcs": iframe_srcs,
        "data_attrs": data_attrs,
    }


def capture_stem(url: str, when: datetime) -> str:
    host = urlparse(url).hostname or "page"
    return f"{host}-{when.strftime('%Y%m%dT%H%M%SZ')}"


def write_capture(
    out_dir: Path,
    stem: str,
    *,
    body: str,
    meta: dict[str, Any],
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    body_path = out_dir / f"{stem}.html"
    meta_path = out_dir / f"{stem}.meta.json"
    body_path.write_text(body, encoding="utf-8")
    meta_path.write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return body_path, meta_path


def _resolve_proxy(args: argparse.Namespace) -> str | None:
    if args.no_proxy:
        return None
    if args.proxy_url is not None:
        return args.proxy_url.strip() or None
    return (os.getenv("EVENT_WATCH_PROXY_URL") or DEFAULT_PROXY).strip() or None


def _switch_vpn(proxy_url: str, verify_url: str, *, rotate: bool, control_url: str) -> vpn_client.SwitchOutcome:
    try:
        rotate_timeout = float(os.getenv("VPN_ROTATE_TIMEOUT") or vpn_client.DEFAULT_ROTATE_TIMEOUT)
    except ValueError:
        rotate_timeout = vpn_client.DEFAULT_ROTATE_TIMEOUT
    gluetun = vpn_client.GluetunClient(control_url=control_url, rotate_timeout=rotate_timeout)
    return gluetun.switch_until_usable(
        proxy_url=proxy_url,
        verify_url=verify_url,
        attempts=int(os.getenv("VPN_SWITCH_ATTEMPTS") or 3),
        prefer_new_ip=rotate,
    )


def _print_report(url: str, status: int, final_url: str, report: dict[str, Any],
                  body_path: Path, meta_path: Path, vpn: dict[str, Any] | None) -> None:
    print(f"url:        {url}")
    print(f"final:      {final_url}")
    print(f"status:     {status}")
    print(f"title:      {report['title'] or '(none)'}")
    print(f"vendors:    {', '.join(report['vendors']) or '(none)'}")
    if vpn is not None:
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
    print(f"saved:      {body_path}")
    print(f"            {meta_path}")


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

    proxy_url = _resolve_proxy(args)
    vpn_meta: dict[str, Any] | None = None
    if proxy_url:
        outcome = _switch_vpn(
            proxy_url,
            args.verify_url,
            rotate=not args.no_rotate,
            control_url=args.control_url,
        )
        vpn_meta = {
            "ok": outcome.ok,
            "ip": outcome.ip,
            "changed": outcome.changed,
            "attempts": outcome.attempts,
            "seconds": round(outcome.seconds, 2),
            "reason": outcome.reason,
            "tried": [{"ip": ip, "ok": ok} for ip, ok in outcome.tried],
            "verify_url": args.verify_url,
        }
        if not outcome.ok:
            print(f"vpn switch failed: {outcome.reason}", file=sys.stderr)
            return 2

    client = HttpClient(
        timeout=args.timeout,
        user_agent=args.user_agent,
        proxy_url=proxy_url,
        proxy_env=None,
    )
    try:
        # Do not raise_for_status: 403/401 bodies are the finding.
        resp = client.session.get(args.url, timeout=args.timeout, allow_redirects=True)
        body = resp.text
        status = resp.status_code
        final_url = str(resp.url)
        headers = {k: v for k, v in resp.headers.items()}
    finally:
        client.close()

    report = fingerprint(body)
    now = datetime.now(UTC)
    stem = capture_stem(args.url, now)
    meta = {
        "url": args.url,
        "final_url": final_url,
        "status": status,
        "fetched_at": now.isoformat(),
        "proxy_url": proxy_url,
        "user_agent": args.user_agent,
        "headers": headers,
        "bytes": len(body.encode("utf-8")),
        "fingerprint": report,
        "vpn": vpn_meta,
    }
    body_path, meta_path = write_capture(Path(args.out), stem, body=body, meta=meta)
    _print_report(args.url, status, final_url, report, body_path, meta_path, vpn_meta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
