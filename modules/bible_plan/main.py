from __future__ import annotations

from datetime import datetime
from typing import Any

from .lib import (
    assemble_email_html,
    commentary_url,
    days_since,
    generate_commentary,
    load,
    load_plan,
    log,
    nkjv_link,
    resolve_date,
)


def run(
    *,
    for_date: str | None = None,
    force_index: int | None = None,
    commentary_model_env: str = "OPENAI_MODEL_BIBLE",
    commentary_temp_env: str = "OPENAI_TEMP_BIBLE",
) -> str | None | tuple[str, dict[str, Any]]:
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
    prev = plan[(idx - 1) % len(plan)]

    reading_link = nkjv_link(item.book, item.chapter)
    calvin = commentary_url("calvin", item.book, item.chapter, probe=not cfg.skip_probe)
    mh = commentary_url("mhc", item.book, item.chapter, probe=not cfg.skip_probe)

    reflection = generate_commentary(
        book=item.book,
        chapter=item.chapter,
        prev_book=prev.book,
        prev_chapter=prev.chapter,
        calvin_url=calvin,
        mh_url=mh,
        model_env=commentary_model_env,
        temp_env=commentary_temp_env,
        enable=cfg.enable_llm,
    )

    html = assemble_email_html(reading_link, calvin, mh, reflection)
    meta = {
        "subject": f"Daily Reading {item.book} {item.chapter} - {target!s}",
        "message": f"{item.book} {item.chapter}",
        "plan_start": cfg.plan_start,
        "idx": idx,
        "for_date": str(target),
        "links": {
            "youversion": reading_link,  # already a full <a>; your runner just logs meta
            "calvin": calvin,
            "matthew_henry": mh,
        },
        "llm": bool(reflection),
        "skip_probe": cfg.skip_probe,
    }
    return html, meta
