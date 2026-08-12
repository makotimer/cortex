from __future__ import annotations

from datetime import datetime
from typing import Any

from .lib import (
    assemble_email_html,
    days_since,
    load,
    load_plan,
    log,
    prayer_for,
    resolve_date,
)

STUDY_URL = "https://study.coviecraft.dev"


def run(
    *,
    for_date: str | None = None,
    force_index: int | None = None,
) -> str | tuple[str, dict[str, Any]] | None:
    cfg = load()
    start = datetime.strptime(cfg.plan_start, "%Y-%m-%d").date()
    target = resolve_date(for_date, cfg.tz_name)

    plan = load_plan()

    if force_index is not None:
        idx = int(force_index)
    else:
        delta = days_since(start, target)
        if delta < 0:
            log.activity({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "source": "modules.bible_plan",
                "event": "no_output_before_start",
                "for_date": str(target),
                "start_date": cfg.plan_start,
            })
            return None
        idx = delta

    item = plan[idx % len(plan)]
    prayer_title, prayer_topics = prayer_for(target, start, cfg.prayer_count)

    html = assemble_email_html(STUDY_URL, prayer_title, prayer_topics)
    meta = {
        "subject": f"Daily Reading {item.book} {item.chapter} - {target!s}",
        "message": f"{item.book} {item.chapter}",
        "plan_start": cfg.plan_start,
        "idx": idx,
        "for_date": str(target),
        "prayer_day": prayer_title,
        "prayer_topics": prayer_topics,
    }
    return html, meta
