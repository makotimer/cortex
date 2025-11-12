# service/commands/__init__.py
"""
Command handling for IMAP email interface.

Public API:
    handle_command(raw_email, cfg, scheduler, from_addr) -> (reply_to, subject, html)
"""

from __future__ import annotations

from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

# Import only what the outside world needs
from .handlers import handle_command

# Re-export for convenience (optional, but clean)
__all__ = ["handle_command"]
