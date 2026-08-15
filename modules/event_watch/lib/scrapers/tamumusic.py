"""Texas A&M Music Activities calendar, via the LiveWhale group JSON.

Same host and record shape as ``tamu``. This is the Music Activities group
(gid 151), not the campus-wide category feed. The main calendar carries
syndicated copies with different ids and ``parent`` set to these native ids;
downstream duplicate detection owns that overlap. ``tamu`` is left unchanged.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

from . import tamu
from .base import RawEvent, ScraperError

GROUP = "Music Activities"
JSON_URL = "https://calendar.tamu.edu/live/json/events"
TIMEZONE = tamu.TIMEZONE
CHUNK_DAYS = tamu.CHUNK_DAYS


class TamuMusicScraper(tamu.TamuScraper):
    kind = "tamumusic"
    source_slug = "tamu-music"
    source_name = "Texas A&M Music Activities"
    verify_url = f"{JSON_URL}/group/{quote(GROUP)}/max/1"

    def fetch(self, window_start: date, window_end: date, *, skip_network: bool) -> list[RawEvent]:
        if skip_network:
            return []
        from modules._shared.http import HttpClient

        client = self._client or HttpClient(
            user_agent="CortexEventWatch/1.0 (+https://discoverbcs.org)",
            proxy_url=self._proxy_url,
            proxy_env=None,
        )
        self._client = client

        seen: set[tuple[str, str]] = set()
        raw: list[RawEvent] = []
        chunk_start = window_start
        while chunk_start < window_end:
            chunk_end = min(window_end, chunk_start + timedelta(days=CHUNK_DAYS))
            for record in _fetch_chunk(client, chunk_start, chunk_end):
                item = tamu.to_raw(record)
                key = (item.series_uid, item.occurrence_tid)
                if not item.series_uid or not item.occurrence_tid or key in seen:
                    continue
                seen.add(key)
                raw.append(item)
            chunk_start = chunk_end

        cache: dict[str, dict] = {}
        if self._state_dir:
            from .. import state

            cache = state.load_addresses(self._state_dir, self.source_slug)
        tamu.enrich_places(raw, resolve=self._resolve or tamu.kbtx.resolve_address, cache=cache)
        if self._state_dir:
            from .. import state

            state.save_addresses(self._state_dir, self.source_slug, cache)
        return raw

    def normalize(self, raw: list[RawEvent]) -> tuple[list[dict], list[dict]]:
        payloads, rejected = super().normalize(raw)
        for payload in payloads:
            topics = set(payload["series"].get("topics") or [])
            topics.add("music")
            payload["series"]["topics"] = sorted(topics)
        return payloads, rejected


def _fetch_chunk(client: Any, start: date, end: date) -> list[dict]:
    url = f"{JSON_URL}/group/{quote(GROUP)}/start_date/{start.isoformat()}/end_date/{end.isoformat()}/max/400"
    data = client.get_json(url)
    if not isinstance(data, list):
        raise ScraperError(f"tamumusic: expected a JSON array from {url}")
    return data
