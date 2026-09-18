"""Fetch one page through gluetun, the way a scraper sees it.

Shared by ``scripts/event_probe.py`` (the CLI) and ``service/mcp_vpn.py``
(the Claude Code tool) so there is exactly one fetch path: verify the exit
against a neutral URL, GET the page through the proxy, fingerprint the HTML
and write a capture to disk. A 403 is a finding, not a failed switch.

Everything but ``probe()`` is pure and driven from saved HTML in the tests.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from modules._shared import vpn_client
from modules._shared.http import HttpClient

DEFAULT_OUT = "/app/local/state/event_probe"
DEFAULT_PROXY = "http://vpn:8888"
DEFAULT_CONTROL = "http://vpn:8000"
DEFAULT_VERIFY = "https://www.cloudflare.com/cdn-cgi/trace"
DEFAULT_TIMEOUT = 20.0
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
    # Cloudflare's managed / JS challenge. Not IP-gated: rotating the exit
    # does nothing, only a real browser passes it. Named so the report says so.
    ("cloudflare-challenge", (
        "window._cf_chl_opt",
        "cf_chl_tk=",
        "/cdn-cgi/challenge-platform/",
    )),
)

_CALENDAR_DATA_HINTS = ("tockify", "calendar", "localist", "evvnt", "event")
_DROP_TAGS = ("script", "style", "noscript", "svg", "template", "head")


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


def page_text(html: str) -> str:
    """The page as a reader would see it: scripts, styles and markup gone,
    one line per block, runs of blank lines collapsed. Pure."""
    soup = BeautifulSoup(html or "", "html.parser")
    for tag in soup.find_all(_DROP_TAGS):
        tag.decompose()
    text = soup.get_text("\n")
    lines = [re.sub(r"[ \t\xa0]+", " ", ln).strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln:
            out.append(ln)
        elif out and out[-1] != "":
            out.append("")
    return "\n".join(out).strip()


def truncate(text: str, max_chars: int) -> tuple[str, bool]:
    """Cut ``text`` to ``max_chars`` and say whether anything was lost, so a
    caller can report the cut instead of silently handing over half a page."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text, False
    return text[:max_chars].rstrip() + "\n[… truncated]", True


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


def switch_vpn(
    proxy_url: str,
    verify_url: str,
    *,
    rotate: bool,
    control_url: str,
) -> vpn_client.SwitchOutcome:
    """Bring up a usable exit. ``rotate=False`` keeps the current one when it
    already carries traffic — the default for anything a person triggers, so
    a casual fetch cannot burn exits under a running career_watch window."""
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


def vpn_meta(outcome: vpn_client.SwitchOutcome, verify_url: str) -> dict[str, Any]:
    return {
        "ok": outcome.ok,
        "ip": outcome.ip,
        "changed": outcome.changed,
        "attempts": outcome.attempts,
        "seconds": round(outcome.seconds, 2),
        "reason": outcome.reason,
        "tried": [{"ip": ip, "ok": ok} for ip, ok in outcome.tried],
        "verify_url": verify_url,
    }


class VPNSwitchFailed(RuntimeError):
    """No usable exit came up; nothing was fetched."""

    def __init__(self, outcome: vpn_client.SwitchOutcome) -> None:
        super().__init__(f"vpn switch failed: {outcome.reason}")
        self.outcome = outcome


@dataclass
class ProbeResult:
    url: str
    final_url: str
    status: int
    body: str
    headers: dict[str, str]
    fingerprint: dict[str, Any]
    fetched_at: str
    proxy_url: str | None
    vpn: dict[str, Any] | None
    body_path: Path | None = None
    meta_path: Path | None = None

    def meta(self) -> dict[str, Any]:
        """The sidecar record: everything but the body and the capture paths."""
        d = asdict(self)
        for key in ("body", "body_path", "meta_path"):
            d.pop(key)
        d["bytes"] = len(self.body.encode("utf-8"))
        return d


def probe(
    url: str,
    *,
    proxy_url: str | None = DEFAULT_PROXY,
    control_url: str = DEFAULT_CONTROL,
    verify_url: str = DEFAULT_VERIFY,
    rotate: bool = False,
    timeout: float = DEFAULT_TIMEOUT,
    user_agent: str = BROWSER_UA,
    out_dir: Path | None = Path(DEFAULT_OUT),
) -> ProbeResult:
    """GET ``url`` through the tunnel and capture what came back.

    ``proxy_url=None`` goes direct from the container. Raises
    :class:`VPNSwitchFailed` when no exit is usable; never raises on a 4xx/5xx
    answer, because that body is the finding. ``out_dir=None`` skips the
    on-disk capture.
    """
    meta: dict[str, Any] | None = None
    if proxy_url:
        outcome = switch_vpn(proxy_url, verify_url, rotate=rotate, control_url=control_url)
        meta = vpn_meta(outcome, verify_url)
        if not outcome.ok:
            raise VPNSwitchFailed(outcome)

    client = HttpClient(timeout=timeout, user_agent=user_agent, proxy_url=proxy_url, proxy_env=None)
    try:
        # Do not raise_for_status: 403/401 bodies are the finding.
        resp = client.session.get(url, timeout=timeout, allow_redirects=True)
        body = resp.text
        status = resp.status_code
        final_url = str(resp.url)
        headers = dict(resp.headers.items())
    finally:
        client.close()

    now = datetime.now(UTC)
    result = ProbeResult(
        url=url,
        final_url=final_url,
        status=status,
        body=body,
        headers=headers,
        fingerprint=fingerprint(body),
        fetched_at=now.isoformat(),
        proxy_url=proxy_url,
        vpn=meta,
    )
    if out_dir is not None:
        stem = capture_stem(url, now)
        result.body_path, result.meta_path = write_capture(
            Path(out_dir), stem, body=body, meta={**result.meta(), "user_agent": user_agent},
        )
    return result
