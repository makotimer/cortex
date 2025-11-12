# career_watch/http_client.py
from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any, Optional

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
    ):
        self.timeout = float(timeout)
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

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
        return resp.text

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
