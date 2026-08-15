"""event_probe: identify what a calendar page actually is.

The live fetch goes through gluetun and is not tested here. These pin the
pure fingerprint so a Tockify embed, an Evvnt widget, and a PerimeterX wall
cannot be confused with each other.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "event_probe", Path(__file__).resolve().parent.parent / "scripts" / "event_probe.py"
)
assert _spec is not None and _spec.loader is not None
event_probe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(event_probe)


TOCKIFY_HTML = """
<!doctype html>
<html><head><title>Community Calendar</title></head>
<body>
  <div data-tockify-component="calendar"
       data-tockify-calendar="kwtx.calendar"
       data-tockify-script="embed"></div>
  <script src="//public.tockify.com/browser/embed.js"></script>
  <iframe src="https://www.googletagmanager.com/ns.html?id=GTM-X"></iframe>
</body></html>
"""

PERIMETERX_HTML = """
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta name="description" content="px-captcha" />
    <title>Access to this page has been denied</title>
  </head>
  <body>
    <script>
      window._pxAppId = 'PXCvbtpUrj';
      script.src = 'https://captcha.px-cloud.net/PXCvbtpUrj/captcha.js';
    </script>
  </body>
</html>
"""

EVVNT_HTML = """
<html><head><title>Arts &amp; Entertainment Calendar</title></head>
<body>
  <p>Powered by Evvnt</p>
  <script src="https://embed.evvnt.com/calendar.js"></script>
</body></html>
"""


def test_fingerprint_names_a_tockify_embed():
    report = event_probe.fingerprint(TOCKIFY_HTML)
    assert report["title"] == "Community Calendar"
    assert "tockify" in report["vendors"]
    assert any("tockify.com" in src for src in report["script_srcs"])
    assert any(
        attr["name"] == "data-tockify-calendar" and attr["value"] == "kwtx.calendar"
        for attr in report["data_attrs"]
    )


def test_fingerprint_names_a_perimeterx_wall():
    report = event_probe.fingerprint(PERIMETERX_HTML)
    assert report["title"] == "Access to this page has been denied"
    assert "perimeterx" in report["vendors"]
    assert "tockify" not in report["vendors"]


def test_fingerprint_names_an_evvnt_widget():
    report = event_probe.fingerprint(EVVNT_HTML)
    assert "evvnt" in report["vendors"]
    assert any("evvnt.com" in src for src in report["script_srcs"])


def test_fingerprint_lists_iframe_sources():
    report = event_probe.fingerprint(TOCKIFY_HTML)
    assert report["iframe_srcs"] == ["https://www.googletagmanager.com/ns.html?id=GTM-X"]


def test_fingerprint_empty_html_is_honest():
    report = event_probe.fingerprint("")
    assert report["title"] is None
    assert report["vendors"] == []
    assert report["script_srcs"] == []
    assert report["iframe_srcs"] == []
    assert report["data_attrs"] == []
