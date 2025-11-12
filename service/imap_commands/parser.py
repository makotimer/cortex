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

    # --- 1. Single word command (e.g., LIST) ---
    if re.fullmatch(r"\w+", line, re.IGNORECASE):
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
        cmd = {
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
