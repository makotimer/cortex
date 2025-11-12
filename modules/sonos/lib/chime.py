from __future__ import annotations

import os
from typing import Any

from .client import SonosClient
from .utils import _resolve_hour_str2, build_file_url


def _default_hourly_filename() -> str:
    # Matches scheme like ..._05.wav, ..._12.wav
    hour_str2 = _resolve_hour_str2()
    return f"grandfather_clock_chime_{hour_str2}.wav"


def run_chime(
    *,
    play_volume: int | None = None,  # <-- exact volume for chime playback
    fade_restore: bool = False,
) -> dict[str, Any]:
    """
    Core chime logic:
      - Play an hourly pattern like 'grandfather_clock_chime_05.wav'.
      - Snapshot/restore to preserve queue and context.
    """
    nas_ip = os.getenv("NAS_IP", "").strip()
    leaf = _default_hourly_filename()
    resolved_uri = build_file_url(nas_ip, leaf)

    coordinator_ip = os.getenv("COORDINATOR_IP", "").strip()
    client = SonosClient(coordinator_ip)
    from .playback import play_uri_with_snapshot

    wait_seconds = 75  # approximate longest chime length

    return play_uri_with_snapshot(
        client,
        uri=resolved_uri,
        title="Chime",
        play_volume=play_volume,
        wait_seconds=wait_seconds,
        fade_restore=fade_restore,
    )


def run_action(**kwargs) -> dict[str, Any]:
    """
    Scheduler-friendly shim.

    Expected kwargs (already-resolved values):
      - sonos_volume:       optional int (0..100) exact playback volume for the chime.
      - action:             optional, defaults to "chime" (ignored otherwise).

    No other kwargs are used in this path.
    """
    # Accept/ignore 'action' for compatibility
    _ = kwargs.get("action") or "chime"

    play_volume = None
    if "sonos_volume" in kwargs:
        try:
            play_volume = max(0, min(100, int(kwargs["sonos_volume"])))
        except Exception:
            play_volume = None  # ignore if unparseable

    # Use hourly default filename and standard timing; no other kwargs expected.
    return run_chime(
        play_volume=play_volume,
        fade_restore=False,
    )
