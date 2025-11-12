# tests/test_sonos_live.py
from __future__ import annotations

import contextlib
import http.client
import json
import os
import re
import socket

import pytest
from soco.snapshot import Snapshot

from modules.sonos.lib.client import SonosClient
from modules.sonos.lib.utils import build_file_url, hour_12_from_override_str2, hour_12_now_str2


def _first_res_uri_of(item):
    # SoCo DIDL objects usually store resources in .resources (list of DidlResource)
    try:
        resources = getattr(item, "resources", None)
        if resources and len(resources) > 0:
            # each resource has .uri
            return getattr(resources[0], "uri", "") or ""
    except Exception:
        pass
    # Fallback: some SoCo objects can serialize to dict or xml
    try:
        to_dict = getattr(item, "to_dict", None)
        if callable(to_dict):
            d = to_dict()
            # sometimes d.get("resources")[0]["uri"]
            res = d.get("resources") or []
            if res and isinstance(res, list):
                u = res[0].get("uri")
                if u:
                    return u
    except Exception:
        pass
    try:
        to_xml = getattr(item, "to_xml", None)
        if callable(to_xml):
            xml = to_xml()
            m = re.search(r"<res[^>]*>([^<]+)</res>", xml)
            if m:
                return m.group(1)
    except Exception:
        pass
    return ""


def test_dump_queue_resource_uris():
    coord_ip = _env_or_skip("COORDINATOR_IP")
    client = SonosClient(coord_ip)
    c = client.coord

    # 1) Pull via SoCo get_queue() and extract URIs from DIDL resources
    try:
        q = c.get_queue()
        print(f"queue_length: {len(q)}")

        sample = []
        for i, item in enumerate(q[:8]):  # peek a few
            title = getattr(item, "title", "") or ""
            artist = getattr(item, "creator", "") or ""
            uri = _first_res_uri_of(item)  # <— this is the important bit
            sample.append({"index": i, "title": title, "artist": artist, "res_uri": uri})
        print("queue_resource_preview_first8:")
        print(json.dumps({"items": sample}, indent=2, sort_keys=True, ensure_ascii=False))
    except Exception as e:
        print(f"get_queue/resources: <error: {e!r}>")

    # 2) Do a raw ContentDirectory.Browse of the queue to see the <res> that Sonos returns
    #    (ObjectID 'Q:0' is the active queue)
    try:
        # Fetch a page of items (tune the count as needed)
        resp = c.contentDirectory.Browse([
            ("ObjectID", "Q:0"),
            ("BrowseFlag", "BrowseDirectChildren"),
            ("Filter", "*"),
            ("StartingIndex", 0),
            ("RequestedCount", 12),
            ("SortCriteria", ""),
        ])
        result_xml = resp.get("Result", "")
        # Show the first few <res> and <dc:title> pairs for visual confirmation
        titles = re.findall(r"<dc:title>(.*?)</dc:title>", result_xml)
        reses = re.findall(r"<res[^>]*>([^<]+)</res>", result_xml)
        preview_pairs = [
            {
                "index": i,
                "title": titles[i] if i < len(titles) else "",
                "res": reses[i] if i < len(reses) else "",
            }
            for i in range(min(12, max(len(titles), len(reses))))
        ]
        print("contentDirectory.Browse(Q:0) preview (title,res) first 12:")
        print(json.dumps({"items": preview_pairs}, indent=2, sort_keys=True, ensure_ascii=False))
    except Exception as e:
        print(f"contentDirectory.Browse: <error: {e!r}>")


def _print_kv(title: str, data: dict | None) -> None:
    try:
        if not data:
            print(f"{title}: <none>")
            return
        # pretty and stable ordering
        print(f"{title}:")
        print(json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False))
    except Exception as e:
        print(f"{title}: <error: {e!r}>")


