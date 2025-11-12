# service/imap_listener.py
from __future__ import annotations

import logging
import os
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler
from imapclient import IMAPClient

from service.emailer import EmailSendError, send_html
from service.imap_commands import handle_command

logger = logging.getLogger(__name__)
if not logger.handlers:
    logger.addHandler(logging.NullHandler())

# --------------------------------------------------------------------------- #
# Public control surface
# --------------------------------------------------------------------------- #
_thread: threading.Thread | None = None
_stop_event = threading.Event()


class ListenerController:
    def __init__(self, thread: threading.Thread, stop_event: threading.Event):
        self._thread = thread
        self._stop = stop_event

    def stop(self) -> None:
        """Signal the listener loop to exit promptly."""
        self._stop.set()

    def join(self, timeout: float | None = None) -> None:
        """Wait for the listener thread to exit."""
        if self._thread.is_alive():
            self._thread.join(timeout=timeout)

    @property
    def thread(self) -> threading.Thread:
        return self._thread


def start(
    cfg_getter: Callable[[], dict[str, Any]] | None = None,
    scheduler_getter: Callable[[], BackgroundScheduler] | None = None,
) -> ListenerController:
    """
    Start the IMAP listener in a background thread (non-blocking).
    Returns a controller with .stop() and .join().
    """
    global _thread
    if _thread and _thread.is_alive():
        logger.info("[commands] imap_listener already running")
        return ListenerController(_thread, _stop_event)

    _stop_event.clear()
    t = threading.Thread(
        target=_command_listener_loop,
        name="imap-command-listener",
        args=(cfg_getter, scheduler_getter, _stop_event),
        daemon=True,
    )
    t.start()
    _thread = t
    return ListenerController(t, _stop_event)


def stop() -> None:
    """Module-level convenience to stop the listener loop."""
    _stop_event.set()


