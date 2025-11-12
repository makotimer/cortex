# Re-export lib API for tests and main.py
from . import logging_bridge as log
from .biblehub import commentary_url
from .config import Settings, load
from .dates import days_since, resolve_date
from .links import linkify_scripture_refs, nkjv_link
from .llm import generate_commentary
from .plan import PlanItem, load_plan
from .render import assemble_email_html

__all__ = [
    "PlanItem",
    "Settings",
    "assemble_email_html",
    "commentary_url",
    "days_since",
    "generate_commentary",
    "linkify_scripture_refs",
    "load",
    "load_plan",
    "log",
    "nkjv_link",
    "resolve_date",
]
