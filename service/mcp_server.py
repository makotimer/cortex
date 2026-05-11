# service/mcp_server.py
"""MCP server exposing ProtonMail Bridge IMAP as Claude Code tools."""
from __future__ import annotations

import email.header
import os
import re
from collections.abc import Generator
from contextlib import contextmanager, suppress

import mailparser
from dotenv import load_dotenv
from imapclient import IMAPClient
from mcp.server.fastmcp import FastMCP

load_dotenv()

mcp = FastMCP("protonmail-bridge")


# ---------------------------------------------------------------------------
# IMAP connection helpers
# ---------------------------------------------------------------------------


@contextmanager
def _client() -> Generator[IMAPClient, None, None]:
    host = os.getenv("PROTON_IMAP_HOST", "cortex_bridge")
    port = int(os.getenv("PROTON_IMAP_PORT", "143"))
    user = os.getenv("BRIDGE_USERNAME")
    pwd = os.getenv("BRIDGE_PASSWORD")
    if not user or not pwd:
        raise RuntimeError("Missing BRIDGE_USERNAME or BRIDGE_PASSWORD in environment")
    c = IMAPClient(host, port, ssl=False)
    c.login(user, pwd)
    try:
        yield c
    finally:
        with suppress(Exception):
            c.logout()


def _resolve(c: IMAPClient, name: str) -> str:
    """Resolve a short folder name to its full IMAP path (case-insensitive)."""
    lc = name.lower()
    folders = [n.decode() if isinstance(n, bytes) else n for _, _, n in c.list_folders()]
    if name in folders:
        return name
    for n in folders:
        if n.lower() == lc:
            return n
    for prefix in ("Labels/", "Folders/"):
        cand = prefix + name
        for n in folders:
            if n.lower() == cand.lower():
                return n
    for n in folders:
        if n.lower().endswith("/" + lc):
            return n
    raise ValueError(f"Folder {name!r} not found. Call list_folders() to see available folders.")


def _decode_subject(raw: bytes | None) -> str:
    if not raw:
        return "(no subject)"
    try:
        parts = email.header.decode_header(raw.decode("latin-1"))
        return "".join(
            p.decode(charset or "utf-8", errors="replace") if isinstance(p, bytes) else p
            for p, charset in parts
        )
    except Exception:
        return raw.decode("utf-8", errors="replace")


def _from_str(env_addr: object) -> str:
    if not env_addr:
        return ""
    try:
        addr = env_addr[0] if isinstance(env_addr, (list, tuple)) else env_addr
        mb = addr.mailbox.decode(errors="replace") if addr.mailbox else ""  # type: ignore[union-attr]
        host = addr.host.decode(errors="replace") if addr.host else ""  # type: ignore[union-attr]
        return f"{mb}@{host}"
    except Exception:
        return str(env_addr)


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def list_folders() -> str:
    """List all IMAP folders/labels in the ProtonMail mailbox."""
    with _client() as c:
        rows = []
        for flags, _delim, name in c.list_folders():
            name_str = name.decode() if isinstance(name, bytes) else name
            flag_str = ", ".join(f.decode() if isinstance(f, bytes) else f for f in flags)
            rows.append(f"  {name_str}  [{flag_str}]")
        return "Folders:\n" + "\n".join(rows)


@mcp.tool()
def list_emails(folder: str, limit: int = 20, unseen_only: bool = False) -> str:
    """List emails in a folder showing UID, date, sender, and subject (newest first).

    Args:
        folder: Folder name, e.g. "INBOX" or "Labels/Newsletter". Call list_folders() to see options.
        limit: Max emails to return (default 20).
        unseen_only: Only return unread emails.
    """
    with _client() as c:
        resolved = _resolve(c, folder)
        c.select_folder(resolved, readonly=True)
        criteria = ["UNSEEN"] if unseen_only else ["ALL"]
        uids = sorted(c.search(criteria), reverse=True)[:limit]
        if not uids:
            label = "unread " if unseen_only else ""
            return f"No {label}emails in {resolved}."

        data = c.fetch(uids, ["ENVELOPE", "FLAGS"])
        lines = [f"{'UID':<7} {'Date':<18} {'From':<38} {'Flags':<14} Subject"]
        lines.append("-" * 115)
        for uid in uids:
            env = data[uid].get(b"ENVELOPE")
            raw_flags = data[uid].get(b"FLAGS", ())
            flag_str = " ".join(f.decode() if isinstance(f, bytes) else f for f in raw_flags)
            if env:
                date = env.date.strftime("%Y-%m-%d %H:%M") if env.date else "?"
                from_addr = _from_str(env.from_)
                subject = _decode_subject(env.subject)
                lines.append(
                    f"{uid:<7} {date:<18} {from_addr[:37]:<38} {flag_str[:13]:<14} {subject[:60]}"
                )
            else:
                lines.append(f"{uid:<7} (no envelope data)")
        return "\n".join(lines)


