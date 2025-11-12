# tests/career_live/test_boeing_live.py
from __future__ import annotations

import os
import types

import pytest

# Optional safety net if PYTHONPATH isn't set when running from repo root.
try:
    from modules.career_watch.lib.scrapers.boeing import BoeingScraper
except ModuleNotFoundError:
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[1]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from modules.career_watch.lib.scrapers.boeing import BoeingScraper


def _make_spec(
    *,
    start_urls,
    delay_seconds: float = 3.0,
    query: str | None = None,
    source_label: str = "boeing:multi-tenant",
):
    params = {"start_urls": start_urls, "delay_seconds": delay_seconds}
    if query is not None:
        params["query"] = query
    return types.SimpleNamespace(source=source_label, params=params)


def _print_results(label: str, results, max_items: int | None = None) -> None:
    if max_items is None:
        env_max = os.getenv("BOEING_MAX_PRINT")
        max_items = int(env_max) if env_max else None

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
def test_boeing_virtual_software_basic_live():
    """
    Live smoke test: Boeing remote software page.
    Asserts structure and prints kept postings (post-filter).
    """
    scraper = BoeingScraper()
    spec = _make_spec(
        start_urls=[
            [
                "https://jobs.boeing.com/employment/remote-engineering-software-jobs/185/2649/1000000000100/2",
                "boeing:virtual-software",
            ]
        ],
        delay_seconds=2.5,
        query=os.getenv("BOEING_TEST_QUERY") or None,
    )

    results = scraper.run(person_env="LiveTest", specs=[spec], skip_network=False)
    assert isinstance(results, list) and len(results) == 1

    r = results[0]
    assert hasattr(r, "items") and hasattr(r, "errors")
    _print_results("boeing-virtual-software", results)

    if r.items:
        for p in r.items:
            assert p.title.strip()
            assert p.url.startswith("http")
            assert p.source == "boeing:virtual-software"
