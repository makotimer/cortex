# tests/career_live/test_lever_live.py
from __future__ import annotations

import os
import types

import pytest

# Import your scraper. Adjust the import path if your project structure differs.
# If you placed LeverScraper at modules/career_watch/lib/scrapers/lever.py, this will work:
from modules.career_watch.lib.scrapers.lever import LeverScraper


def _make_spec(
    *,
    start_urls: list[list[str]] | list[tuple[str, str]],
    delay_seconds: float = 3.0,
    query: str | None = None,
    exclude: list[str] | None = None,
    source_label: str = "lever:multi-tenant",
):
    """
    Build a ScraperConfig-like object using SimpleNamespace to avoid importing your config classes.
    LeverScraper only needs `.source` and `.params`.
    """
    params = {
        "start_urls": start_urls,
        "delay_seconds": delay_seconds,
    }
    if query is not None:
        params["query"] = query
    if exclude is not None:
        params["exclude"] = exclude
    return types.SimpleNamespace(source=source_label, params=params)


def _print_results(label: str, results, max_items: int | None = None) -> None:
    # allow override via env (e.g., LEVER_MAX_PRINT=999)
    if max_items is None:
        env_max = os.getenv("LEVER_MAX_PRINT")
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
def test_lever_palantir_basic_live():
    """
    Live smoke test: fetch Palantir Dev jobs list from Lever.
    This will NOT fail the suite if zero items are returned (content changes are normal),
    but it will assert the shape of ScrapeResult/Posting objects when present.
    """
    scraper = LeverScraper()
    spec = _make_spec(
        start_urls=[["https://jobs.lever.co/palantir?team=Dev", "lever:palantir"]],
        delay_seconds=2.5,
        # query=None,  # uncomment to filter by substring in title
    )

    results = scraper.run(person_env="LiveTest", specs=[spec], skip_network=False)

    # We expect exactly one tenant-result for the single start_urls entry.
    assert isinstance(results, list) and len(results) == 1

    r = results[0]
    # results should carry items/errors lists
    assert hasattr(r, "items") and hasattr(r, "errors")

    _print_results("palantir-basic", results, max_items=25)

    # If we did receive items, validate a few fields.
    if r.items:
        for p in r.items:
            assert getattr(p, "title", "").strip() != ""
            assert getattr(p, "url", "").startswith("https://jobs.lever.co/")
            assert getattr(p, "source", "") == "lever:palantir"


@pytest.mark.live
def test_lever_multiple_tenants_and_query_live():
    """
    Live test: multiple tenants and a title filter (query).
    Adjust or add tenants to taste; the test is tolerant to zero results but validates structure.
    """
    scraper = LeverScraper()
    spec = _make_spec(
        start_urls=[
            ["https://jobs.lever.co/palantir?team=Dev", "lever:palantir"],
            # Add another Lever tenant you care about; safe to leave just Palantir as well.
            # ["https://jobs.lever.co/acutronicse?team=Engineering", "lever:acutronicse"],
        ],
        delay_seconds=2.0,
        query=os.getenv("LEVER_TEST_QUERY", "Software"),  # override via env to experiment
        exclude=["on-site", "onsite", "internship"],
    )

    results = scraper.run(person_env="LiveTest", specs=[spec], skip_network=False)

    assert isinstance(results, list) and len(results) >= 1  # one per tenant

    _print_results("multi-tenant-with-query", results, max_items=25)

    # For any items returned, ensure the query filter was respected.
    for r in results:
        for p in r.items or []:
            assert "software" in p.title.lower()
            assert p.url.startswith("https://jobs.lever.co/")
