# tests/career_live/test_bae_live.py
from __future__ import annotations

import os
import types

import pytest

# Optional safety net if PYTHONPATH is not set
try:
    from modules.career_watch.lib.scrapers.bae import BaePhenomAPIScraper
except ModuleNotFoundError:
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[1]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from modules.career_watch.lib.scrapers.bae import BaePhenomAPIScraper


def _make_spec(*, start_targets, delay_seconds=4.0, page_size=50, max_pages=3, source="bae:phenom-api"):
    params = {
        "start_targets": start_targets,
        "delay_seconds": delay_seconds,
        "page_size": page_size,
        "max_pages": max_pages,
    }
    return types.SimpleNamespace(source=source, params=params)


def _print_results(label: str, results, max_items: int | None = None) -> None:
    if max_items is None:
        env_max = os.getenv("BAE_MAX_PRINT")
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
def test_bae_remote_engtech_live():
    """
    Live smoke test against BAE Widgets API with 'remote eng/tech' filters.
    Prints kept postings (post-filter normalization) and validates structure.
    """
    scraper = BaePhenomAPIScraper()
    # Target mirrors your working example; page_size/max_pages are controlled by the scraper.
    start_targets = [
        [
            "https://jobs.baesystems.com/widgets",
            "bae:remote-engtech",
            {
                "ddoKey": "refineSearch",
                "pageName": "search-results",
                "pageId": "page1-migration-ds",
                "from": 0,
                "size": 10,
                "sortBy": "",
                "counts": True,
                "jobs": True,
                "global": True,
                "jsdsource": "facets",
                "selected_fields": {
                    "category": ["Engineering & Technology"],
                    "physicalLocation": ["Remote Work Considered", "Full-time remote"],
                },
                "keywords": "",
                "subsearch": "engineer",
                "lang": "en_global",
                "country": "global",
                "deviceType": "desktop",
                "siteType": "external",
                "locationData": {},
                "isSliderEnable": False,
                "ak": "",
                "all_fields": [
                    "category",
                    "country",
                    "state",
                    "city",
                    "sector",
                    "isSecurityClearanceRequired",
                    "careerLevel",
                ],
                "clearAll": False,
            },
        ]
    ]

    spec = _make_spec(start_targets=start_targets, delay_seconds=2.5, page_size=50, max_pages=5)

    results = scraper.run(person_env="LiveTest", specs=[spec], skip_network=False)
    assert isinstance(results, list) and len(results) == 1

    r = results[0]
    assert hasattr(r, "items") and hasattr(r, "errors")
    _print_results("bae-remote-engtech", results)

    if r.items:
        for p in r.items:
            assert p.title.strip()
            assert p.url.startswith("http")
            assert p.source == "bae:remote-engtech"
