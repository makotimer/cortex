from __future__ import annotations

from typing import Any

try:
    from service.logging_utils import write_activity_log as _act
    from service.logging_utils import write_error_log as _err
except Exception:

    def _act(_: dict[str, Any]) -> None:
        pass

    def _err(_: dict[str, Any]) -> None:
        pass


def activity(record: dict[str, Any]) -> None:
    _act(dict(record))


def error(record: dict[str, Any]) -> None:
    _err(dict(record))
