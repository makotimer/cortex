from __future__ import annotations

import re

try:
    import requests
except Exception:
    requests = None  # type: ignore

_BASE = "https://biblehub.com/commentaries"

_ALIASES = {...}  # as you had
_NUM = {"first": "1", "second": "2", "third": "3", "1st": "1", "2nd": "2", "3rd": "3"}


def _normalize(book: str) -> str:
    s = (book or "").strip().lower()
    s = s.replace("\u2013", " ").replace("\u2014", " ").replace("-", " ")
    s = re.sub(r"(\d)([a-z])", r"\1 \2", s)
    parts = s.split()
    if parts:
        parts[0] = _NUM.get(parts[0], parts[0])
        s = " ".join(parts)
    s = "".join(ch if (ch.isalnum() or ch.isspace()) else " " for ch in s)
    s = " ".join(s.split())
    if s in _ALIASES:
        return _ALIASES[s]
    j = s.replace(" ", "")
    if j in _ALIASES:
        return _ALIASES[j]
    if s == "psalm":
        return "psalms"
    return s.replace(" ", "_")


def _probe(url: str) -> bool:
    if not requests:
        return True  # treat as OK when requests missing (tests)
    try:
        r = requests.head(url, allow_redirects=True, timeout=6.0)
        if r.status_code == 200:
            return True
        if r.status_code in (403, 405):
            gr = requests.get(url, allow_redirects=True, timeout=6.0)
            return gr.status_code == 200
        return 200 <= r.status_code < 400
    except Exception:
        return False


def commentary_url(series: str, book: str, chapter: int, probe: bool) -> str | None:
    url = f"{_BASE}/{series}/{_normalize(book)}/{int(chapter)}.htm"
    return url if (not probe or _probe(url)) else None
