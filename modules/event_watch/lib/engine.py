"""Sequencing and the failure ladder. No source-specific logic lives here.

Ladder (design §8):

| Failure                  | Behaviour                                        |
|--------------------------|--------------------------------------------------|
| VPN unhealthy            | Bail. Trace `vpn_health_fail`. No state written   |
| Fetch fails              | Abort that source. Publish nothing, cancel nothing|
| LLM unavailable          | Publish without topics                            |
| >25% of window missing   | Cancel nothing, alert, keep previous state        |
| Unknown venue            | Fail loudly — do not publish that event           |
| Bus publish fails        | The kit retries, then dead-letters                |

A run that bails early writes no state, so nothing is ever falsely cancelled
after an outage.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta

from modules._shared import vpn_client

from . import logging_bridge, publish, state
from .config import Settings
from .scrapers.base import BaseEventScraper, ScraperError


class VPNUnavailableError(RuntimeError):
    """Raised when the VPN health check fails (fail-closed).

    Raising rather than returning None makes the runner record ok=False, so a
    missed run surfaces in the FAILED RUNS section instead of hiding as a no-op.
    """


@dataclass
class Plan:
    """What one run decided to do. Pure data — no I/O has happened yet."""

    upserts: list[dict] = field(default_factory=list)
    cancels: list[tuple[str, str]] = field(default_factory=list)
    unchanged: int = 0
    guard_tripped: bool = False
    missing_count: int = 0
    previous_in_window: int = 0
    #: key -> digest for everything currently observed, saved only on success.
    digests: dict[str, str] = field(default_factory=dict)


def reconcile(
    previous_sent: dict[str, str],
    payloads: list[dict],
    window_ms: tuple[int, int],
    guard: float = 0.25,
) -> Plan:
    """Diff this run's payloads against what was sent last time.

    Pure, so both sides of the guard boundary are testable without a feed.

    Only occurrences *inside the fetched window* are eligible for cancellation,
    which is what makes this correct at any window size: an occurrence outside
    the window was never looked for, so its absence means nothing.
    """
    plan = Plan()
    start_ms, end_ms = window_ms

    current: dict[str, dict] = {}
    for payload in payloads:
        key = state.occurrence_key(
            payload["series"]["source_series_uid"],
            payload["occurrence"]["source_occurrence_tid"],
        )
        current[key] = payload
        digest = state.payload_digest(payload)
        plan.digests[key] = digest
        if previous_sent.get(key) == digest:
            plan.unchanged += 1
        else:
            plan.upserts.append(payload)

    previous_in_window = {
        key for key in previous_sent if _tid_in_window(key, start_ms, end_ms)
    }
    plan.previous_in_window = len(previous_in_window)
    missing = previous_in_window - set(current)
    plan.missing_count = len(missing)

    if missing and previous_in_window and len(missing) > guard * len(previous_in_window):
        # That shape is a broken fetch, not forty cancellations.
        plan.guard_tripped = True
        return plan

    plan.cancels = sorted(_split_key(key) for key in missing)
    return plan


def _tid_in_window(key: str, start_ms: int, end_ms: int) -> bool:
    _uid, _sep, tid = key.partition("|")
    try:
        return start_ms <= int(tid) <= end_ms
    except ValueError:
        # A non-numeric occurrence id cannot be placed in time, so it is never
        # eligible for cancellation — absence of evidence, not evidence.
        return False


def _split_key(key: str) -> tuple[str, str]:
    uid, _sep, tid = key.partition("|")
    return uid, tid


def run_once(
    settings: Settings,
    scrapers: list[BaseEventScraper] | None = None,
    now: datetime | None = None,
) -> tuple[str, dict] | None:
    """One full cycle. Returns a summary only when something wants a human."""
    now = now or datetime.now(UTC)
    window_start = now.date()
    window_end = window_start + timedelta(days=settings.window_days)

    scrapers = scrapers if scrapers is not None else _default_scrapers(settings)
    # Verify against a host this run will actually fetch from. getattr, because
    # a scraper injected by a test need not declare one.
    _check_vpn(settings, next(
        (u for u in (getattr(s, "verify_url", "") for s in scrapers) if u), ""))
    publisher = publish.Publisher(settings.site, dry_run=settings.dry_run)

    attention: list[str] = []
    totals = {"upserted": 0, "cancelled": 0, "unchanged": 0, "rejected": 0}

    for scraper in scrapers:
        outcome = _run_source(scraper, settings, publisher, window_start, window_end, now)
        for key in totals:
            totals[key] += outcome.get(key, 0)
        attention.extend(outcome.get("attention", []))

    if attention:
        lines = "".join(f"<li>{line}</li>" for line in attention)
        return (f"<h2>event_watch</h2><ul>{lines}</ul>", {"subject": "event_watch needs attention"})
    return None


def _run_source(
    scraper: BaseEventScraper,
    settings: Settings,
    publisher: publish.Publisher,
    window_start: date,
    window_end: date,
    now: datetime,
) -> dict:
    slug = scraper.source_slug
    attention: list[str] = []
    # A source may see less far ahead than the run asked for. Narrowing happens
    # here rather than inside fetch because reconcile() measures disappearance
    # against this same window: a scraper that quietly clamped its own fetch
    # would have every occurrence beyond its horizon counted as vanished, and a
    # run would cancel a calendar it never actually looked at.
    # getattr, because a scraper injected by a test need not declare one.
    max_days = getattr(scraper, "max_window_days", None)
    if max_days:
        window_end = min(window_end, window_start + timedelta(days=int(max_days)))
    try:
        raw = scraper.fetch(window_start, window_end, skip_network=settings.skip_network)
    except Exception as exc:
        # Abort this source only. Publish nothing, cancel nothing, write no state.
        logging_bridge.error({
            "component": "event_watch.engine", "op": "fetch_failed",
            "source": slug, "error": repr(exc),
        })
        return {"attention": [f"{slug}: fetch failed — {exc!r}"]}

    payloads, rejected = scraper.normalize(raw)
    if rejected:
        logging_bridge.error({
            "component": "event_watch.engine", "op": "rejected_records",
            "source": slug, "count": len(rejected),
            "reasons": sorted({r["reason"] for r in rejected}),
        })
        attention.append(
            f"{slug}: {len(rejected)} record(s) not publishable — "
            + "; ".join(sorted({r["reason"] for r in rejected}))
        )

    window_ms = (
        int(datetime.combine(window_start, datetime.min.time(), UTC).timestamp() * 1000),
        int(datetime.combine(window_end, datetime.min.time(), UTC).timestamp() * 1000),
    )
    previous = state.load(settings.state_dir, slug)
    plan = reconcile(previous.get("sent") or {}, payloads, window_ms, settings.disappearance_guard)

    if plan.guard_tripped:
        logging_bridge.error({
            "component": "event_watch.engine", "op": "disappearance_guard",
            "source": slug, "missing": plan.missing_count,
            "previous_in_window": plan.previous_in_window,
            "guard": settings.disappearance_guard,
        })
        # Keep the old state: cancelling nothing is recoverable, cancelling the
        # whole calendar is not.
        return {
            "rejected": len(rejected),
            "attention": [
                *attention,
                f"{slug}: {plan.missing_count} of {plan.previous_in_window} in-window "
                f"occurrences vanished (>{settings.disappearance_guard:.0%}); cancelled nothing",
            ],
        }

    for payload in plan.upserts:
        publisher.upsert(payload)
    for series_uid, occurrence_tid in plan.cancels:
        publisher.cancel({
            "schema_version": publish.SCHEMA_VERSION,
            "source": {"slug": slug, "name": scraper.source_name},
            "series": {"source_series_uid": series_uid},
            "occurrence": {"source_occurrence_tid": occurrence_tid},
            "cancel_note": "no longer listed by source",
        })

    counts = {
        "upserted": len(plan.upserts),
        "cancelled": len(plan.cancels),
        "unchanged": plan.unchanged,
        "rejected": len(rejected),
    }
    window = {"start": window_start.isoformat(), "end": window_end.isoformat()}
    # Sent on every run, including runs that changed nothing — otherwise a quiet
    # source and a dead injector look identical at /admin/intake.
    publisher.report(slug, window, counts, now.isoformat().replace("+00:00", "Z"))

    logging_bridge.activity({
        "component": "event_watch.engine", "op": "summary",
        "source": slug, "window": window, "dry_run": settings.dry_run, **counts,
    })

    if not settings.dry_run:
        state.save(settings.state_dir, slug, plan.digests, {"start_ms": window_ms[0], "end_ms": window_ms[1]})

    return {**counts, "attention": attention}


def _check_vpn(settings: Settings, verify_url: str = "") -> None:
    """Fail-closed, but on whether the exit can actually reach the source.

    The old check asked gluetun whether it had a public IP. That question has
    been answered "yes" by an exit that could not reach the target at all, so
    a run passed the gate and then failed its fetch. This switches exits until
    one verifiably works, and only bails when none does.
    """
    if not settings.proxy_url:
        return
    control_url = os.getenv("VPN_CONTROL_URL", "http://vpn:8000")
    try:
        rotate_timeout = float(
            os.getenv("VPN_ROTATE_TIMEOUT") or vpn_client.DEFAULT_ROTATE_TIMEOUT
        )
    except ValueError:
        rotate_timeout = vpn_client.DEFAULT_ROTATE_TIMEOUT

    gluetun = vpn_client.GluetunClient(
        control_url=control_url,
        rotate_timeout=rotate_timeout,
    )
    outcome = gluetun.switch_until_usable(
        proxy_url=settings.proxy_url,
        verify_url=verify_url or "https://tockify.com/",
        attempts=int(os.getenv("VPN_SWITCH_ATTEMPTS") or 3),
        prefer_new_ip=settings.rotate_vpn_per_run,
    )
    # Logged whether or not it worked: the (ip, ok) pairs are the only record
    # of which exits were tried.
    logging_bridge.activity({
        "component": "event_watch.engine", "op": "vpn_switch",
        "ok": outcome.ok, "ip": outcome.ip, "changed": outcome.changed,
        "attempts": outcome.attempts, "seconds": round(outcome.seconds, 2),
        "reason": outcome.reason,
        "tried": [{"ip": ip, "ok": ok} for ip, ok in outcome.tried],
        "restarts": outcome.restarts,
        "verify_url": verify_url,
    })
    if not outcome.ok:
        # Kept for compatibility: cortex's nightly anomaly detection keys off
        # this op, and design §8 specifies it.
        logging_bridge.activity({
            "component": "event_watch.engine", "op": "vpn_health_fail",
            "control_url": control_url, "reason": outcome.reason,
        })
        raise VPNUnavailableError(
            f"no usable VPN exit after {outcome.attempts} attempt(s): {outcome.reason}"
        )


def _default_scrapers(settings: Settings) -> list[BaseEventScraper]:
    from .scrapers.bcschamber import BcsChamberScraper
    from .scrapers.bryantx import BryanTxScraper
    from .scrapers.bush41 import Bush41Scraper
    from .scrapers.bvso import BvsoScraper
    from .scrapers.bvmuseum import BvMuseumScraper
    from .scrapers.challenge import ChallengeScraper
    from .scrapers.cityspark import CitySparkScraper
    from .scrapers.destbryan import DestBryanScraper
    from .scrapers.hyperbole import HyperboleScraper
    from .scrapers.kbtx import KbtxScraper
    from .scrapers.lakewalk import LakeWalkScraper
    from .scrapers.tamu import TamuScraper
    from .scrapers.tamumusic import TamuMusicScraper
    from .scrapers.tockify import TockifyScraper
    from .scrapers.ttc import TtcScraper
    from .scrapers.visitcstx import VisitCstxScraper

    registry = {
        "tockify": TockifyScraper,
        "challenge": ChallengeScraper,
        "kbtx": KbtxScraper,
        "cityspark": CitySparkScraper,
        "tamu": TamuScraper,
        "tamumusic": TamuMusicScraper,
        "bryantx": BryanTxScraper,
        "lakewalk": LakeWalkScraper,
        "destbryan": DestBryanScraper,
        "visitcstx": VisitCstxScraper,
        "bvmuseum": BvMuseumScraper,
        "bush41": Bush41Scraper,
        "bvso": BvsoScraper,
        "hyperbole": HyperboleScraper,
        "bcschamber": BcsChamberScraper,
        "ttc": TtcScraper,
    }
    out: list[BaseEventScraper] = []
    for kind in settings.kinds:
        cls = registry.get(kind)
        if cls is None:
            raise ScraperError(f"unknown event scraper kind {kind!r}")
        if cls in {
            KbtxScraper, TamuScraper, TamuMusicScraper,
            DestBryanScraper, VisitCstxScraper,
        }:
            out.append(cls(proxy_url=settings.proxy_url, state_dir=settings.state_dir))
        else:
            out.append(cls(proxy_url=settings.proxy_url))
    return out
