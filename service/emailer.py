# service/emailer.py
from __future__ import annotations

import os
import smtplib
import ssl
import time
import uuid
from collections.abc import Iterable
from datetime import datetime
from email.message import EmailMessage
from email.utils import formatdate, make_msgid

# ---- Errors -----------------------------------------------------------------


class EmailSendError(RuntimeError):
    """Raised when an email cannot be delivered."""


# ---- Env / Settings ----------------------------------------------------------


def _getenv_any(*names: str, default: str | None = None) -> str | None:
    for n in names:
        v = os.getenv(n)
        if v is not None and v != "":
            return v
    return default


def _resolve_smtp_settings() -> dict:
    """
    Resolve SMTP settings from env with sane defaults and Proton Bridge compatibility.

    Preferred:
      - BRIDGE_USERNAME / BRIDGE_PASSWORD (Proton Bridge)
      - SMTP_HOST / SMTP_PORT
      - SMTP_USE_SSL = "true" | "false"
      - SMTP_STARTTLS = "true" | "false" | "auto" (default)

    Back-compat (older code):
      - PROTON_SMTP_HOST / PROTON_SMTP_PORT
      - PROTON_USERNAME / PROTON_SMTP_TOKEN
      - PROTON_SMTP_STARTTLS
    """
    host = _getenv_any("BRIDGE_HOST", "SMTP_HOST", "PROTON_SMTP_HOST", default="127.0.0.1")
    port = int(_getenv_any("BRIDGE_SMTP_PORT", "SMTP_PORT", "PROTON_SMTP_PORT", default="1025") or 1025)

    username = _getenv_any("BRIDGE_USERNAME", "SMTP_USERNAME", "PROTON_USERNAME")
    password = _getenv_any("BRIDGE_PASSWORD", "SMTP_PASSWORD", "PROTON_SMTP_TOKEN")

    # Mutually exclusive knobs: prefer explicit USE_SSL when provided
    use_ssl = (_getenv_any("SMTP_USE_SSL", default="false") or "false").strip().lower() == "true"
    starttls = (_getenv_any("SMTP_STARTTLS", "PROTON_SMTP_STARTTLS", default="auto") or "auto").strip().lower()
    if use_ssl:
        # If explicit SSL, ignore STARTTLS
        starttls = "false"

    # Default From
    default_from_addr = _getenv_any("SMTP_FROM", "PROTON_FROM", default=username or "")
    default_from_name = _getenv_any("BRIDGE_DISPLAY", default=username or "")

    # Optional permissive TLS
    insecure_tls = (_getenv_any("SMTP_INSECURE_TLS", default="false") or "false").strip().lower() == "true"

    return {
        "host": host,
        "port": port,
        "username": username,
        "password": password,
        "use_ssl": use_ssl,
        "starttls": starttls,  # "true" | "false" | "auto"
        "default_from_addr": default_from_addr,
        "default_from_name": default_from_name,
        "insecure_tls": insecure_tls,
    }


# ---- Helpers ----------------------------------------------------------------


def _as_list(values: Iterable[str] | None) -> list[str]:
    if not values:
        return []
    return [v for v in (s.strip() for s in values) if v]


def _should_starttls(port: int, starttls_setting: str) -> bool:
    if starttls_setting == "true":
        return True
    if starttls_setting == "false":
        return False
    # "auto": commonly enable STARTTLS except when obviously cleartext relay ports are used explicitly
    return port not in (25, 2525)


def _build_message(
    *,
    subject: str,
    html: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    from_name: str | None,
    from_addr: str,
    reply_to: str | None,
    headers: dict[str, str] | None,
) -> EmailMessage:
    if not subject or not subject.strip():
        raise EmailSendError("Missing subject.")
    if not html or not html.strip():
        raise EmailSendError("Missing HTML body.")
    if not to and not cc and not bcc:
        raise EmailSendError("No recipients (to/cc/bcc).")

    msg = EmailMessage()

    # From
    if from_name:
        msg["From"] = f"{from_name} <{from_addr}>"
    else:
        msg["From"] = from_addr

    # Recipients (note: bcc is not added to headers)
    if to:
        msg["To"] = ", ".join(to)
    if cc:
        msg["Cc"] = ", ".join(cc)
    if reply_to:
        msg["Reply-To"] = reply_to

    msg["Subject"] = subject

    # Standard headers
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid()
    msg["X-Mailer-Nonce"] = uuid.uuid4().hex

    # User-supplied extra headers (avoid dangerous ones)
    if headers:
        for k, v in headers.items():
            lk = k.lower()
            if lk in {"from", "to", "cc", "bcc", "subject", "date", "message-id"}:
                # Ignore attempts to override critical headers
                continue
            msg[k] = v

    # Body (HTML with plain-text fallback)
    # Add a tiny stamp comment for traceability (harmless in HTML)
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    nonce = uuid.uuid4().hex[:8]
    html_augmented = html.rstrip() + f"\n<!-- mailer-ts:{stamp} nonce:{nonce} -->"

    msg.set_content("This message requires an HTML-capable client.")
    msg.add_alternative(html_augmented, subtype="html", charset="utf-8")

    return msg


