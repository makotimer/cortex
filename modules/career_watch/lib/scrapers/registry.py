from __future__ import annotations

from .base import BaseScraper

# Global in-process registry: kind -> scraper class
_REGISTRY: dict[str, type[BaseScraper]] = {}


def register(cls: type[BaseScraper]) -> type[BaseScraper]:
    """
    Class decorator or direct call to register a scraper class.
    Requires cls.kind to be a non-empty string.
    """
    kind = getattr(cls, "kind", "") or ""
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError(f"Cannot register scraper {cls!r}: missing/empty 'kind'.")
    key = kind.strip().lower()
    if key in _REGISTRY and _REGISTRY[key] is not cls:
        # Allow idempotent re-registers of the same class; otherwise reject.
        raise ValueError(f"Scraper kind {key!r} already registered to {_REGISTRY[key]!r}.")
    _REGISTRY[key] = cls
    return cls


def get(kind: str) -> type[BaseScraper]:
    """
    Look up a scraper class by kind (case-insensitive).
    Raises KeyError if not found.
    """
    key = (kind or "").strip().lower()
    if key not in _REGISTRY:
        raise KeyError(f"No scraper registered for kind {kind!r}.")
    return _REGISTRY[key]


def all_kinds() -> dict[str, type[BaseScraper]]:
    """
    Return a shallow copy of the registry (useful for debugging/tests).
    """
    return dict(_REGISTRY)
