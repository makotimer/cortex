"""Scraper interface for one event source.

Mirrors ``career_watch/lib/scrapers/base.py``. The split that matters is I/O
versus pure: ``fetch`` touches the network, ``normalize`` must not. Keeping them
as two methods is what lets every per-source rule be tested from saved fixtures
with no network, no Redis and no VPN.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date
from typing import Any


class ScraperError(Exception):
    """Base exception for event scraper failures."""


class UnknownVenueError(ScraperError):
    """A record names a venue with no known area mapping.

    Fails loudly by design (§5): the contract has no way to express "somewhere in
    Brazos County", and guessing an area sends families to the wrong town.
    """


@dataclass
class RawEvent:
    """One record as the source shaped it, plus the identity keys normalization needs."""

    series_uid: str
    occurrence_tid: str
    record: dict[str, Any] = field(default_factory=dict)
    #: Supplementary text keyed by series uid (e.g. the ICS description, which
    #: preserves URLs the JSON flattens away).
    supplement: dict[str, Any] = field(default_factory=dict)


class BaseEventScraper(ABC):
    """One source. Instances are cheap and single-use."""

    kind: str = ""
    #: Stable source identity, half of the contract's idempotency key.
    source_slug: str = ""
    source_name: str = ""

    @abstractmethod
    def fetch(
        self, window_start: date, window_end: date, *, skip_network: bool
    ) -> list[RawEvent]:
        """Every raw record in the window. No side effects, no email."""
        raise NotImplementedError

    @abstractmethod
    def normalize(self, raw: list[RawEvent]) -> tuple[list[dict], list[dict]]:
        """Raw records -> (contract payloads, rejected records).

        PURE: no network, no clock, no state. Rejections are returned rather
        than raised so that one unmappable venue cannot cost the whole run.
        """
        raise NotImplementedError
