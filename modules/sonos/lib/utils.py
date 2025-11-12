from __future__ import annotations

import contextlib
import os
from collections.abc import Iterator
from datetime import datetime
from typing import Optional

# ----------------------------
# Small utilities for Sonos
# ----------------------------


def getenv_str(name: str, default: str | None = None) -> str | None:
    val = os.getenv(name)
    return val if val is not None else default


def require(value: str | None, *, error: str) -> str:
    if value is None or not str(value).strip():
        raise RuntimeError(error)
    return str(value).strip()


def require_env(env_name: str) -> str:
    """Return env var value or raise a clear error."""
    return require(
        getenv_str(env_name),
        error=f"Environment variable {env_name} is required but not set.",
    )


def build_file_url(nas_ip: str, path: str) -> str:
    nas = str(nas_ip).strip().rstrip("/")
    leaf = str(path).lstrip("/")
    return f"http://{nas}/{leaf}"


def hour_12_now_str2() -> str:
    h = int(datetime.now().strftime("%I"))  # 01..12
    return f"{h:02d}"


def hour_12_from_override_str2(s: str | None) -> str | None:
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None
    try:
        h = int(s, 10)
    except Exception:
        return None
    if h < 1 or h > 12:
        return None
    return f"{h:02d}"


def _resolve_hour_str2() -> str:
    from_env = hour_12_from_override_str2(os.getenv("HOUR_OVERRIDE"))
    return from_env if from_env is not None else hour_12_now_str2()


@contextlib.contextmanager
def temporary_volume(client, vol: int | None) -> Iterator[None]:
    prev: int | None = None
    try:
        try:
            prev = client.get_volume()
        except Exception:
            prev = None
        if vol is not None:
            client.set_volume(int(max(0, min(100, vol))))
        yield
    finally:
        if prev is not None:
            with contextlib.suppress(Exception):
                client.set_volume(prev)
