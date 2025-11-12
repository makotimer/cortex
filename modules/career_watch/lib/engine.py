"""
Engine for running career watch scrapers, detecting new postings, and rendering emails.

Features:
  - Parallel execution by scraper kind
  - DB deduplication via `filter_new`
  - Special modes: `ingest_only_no_email`, `email_all_even_if_seen`
  - Dependency injection for testability (`get_scraper`)
  - Comprehensive logging via `logging_bridge`
"""

from __future__ import annotations

import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import db, logging_bridge, render
from .config import ScraperConfig, Settings
from .models import Posting, ScrapeResult
from .scrapers.base import BaseScraper


# =============================================================================
# DEFAULT SCRAPER LOOKUP (PRODUCTION)
# =============================================================================
def _default_get_scraper(kind: str) -> type[BaseScraper]:
    """
    Resolve scraper class from registry.

    This is the default behavior in production. It is only called if no
    `get_scraper` override is provided.
    """
    from .scrapers.registry import get as get_scraper_class

    return get_scraper_class(kind)


# =============================================================================
# MAIN ORCHESTRATOR
# =============================================================================
def run_once(
    settings: Settings,
    get_scraper: Callable[[str], type[BaseScraper]] | None = None,
) -> tuple[str, dict] | None:
    """
    Run one complete cycle of scraping, deduplication, and optional email rendering.

    Args:
        settings: Configuration including DB path, flags, and scraper specs.
        get_scraper: Optional override to inject scraper classes (for testing).

    Returns:
        (html, meta_dict) if email should be sent, else None.
    """
    start_ns = time.perf_counter_ns()
    person_env = settings.person_env

    # Resolve scraper lookup function (test override or production default)
    get_scraper_func = get_scraper or _default_get_scraper

    # Group scraper configs by kind (e.g., 'lever', 'greenhouse')
    by_kind: dict[str, list[ScraperConfig]] = settings.group_by_kind()

    durations_us: dict[str, int] = {}
    planned_specs_by_kind: dict[str, int] = {k: len(v) for k, v in by_kind.items()}
    all_results: list[ScrapeResult] = []

    # -------------------------------------------------------------------------
    # INNER: Run one scraper kind in a thread
    # -------------------------------------------------------------------------
    def _run_kind(kind: str, specs: list[ScraperConfig]) -> tuple[str, list[ScrapeResult], int, bool]:
        t0 = time.perf_counter_ns()

        # Skip network I/O if requested
        if settings.skip_network:
            logging_bridge.activity({
                "component": "career_watch.engine",
                "op": "skipped_kind",
                "person": person_env,
                "kind": kind,
                "reason": "skip_network",
                "spec_count": len(specs),
                "sources": [s.source for s in specs],
            })
            return (kind, [], 0, True)

        # Resolve and instantiate scraper
        scraper_cls = get_scraper_func(kind)
        scraper: BaseScraper = scraper_cls()
        results = scraper.run(person_env, specs, skip_network=settings.skip_network)

        dt_us = int((time.perf_counter_ns() - t0) // 1000)
        return (kind, results, dt_us, False)

    # -------------------------------------------------------------------------
    # EXECUTE SCRAPERS IN PARALLEL (one thread per kind)
    # -------------------------------------------------------------------------
    with ThreadPoolExecutor(max_workers=min(len(by_kind) or 1, settings.max_threads)) as pool:
        futures = {pool.submit(_run_kind, k, specs): k for k, specs in by_kind.items()}
        for fut in as_completed(futures):
            kind = futures[fut]
            try:
                k, results, dt_us, _skipped = fut.result()
                durations_us[k] = dt_us
                all_results.extend(results)
            except Exception as e:
                durations_us[kind] = durations_us.get(kind, 0)
                logging_bridge.error({
                    "component": "career_watch.engine",
                    "op": "scraper_run",
                    "kind": kind,
                    "error": repr(e),
                })

    # -------------------------------------------------------------------------
    # MERGE RESULTS (pre-dedupe)
    # -------------------------------------------------------------------------
    all_postings: list[Posting] = []
    pre_by_source: dict[str, list[Posting]] = {}
    for res in all_results:
        pre_by_source.setdefault(res.source, []).extend(res.items)
        all_postings.extend(res.items)

    # -------------------------------------------------------------------------
    # DEDUPLICATE: insert only new postings into DB
    # -------------------------------------------------------------------------
    new_postings = db.filter_new(settings.sqlite_path, person_env, all_postings)
    new_by_source: dict[str, list[Posting]] = {}
    for p in new_postings:
        new_by_source.setdefault(p.source, []).append(p)

    # -------------------------------------------------------------------------
    # SUMMARY LOG (always emitted)
    # -------------------------------------------------------------------------
    found_by_source_counts = {src: len(items) for src, items in pre_by_source.items()}
    new_by_source_counts = {src: len(items) for src, items in new_by_source.items()}
    total_us = int((time.perf_counter_ns() - start_ns) // 1000)

    logging_bridge.activity({
        "component": "career_watch.engine",
        "op": "summary",
        "person": person_env,
        "skip_network": settings.skip_network,
        "planned_specs_by_kind": planned_specs_by_kind,
        "found_by_source": found_by_source_counts,
        "new_by_source": new_by_source_counts,
        "durations_us": durations_us,
        "total_us": total_us,
    })

    # -------------------------------------------------------------------------
    # INGEST-ONLY MODE: just update DB, no email
    # -------------------------------------------------------------------------
    if settings.ingest_only_no_email:
        logging_bridge.activity({
            "component": "career_watch.engine",
            "op": "ingest_only",
            "person": person_env,
            "new_total": sum(new_by_source_counts.values()),
            "sources": sorted(new_by_source_counts.keys()),
            "durations_us": durations_us,
            "total_us": total_us,
        })
        return None

    # -------------------------------------------------------------------------
    # DECIDE WHAT TO RENDER
    # -------------------------------------------------------------------------
    if settings.email_all_even_if_seen:
        to_render = pre_by_source
        msg = _summary_message(pre_by_source, person_env, label="all postings")
        new_total = sum(new_by_source_counts.values())
    else:
        to_render = new_by_source
        if not to_render:
            logging_bridge.activity({
                "component": "career_watch.engine",
                "op": "no_new",
                "person": person_env,
                "durations_us": durations_us,
                "total_us": total_us,
            })
            return None
        msg = _summary_message(new_by_source, person_env, label="new postings")
        new_total = sum(new_by_source_counts.values())

    # -------------------------------------------------------------------------
    # BUILD SUBJECT (only if we're sending)
    # -------------------------------------------------------------------------
    sources_with_jobs = [src for src, items in new_by_source.items() if items]
    if len(sources_with_jobs) == 1:
        subject = f"Career Watch — {new_total} new at {sources_with_jobs[0]}"
    else:
        subject = f"Career Watch — {new_total} new jobs ({len(sources_with_jobs)} companies)"

    # -------------------------------------------------------------------------
    # RENDER HTML
    # -------------------------------------------------------------------------
    tables_html = render.build_tables(to_render)
    heading = f"Career Watch — {person_env}"
    html = render.wrap_document(tables_html, heading=heading, intro=msg)

    # -------------------------------------------------------------------------
    # BUILD META (for caller)
    # -------------------------------------------------------------------------
    by_source_counts = {src: len(items) for src, items in to_render.items()}

    meta_dict = {
        "message": msg,
        "person": person_env,
        "new_total": new_total,
        "by_source": by_source_counts,
        "subject": subject,
        "durations_us": {**durations_us, "_total_us": total_us},
        "email_all_even_if_seen": settings.email_all_even_if_seen,
        "ingest_only_no_email": settings.ingest_only_no_email,
    }

    logging_bridge.activity({
        "component": "career_watch.engine",
        "op": "rendered",
        "person": person_env,
        "counts": by_source_counts,
        "new_total": new_total,
        "email_all_even_if_seen": settings.email_all_even_if_seen,
        "durations_us": durations_us,
        "total_us": total_us,
    })

    return (html, meta_dict)


# =============================================================================
# HELPER: human-readable summary message
# =============================================================================
def _summary_message(
    by_source: dict[str, list[Posting]],
    person_env: str,
    *,
    label: str,
) -> str:
    """
    Generate a friendly summary line like:
        "3 new postings across 2 sources for Alice"
    """
    total = sum(len(v) for v in by_source.values())
    num_sources = len([src for src, items in by_source.items() if items])
    return f"{total} {label} across {num_sources} sources for {person_env}"
