from __future__ import annotations

from datetime import date

from .dates import days_since

# Weekday prayer themes keyed by Python date.weekday() (Mon=0 .. Sun=6).
# Each value is (display label, ordered list of discrete prayer topics).
WEEKDAY_PRAYERS: dict[int, tuple[str, list[str]]] = {
    0: (
        "Monday",
        [
            "Personal sanctification",
            "Mortification of sin",
            "Growth in holiness",
            "Humility",
            "Self-control",
            "Illumination of Scripture",
            "Prayerfulness",
            "Dependence upon the Spirit",
        ],
    ),
    1: (
        "Tuesday",
        [
            "Marriage",
            "Children",
            "Family worship",
            "Homeschooling",
            "Discipline with gentleness",
            "Joyful obedience",
            "Household peace",
            "Protection from worldliness",
            "Raising covenant children in the fear of God",
        ],
    ),
    2: (
        "Wednesday (The visible church)",
        [
            "Elders",
            "Deacons",
            "Preaching",
            "Reverent worship",
            "Psalm singing",
            "Unity and purity",
            "Church discipline",
            "Confessional faithfulness",
            "Church plants",
            "Perseverance of the saints",
        ],
    ),
    3: (
        "Thursday",
        [
            "Gospel advance",
            "Missions",
            "Evangelism",
            "Hospitality",
            "Mercy ministry",
            "Boldness with neighbors and coworkers",
            "Revival grounded in truth",
            "Growth of Christ's kingdom among the nations",
        ],
    ),
    4: (
        "Friday",
        [
            "Civil authorities",
            "Justice",
            "Peace and order in society",
            "Protection of the weak and unborn",
            "Cultural wisdom",
            "Suffering saints",
            "The persecuted church",
            "Steadfast hope in Christ's return",
        ],
    ),
    5: (
        "Saturday",
        [
            "Thanksgiving and confession from the past week",
            "Preparation for the Lord's Day",
            "Physical and spiritual rest",
            "Reconciliation where needed",
            "Meditation on upcoming worship",
            "Consecration of the household for Sabbath delight",
        ],
    ),
    6: (
        "Lord's Day",
        [
            "Worship preparation and participation",
            "Resurrection joy",
            "Covenant renewal",
            "Sermon application",
            "Sacraments",
            "Fellowship",
            "Hospitality",
            "Thanksgiving for the ordinary means of grace",
        ],
    ),
}


def prayer_for(target: date, plan_start: date, count: int) -> tuple[str, list[str]]:
    """Return (label, selected topics) for ``target``.

    A window of ``count`` topics is selected from the weekday's list. The window
    advances by ``count`` each week (weeks measured from ``plan_start``), so the
    same weekday cycles through every topic over ceil(n / count) weeks. These are
    prayer *topics* to cover, never a written-out prayer.
    """
    label, topics = WEEKDAY_PRAYERS[target.weekday()]
    n = len(topics)
    if count >= n:
        return label, list(topics)
    week = days_since(plan_start, target) // 7  # floor division; safe if negative
    start = (week * count) % n                  # non-negative for positive n
    selected = [topics[(start + i) % n] for i in range(count)]
    return label, selected
