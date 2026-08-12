"""Topic assignment.

Free signal only, for now. Design §6 puts everything the feed does not label
through an LLM once per series, but cortex currently has **no route to any LLM**
(see cortex CLAUDE.md: every ``llm-proxy`` is a per-site sidecar and cortex joins
only ``mailnet`` and ``eventbus``). Design §11 open decision 1 anticipated this
and says to ship labels-only until it is resolved — nothing else depends on it.

The seam for the LLM pass is ``classify_series``: give it a callable and the
cache, and the rest of the module keeps working unchanged.
"""
from __future__ import annotations

from collections.abc import Callable

#: The closed vocabulary, mirrored from docs/intake-contract.md §4. A slug the
#: site does not know dead-letters the whole event, so anything not in this set
#: is dropped silently rather than sent.
TOPICS = {
    "science", "history", "arts", "music", "sports", "outdoors", "nature",
    "reading", "technology", "crafts", "community", "faith", "camp",
}

#: Feed labels that map to a topic outright. These are free and certain.
LABEL_TOPICS = {
    "SRP": "reading",
    "Community-Events": "community",
}


def from_labels(labels: list[str]) -> list[str]:
    """Topics derivable from the source's own labels. Never guesses."""
    out = {LABEL_TOPICS[label] for label in labels if label in LABEL_TOPICS}
    return sorted(out)


def validate(slugs: list[str]) -> list[str]:
    """Keep only slugs the site's vocabulary contains.

    Dropping is deliberate: an unrecognized topic would dead-letter the entire
    event, and a missing topic is strictly better than a wrong one.
    """
    return sorted({s for s in slugs if s in TOPICS})


def classify_series(
    series_uid: str,
    content_hash: str,
    labels: list[str],
    cache: dict[str, list[str]],
    llm: Callable[[str], list[str]] | None = None,
    text: str = "",
) -> list[str]:
    """Topics for one series, using labels first and the cache second.

    ``llm`` is the not-yet-available classifier. When it is None (today) or it
    raises, the series simply gets its label topics — failure never blocks
    publication, per design §6.
    """
    label_topics = from_labels(labels)
    if label_topics:
        return label_topics

    key = f"{series_uid}|{content_hash}"
    if key in cache:
        return validate(cache[key])

    if llm is None:
        return []

    try:
        topics = validate(llm(text))
    except Exception:
        return []

    cache[key] = topics
    return topics