# --------------------------------------------------------------------------- #
# IMAP loop
# --------------------------------------------------------------------------- #
def _command_listener_loop(
    cfg_getter: Callable[[], dict[str, Any]] | None,
    scheduler_getter: Callable[[], BackgroundScheduler] | None,
    stop_event: threading.Event,
) -> None:
    """
    Main IMAP listener loop:
    - Connects to Proton Bridge via IMAP
    - Watches the configured 'Command' folder
    - Processes new emails as commands
    - Replies via email
    - Persists last processed UID to resume after restarts
    - Uses exponential backoff on errors
    """
    host = os.getenv("PROTON_IMAP_HOST", "cortex_bridge")
    port = int(os.getenv("PROTON_IMAP_PORT", "143"))
    user = os.getenv("BRIDGE_USERNAME")
    pwd = os.getenv("BRIDGE_PASSWORD")
    want_folder = os.getenv("COMMANDS_FOLDER", "Command")
    idle_timeout = int(os.getenv("IMAP_IDLE_TIMEOUT", "30"))

    if not (user and pwd):
        logger.error("Missing BRIDGE_USERNAME/BRIDGE_PASSWORD; command listener disabled.")
        return

    scheduler = scheduler_getter() if scheduler_getter else None
    backoff = 30  # Initial retry delay in seconds

    # --------------------------------------------------------------------- #
    # Helper: resolve mailbox name (case-insensitive, with common prefixes)
    # --------------------------------------------------------------------- #
    def _resolve_mailbox(c: IMAPClient, desired: str) -> str:
        desired_lc = desired.lower()
        folders = [name.decode() if isinstance(name, bytes) else name for _flags, _delim, name in c.list_folders()]

        # Exact match
        if desired in folders:
            return desired
        # Case-insensitive match
        for n in folders:
            if n.lower() == desired_lc:
                return n
        # Common prefixes: Labels/, Folders/
        for prefix in ("Labels/", "Folders/"):
            cand = prefix + desired
            if cand in folders:
                return cand
            for n in folders:
                if n.lower() == cand.lower():
                    return n
        # Fallback: ends with "/Command"
        for n in folders:
            if n.lower().endswith("/" + desired_lc):
                return n
        raise ValueError(f"Mailbox {desired!r} not found")

    # --------------------------------------------------------------------- #
    # Helper: persistent state file for last processed UID
    # --------------------------------------------------------------------- #
    def _state_uid_path(mailbox: str) -> Path:
        override = os.getenv("COMMAND_STATE_FILE")
        if override:
            p = Path(override)
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", mailbox)
        base = Path("/app/state")
        base.mkdir(parents=True, exist_ok=True)
        return base / f"command_last_uid_{safe}.txt"

    def _load_last_uid(p: Path) -> int:
        try:
            return int(p.read_text().strip())
        except Exception:
            return 0  # Start from scratch if file is missing/corrupted

    def _save_last_uid(p: Path, uid: int) -> None:
        try:
            p.write_text(str(uid))
        except Exception as e:
            logger.warning("Failed to save last UID to %s: %s", p, e)

    # --------------------------------------------------------------------- #
    # Main retry loop
    # --------------------------------------------------------------------- #
    while not stop_event.is_set():
        try:
            with IMAPClient(host, port, ssl=False) as client:
                client.login(user, pwd)
                mailbox = _resolve_mailbox(client, want_folder)
                logger.info("[commands] Listening on mailbox: %s", mailbox)
                client.select_folder(mailbox)

                state_file = _state_uid_path(mailbox)
                last_uid = _load_last_uid(state_file)

                # If no state, initialize from current mailbox
                if last_uid == 0:
                    uids = client.search(["ALL"])
                    last_uid = max(uids) if uids else 0
                    _save_last_uid(state_file, last_uid)

                # -----------------------------------------------------------------
                # Process any emails already present (above last_uid)
                # -----------------------------------------------------------------
                def _process_new_emails(state_file: Path) -> None:
                    nonlocal last_uid
                    if stop_event.is_set():
                        return
                    new_uids = [u for u in client.search(["ALL"]) if u > last_uid]
                    if not new_uids:
                        return
                    new_uids.sort()
                    for uid in new_uids:
                        if stop_event.is_set():
                            return
                        try:
                            data = client.fetch(uid, ["RFC822", "ENVELOPE"])
                            raw_email = data[uid][b"RFC822"]
                            envelope = data[uid].get(b"ENVELOPE")

                            # Extract sender reliably from ENVELOPE
                            from_addr = None
                            if envelope and envelope.from_ and envelope.from_[0]:
                                addr = envelope.from_[0]
                                from_addr = f"{addr.mailbox.decode()}@{addr.host.decode()}"

                            cfg = cfg_getter() if cfg_getter else {}
                            to_addr, subject, content = handle_command(raw_email, cfg, scheduler, from_addr)

                            reply_to = to_addr or from_addr
                            if reply_to:
                                try:
                                    if content.startswith("<"):
                                        send_html(subject=subject, html=content, to=[reply_to])
                                    else:
                                        send_html(
                                            subject=subject,
                                            html="<pre style='font-family: monospace; "
                                            "white-space: pre-wrap;'>{content}</pre>",
                                            to=[reply_to],
                                        )
                                except EmailSendError:
                                    logger.error("Failed to send reply to %s", reply_to, exc_info=True)

                            last_uid = uid
                            _save_last_uid(state_file, last_uid)
                        except Exception as e:
                            logger.error("Failed to process email UID %s: %s", uid, e, exc_info=True)

                _process_new_emails(state_file)

                # -----------------------------------------------------------------
                # Enter IDLE mode and wait for new emails
                # -----------------------------------------------------------------
                while not stop_event.is_set():
                    client.idle()
                    try:
                        client.idle_check(timeout=idle_timeout)
                    finally:
                        client.idle_done()
                    _process_new_emails(state_file)

            # Success: reset backoff
            backoff = 30

        except Exception as e:  # noqa: PERF203
            # Only handle errors if not shutting down
            if stop_event.is_set():
                break

            logger.error("[commands] Connection/error: %r - retrying in %ds", e, backoff)

            # Exponential backoff with cap
            slept = 0
            while slept < backoff and not stop_event.is_set():
                time.sleep(1)
                slept += 1
            backoff = min(backoff * 2, 300)  # Max 5 minutes

    logger.info("[commands] Listener stopped")
