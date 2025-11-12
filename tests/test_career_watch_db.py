# tests/test_career_watch_db.py
from modules.career_watch.lib.db import count_rows, filter_new, init_db, reset_db
from modules.career_watch.lib.models import Posting


def test_db_dedupe(tmp_path):
    dbp = tmp_path / "cw2.db"
    reset_db(str(dbp))
    init_db(str(dbp))

    person = "The Archivist"
    posts = [
        Posting(source="s1", person_env=person, title="T1", url="U1"),
        Posting(source="s1", person_env=person, title="T2", url="U2"),
    ]
    new1 = filter_new(str(dbp), person, posts)
    assert len(new1) == 2
    assert count_rows(str(dbp)) == 2

    # Same again → no new
    new2 = filter_new(str(dbp), person, posts)
    assert len(new2) == 0
    assert count_rows(str(dbp)) == 2
