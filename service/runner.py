# service/runner.py
from __future__ import annotations

import importlib
import json
import logging
import os
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from typing import Any

from service.emailer import EmailSendError, send_html

# -----------------------------------------------------------------------------
# Config / Environment
# -----------------------------------------------------------------------------
# Optional local JSONL sink if logging_utils is not available
ACTIVITY_LOG_PATH = os.getenv("ACTIVITY_LOG_PATH", "/app/local/activity.log")

# -----------------------------------------------------------------------------
# Optional imports (graceful fallback)
# -----------------------------------------------------------------------------
_build_email = None
_write_activity_log = None

try:
    _build_email = importlib.import_module("modules._shared.html").build_email  # type: ignore[attr-defined]
except Exception:
    _build_email = None

try:
    _write_activity_log = importlib.import_module("service.logging_utils").write_activity_log  # type: ignore[attr-defined]
except Exception:
    _write_activity_log = None

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
log = logging.getLogger(__name__)
if not log.handlers:
    # Minimal default handler if the app didn't configure logging yet.
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _html_email_fallback(title: str, body_inner_html: str) -> str:
    """Simple wrapper if modules._shared.html.build_email is not available."""
    return f"""<html>
  <body style="font-family:ui-sans-serif,system-ui;line-height:1.5;margin:0;padding:8px">
    <style>
      body {{ margin:0; padding:8px; }}
      h2,h3,h4 {{ margin:8px 0 4px; }}
      p {{ margin:4px 0; }}
      ul,ol {{ margin:4px 0 4px 20px; padding:0; }}
      li {{ margin:2px 0; }}
      blockquote {{ margin:6px 0; padding-left:10px; border-left:3px solid #ccc; color:#555; }}
      sup {{ color:#666; margin-right:4px; }}
    </style>
    <h2>{escape(title)}</h2>
    {body_inner_html}
  </body>
</html>"""


def _maybe_bool(v: Any) -> Any:
    if isinstance(v, str):
        low = v.strip().lower()
        if low in ("true", "t", "yes", "y", "1"):
            return True
        if low in ("false", "f", "no", "n", "0"):
            return False
    return v


def _maybe_number(v: Any) -> Any:
    if isinstance(v, str):
        s = v.strip()
        # try int
        try:
            if s.isdigit() or (s.startswith("-") and s[1:].isdigit()):
                return int(s)
        except Exception:
            pass
        # try float
        try:
            return float(s)
        except Exception:
            pass
    return v


def _normalize_kwargs_types(kwargs: dict[str, object] | None) -> dict[str, object]:
    """
    Normalize job kwargs:

      • For keys ending with "_env":
          - Treat the string value as an ENV VAR NAME (e.g., "SCRAPER_USER_1").
          - Replace with os.getenv(<name>, "").
          - Do NOT coerce (keep as the resolved string).
          - Keep the key name as-is (e.g., person_env).

      • For all other keys:
          - If a string looks like JSON ({...} or [...]), parse it.
          - Else coerce common bool/number string forms.
          - Leave non-strings unchanged.

    This runs right before module.run(**kwargs).
    """
    if not kwargs:
        return {}

    def _resolve_env_name(name: str) -> str:
        # Strictly use the string as an env var name; if missing, return empty string.
        return os.getenv(name.strip(), "")

    def _maybe_bool(v: Any) -> Any:
        if isinstance(v, str):
            low = v.strip().lower()
            if low in ("true", "t", "yes", "y", "1"):
                return True
            if low in ("false", "f", "no", "n", "0"):
                return False
        return v

    def _maybe_number(v: Any) -> Any:
        if isinstance(v, str):
            s = v.strip()
            try:
                # int?
                if s and (s.isdigit() or (s.startswith("-") and s[1:].isdigit())):
                    return int(s)
            except Exception:
                pass
            try:
                # float?
                return float(s)
            except Exception:
                pass
        return v

    normalized: dict[str, object] = {}
    for k, v in kwargs.items():
        # Keys ending with "_env" => look up env var by NAME provided in the value.
        if isinstance(k, str) and k.endswith("_env") and isinstance(v, str):
            normalized[k] = _resolve_env_name(v)
            continue

        # All other keys: run the usual string normalization.
        if isinstance(v, str):
            s = v.strip()
            # Prefer JSON if it looks like JSON
            if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
                try:
                    normalized[k] = json.loads(s)
                    continue
                except Exception:
                    # fall through to bool/number coercion
                    pass
            nv = _maybe_bool(s)
            nv = _maybe_number(nv)
            normalized[k] = nv
        else:
            normalized[k] = v

    return normalized


