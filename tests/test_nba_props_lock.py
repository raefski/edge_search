from datetime import datetime, timedelta, timezone

from scripts.nba_props_lock import due_marks

TIP = datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc)
LATE = TIP + timedelta(hours=3)


def test_each_mark_fires_once_in_its_window():
    for m in (60, 30, 15, 10, 5):
        now = TIP - timedelta(minutes=m) + timedelta(seconds=30)
        assert due_marks([TIP, LATE], set(), now) == [TIP - timedelta(minutes=m)]
        assert due_marks([TIP, LATE], {(TIP - timedelta(minutes=m)).isoformat()}, now) == []


def test_nothing_between_marks_or_after_tip():
    assert due_marks([TIP], set(), TIP - timedelta(minutes=20)) == []
    assert due_marks([TIP], set(), TIP - timedelta(minutes=11, seconds=30)) == []
    assert due_marks([TIP], set(), TIP + timedelta(minutes=1)) == []
