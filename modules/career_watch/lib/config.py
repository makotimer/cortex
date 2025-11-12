from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .utils import truthy


# -----------------------------
# Exceptions
# -----------------------------
class ConfigError(ValueError):
    """Raised when provided kwargs/env cannot form a valid Settings."""


# -----------------------------
# Models
# -----------------------------
@dataclass(frozen=True)
class ScraperConfig:
    """
    One logical scraper invocation specification.
    - kind: scraper family (e.g., "workday", "lever", "greenhouse", "stub")
    - source: human-stable label used in output & DB (e.g., "workday:acme")
    - params: arbitrary dict passed to the scraper; scraper will sequence
              multiple companies internally if given.
    """

    kind: str
    source: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass
class Settings:
    """
    Canonical configuration for a 'career_watch' run.

    Selection is file-based only. We always load a flat list of scrapers from:
        /app/local/config/career_watch_groups.<person>.json

    where <person> is the **resolved human name** carried in `person_env`
    (the runner already replaced the *_env value with the environment value).

    Optionally, tests/dev runs can override with groups_path.
    """

    # Person resolution (NOTE: now holds the resolved person name, not an env var name)
    person_env: str = ""  # e.g., "Ben Price" (required; populated by runner)

    # File-based selection (derived from person_env unless overridden)
    groups_path: str | None = None
    _selected: list[ScraperConfig] = field(default_factory=list, repr=False)

    # Runtime behavior
    sqlite_path: str = "/app/local/state/careerwatch.db"
    max_threads: int = 8
    skip_network: bool = False

    # Special-run flags
    email_all_even_if_seen: bool = False
    ingest_only_no_email: bool = False

    # ------------- convenience -------------
    def group_by_kind(self) -> dict[str, list[ScraperConfig]]:
        """
        Partition selected scrapers by their 'kind' for the engine's fan-out.
        """
        selected = self._selected_scrapers()
        by_kind: dict[str, list[ScraperConfig]] = {}
        for sc in selected:
            by_kind.setdefault(sc.kind, []).append(sc)
        return by_kind

    def _slugify(self, s: str) -> str:
        s = re.sub(r"[^0-9a-zA-Z]+", "_", s.lower())
        return re.sub(r"_{2,}", "_", s).strip("_")

    def _selected_scrapers(self) -> list[ScraperConfig]:
        """
        Return the active list of ScraperConfig for this run (loaded from file).
        If groups_path is provided, use it directly. Otherwise derive the path
        from the resolved person name in person_env.
        """
        if self._selected:
            return self._selected

        if self.groups_path:
            path = self.groups_path
        else:
            person_name = (self.person_env or "").strip()
            if not person_name:
                raise ConfigError(
                    "Cannot derive groups file path without a resolved person name. "
                    "Provide 'person_env' or an explicit 'groups_path'."
                )
            path = f"/app/local/config/career_watch_groups.{self._slugify(person_name)}.json"

        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError as e:
            raise ConfigError(f"career_watch groups file not found: {path}") from e
        except json.JSONDecodeError as e:
            raise ConfigError(f"career_watch groups file is invalid JSON: {path}") from e

        self._selected = _parse_scrapers_list(data)
        if not self._selected:
            raise ConfigError(f"No scrapers found in {path}")
        return self._selected

    # ------------- constructors -------------
    @classmethod
    def from_env_and_kwargs(cls, kwargs: Mapping[str, Any] | None) -> Settings:
        """
        Build Settings from kwargs with validation.

        Expected kwargs (all optional unless stated otherwise):

            person_env: str  # RESOLVED human name (runner expanded *_env already)
            # If groups_path is provided, person_env/person are not required.

            sqlite_path: str = "/app/local/state/careerwatch.db"
            max_threads: int = 8
            skip_network: bool = false

            # Special runs
            email_all_even_if_seen: bool = false
            ingest_only_no_email: bool = false

            # Selection (file-based)
            groups_path: str  # optional explicit path (otherwise derived from person_env)
        """
        kw = dict(kwargs or {})

        # Explicit file selection (if present, we won't require person_env)
        groups_path = kw.get("groups_path")
        if groups_path is not None:
            groups_path = str(groups_path).strip() or None

        # Person resolution (runner should have resolved *_env values already)
        person_env_val = str(kw.get("person_env") or "").strip()

        # If there's no explicit groups_path, we need a person to derive it later
        if not groups_path and not person_env_val:
            raise ConfigError(
                "Missing person name. Provide 'person_env' (resolved human name), "
                "or pass an explicit 'groups_path'."
            )

        # Core fields
        sqlite_path = str(kw.get("sqlite_path") or "/app/local/state/careerwatch.db")
        max_threads = int(kw.get("max_threads") or 8)
        skip_network = truthy(kw.get("skip_network"))

        # Special-run flags
        email_all_even_if_seen = truthy(kw.get("email_all_even_if_seen"))
        ingest_only_no_email = truthy(kw.get("ingest_only_no_email"))

        settings = cls(
            person_env=person_env_val,  # may be "" if groups_path is given
            groups_path=groups_path if groups_path else None,
            sqlite_path=sqlite_path,
            max_threads=max_threads,
            skip_network=skip_network,
            email_all_even_if_seen=email_all_even_if_seen,
            ingest_only_no_email=ingest_only_no_email,
        )
        _validate_settings(settings)
        return settings


# -----------------------------
# Helpers
# -----------------------------
def _parse_scrapers_list(value: Any) -> list[ScraperConfig]:
    """
    Parse a flat list into ScraperConfig objects.
    Accepts: [{"kind": "...", "source": "...", "params": {...}}, ...]
    """
    if not value:
        return []
    if not isinstance(value, list):
        raise ConfigError("Expected a list of scraper objects.")
    out: list[ScraperConfig] = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise ConfigError(f"Item[{i}] must be an object.")
        kind = item.get("kind")
        source = item.get("source")
        params = item.get("params") or {}
        if not kind or not source:
            raise ConfigError(f"Item[{i}] requires 'kind' and 'source'.")
        if not isinstance(params, dict):
            raise ConfigError(f"Item[{i}].params must be an object.")
        out.append(ScraperConfig(kind=str(kind), source=str(source), params=dict(params)))
    return out


def _validate_settings(s: Settings) -> None:
    # If groups_path is given, person_env may be empty. Otherwise we require it.
    if not s.groups_path and not (s.person_env or "").strip():
        raise ConfigError("Resolved 'person_env' (human name) cannot be empty when 'groups_path' is not provided.")

    if s.max_threads <= 0:
        raise ConfigError("'max_threads' must be >= 1.")
    if not s.sqlite_path.strip():
        raise ConfigError("'sqlite_path' cannot be empty.")

    # Ensure there is at least one scraper after selection (file-backed)
    selected = s._selected_scrapers()
    if not selected:
        raise ConfigError("No selected scrapers to run.")

    # Basic sanity on selected scrapers
    for sc in selected:
        if not sc.kind or not sc.source:
            raise ConfigError("Each ScraperConfig must have 'kind' and 'source'.")
