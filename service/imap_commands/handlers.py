# service/imap_commands/handlers.py
import logging
import re
import subprocess
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from apscheduler.schedulers.background import BackgroundScheduler

from service import runner
from service.imap_commands.parser import parse_command_line
from service.imap_commands.templates import list_html
from service.scheduler import _add_job, _make_job_spec, _resolve_timezone

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Helper: strip HTML → plain text
# --------------------------------------------------------------------------- #
class _Stripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, d: str):
        self.parts.append(d)

    def get(self) -> str:
        return "".join(self.parts)


def extract_text_from_email(msg) -> str:
    """Prefer first non-empty text/plain; fallback to longest stripped HTML."""
    plain = None
    html_parts = []

    for part in msg.walk() if msg.is_multipart() else [msg]:
        ctype = (part.get_content_type() or "").lower()
        disp = (part.get("Content-Disposition") or "").lower()
        if "attachment" in disp:
            continue
        if ctype == "text/plain":
            text = part.get_content().strip()
            if text and plain is None:
                plain = text
            continue
        if ctype == "text/html":
            html = part.get_content()
            if html:
                html_parts.append(html)

    if plain:
        return plain
    if html_parts:
        html = max(html_parts, key=len)
        s = _Stripper()
        s.feed(html)
        return s.get().strip()
    return ""


def handle_command(
    raw_email: bytes,
    cfg: dict[str, Any],
    scheduler: BackgroundScheduler | None,
    from_addr: str | None = None,
) -> tuple[str, str, str]:
    from email import message_from_bytes
    from email.policy import default

    """
    Process incoming email and return (reply_to, subject, html).
    NEVER sends email — caller handles it.
    reply_to = to_addr or sender (from ENVELOPE or From header)
    """
    msg = message_from_bytes(raw_email, policy=default)

    # Use passed from_addr; fall back to parsing if missing (defensive)
    sender = from_addr or msg["From"]
    subject = msg["Subject"] or "(no subject)"

    text = extract_text_from_email(msg)
    lines = [line.strip() for line in text.splitlines() if line.strip()]

    # Find command line
    command_line = subject
    if not any(word in command_line.upper() for word in ("RUN", "LIST", "CAREER", "REPORT")):
        for line in lines:
            if any(word in line.upper() for word in ("RUN", "LIST", "CAREER", "REPORT")):
                command_line = line
                break

    logger.info("Command from %s: %s -- %r", sender, command_line.strip(), lines)
    cmd = parse_command_line(command_line)

    to_addr = None

    # ===================================================================
    # LIST
    # ===================================================================
    if cmd["command"] == "LIST":
        jobs = scheduler.get_jobs()
        first_id = jobs[0].id if jobs else None
        subj = "Scheduled Jobs"
        html = list_html(jobs, first_id)

    # ===================================================================
    # CAREER REPORT
    # ===================================================================
    elif cmd["command"] == "CAREER REPORT":
        logger.info("CAREER REPORT command from %s", sender)
        to_addr, subj, html = _handle_career_report()

    # ===================================================================
    # RUN MODULE=...
    # ===================================================================
    elif cmd["command"] == "RUN":
        module_id = cmd["module_id"]
        if not module_id:
            subj = "Command Error"
            html = "<p>RUN requires MODULE=...</p>"
        else:
            kwargs = cmd["kwargs"]
            no_email = cmd["no_email"]
            print_html = cmd["print_html"]
            to_addr, subj, html = _handle_run(module_id, kwargs, no_email, print_html, cfg, scheduler)

    # ===================================================================
    # UNKNOWN
    # ===================================================================
    else:
        subj = "Unknown Command"
        html = f"<p>Did not understand:</p><pre>{command_line.strip()}</pre>"

    return to_addr or sender, subj, html


def _handle_career_report() -> tuple[str, str, str]:
    script_path = Path("/app/scripts/career_check.py")
    if not script_path.exists():
        return "", "Career Report - Error", "Error: career_check.py not found."

    try:
        result = subprocess.run(
            ["python", str(script_path)],
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            output = f"Script failed (exit {result.returncode})\n\n{result.stderr.strip()}\n\n{output}"
        if not output:
            output = "No new postings found."
        return "", "Career Report", output
    except subprocess.TimeoutExpired:
        return "", "Career Report - Timeout", "Error: Timed out after 120s"
    except Exception as e:
        return "", "Career Report - Error", f"Exception: {e}"


def _handle_run(
    module_id: str,
    kwargs: dict,
    no_email: bool,
    print_html: bool,
    cfg: dict[str, Any],
    scheduler: BackgroundScheduler | None,
) -> tuple[str, str, str]:
    job_cfg = next((j for j in cfg.get("jobs", []) if j.get("id") == module_id), None)

    # Merge config defaults with any user overrides
    final_kwargs = {
        **job_cfg.get("kwargs", {}),
        **kwargs,
    }

    send_email = job_cfg.get("send_email", not no_email)

    # Use the same recipient lists the scheduled run would use
    email_to = job_cfg.get("email_to") or []
    email_cc = job_cfg.get("email_cc") or []
    email_bcc = job_cfg.get("email_bcc") or []

    # ------------------------------------------------------------------ #
    # Skip next scheduled run if within 6 hours
    # ------------------------------------------------------------------ #
    live_job = scheduler.get_job(module_id) if scheduler else None
    if live_job and live_job.next_run_time:
        now = datetime.now(live_job.next_run_time.tzinfo)
        if live_job.next_run_time < now + timedelta(hours=6):
            next_fire = live_job.next_run_time
            live_job.remove()
            logger.info("Removed job %s to skip next run at %s", module_id, next_fire)

            resume_time = next_fire + timedelta(minutes=1)
            job_defaults = {"coalesce": True, "max_instances": 1}
            tz = _resolve_timezone(cfg)

            def delayed_re_add():
                spec = _make_job_spec(job_cfg, default_job_defaults=job_defaults, tz=tz)
                _add_job(scheduler, spec)
                logger.info("Re-added job %s after skip", module_id)

            scheduler.add_job(
                delayed_re_add,
                "date",
                run_date=resume_time,
                id=f"resume_{module_id}_{next_fire.isoformat()}",
                replace_existing=True,
            )

    # ------------------------------------------------------------------ #
    # 4. Execute via runner (identical to scheduler path)
    # ------------------------------------------------------------------ #

    try:
        _, run_id = runner.run_module_once(
            module=job_cfg["module"],
            kwargs=final_kwargs,
            email_to=email_to or None,
            cc=email_cc or None,
            bcc=email_bcc or None,
            subject=job_cfg.get("subject"),
            send_email=send_email,
            trigger_type="command",
            timeout_sec=job_cfg.get("timeout_sec"),
        )
        if not send_email:
            logger.info("Job %s finished silently (send_email=False)", module_id)
            return None, None, None  # no reply at all

        # Below result_html could be included in this email confirmation, but would be redundant
        # since the job itself will send the same email content
        # result_html = html_out or "<i>(no HTML returned)</i>"
        reply_html = f"<p>Job <code>{module_id}</code> executed (run-id <code>{run_id}</code>).</p>"
        reply_subject = f"Result: {job_cfg.get('summary', module_id)}"
    except Exception as exc:
        logger.exception("Command-run failed for %s", module_id)
        reply_html = f"""
        <p><b>Execution failed:</b></p>
        <pre>{exc}</pre>
        """
        reply_subject = f"Result: {module_id}"

    return email_to, reply_subject, reply_html
