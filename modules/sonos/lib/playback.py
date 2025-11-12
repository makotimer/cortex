from __future__ import annotations

import contextlib
import time
from typing import Any

from soco.snapshot import Snapshot

from .client import SonosClient


def _safe_transport_state(c) -> str:
    """Return current transport state with safe fallback."""
    try:
        info = c.get_current_transport_info() or {}
        return str(info.get("current_transport_state", "")).strip()
    except Exception:
        return ""


def _safe_track_uri(c) -> str:
    """Return current track URI with safe fallback."""
    try:
        ti = c.get_current_track_info() or {}
        return str(ti.get("uri", "")).strip()
    except Exception:
        return ""


def play_uri_with_snapshot(
    client: SonosClient,
    *,
    uri: str,
    title: str = "Alert",
    play_volume: int | None = None,  # exact volume for chime playback
    wait_seconds: float = 5.0,  # overall timeout while polling
    fade_restore: bool = False,
) -> dict[str, Any]:
    """
    Play a direct URI (does not touch the queue), preserving the user's session via SoCo Snapshot.

    Behavior (aligned with the verified Pytest):
      1) Take Snapshot of the coordinator (captures queue + position context).
      2) Optionally set an exact playback volume (remember previous volume).
      3) Play the URI.
      4) Poll transport_state until it has played and then finished OR until wait_seconds timeout.
      5) Restore snapshot (no assertions on transport transitions).
      6) Restore previous volume.

    Returns a small summary dict suitable for logs/metrics.
    """
    c = client.coord

    # Best-effort skip if TV input is active (Sonos cannot pause TV cleanly)
    try:
        if getattr(c, "is_playing_tv", False):
            return {"skipped": "tv_active"}
    except Exception:
        pass

    # Baseline for invariants / debug
    before_qsize = client.queue_size
    before_uri = _safe_track_uri(c)

    # 1) Snapshot (queue + position context)
    snap = Snapshot(c)
    snap.snapshot()

    # 2) Remember previous volume and set exact playback volume if requested
    try:
        prev_vol = c.volume
    except Exception:
        prev_vol = None

    if play_volume is not None:
        with contextlib.suppress(Exception):
            c.volume = max(0, min(100, int(play_volume)))

    # 3) Fire the alert
    c.play_uri(uri=uri, title=title or "")

    # 4) Poll for completion up to timeout
    # Strategy:
    #   - Wait until we observe PLAYING at least once (so playback actually began)
    #   - After that, break when it is no longer PLAYING
    #   - Stop trying altogether once wait_seconds has elapsed
    deadline = time.monotonic() + max(0.0, float(wait_seconds))
    saw_playing = False
    while time.monotonic() < deadline:
        state = _safe_transport_state(c)
        if state.upper() == "PLAYING":
            saw_playing = True
        elif saw_playing and state.upper() != "PLAYING":
            # finished (or left PLAYING)
            break
        time.sleep(0.25)

    # 5) Restore snapshot (queue and playback context)
    snap.restore(fade=fade_restore)

    # 6) Restore previous volume exactly
    if prev_vol is not None:
        with contextlib.suppress(Exception):
            c.volume = prev_vol

    # After-state for invariants / debug
    after_qsize = client.queue_size
    after_uri = _safe_track_uri(c)

    return {
        "queue_size": {"before": before_qsize, "after": after_qsize},
        "uri_roundtrip_equal": bool(before_uri and (before_uri == after_uri)),
        "before_uri": before_uri,
        "after_uri": after_uri,
        "saw_playing": saw_playing,
        "timed_out": time.monotonic() >= deadline
        and (not saw_playing or _safe_transport_state(c).upper() == "PLAYING"),
    }
