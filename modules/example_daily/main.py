from __future__ import annotations

import html
import json
from datetime import datetime, timezone
from typing import Any

from .lib.transform import titleize


def _jsonify(val: Any) -> str:
    try:
        return json.dumps(val, ensure_ascii=False, indent=2, sort_keys=True)
    except Exception:
        return repr(val)


def run(**kwargs) -> str | None:
    """
    Minimal demo module.

    kwargs:
      name: str = "World"
      items: list[str] | str(JSON) = []
      fail: bool = False   -> if True, raise to exercise error paths

    Returns:
      dict with keys {ok, message, html, subject}. Runner may use subject/html for email.
    """
    if kwargs.get("fail"):
        raise RuntimeError("example_daily: failure requested via kwargs.fail=True")

    name = titleize(kwargs.get("name", "World"))

    # items can come in as a real list (CLI JSON-decoded) or a JSON string; normalize
    items_raw = kwargs.get("items", [])
    if isinstance(items_raw, str):
        try:
            items: list[Any] = json.loads(items_raw)
        except Exception:
            items = [items_raw]
    elif isinstance(items_raw, list):
        items = items_raw
    else:
        items = [items_raw] if items_raw not in (None, "") else []

    now = datetime.now(timezone.utc)
    ts = now.isoformat(timespec="seconds")
    subject = f"[example_daily] Hello, {name} — {ts}"

    summary = f"Ran at {ts} with {len(items)} item(s)."
    if items:
        summary += f" First item: {items[0]!r}"

    # Super simple HTML (safe to send)
    escaped_name = html.escape(name)
    return f"""
<!doctype html>
<html>
  <head><meta charset="utf-8"><title>{html.escape(subject)}</title></head>
  <body style="font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif;">
    <h1 style="margin:0 0 8px 0;">Hello, {escaped_name}!</h1>
    <p style="margin:0 0 12px 0; color:#444;">This is the <code>modules.example_daily</code> test module.</p>
    <hr style="border:none;border-top:1px solid #ddd;margin:12px 0;" />
    <p><strong>Run time (UTC):</strong> {html.escape(ts)}</p>
    <p><strong>Items count:</strong> {len(items)}</p>
    <pre style="background:#f7f7f7;padding:8px;border-radius:6px;overflow:auto;">kwargs =
{html.escape(_jsonify(kwargs))}
</pre>
  </body>
</html>
    """.strip()
