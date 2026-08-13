"""Shared HTTP client: sane defaults, retry, and optional proxying.

Promoted here from ``career_watch/lib/http_client.py`` when ``event_watch`` became
its second consumer. Two defaults are deliberately still career_watch's:

* ``proxy_env`` defaults to ``CAREER_WATCH_PROXY_URL``, because six career_watch
  scrapers rely on this class reading that variable for them. A different default
  would send those scrapers out un-proxied — a silent fail-open that leaks the
  host IP rather than raising. New callers should pass ``proxy_url`` (or their own
  ``proxy_env``) explicitly instead of relying on this.
* ``user_agent`` keeps its historical value for the same reason: job boards can be
  UA-sensitive, and one career_watch scraper constructs this class with no
  arguments at all.
"""
from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

LOG = logging.getLogger(__name__)


class HttpClient:
    """Shared HTTP client with sane defaults and simple helpers."""

    def __init__(
        self,
        timeout: float = 15.0,
        user_agent: str = "JobWatch/0.1 (+https://example.invalid)",
        proxy_url: str | None = None,
        proxy_env: str | None = "CAREER_WATCH_PROXY_URL",
    ):
        self.timeout = float(timeout)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

        # proxy_env=None disables the environment fallback entirely, for callers
        # that already resolved the proxy themselves. Without it, passing
        # proxy_url=None to mean "go direct" is silently overridden by the
        # environment — and the caller then proxies without having run the VPN
        # health check that is supposed to gate proxied traffic.
        _env_proxy = os.getenv(proxy_env) if proxy_env else None
        _proxy = (proxy_url or _env_proxy or "").strip()
        if _proxy:
            self.session.proxies = {"http": _proxy, "https": _proxy}

        retry = Retry(
            total=3,
            backoff_factor=0.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset(["GET", "POST", "HEAD"]),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=20)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    # ---- convenience ----
    def get_text(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        encoding: str | None = None,
        **kwargs: Any,  # e.g., allow redirects=False, proxies=..., etc.
    ) -> str:
        """GET and return decoded text with gentle encoding hints."""
        resp = self.session.get(url, params=params, headers=headers, timeout=timeout or self.timeout, **kwargs)
        resp.raise_for_status()
        if encoding:
            resp.encoding = encoding
        elif not resp.encoding and resp.apparent_encoding:
            resp.encoding = resp.apparent_encoding
        return str(resp.text)

    def post_text(
        self,
        url: str,
        *,
        data: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        encoding: str | None = None,
        **kwargs: Any,
    ) -> str:
        """POST a form body and return decoded text.

        For endpoints that answer with a fragment rather than JSON — WordPress
        ``admin-ajax.php`` being the case that prompted this. ``Retry`` already
        lists POST in ``allowed_methods``, so this inherits the same backoff as
        the GET helpers; that is only safe because the callers here are reads
        dressed up as posts, not state changes.
        """
        resp = self.session.post(
            url, data=data, headers=headers, timeout=timeout or self.timeout, **kwargs
        )
        resp.raise_for_status()
        if encoding:
            resp.encoding = encoding
        elif not resp.encoding and resp.apparent_encoding:
            resp.encoding = resp.apparent_encoding
        return str(resp.text)

    def get_json(
        self,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        timeout: float | None = None,
        **kwargs: Any,
    ) -> Any:
        """GET and parse JSON with clearer errors if decoding fails."""
        resp = self.session.get(url, params=params, headers=headers, timeout=timeout or self.timeout, **kwargs)
        resp.raise_for_status()
        # Prefer requests' decoder; fall back to manual if Content-Type is misleading.
        try:
            return resp.json()
        except ValueError as e:
            # Last-ditch try in case server sent text/plain but body is JSON.
            try:
                return json.loads(resp.text)
            except Exception:
                # Re-raise with context that includes URL and a short body preview.
                preview = resp.text[:200].replace("\n", " ")
                raise ValueError(f"JSON decode failed for {url!r}; body starts: {preview!r}") from e

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            LOG.debug("HttpClient.close() swallow", exc_info=True)
