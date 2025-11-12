# tests/career_live/test_avature_live.py
"""
Live smoke test for the Avature scraper (ManTech fully-remote jobs).
"""

from __future__ import annotations

import os
import types

import pytest

# Adjust path based on your actual location
try:
    from modules.career_watch.lib.scrapers.avature import AvatureScraper
except ModuleNotFoundError:
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[2]  # go up two levels: tests/career_live/ -> project root
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from modules.career_watch.lib.scrapers.avature import AvatureScraper


def _make_spec(
    *,
    start_urls: list[list[str]] | None = None,
    search_url: str | None = None,
    source_label: str = "avature:multi-tenant",
    delay_seconds: float = 2.5,
    query: str | None = None,
    per_page: int | None = None,
) -> types.SimpleNamespace:
    params: dict = {"delay_seconds": delay_seconds}
    if start_urls:
        params["start_urls"] = start_urls
    elif search_url:
        params["search_url"] = search_url
    if query is not None:
        params["query"] = query
    if per_page is not None:
        params["per_page"] = per_page

    return types.SimpleNamespace(source=source_label, params=params)


def _print_results(label: str, results: list, max_items: int | None = None) -> None:
    if max_items is None:
        env_max = os.getenv("AVATURE_MAX_PRINT")
        max_items = int(env_max) if env_max else 25

    total_items = sum(len(r.items or []) for r in results)
    total_errors = sum(len(r.errors or []) for r in results)
    print(f"\n[{label}] tenants: {len(results)}  items: {total_items}  errors: {total_errors}")

    for r in results:
        items = r.items or []
        limit = len(items) if max_items is None else min(max_items, len(items))
        print(f"  - source={getattr(r, 'source', '?')}  items={len(items)}  errors={len(r.errors or [])}")
        for e in r.errors or []:
            print(f"      error: {e}")
        for p in items[:limit]:
            print(f"      • {p.title}  [{p.url}]")


@pytest.mark.live
def test_avature_mantech_fully_remote_live():
    scraper = AvatureScraper()

    search_url = (
        "https://mantech.avature.net/en_US/careers/SearchJobs/"
        "?1328=%5B7006%5D&1328_format=1841&listFilterMode=1&jobRecordsPerPage=500"
    )

    spec = _make_spec(
        search_url=search_url,
        source_label="mantech:fully_remote",
        delay_seconds=2.0,
        query=os.getenv("AVATURE_TEST_QUERY"),
        per_page=500,
    )

    results = scraper.run(person_env="LiveTest", specs=[spec], skip_network=False)

    assert isinstance(results, list) and len(results) == 1
    r = results[0]
    assert hasattr(r, "items") and hasattr(r, "errors")

    _print_results("mantech-fully-remote", results)

    if r.items:
        for p in r.items:
            assert p.title.strip()
            assert p.url.startswith("http")
            assert p.source == "mantech:fully_remote"
