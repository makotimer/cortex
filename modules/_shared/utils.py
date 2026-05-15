# modules/_shared/utils.py
from __future__ import annotations

import contextlib
import glob
import html
import logging
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime

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


def md_to_html(md: str) -> str:
    import markdown as _md

    return _md.markdown(md, extensions=["fenced_code", "tables"])


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
                ts = datetime.now(UTC).strftime("%Y-%m-%dT%H-%M-%SZ")
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
