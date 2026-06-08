import datetime as dt

from modules.bible_plan.lib import prayer


def test_weekday_labels():
    # date.weekday(): Mon=0 .. Sun=6
    assert prayer.WEEKDAY_PRAYERS[6][0] == "Lord's Day"
    assert "visible church" in prayer.WEEKDAY_PRAYERS[2][0]
    monday = dt.date(2025, 9, 8)  # a Monday
    label, _ = prayer.prayer_for(monday, monday, 3)
    assert label == "Monday"


def test_subset_size_and_membership_when_count_lt_n():
    monday = dt.date(2025, 9, 8)  # Monday, 8 topics
    _, topics = prayer.prayer_for(monday, monday, 3)
    full = prayer.WEEKDAY_PRAYERS[0][1]
    assert len(topics) == 3
    assert all(t in full for t in topics)
    assert len(set(topics)) == 3  # no duplicates within a single week


def test_count_ge_n_returns_full_list():
    saturday = dt.date(2025, 9, 13)  # Saturday, 6 topics
    _, topics = prayer.prayer_for(saturday, saturday, 99)
    assert topics == prayer.WEEKDAY_PRAYERS[5][1]


def test_rotation_advances_each_week():
    plan_start = dt.date(2025, 9, 8)  # Monday
    wk0 = prayer.prayer_for(plan_start, plan_start, 3)[1]
    wk1 = prayer.prayer_for(plan_start + dt.timedelta(days=7), plan_start, 3)[1]
    assert wk0 != wk1


def test_rotation_covers_all_topics_over_cycle():
    # Monday n=8, count=3 -> ceil(8/3)=3 weeks should cover every topic.
    plan_start = dt.date(2025, 9, 8)
    seen: set[str] = set()
    for w in range(3):
        d = plan_start + dt.timedelta(days=7 * w)
        seen.update(prayer.prayer_for(d, plan_start, 3)[1])
    assert seen == set(prayer.WEEKDAY_PRAYERS[0][1])
