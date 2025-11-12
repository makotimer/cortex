# modules/_shared/utils.py
from __future__ import annotations

import contextlib
import glob
import html
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger(__name__)


def _truthy(s: str | None) -> bool:
    return (s or "").strip().lower() in {"1", "true", "yes", "on"}


def _get_float_env(name: str | None, default: float) -> float:
    if not name:
        return default
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except Exception:
        log.warning("Invalid float in %s=%r; using default %s", name, raw, default)
        return default


# -----------------------------------------------------------------------------
# Markup helpers
# -----------------------------------------------------------------------------
def esc(s: str) -> str:
    return html.escape(s, quote=True)


_URL_RE = re.compile(r'(?P<url>https?://[^\s<>()"]+)', re.I)
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)", re.I)
_BOLD_RE = re.compile(r"(\*\*|__)(.+?)\1")
_ITALIC_RE = re.compile(r"(\*|_)([^*_].*?)\1")
_STRIKE_RE = re.compile(r"~~(.+?)~~")
_CODE_RE = re.compile(r"`([^`]+)`")


def _inline_md(s: str) -> str:
    """Apply inline markdown on an already-escaped string."""
    s = _LINK_RE.sub(lambda m: f'<a href="{m.group(2)}">{m.group(1)}</a>', s)
    s = _URL_RE.sub(lambda m: f'<a href="{m.group("url")}">{m.group("url")}</a>', s)
    s = _CODE_RE.sub(lambda m: f"<code>{m.group(1)}</code>", s)
    s = _BOLD_RE.sub(lambda m: f"<strong>{m.group(2)}</strong>", s)
    s = _ITALIC_RE.sub(lambda m: f"<em>{m.group(2)}</em>", s)
    s = _STRIKE_RE.sub(lambda m: f"<del>{m.group(1)}</del>", s)
    return s


def md_to_html(md: str) -> str:
    text = md.replace("\r\n", "\n")
    lines = text.split("\n")

    html_out = []
    in_blockquote = in_code = False
    code_lang = None
    code_buf = []

    def parse_nested_lists(lines):
        if not lines:
            return ""

        # Find min indent
        def get_indent(line):
            return len((ul_re.match(line) or ol_re.match(line) or (None,)).group(1) or "")

        min_indent = min(get_indent(line) for line in lines if get_indent(line) > -1)
        # Assume 2 spaces per level; adjust if needed
        space_per_level = 2
        stack = []
        html = []
        last_level = -1
        for line in lines:
            m_ul = ul_re.match(line)
            m_ol = ol_re.match(line)
            if not m_ul and not m_ol:
                continue
            m = m_ul if m_ul else m_ol
            indent = len(m.group(1))
            level = (indent - min_indent) // space_per_level
            list_type = "ul" if m_ul else "ol"
            content_group = 3
            content = _inline_md(esc(m.group(content_group)))
            # Close higher levels
            while last_level > level:
                html.append("</li>")
                html.append(f"</{stack.pop()}>")
                last_level -= 1
            # Same level: close current li (but not for the first)
            if level == last_level:
                html.append("</li>")
            # Open new levels if deeper
            while last_level < level:
                html.append(f"<{list_type}>")
                stack.append(list_type)
                last_level += 1
            # Open new li
            html.append(f"<li>{content}")
        # Close open li and all lists
        if last_level >= 0:
            html.append("</li>")
        while stack:
            html.append(f"</{stack.pop()}>")
        return "".join(html)

    def flush_blockquote_if_needed(next_is_bq=False):
        nonlocal in_blockquote
        if in_blockquote and not next_is_bq:
            html_out.append("</blockquote>")
            in_blockquote = False

    def flush_codeblock_if_needed():
        nonlocal in_code, code_buf, code_lang
        if in_code:
            esc_code = html.escape("\n".join(code_buf))
            lang_cls = f' class="language-{code_lang}"' if code_lang else ""
            html_out.append(f"<pre><code{lang_cls}>{esc_code}</code></pre>")
            in_code = False
            code_buf = []
            code_lang = None

    header_re = re.compile(r"^(#{1,5})\s+(.*)$")
    # Update ul_re to handle more bullet types like 'o' or '•'
    ul_re = re.compile(r"^(\s*)([-*+o\u2022])\s+(.*)$")
    ol_re = re.compile(r"^(\s*)(\d+)\.\s+(.*)$")
    bq_re = re.compile(r"^\s*>\s?(.*)$")
    hr_re = re.compile(r"^\s*---\s*$")
    fence_re = re.compile(r"^\s*```(?:\s*([A-Za-z0-9_+-]+))?\s*$")

    i = 0
    while i < len(lines):
        raw = lines[i]

        # Fenced code blocks
        m_fence = fence_re.match(raw)
        if m_fence:
            if in_code:
                flush_codeblock_if_needed()
            else:
                flush_blockquote_if_needed()
                in_code = True
                code_lang = m_fence.group(1)
                code_buf = []
            i += 1
            continue

        if in_code:
            code_buf.append(raw)
            i += 1
            continue

        # Horizontal rule
        if hr_re.match(raw):
            flush_blockquote_if_needed()
            html_out.append("<hr/>")
            i += 1
            continue

        # Headers
        m_h = header_re.match(raw)
        if m_h:
            flush_blockquote_if_needed()
            level = len(m_h.group(1))
            content = _inline_md(esc(m_h.group(2)))
            html_out.append(f"<h{level}>{content}</h{level}>")
            i += 1
            continue

        # Blockquote
        m_bq = bq_re.match(raw)
        if m_bq:
            if not in_blockquote:
                html_out.append("<blockquote>")
                in_blockquote = True
            inner = _inline_md(esc(m_bq.group(1) or ""))
            html_out.append(f"<p>{inner or '&nbsp;'}</p>")
            i += 1
            continue
        else:
            flush_blockquote_if_needed()

        # Lists: collect consecutive list lines and parse nested
        m_ul = ul_re.match(raw)
        m_ol = ol_re.match(raw)
        if m_ul or m_ol:
            list_lines = []
            while i < len(lines) and (ul_re.match(lines[i]) or ol_re.match(lines[i])):
                list_lines.append(lines[i])
                i += 1
            html_out.append(parse_nested_lists(list_lines))
            continue  # Note: no i += 1 here as i is already advanced

        # Blank
        if raw.strip() == "":
            flush_blockquote_if_needed()
            i += 1
            continue

        # Paragraph
        flush_blockquote_if_needed()
        inner = _inline_md(esc(raw))
        html_out.append(f"<p>{inner}</p>")
        i += 1

    # Flush open blocks
    flush_codeblock_if_needed()
    flush_blockquote_if_needed()

    return "\n".join(html_out)


