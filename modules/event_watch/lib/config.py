"""Settings for event_watch, resolved from kwargs then environment.

Mirrors ``career_watch/lib/config.py``: explicit kwargs win over environment,
environment wins over the defaults here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

#: Days ahead to fetch — roughly nine months. Design §3 leaves the window
#: deliberately unscoped and is correct at any size, because disappearance
#: reconciliation only ever considers occurrences inside the window it actually
#: fetched.
#:
#: Nine months is past the feed's own horizon today: asking for ten years
#: returns the same 157 occurrences as one year, ending 2027-01-01. So this
#: currently captures everything the source has while still bounding future runs
#: — which keeps the disappearance guard meaningful rather than open-ended.
DEFAULT_WINDOW_DAYS = 270

DEFAULT_STATE_DIR = "/app/local/state/event_watch"


def truthy(value: Any) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    #: Which scraper kinds to run. Only "tockify" exists today.
    kinds: list[str] = field(default_factory=lambda: ["tockify"])
    site: str = "discoverbcs"
    window_days: int = DEFAULT_WINDOW_DAYS
    state_dir: str = DEFAULT_STATE_DIR

    #: gluetun HTTP proxy; None = direct. Fail-closed health checking only
    #: happens when this is set, exactly like career_watch.
    proxy_url: str | None = None
    rotate_vpn_per_run: bool = True

    #: Normalize and log payloads without publishing anything to the bus.
    dry_run: bool = False
    #: Cortex-wide convention: avoid real HTTP (tests, offline runs).
    skip_network: bool = False

    #: Fraction of the previous in-window set that may vanish before the run
    #: refuses to cancel anything (design §7 step 3).
    disappearance_guard: float = 0.25

    @classmethod
    def from_env_and_kwargs(cls, kw: dict[str, Any] | None = None) -> Settings:
        kw = dict(kw or {})

        kinds = kw.get("kinds") or ["tockify"]
        if isinstance(kinds, str):
            kinds = [k.strip() for k in kinds.split(",") if k.strip()]

        window_days = int(
            kw.get("window_days") or os.getenv("EVENT_WATCH_WINDOW_DAYS") or DEFAULT_WINDOW_DAYS
        )

        # Proxy: explicit kwarg wins even when empty, then env, then None.
        if "proxy_url" in kw:
            proxy_url = str(kw["proxy_url"]).strip() or None
        else:
            proxy_url = str(os.getenv("EVENT_WATCH_PROXY_URL") or "").strip() or None

        rotate_raw = kw.get("rotate_vpn_per_run")
        if rotate_raw is None:
            rotate_raw = os.getenv("EVENT_WATCH_ROTATE_VPN", "1")
        rotate_vpn_per_run = truthy(rotate_raw)

        return cls(
            kinds=list(kinds),
            site=str(kw.get("site") or "discoverbcs"),
            window_days=window_days,
            state_dir=str(kw.get("state_dir") or os.getenv("EVENT_WATCH_STATE_DIR") or DEFAULT_STATE_DIR),
            proxy_url=proxy_url,
            rotate_vpn_per_run=rotate_vpn_per_run,
            dry_run=truthy(kw.get("dry_run", os.getenv("EVENT_WATCH_DRY_RUN", "0"))),
            skip_network=truthy(kw.get("skip_network", "0")),
            disappearance_guard=float(kw.get("disappearance_guard") or 0.25),
        )
