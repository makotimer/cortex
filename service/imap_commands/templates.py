# service/imap_commands/templates.py
import textwrap
from datetime import datetime

from apscheduler.job import Job


def list_html(jobs: list[Job], first_id: str | None = None) -> str:
    if not jobs:
        return "<p>No jobs currently scheduled.</p>"

    rows = []
    for job in jobs:
        trigger = job.trigger
        trigger_type = type(trigger).__name__.replace("Trigger", "").lower()

        if "CronTrigger" in type(trigger).__name__:
            minute = getattr(trigger, "_minute", "*")
            hour = getattr(trigger, "_hour", "*")
            day = getattr(trigger, "_day", "*")
            month = getattr(trigger, "_month", "*")
            dow = getattr(trigger, "_day_of_week", "*")
            trigger_str = f"{minute} {hour} {day} {month} {dow}"
        elif hasattr(trigger, "interval"):
            seconds = trigger.interval.total_seconds()
            if seconds % 3600 == 0:
                trigger_str = f"every {int(seconds // 3600)} hour(s)"
            elif seconds % 60 == 0:
                trigger_str = f"every {int(seconds // 60)} minute(s)"
            else:
                trigger_str = f"every {seconds} second(s)"
        elif hasattr(trigger, "run_date"):
            trigger_str = trigger.run_date.strftime("%Y-%m-%d %H:%M:%S") if trigger.run_date else "—"
        else:
            trigger_str = str(trigger)

        next_run = job.next_run_time
        next_run_str = next_run.strftime("%Y-%m-%d %H:%M:%S") if next_run else "—"

        module = job.kwargs.get("module", job.id)
        send_email = job.kwargs.get("send_email", False)
        email_flag = "Yes" if send_email else "No"

        rows.append(f"""
        <tr>
            <td><code>{job.id}</code></td>
            <td><code>{module}</code></td>
            <td>{trigger_type}</td>
            <td><code>{trigger_str}</code></td>
            <td>{next_run_str}</td>
            <td>{email_flag}</td>
        </tr>
        """)

    button_html = ""
    if first_id:
        mailto_link = (
            f"mailto:?subject=RUN%20MODULE%3D{first_id}"
            f"&body=RUN%20MODULE%3D{first_id}%0A"
            f"KWARGS%3D%7B%7D%0A"
            f"NO_EMAIL%3Dfalse%0A"
            f"PRINT_HTML%3Dfalse"
        )
        button_html = f"""
        <p>
            <a href="{mailto_link}"
               style="background:#0066cc;color:white;padding:10px 16px;text-decoration:none;border-radius:4px;font-weight:bold;">
               Run {first_id} Now
            </a>
        </p>
        """  # noqa: E501

    return textwrap.dedent(f"""
        <h2>Scheduled Jobs ({len(jobs)})</h2>
        <table border="1" cellpadding="8" cellspacing="0" style="font-family: monospace; border-collapse: collapse;">
            <thead>
                <tr style="background: #f0f0f0;">
                    <th>ID</th>
                    <th>Module</th>
                    <th>Type</th>
                    <th>Trigger</th>
                    <th>Next Run</th>
                    <th>Email</th>
                </tr>
            </thead>
            <tbody>
                {"".join(rows)}
            </tbody>
        </table>

        <hr style="margin: 20px 0; border: 1px solid #eee;">

        <h3>Run a Module Now</h3>
        <p>Click below to reply and run the <strong>first job</strong> instantly:</p>
        {button_html}

        <p>Or reply with:</p>
        <pre style="background:#f8f8f8;padding:10px;border:1px solid #ddd;">
        RUN MODULE=&lt;ID&gt;
        KWARGS={{}}
        NO_EMAIL=false
        PRINT_HTML=false
        </pre>

        <p><strong>Examples:</strong></p>
        <ul>
            <li><code>RUN MODULE=career-watch</code></li>
            <li><code>RUN MODULE=career-watch KWARGS={{"max_pages":5}}</code></li>
        </ul>

        <p><i>Generated at {datetime.now():%Y-%m-%d %H:%M:%S}</i></p>
    """).strip()  # noqa: E501


def career_report_html(body: str) -> str:
    return f"""
    <h3>Career Report - {datetime.now():%Y-%m-%d %H:%M}</h3>
    <pre style='font-family: monospace; white-space: pre-wrap;'>{body}</pre>
    <p><i>Generated via email command</i></p>
    """.strip()
