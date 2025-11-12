from __future__ import annotations

import contextlib
import json
import os
import time

import pytest
from soco.snapshot import Snapshot

from modules.sonos.lib.client import SonosClient

# -------------------------
# Fixed test inputs
# -------------------------
CHIME_URI = "http://192.168.1.190/grandfather_clock_chime_05.wav"

PLAYING_WAIT = 5.0  # after starting the chime


def _state(c):
    """
    Return a snapshot of device state as a tuple:
        (transport_state, current_track_info_dict, queue_size_int)

    All components are returned with safe fallbacks so the test keeps running
    (and logs are still useful) even if an individual call fails transiently.
    """
    try:
        ti = c.get_current_transport_info() or {}
    except Exception as e:
        ti = {"current_transport_state": f"ERR:{e}"}

    try:
        ct = c.get_current_track_info() or {}
    except Exception as e:
        ct = {"uri": "", "playlist_position": "0", "ERR": str(e)}

    qsize = c.queue_size or 0
    return ti.get("current_transport_state", ""), ct, qsize


@pytest.mark.live
def test_snapshot_restore_without_queue_clear():
    """
    Goal: Prove Snapshot() roundtrips the 'what was playing' context and
    (critically) does NOT mutate the queue contents/order/size.

    Intentionally *do not* assert on the detailed transport transitions.
    We only care about these invariants:
      - Queue size is unchanged.
      - If there was a concrete track URI before, the same URI is present after restore.

    Test flow (single coordinator):
      1) Read baseline: transport state, current track info, queue size.
      2) Take Snapshot of the coordinator.
      3) Play a short CHIME_URI via play_uri() (this does NOT touch the queue).
      4) Restore snapshot (no fade).
      5) Allow brief settle time.
      6) Assert queue size unchanged and (when applicable) the URI is the same.
    """
    coord_ip = os.getenv("COORDINATOR_IP", "").strip()
    if not coord_ip:
        pytest.fail("COORDINATOR_IP not set in environment — aborting test.")

    client = SonosClient(coord_ip)
    c = client.coord

    # Skip if TV input is active — Sonos can't pause TV, and SoCo restore logic
    # will be inconsistent in that mode, which makes this test noisy.
    try:
        if getattr(c, "is_playing_tv", False):
            pytest.skip("Coordinator is playing TV input; skipping snapshot/restore test.")
    except Exception:
        # If property lookup itself fails, keep going — this is a best-effort guard.
        pass

    # 1) Baseline snapshot for assertions & debug
    before_state, before_track, before_qsize = _state(c)
    print(
        "[dbg] baseline:",
        json.dumps(
            {
                "transport": before_state,
                "queue_size": before_qsize,
                "track": {
                    "uri": before_track.get("uri", ""),
                    "title": before_track.get("title", ""),
                    "playlist_position": before_track.get("playlist_position", ""),
                },
            },
            ensure_ascii=False,
        ),
    )

    # 2) Snapshot the coordinator (includes queue + position context)
    snap = Snapshot(c)
    snap.snapshot()

    # 3) Play alert (do not alter queue). Use safe volume handling.
    try:
        prev_vol = c.volume
    except Exception:
        prev_vol = None

    try:
        if prev_vol is not None:
            c.volume = min(35, max(5, prev_vol))  # keep modest; audible but gentle
    except Exception:
        pass

    print(f"[dbg] playing alert: {CHIME_URI}")
    c.play_uri(uri=CHIME_URI, title="Test Alert")
    time.sleep(PLAYING_WAIT)  # let playback actually begin

    # 4) Restore snapshot; we don't care about intermediate transport transitions
    snap.restore(fade=False)

    _, after_track, after_qsize = _state(c)

    # 5a) Queue must be unchanged (size as a simple, stable proxy)
    assert after_qsize == before_qsize, f"Queue size changed: before={before_qsize}, after={after_qsize}"

    # 5b) If the 'before' had a concrete track URI, assert it roundtrips.
    # We explicitly do not assert transport state equivalence.
    before_uri = (before_track.get("uri") or "").strip()
    after_uri = (after_track.get("uri") or "").strip()
    if before_uri:
        assert after_uri == before_uri, f"URI mismatch after restore\nbefore: {before_uri}\n after: {after_uri}"

    # Best-effort: restore volume
    if prev_vol is not None:
        with contextlib.suppress(Exception):
            c.volume = prev_vol

    print("[dbg] snapshot restore test passed without queue mutation.")
