# career_watch/scrapers/_targets.py
"""Shared helper for normalising [url, label] target lists from scraper params."""
from __future__ import annotations


def parse_url_label_list(
    raw: object,
    *,
    url_keys: tuple[str, ...] = ("url", "list_url", "search_url"),
    source_keys: tuple[str, ...] = ("source", "source_label"),
) -> list[tuple[str, str]]:
    """
    Normalise a list of scraper target specs into (url, label) pairs.

    Accepts either:
      - [[url, label], ...]
      - [{"url": ..., "source": ...}, ...]
    or a mix.  Any item that can't produce both a non-empty url AND label is skipped.
    """
    out: list[tuple[str, str]] = []
    if not raw or not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            url = str(item[0]).strip()
            label = str(item[1]).strip()
            if url and label:
                out.append((url, label))
        elif isinstance(item, dict):
            url = next((str(item.get(k) or "").strip() for k in url_keys if item.get(k)), "")
            label = next((str(item.get(k) or "").strip() for k in source_keys if item.get(k)), "")
            if url and label:
                out.append((url, label))
    return out
