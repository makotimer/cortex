# service/imap_commands/parser.py
import contextlib
import json
import re
from typing import Any


def parse_command_line(line: str) -> dict[str, Any]:
    """
    Parse email command line into a structured dict.

    SUPPORTED COMMAND FORMATS (case-insensitive):

    1. LIST
       → {"command": "LIST"}

    2. CAREER REPORT
       → {"command": "CAREER REPORT"}

    3. RUN MODULE=<id-or-path>
       → {"command": "RUN", "module_id": "<value>"}

    4. RUN MODULE=<id> KWARGS={"key": "value"} NO_EMAIL=true PRINT_HTML=true
       → {"command": "RUN", "module_id": "...", "kwargs": {...}, "no_email": True, "print_html": True}

    RULES:
    - Single word → command (e.g., LIST)
    - Two words → multi-word command (e.g., CAREER REPORT)
    - RUN MODULE=... → special handling
    - Optional KWARGS=..., NO_EMAIL=..., PRINT_HTML=... (any order)
    - Quoted values allowed (e.g., "my job")
    - Case-insensitive for keywords
    """
    line = line.strip()
    if not line:
        return {"command": None}

    # Strip leading reply/forward prefixes a mail client prepends to a reply subject
    # (e.g. "Re: APPROVE hs-…", "Fwd: re: LIST") so subject-based commands still parse.
    line = re.sub(r"^(?:(?:re|fwd|fw)\s*:\s*)+", "", line, flags=re.IGNORECASE).strip()
    if not line:
        return {"command": None}

    # --- APPROVE / DENY <token> (token is site-prefixed, e.g. hs-…) ---
    m = re.match(r"^(APPROVE|DENY)\s+([A-Za-z0-9][A-Za-z0-9._-]*-[A-Za-z0-9._-]+)\s*$", line, re.IGNORECASE)
    if m:
        return {"command": m.group(1).upper(), "token": m.group(2)}

    # --- 1. Single word command (e.g., LIST) ---
    if re.fullmatch(r"\w+", line, re.IGNORECASE) and line.upper() not in ("APPROVE", "DENY"):
        return {"command": line.upper()}

    # --- 2. Multi-word command (e.g., CAREER REPORT) ---
    upper_line = line.upper()
    if upper_line == "CAREER REPORT":
        return {"command": "CAREER REPORT"}

    # --- 3. RUN MODULE=... with optional args ---
    run_pattern = re.compile(
        r"""
        ^\s*RUN\s+MODULE=(?P<module_id>[^\s"'][^\s]*|"[^"]*"|'[^']*')\s*
        (?:KWARGS=(?P<kwargs>"[^"]*"|'[^']*'|\{.*\}))?\s*
        (?:NO_EMAIL=(?P<no_email>true|false))?\s*
        (?:PRINT_HTML=(?P<print_html>true|false))?\s*$
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    match = run_pattern.match(line)
    if match:
        cmd: dict[str, Any] = {
            "command": "RUN",
            "module_id": match.group("module_id").strip(" \"'"),
            "kwargs": {},
            "no_email": False,
            "print_html": False,
        }

        if match.group("kwargs"):
            with contextlib.suppress(json.JSONDecodeError):
                cmd["kwargs"] = json.loads(match.group("kwargs").strip(" \"'"))

        cmd["no_email"] = match.group("no_email") == "true"
        cmd["print_html"] = match.group("print_html") == "true"
        return cmd

    # --- Unknown ---
    return {"command": None}