def _send_via_smtp(msg: EmailMessage, *, rcpt_to: list[str], settings: dict) -> None:
    host = settings["host"]
    port = settings["port"]
    username = settings["username"]
    password = settings["password"]
    use_ssl = settings["use_ssl"]
    starttls_setting = settings["starttls"]
    insecure_tls = settings["insecure_tls"]

    if not (host and username and password):
        raise EmailSendError(
            "Missing SMTP credentials or host. "
            "Expected BRIDGE_USERNAME/BRIDGE_PASSWORD and SMTP_HOST (or compatible)."
        )

    # TLS context
    context = ssl._create_unverified_context() if insecure_tls else ssl.create_default_context()

    try:
        server = smtplib.SMTP_SSL(host, port, context=context) if use_ssl else smtplib.SMTP(host, port)
        with server:
            server.ehlo()
            if not use_ssl and _should_starttls(port, starttls_setting):
                server.starttls(context=context)
                server.ehlo()
            server.login(username, password)
            # send_message will derive recipients from headers; include BCC by explicit rcpt_to
            server.send_message(msg, to_addrs=rcpt_to)
    except EmailSendError:
        raise
    except Exception as e:
        raise EmailSendError(f"SMTP send failed: {e}") from e


def _flatten_recipient_list(recipients: list | str | None) -> list[str]:
    """
    Normalize recipients:
    - None → []
    - str → [str]
    - [str, ...] → [str, ...]
    - [[str, ...]] → [str, ...]  ← Now handles multiple emails in inner list
    - Deeply nested? → Only flatten one level
    """
    if recipients is None:
        return []

    result: list[str] = []

    # Handle string
    if isinstance(recipients, str):
        result = [recipients]

    # Handle list
    elif isinstance(recipients, list):
        # Case 1: List of strings → use directly
        if recipients and isinstance(recipients[0], str):
            result = [email for email in recipients if isinstance(email, str)]

        # Case 2: List containing one list → flatten it
        elif len(recipients) == 1 and isinstance(recipients[0], list):
            inner = recipients[0]
            result = inner if all(isinstance(x, str) for x in inner) else [x for x in inner if isinstance(x, str)]
        # Case 3: List of mixed/nested → extract strings
        else:
            for item in recipients:
                if isinstance(item, str):
                    result.append(item)
                elif isinstance(item, list):
                    # Flatten any list inside, but only take strings
                    result.extend(x for x in item if isinstance(x, str))
    return result


# ---- Public API --------------------------------------------------------------


def send_html(
    *,
    subject: str,
    html: str,
    to: list[str],
    cc: list[str] | None = None,
    bcc: list[str] | None = None,
    headers: dict[str, str] | None = None,
) -> str:
    """
    Send an HTML email.

    Returns:
        message_id (str): RFC-822 Message-ID generated by the sender.

    Raises:
        EmailSendError on any failure (connection/auth/SMTP/validation/etc).
    """
    # === Normalize all recipient lists ===
    to = _flatten_recipient_list(to)
    cc = _flatten_recipient_list(cc)
    bcc = _flatten_recipient_list(bcc)

    settings = _resolve_smtp_settings()

    # Resolve recipients and from
    to_l = _as_list(to)
    cc_l = _as_list(cc)
    bcc_l = _as_list(bcc)
    from_name_final = settings["default_from_name"].strip()
    from_addr_final = settings["default_from_addr"].strip()
    if not from_addr_final:
        raise EmailSendError("No from address resolved. Set SMTP_FROM or BRIDGE_USERNAME (or PROTON_USERNAME).")

    msg = _build_message(
        subject=subject,
        html=html,
        to=to_l,
        cc=cc_l,
        bcc=bcc_l,
        from_name=from_name_final,
        from_addr=from_addr_final,
        reply_to=from_addr_final,
        headers=headers,
    )

    # Build full envelope recipients (To + Cc + Bcc)
    rcpt_to = [*to_l, *cc_l, *bcc_l]
    if not rcpt_to:
        raise EmailSendError("No envelope recipients.")

    for attempt in range(4):
        try:
            _send_via_smtp(msg, rcpt_to=rcpt_to, settings=settings)
            return str(msg["Message-ID"])  # success
        except EmailSendError as e:  # noqa: PERF203
            if "5xx" not in str(e) and "Internal server error" not in str(e):
                raise
            time.sleep(2**attempt)  # 2s, 4s, 8s, 16s
    raise EmailSendError("Permanent send failure after retries")


def ping() -> bool:
    """
    Lightweight health check against the SMTP relay.
    Returns True if login succeeds; raises EmailSendError otherwise.
    """
    settings = _resolve_smtp_settings()

    host = settings["host"]
    port = settings["port"]
    username = settings["username"]
    password = settings["password"]
    use_ssl = settings["use_ssl"]
    starttls_setting = settings["starttls"]
    insecure_tls = settings["insecure_tls"]

    if not (host and username and password):
        raise EmailSendError("SMTP health check failed: missing host/credentials.")

    context = ssl._create_unverified_context() if insecure_tls else ssl.create_default_context()

    try:
        server = smtplib.SMTP_SSL(host, port, context=context) if use_ssl else smtplib.SMTP(host, port)
        with server:
            server.ehlo()
            if not use_ssl and _should_starttls(port, starttls_setting):
                server.starttls(context=context)
                server.ehlo()
            server.login(username, password)
        return True
    except Exception as e:
        raise EmailSendError(f"SMTP health check failed: {e}") from e
