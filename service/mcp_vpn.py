# service/mcp_vpn.py
"""MCP server that fetches a page through the gluetun VPN as Claude Code tools.

Started on demand by the MCP wiring::

    docker exec -i cortex-cortex-1 python -m service.mcp_vpn

Same fetch path as ``scripts/event_probe.py`` (``modules/_shared/probe``):
the exit is verified against a neutral URL first, the page is GET-only, a
4xx/5xx body is returned as the finding, and every fetch is captured under
``local/state/event_probe/``. Rotation is off by default so a session cannot
burn exits under career_watch's daytime window.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer

from modules._shared import vpn_client
from modules._shared.probe import (
    DEFAULT_CONTROL,
    DEFAULT_OUT,
    DEFAULT_PROXY,
    DEFAULT_VERIFY,
    VPNSwitchFailed,
    page_text,
    probe,
    truncate,
)

load_dotenv()

mcp = MCPServer("vpn")

DEFAULT_MAX_CHARS = 20_000
HARD_MAX_CHARS = 200_000


def _proxy_url() -> str:
    return (os.getenv("EVENT_WATCH_PROXY_URL") or DEFAULT_PROXY).strip()


def _control_url() -> str:
    return (os.getenv("VPN_CONTROL_URL") or DEFAULT_CONTROL).strip()


def _clamp(max_chars: int) -> int:
    return max(1, min(int(max_chars), HARD_MAX_CHARS))


@mcp.tool()
def vpn_status() -> str:
    """Report the VPN tunnel: whether gluetun is healthy, its current public
    exit IP, and whether the exit can actually reach the web (verified against
    a neutral Cloudflare URL). Read-only; never rotates the exit."""
    gluetun = vpn_client.GluetunClient(control_url=_control_url())
    ip = gluetun.current_ip()
    usable = gluetun.usable(_proxy_url(), DEFAULT_VERIFY) if ip else False
    return json.dumps({
        "healthy": bool(ip),
        "ip": ip,
        "usable": usable,
        "proxy_url": _proxy_url(),
        "verify_url": DEFAULT_VERIFY,
    }, indent=2)


@mcp.tool()
def vpn_fetch(
    url: str,
    rotate: bool = False,
    as_text: bool = True,
    max_chars: int = DEFAULT_MAX_CHARS,
    timeout: float = 20.0,
) -> str:
    """GET a web page through the private VPN exit and return what came back.

    Use it for pages that block or geo-gate this box's own address, or to see
    a page exactly as the event_watch scrapers would. Always a GET; a 403 or
    other error status is returned with its body rather than raised, because
    that body is the finding (the `vendors` list names PerimeterX or a
    Cloudflare JS challenge when one is in the way — a Cloudflare challenge is
    not IP-gated, so rotating will not help).

    Args:
        url: The page to fetch.
        rotate: Force a new exit IP first. Leave False unless the current exit
            is blocked — rotating mid-day disturbs career_watch's window.
        as_text: True returns readable text (scripts/markup stripped);
            False returns the raw HTML.
        max_chars: Cap on the returned body (default 20000, hard max 200000).
            The full body is always saved to disk; the response says where.
        timeout: Per-request timeout in seconds.

    Returns JSON: status, final_url, title, vendors, vpn {ip, changed, reason},
    saved paths, `truncated`, and `body`.
    """
    try:
        result = probe(
            url,
            proxy_url=_proxy_url(),
            control_url=_control_url(),
            rotate=rotate,
            timeout=float(timeout),
            out_dir=Path(os.getenv("EVENT_PROBE_OUT") or DEFAULT_OUT),
        )
    except VPNSwitchFailed as exc:
        return json.dumps({
            "error": str(exc),
            "vpn": {
                "ok": False,
                "ip": exc.outcome.ip,
                "reason": exc.outcome.reason,
                "tried": [{"ip": ip, "ok": ok} for ip, ok in exc.outcome.tried],
            },
        }, indent=2)

    body = page_text(result.body) if as_text else result.body
    body, cut = truncate(body, _clamp(max_chars))
    vpn = result.vpn or {}
    return json.dumps({
        "url": result.url,
        "final_url": result.final_url,
        "status": result.status,
        "title": result.fingerprint.get("title"),
        "vendors": result.fingerprint.get("vendors", []),
        "content_type": result.headers.get("Content-Type"),
        "vpn": {
            "ip": vpn.get("ip"),
            "changed": vpn.get("changed"),
            "reason": vpn.get("reason"),
        },
        "saved": {
            "html": str(result.body_path) if result.body_path else None,
            "meta": str(result.meta_path) if result.meta_path else None,
        },
        "as_text": as_text,
        "truncated": cut,
        "body": body,
    }, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
