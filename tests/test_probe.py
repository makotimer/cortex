"""modules/_shared/probe: the pure parts of the shared page fetch.

The live fetch goes through gluetun and is not tested here. These pin the
text extraction and the truncation contract the vpn MCP tool relies on, and
the Cloudflare-challenge fingerprint that tells a caller rotating is futile.
"""
from __future__ import annotations

from modules._shared import probe

CF_CHALLENGE_HTML = """
<!DOCTYPE html><html lang="en-US"><head><title>Just a moment...</title>
<style>body{display:flex}</style></head>
<body><div class="main-content"><h1>www.example.com</h1>
<p>Enable JavaScript and cookies to continue</p></div>
<script>(function(){window._cf_chl_opt={cvId:'3',cType:'managed',
cUPMDTk:"/?__cf_chl_tk=abc"};})();</script>
</body></html>
"""

ARTICLE_HTML = """
<html><head><title>Tours</title><style>.x{color:red}</style>
<script>var a = 1;</script></head>
<body>
  <nav>Home   |   Menu</nav>
  <h1>Bakery&nbsp;Tours</h1>


  <p>Fridays at 10:00 AM.</p>
  <p>   </p>
  <p>Saturdays at 10:00 AM.</p>
  <noscript>Please enable JS</noscript>
  <svg><text>icon</text></svg>
</body></html>
"""


def test_page_text_drops_code_and_collapses_whitespace():
    text = probe.page_text(ARTICLE_HTML)
    assert "var a" not in text
    assert "color:red" not in text
    assert "Please enable JS" not in text
    assert "icon" not in text
    assert "Home | Menu" in text
    assert "Bakery Tours" in text
    assert "\n\n\n" not in text
    assert text.index("Fridays") < text.index("Saturdays")


def test_page_text_empty_is_empty():
    assert probe.page_text("") == ""
    assert probe.page_text(None) == ""  # type: ignore[arg-type]


def test_truncate_reports_the_cut():
    body, cut = probe.truncate("abcdef", 3)
    assert cut is True
    assert body.startswith("abc")
    assert body.endswith("[… truncated]")


def test_truncate_leaves_short_text_alone():
    assert probe.truncate("abc", 3) == ("abc", False)
    assert probe.truncate("abc", 0) == ("abc", False)


def test_fingerprint_names_a_cloudflare_challenge():
    report = probe.fingerprint(CF_CHALLENGE_HTML)
    assert report["title"] == "Just a moment..."
    assert report["vendors"] == ["cloudflare-challenge"]


def test_fingerprint_does_not_call_a_plain_page_a_challenge():
    assert probe.fingerprint(ARTICLE_HTML)["vendors"] == []