def test_dump_playback_context_for_visual_match():
    """
    Live diagnostic (no audio start/stop) to compare with the Sonos app.

    Requires:
      - COORDINATOR_IP
    Optional:
      - NAS_IP (only used by other tests)
    """
    coord_ip = _env_or_skip("COORDINATOR_IP")
    client = SonosClient(coord_ip)
    c = client.coord  # underlying SoCo device

    # --- Snapshot using SoCo (no custom dataclass) ---
    try:
        snap = Snapshot(c)
        snap.snapshot()
        print("=== Snapshot() captured current context successfully ===")
    except Exception as e:
        print(f"Snapshot(): <error: {e!r}>")

    # --- Lightweight identity/state ---
    with contextlib.suppress(Exception):
        print(f"player_name: {c.player_name}")
    with contextlib.suppress(Exception):
        print(f"group_coordinator: {c.group.coordinator.player_name if c.group else '(none)'}")
    with contextlib.suppress(Exception):
        print(f"play_mode: {c.play_mode}  cross_fade: {c.cross_fade}  volume: {c.volume}  mute: {bool(c.mute)}")

    # --- Current transport + track info (SoCo helpers) ---
    try:
        ti = c.get_current_transport_info()  # e.g., {'current_transport_state': 'PLAYING', ...}
    except Exception:
        ti = None
    _print_kv("get_current_transport_info()", ti)

    try:
        ct = c.get_current_track_info()  # e.g., position, playlist_position, title, uri
    except Exception:
        ct = None
    _print_kv("get_current_track_info()", ct)

    # --- AVTransport raw calls (deeper truth) ---
    try:
        mi = c.avTransport.GetMediaInfo([("InstanceID", 0)])
    except Exception:
        mi = None
    _print_kv("AVTransport.GetMediaInfo", mi)

    try:
        pi = c.avTransport.GetPositionInfo([("InstanceID", 0)])
    except Exception:
        pi = None
    _print_kv("AVTransport.GetPositionInfo", pi)

    try:
        nav = c.avTransport.GetTransportSettings([("InstanceID", 0)])
    except Exception:
        nav = None
    _print_kv("AVTransport.GetTransportSettings", nav)

    # --- Queue peek (count + first few items URIs/titles) ---
    try:
        q = c.get_queue()
        q_len = len(q) if q is not None else 0
        print(f"queue_length: {q_len}")
        preview = []
        for i, item in enumerate(q[:5]):  # first five for sanity
            # item may be a DidlObject; try attributes then dict
            uri = (
                getattr(item, "uri", None)
                or getattr(item, "resource", None)
                or getattr(item, "get", lambda *_: None)("uri")
            )
            title = getattr(item, "title", None) or getattr(item, "get", lambda *_: None)("title")
            preview.append({"index": i, "title": str(title or ""), "uri": str(uri or "")})
        if preview:
            _print_kv("queue_preview_first5", {"items": preview})
        # Also show what Sonos thinks is the current playlist position (0-based) if we can infer it
        try:
            if isinstance(ct, dict):
                pos = ct.get("playlist_position")
                if pos and str(pos).isdigit():
                    print(f"inferred_current_queue_index: {int(pos) - 1}")
        except Exception:
            pass
    except Exception as e:
        print(f"queue: <error: {e!r}>")


def _env_or_skip(name: str) -> str:
    v = os.getenv(name, "").strip()
    if not v:
        pytest.skip(f"Set {name} in the environment to run this presence check")
    return v


def _tcp_ping(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def test_sonos_coordinator_reachable_and_identifies():
    """
    Non-live (no audio) but real network:
      - Requires COORDINATOR_IP
      - Checks TCP/1400
      - Touches player_name via SonosClient
      - Reads a transport state string
    """
    coord_ip = _env_or_skip("COORDINATOR_IP")
    assert _tcp_ping(coord_ip, 1400, timeout=1.5), f"Cannot reach {coord_ip}:1400"
    client = SonosClient(coord_ip)
    assert isinstance(client.coord.player_name, str) and client.coord.player_name
    # Replace old client.get_state() with the SoCo transport info
    try:
        ti = client.get_current_transport_info() or {}
        state = str(ti.get("current_transport_state", "")).strip()
    except Exception:
        state = ""
    assert state != ""


def test_nas_head_optional():
    """
    Optional: if NAS_IP is provided, ensure HTTP is reachable and the hour file path is well-formed.
    Skips if NAS_IP is not set. Does not require the file to exist; succeeds on 200..404 range.
    """
    nas_ip_env = os.getenv("NAS_IP", "").strip()
    if not nas_ip_env:
        pytest.skip("NAS_IP not set; skipping NAS presence check")

    hour_str = hour_12_from_override_str2(os.getenv("HOUR_OVERRIDE")) or hour_12_now_str2()
    url = build_file_url(nas_ip_env, f"grandfather_clock_chime_{hour_str}.wav")

    host = nas_ip_env.replace("http://", "").replace("https://", "").split("/")[0]
    conn = http.client.HTTPConnection(host, timeout=2.0)
    try:
        # Only test host/port reachability and HTTP serving; path 404 is acceptable.
        path = "/" + url.split("/", 3)[-1].split("/", 1)[-1]
        conn.request("HEAD", path)
        resp = conn.getresponse()
        assert 200 <= resp.status < 500
    finally:
        conn.close()
