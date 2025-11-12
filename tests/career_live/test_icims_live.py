# tests/test_icims_live.py
"""
Live test for multi-tenant iCIMS scraper with include/exclude filters.
"""

from __future__ import annotations

import os
import types

import pytest

try:
    from modules.career_watch.lib.scrapers.icims import IcimsScraper
except ModuleNotFoundError:
    import sys
    from pathlib import Path

    ROOT = Path(__file__).resolve().parents[1]
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from modules.career_watch.lib.scrapers.icims import IcimsScraper


def _make_spec(
    *,
    start_urls: list[list[str]] | None = None,
    search_url: str | None = None,
    source_label: str = "icims:test",
    filters: list[str] | None = None,
    excludes: list[str] | None = None,
) -> types.SimpleNamespace:
    params: dict = {}
    if start_urls:
        params["start_urls"] = start_urls
    if search_url:
        params["search_url"] = search_url
    if filters:
        params["filters"] = filters
    if excludes:
        params["excludes"] = excludes
    return types.SimpleNamespace(source=source_label, params=params)


def _print_results(label: str, results: list, max_items: int = 10) -> None:
    total = sum(len(r.items or []) for r in results)
    print(f"\n[{label}] items: {total}")
    for r in results:
        items = r.items or []
        limit = min(max_items, len(items))
        print(f"  - {r.source}: {len(items)} jobs")
        for p in items[:limit]:
            print(f"      • {p.title}  [{p.url}]")
        if len(items) > limit:
            print(f"      ... {len(items) - limit} more")


@pytest.mark.live
def test_icims_peraton_filters_live():
    scraper = IcimsScraper()

    search_url = (
        "https://careers-peraton.icims.com/jobs/search"
        "?ss=1&searchKeyword=Remote+work+allowed+100%25&searchRelation=keyword_all&in_iframe=1"
    )

    spec = _make_spec(
        search_url=search_url,
        source_label="peraton:remote",
        filters=["engineer", "developer"],
        excludes=["intern", "entry"],
    )

    results = scraper.run(person_env="LiveTest", specs=[spec], skip_network=False)
    assert len(results) == 1
    r = results[0]

    _print_results("peraton-filtered", results)
    for p in r.items:
        assert any(word in p.title.lower() for word in ["engineer", "developer"])
        assert not any(ex in p.title.lower() for ex in ["intern", "entry"])


@pytest.mark.live
def test_icims_gdms_filters_live():
    scraper = IcimsScraper()

    search_url = "https://careers-gdms.icims.com/jobs/search?ss=1&searchLocation=-258926&in_iframe=1"

    spec = _make_spec(
        search_url=search_url,
        source_label="gdms:remote",
        # filters=["engineer", "developer"],
        # excludes=["intern", "entry"],
    )

    results = scraper.run(person_env="LiveTest", specs=[spec], skip_network=False)
    assert len(results) == 1
    # r = results[0]

    _print_results("gdms-filtered", results)
    # for p in r.items:
    #     assert any(word in p.title.lower() for word in ["engineer", "developer"])
    #     assert not any(ex in p.title.lower() for ex in ["intern", "entry"])


# @pytest.mark.live
# def test_icims_multi_tenant_live():
#     """Test two iCIMS tenants (Peraton + mock)."""
#     scraper = IcimsScraper()

#     spec = _make_spec(
#         start_urls=[
#             [
#                 "https://careers-peraton.icims.com/jobs/search?ss=1&searchKeyword=Remote+work+allowed+100%25&searchRelation=keyword_all",
#                 "peraton:remote",
#             ],
#             ["https://careers-gdms.icims.com/jobs/search?ss=1&searchLocation=-258926-", "gd:remote"],
#         ],
#         filters=["manager", "lead"],
#     )

#     results = scraper.run(person_env="LiveTest", specs=[spec], skip_network=False)
#     assert len(results) == 2
#     _print_results("multi-tenant", results)