def _resolve_callable(module_path: str) -> Callable[..., Any]:
    """Import module and return its `run` callable."""
    mod = importlib.import_module(module_path)
    if not hasattr(mod, "run") or not callable(mod.run):
        raise AttributeError(f"Module {module_path!r} does not define a callable `run(**kwargs)`.")
    return mod.run  # type: ignore[no-any-return]


def _wrap_html(subject: str, inner_html: str) -> str:
    if _build_email:
        try:
            return _build_email(subject, inner_html)  # type: ignore[misc]
        except Exception:
            # fall through to local wrapper
            pass
    return _html_email_fallback(subject, inner_html)


def _emit_activity_jsonl(record: dict[str, Any]) -> None:
    """Write a structured activity record either via logging_utils or to a JSONL file."""
    if _write_activity_log:
        try:
            _write_activity_log(record)  # type: ignore[misc]
            return
        except Exception as e:
            log.warning("logging_utils.write_activity_log failed: %s", e)
    # Local JSONL fallback
    try:
        os.makedirs(os.path.dirname(ACTIVITY_LOG_PATH), exist_ok=True)
        with open(ACTIVITY_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log.error("Failed to write activity JSONL: %s", e)


@dataclass
class RunResult:
    ok: bool
    message: str
    html: str | None = None
    meta: dict[str, Any] | None = None
    subject: str | None = None


def _coerce_result(value: Any) -> RunResult:
    """
    Normalize module return into a RunResult.

    Acceptable shapes:
      - str                          -> inner HTML
      - None                         -> no output
      - (str, dict)                  -> inner HTML + meta (may include 'message', 'subject')
      - {'html': str, 'meta': dict}  -> convenience wrapper
      - dict                         -> treated as meta-only (no HTML)
    """
    if isinstance(value, dict) and "html" not in value:
        # Pure-meta modules (sonos, etc.) → success, no HTML
        return RunResult(
            ok=True,
            message=value.get("message", "OK"),
            html=None,
            meta=value,
            subject=value.get("subject"),
        )

    # str => HTML
    if isinstance(value, str):
        return RunResult(ok=True, message="OK", html=value, meta=None)

    # None => no output
    if value is None:
        return RunResult(ok=True, message="OK", html=None, meta=None)

    # dict convenience form: {'html':..., 'meta':...}
    if isinstance(value, dict) and "html" in value:
        html = value.get("html") if isinstance(value.get("html"), str) else None
        meta = value.get("meta") if isinstance(value.get("meta"), dict) else {}
        msg = (meta or {}).get("message", "OK")
        return RunResult(
            ok=True,
            message=msg,
            html=html,
            meta=meta or None,
            subject=(meta or {}).get("subject"),
        )

    # (str, dict) => HTML + meta (optional message/subject)
    if isinstance(value, tuple) and len(value) == 2 and isinstance(value[0], str) and isinstance(value[1], dict):
        meta = value[1]
        msg = meta.get("message", "OK")
        return RunResult(
            ok=True,
            message=msg,
            html=value[0],
            meta=meta,
            subject=meta.get("subject"),
        )

    raise TypeError("Module return must be one of: str, None, (str, dict), or {'html':..., 'meta':...}")


def _default_email_to() -> list[str]:
    """
    Return a list with the Proton Bridge username (or SMTP_FROM / PROTON_USERNAME)
    as the fallback recipient.  This is the address the bridge is logged in as,
    i.e. the address that can actually receive mail.
    """
    from .emailer import _resolve_smtp_settings  # local import to avoid cycles

    settings = _resolve_smtp_settings()
    addr = (settings.get("default_from_addr") or "").strip()
    return [addr] if addr else []


# -----------------------------------------------------------------------------
# Public API
# -----------------------------------------------------------------------------
def run_module_once(
    module: str,
    kwargs: dict[str, object] | None = None,
    email_to: list[str] | None = None,
    subject: str | None = None,
    send_email: bool = True,
    trigger_type: str = "scheduled",
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    job_context: dict[str, object] | None = None,  # e.g., {"job_id": "...", "module": "...", "now_iso": "..."}
    timeout_sec: int | None = None,
) -> tuple[str | None, str]:
    """
    Execute a module's run(**kwargs) once.

    Returns:
        (html_or_none, run_id)
    Raises:
        Propagates exceptions from module execution (caller/CLI will catch and log).
    """
    run_id = uuid.uuid4().hex
    started_at = now_iso()

    # Build base context
    context: dict[str, Any] = {
        "run_id": run_id,
        "module": module,
        "trigger_type": trigger_type,
        "started_at": started_at,
    }
    if job_context:
        # Shallow merge
        context.update({k: v for k, v in job_context.items() if k not in context})

    # Normalize kwargs
    kw = _normalize_kwargs_types(kwargs)

    # Resolve module.run
    run_callable = _resolve_callable(module)

    # Execute with timeout in a worker thread
    result: RunResult
    exc: BaseException | None = None
    duration_ms: int | None = None

    def _invoke() -> Any:
        return run_callable(**kw)

    t0 = datetime.now()
    try:
        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="runner") as pool:
            fut = pool.submit(_invoke)
            value = fut.result(timeout=timeout_sec) if timeout_sec else fut.result()
        result = _coerce_result(value)
    except FutureTimeout:
        exc = TimeoutError(f"Module run timed out after {timeout_sec}s")
        result = RunResult(ok=False, message=str(exc), html=None, meta={"timeout_sec": timeout_sec})
    except BaseException as e:
        exc = e
        result = RunResult(ok=False, message=str(e), html=None, meta={"exception_type": type(e).__name__})
    finally:
        duration_ms = int((datetime.now() - t0).total_seconds() * 1000)

    # Prepare email gating (env + kwarg precedence)
    # Hard override: dry-run disables sending no matter what.
    dry_run = str(os.getenv("SCHEDULED_MODULES_DRY_RUN", "")).strip().lower() in {"1", "true", "yes", "on"}
    env_send_default = str(os.getenv("SEND_EMAIL", "1")).strip().lower() in {"1", "true", "yes", "on"}
    effective_send = send_email if send_email is not None else env_send_default
    if dry_run:
        effective_send = False

    # Prepare email if requested and HTML exists
    html_sent = False
    html_out: str | None = result.html
    email_message_id: str | None = None

    if result.html and effective_send:
        # Prefer module-provided subject; then config.json subject; then fallback.
        subj = result.subject or subject or f"{module} run — {'OK' if result.ok else 'FAILED'}"
        wrapped = _wrap_html(subj, result.html)
        try:
            email_message_id = send_html(
                subject=subj,
                html=wrapped,
                to=email_to or _default_email_to(),
                cc=cc,
                bcc=bcc,
            )
            html_sent = True
            # Return the module's original HTML to the caller (contract)
            html_out = result.html
        except EmailSendError as e:
            log.error("Email send failed: %s", e)
        except Exception as e:
            # Defensive: treat any unexpected error like a send failure, but don't crash the run
            log.exception("Unexpected error during email send: %s", e)

    # Emit structured JSON activity record
    record: dict[str, Any] = {
        "ts": now_iso(),
        "run_id": run_id,
        "module": module,
        "trigger_type": trigger_type,
        "ok": result.ok,
        "message": result.message,
        "duration_ms": duration_ms,
        "emailed": html_sent,
        "email_message_id": email_message_id,
        "email_to": email_to or [],
        "cc": cc or [],
        "bcc": bcc or [],
        "context": context,
        "kwargs": kw,
        "meta": result.meta or {},
    }
    _emit_activity_jsonl(record)

    # If there was an exception, re-raise so CLI can handle exit code/logging
    if exc:
        raise exc

    return html_out, run_id