@mcp.tool()
def read_email(folder: str, uid: int) -> str:
    """Read the full content of an email: headers and body.

    Args:
        folder: Folder containing the email.
        uid: UID of the email (from list_emails or search_emails).
    """
    with _client() as c:
        resolved = _resolve(c, folder)
        c.select_folder(resolved, readonly=True)
        data = c.fetch([uid], ["RFC822"])
        if uid not in data:
            return f"Email UID {uid} not found in {resolved}."
        raw = data[uid][b"RFC822"]
        mail = mailparser.parse_from_bytes(raw)

        parts = [
            f"UID:     {uid}",
            f"Date:    {mail.date}",
            f"From:    {mail.from_}",
            f"To:      {mail.to}",
            f"Subject: {mail.subject}",
            "",
        ]
        if mail.text_plain:
            parts.append(mail.text_plain[0][:5000])
        elif mail.text_html:
            text = re.sub(r"<[^>]+>", " ", mail.text_html[0])
            text = re.sub(r"\s+", " ", text).strip()
            parts.append(text[:5000])
        else:
            parts.append("(no body content)")
        return "\n".join(parts)


@mcp.tool()
def move_email(folder: str, uid: int, destination: str) -> str:
    """Move a single email from one folder to another.

    Args:
        folder: Source folder.
        uid: UID of the email to move.
        destination: Destination folder name.
    """
    with _client() as c:
        src = _resolve(c, folder)
        dst = _resolve(c, destination)
        c.select_folder(src)
        c.copy([uid], dst)
        c.delete_messages([uid])
        c.expunge()
        return f"Moved UID {uid}: {src} → {dst}"


@mcp.tool()
def move_emails(folder: str, uids: list[int], destination: str) -> str:
    """Move multiple emails from one folder to another in a single operation.

    Args:
        folder: Source folder.
        uids: List of UIDs to move.
        destination: Destination folder name.
    """
    if not uids:
        return "No UIDs provided."
    with _client() as c:
        src = _resolve(c, folder)
        dst = _resolve(c, destination)
        c.select_folder(src)
        c.copy(uids, dst)
        c.delete_messages(uids)
        c.expunge()
        return f"Moved {len(uids)} emails: {src} → {dst}  (UIDs: {uids})"


@mcp.tool()
def search_emails(
    folder: str,
    subject_contains: str | None = None,
    from_contains: str | None = None,
    since_date: str | None = None,
    limit: int = 20,
) -> str:
    """Search emails by subject text, sender address, or date.

    Args:
        folder: Folder to search.
        subject_contains: Text that must appear in the subject.
        from_contains: Text that must appear in the From address.
        since_date: Only emails on or after this date, e.g. "1-May-2026".
        limit: Max results (default 20, newest first).
    """
    with _client() as c:
        resolved = _resolve(c, folder)
        c.select_folder(resolved, readonly=True)

        criteria: list[str] = []
        if subject_contains:
            criteria += ["SUBJECT", subject_contains]
        if from_contains:
            criteria += ["FROM", from_contains]
        if since_date:
            criteria += ["SINCE", since_date]
        if not criteria:
            criteria = ["ALL"]

        uids = sorted(c.search(criteria), reverse=True)[:limit]
        if not uids:
            return f"No emails matched in {resolved}."

        data = c.fetch(uids, ["ENVELOPE"])
        lines = [f"{'UID':<7} {'Date':<18} {'From':<38} Subject"]
        lines.append("-" * 100)
        for uid in uids:
            env = data[uid].get(b"ENVELOPE")
            if env:
                date = env.date.strftime("%Y-%m-%d %H:%M") if env.date else "?"
                from_addr = _from_str(env.from_)
                subject = _decode_subject(env.subject)
                lines.append(f"{uid:<7} {date:<18} {from_addr[:37]:<38} {subject[:60]}")
            else:
                lines.append(f"{uid:<7} (no envelope data)")
        return "\n".join(lines)


if __name__ == "__main__":
    mcp.run()
