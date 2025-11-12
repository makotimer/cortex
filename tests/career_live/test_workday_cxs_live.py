# tests/career_live/workday_cxs_live.py
from __future__ import annotations

import os
import types

import pytest

try:
    from modules.career_watch.lib.scrapers.workday_cxs import WorkdayCxSScraper
except ModuleNotFoundError:
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[1]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from modules.career_watch.lib.scrapers.workday_cxs import WorkdayCxSScraper


def _make_spec(start_targets, delay_seconds=3.0, limit=5, max_pages=1, base_payload=None, source="workday:cxs"):
    params = {
        "start_targets": start_targets,
        "delay_seconds": delay_seconds,
        "limit": limit,  # ~5 per tenant
        "max_pages": max_pages,  # single page for speed
    }
    if base_payload is not None:
        params["base_payload"] = base_payload
    return types.SimpleNamespace(source=source, params=params)


def _print_results(label: str, results):
    total_items = sum(len(r.items or []) for r in results)
    total_errors = sum(len(r.errors or []) for r in results)
    print(f"\n[{label}] tenants: {len(results)}  items: {total_items}  errors: {total_errors}")
    for r in results:
        print(f"  - source={getattr(r, 'source', '?')}  items={len(r.items or [])}  errors={len(r.errors or [])}")
        for e in r.errors or []:
            print(f"      error: {e}")
        for p in r.items or []:
            print(f"      • {p.title}  [{p.url}]")


@pytest.mark.live
def test_workday_cxs_multi_tenants_live():
    """
    Use the CxS API for stability; prints ~5 items per tenant (limit=5).
    If a tenant returns 0, we still validate structure and print errors if any.
    """
    scraper = WorkdayCxSScraper()

    # NOTE: You can pass the original list URLs; the scraper infers the CxS endpoint.
    start_targets = [
        [
            "https://tamus.wd1.myworkdayjobs.com/TAMU_External",
            "workday:tamus",
            # {"searchText": "rellis"},
        ],
    ]

    spec = _make_spec(start_targets=start_targets, delay_seconds=2.0, limit=5, max_pages=1)

    results = scraper.run(person_env="LiveTest", specs=[spec], skip_network=False)
    # assert isinstance(results, list) and len(results) == 5

    _print_results("workday-cxs-multi-tenants", results)

    # If any items present, ensure basic shape
    for r in results:
        for p in r.items or []:
            assert p.title.strip()
            assert p.url.startswith("http")
