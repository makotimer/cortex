from __future__ import annotations

from typing import Any

from .lib import chime as chime_action


def run(**kwargs: Any) -> tuple[str, dict] | None:
    """
    Entry for modules.sonos.
    Kwargs:
      action: "chime" (default)
    """
    action = str(kwargs.pop("action", "chime")).lower()
    if action in ("chime", ""):
        return chime_action.run_action(**kwargs)
    raise RuntimeError(f"Unknown Sonos action: {action!r}")
