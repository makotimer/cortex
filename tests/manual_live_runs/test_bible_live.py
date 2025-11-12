# tests/test_bible_live.py
import datetime as dt
import importlib
import json
import os
import sys

import pytest

from modules.bible_plan import main


def _today_ymd() -> str:
    return dt.date.today().strftime("%Y-%m-%d")


def _live_enabled(request) -> bool:
    try:
        return bool(request.config.getoption("--live"))
    except Exception:
        return os.getenv("PYTEST_LIVE", "").strip().lower() in {"1", "true", "yes", "on"}


@pytest.mark.skipif(
    not importlib.util.find_spec("service.emailer"),
    reason="service.emailer not importable in this environment",
)
def test_live_send_today_email(request, monkeypatch, tmp_path):
    """
    LIVE ONLY: Generates today's HTML and emails it using service.emailer.send_html.
    Enable with:  pytest -q tests/test_bible_live.py::test_live_send_today_email --live
    or set PYTEST_LIVE=1
    """
    if not _live_enabled(request):
        pytest.skip("Live test skipped (use --live or PYTEST_LIVE=1)")

    # Recipient (we'll send to yourself)
    to_addr = os.getenv("BRIDGE_USERNAME")
    if not to_addr:
        pytest.skip("BRIDGE_USERNAME not set (recipient)")

    # Create a simple one-item plan in CWD for deterministic output
    monkeypatch.chdir(tmp_path)
    (tmp_path / "chapter_plan.json").write_text(json.dumps(["Genesis 1"]), encoding="utf-8")

    # Respect your suite-wide default; explicitly enable/disable LLM as desired:
    # monkeypatch.setenv("BIBLE_PLAN_ENABLE_LLM", "1")  # uncomment to force LLM on for live

    result = main.run(for_date=_today_ymd())
    assert result is not None and isinstance(result, tuple)
    html, meta = result
    assert isinstance(html, str) and html.strip()
    assert isinstance(meta, dict)

    # Subject per your spec
    subject = f"Test Email: {meta.get('message', '(no message)')}"

    # Send using service.emailer (keyword-only API)
    emailer = importlib.import_module("service.emailer")
    send_html = getattr(emailer, "send_html", None)
    if not callable(send_html):
        pytest.skip("service.emailer.send_html not callable")

    import socket

    def _port_open(host, port, timeout=1.5):
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            return False

    smtp_host = os.getenv("BRIDGE_HOST", "cortex_bridge")
    smtp_port = int(os.getenv("BRIDGE_SMTP_PORT", "1025"))
    if not _port_open(smtp_host, smtp_port):
        pytest.skip(f"SMTP not reachable at {smtp_host}:{smtp_port}")

    result_id = send_html(
        subject=subject,
        html=html,
        to=[to_addr],
    )
    assert isinstance(result_id, str) and result_id  # many mailers return a message-id