@dataclass
class OpenAIChat:
    """
    Thin facade over openai.chat.completions with:
      - model loaded from env via `model_env` (or literal if missing)
      - temperature from `temp_env` (if provided) else `temperature`
      - optional markdown archival controlled by LLM_MD_* envs
    """

    model_env: str
    temp_env: str
    api_key_env: str = "OPENAI_API_KEY"

    def chat(self, system_msg: str, user_msg: str) -> str:
        from openai import OpenAI  # local import to keep tests light

        model = os.getenv(self.model_env) or self.model_env
        api_key = os.getenv(self.api_key_env)
        temperature = os.getenv(self.temp_env) or self.temp_env
        if not api_key:
            raise RuntimeError(f"{self.api_key_env} not set")
        temp = _get_float_env(self.temp_env, temperature)

        log.debug("OpenAIChat.chat(model=%r, temperature=%s)", model, temp)
        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "system", "content": system_msg}, {"role": "user", "content": user_msg}],
            temperature=temp,
        )
        content = (resp.choices[0].message.content or "").strip()
        log.debug("OpenAIChat.chat() received %d chars", len(content))

        # Optional markdown archival
        try:
            if _truthy(os.getenv("LLM_MD_ENABLE")):
                md_dir = os.getenv("LLM_MD_DIR", "/app/state/llm")
                prefix = os.getenv("LLM_MD_PREFIX", "llm")
                max_keep = int(os.getenv("LLM_MD_MAX", "0"))  # 0 = unlimited
                os.makedirs(md_dir, exist_ok=True)
                ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")
                safe_prefix = re.sub(r"[^a-zA-Z0-9._-]+", "-", prefix).strip("-")
                fname = f"{safe_prefix + '-' if safe_prefix else ''}{ts}.md"
                out_path = os.path.join(md_dir, fname)
                with open(out_path, "w", encoding="utf-8") as f:
                    f.write(content + "\n")
                log.debug("Wrote LLM markdown to %s", out_path)
                if max_keep > 0:
                    pattern = os.path.join(md_dir, f"{safe_prefix + '-' if safe_prefix else ''}*.md")
                    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
                    for old in files[max_keep:]:
                        with contextlib.suppress(Exception):
                            os.remove(old)
        except Exception as werr:
            log.debug("LLM markdown write skipped: %r", werr)

        return content
