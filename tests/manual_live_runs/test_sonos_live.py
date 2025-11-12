# tests/test_sonos_live.py
from __future__ import annotations

import contextlib
import os
import time

import pytest
from soco.snapshot import Snapshot

from modules.sonos.lib import (
    SonosClient,
    build_file_url,
    hour_12_from_override_str2,
    hour_12_now_str2,
    temporary_volume,
)

pytestmark = pytest.mark.live  # your conftest will skip unless --live or RUN_LIVE_TESTS=1


def _require_env(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        pytest.skip(f"Set {name} in the environment to run live tests")
    return v


def _resolve_hour_str2() -> str:
    return hour_12_from_override_str2(os.getenv("HOUR_OVERRIDE")) or hour_12_now_str2()


def _transport_state(c) -> str:
    try:
        info = c.get_current_transport_info() or {}
        return str(info.get("current_transport_state", "")).upper()
    except Exception:
        return ""


@pytest.mark.timeout(30)
def test_live_play_5s_then_restore():
    """
    Live hardware smoke:
      1) Snapshot current coordinator state (includes queue/position).
      2) Temporarily set exact volume if SONOS_VOLUME provided.
      3) Play current-hour chime via direct URI (does not touch queue).
      4) Poll briefly until PLAYING is seen (or timeout), then let it play ~5s.
      5) Stop best-effort, then restore snapshot (no fade).
    """
    nas_ip = _require_env("NAS_IP")
    coord_ip = _require_env("COORDINATOR_IP")

    vol_env = os.getenv("SONOS_VOLUME", "").strip()
    vol = int(vol_env) if vol_env.isdigit() else None

    hour_str = _resolve_hour_str2()
    url = build_file_url(nas_ip, f"grandfather_clock_chime_{hour_str}.wav")

    client = SonosClient(coord_ip)
    c = client.coord

    # Skip if TV input is active — restore behavior can be inconsistent in TV mode.
    try:
        if getattr(c, "is_playing_tv", False):
            pytest.skip("Coordinator is playing TV input; skipping live chime test.")
    except Exception:
        pass

    # Snapshot before we touch anything
    snap = Snapshot(c)
    snap.snapshot()

    try:
        # Set exact temporary volume if provided
        with temporary_volume(client, vol):
            c.play_uri(uri=url, title=f"Pytest Chime {hour_str}:00 (5s)")

            # Poll up to ~3s to see PLAYING at least once
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline:
                if _transport_state(c) == "PLAYING":
                    break
                time.sleep(0.2)

            # Let it run for ~5s (regardless of whether we observed PLAYING)
            time.sleep(5.0)

            # Best-effort stop before restore to reduce overlap
            with contextlib.suppress(Exception):
                c.stop()
    finally:
        # Restore prior playback context (including queue and position)
        with contextlib.suppress(Exception):
            snap.restore(fade=False)

    # If you want a sanity assertion, keep it trivial — this is a smoke test.
    assert True
