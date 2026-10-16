"""edge/odds/archive.py + OddsStore.archive_old: old scans move to the archive
as runs of unchanged prices, and every reader gets the same rows back."""
from __future__ import annotations

from datetime import timedelta

import pytest

from edge.odds import archive
from edge.odds.store import EventRow, OddsStore, QuoteRow, _iso, utcnow

EV = EventRow("ev1", "basketball_nba", "NBA", "2026-09-10T23:00:00+00:00", "BOS", "NYK")


def q(book, decimal, cap, side="over", subject="Jayson Tatum", point=24.5):
    gk = f"ev1|player_points|{subject}|{point}"
    return QuoteRow(gk, "ev1", "basketball_nba", "player_points", subject, point, side, book,
                    decimal, cap)


def scans(store):
    """Four dfs_nba scans 30 days ago: DK's over moves at scan 3; FanDuel misses
    scan 3 and comes back; one quote is new at scan 4. Plus an arb scan between."""
    t0 = utcnow() - timedelta(days=30)
    plan = [
        [q("draftkings", 1.91, "a1"), q("fanduel", 1.87, "b1"), q("draftkings", 1.91, "c1", "under")],
        [q("draftkings", 1.91, "a2"), q("fanduel", 1.87, "b2"), q("draftkings", 1.91, "c2", "under")],
        [q("draftkings", 1.80, "a3"), q("draftkings", 2.05, "c3", "under")],
        [q("draftkings", 1.80, "a4"), q("fanduel", 1.87, "b4"), q("draftkings", 2.05, "c4", "under"),
         q("fanduel", 1.95, "d4", subject="Jaylen Brown", point=20.5)],
    ]
    ids = []
    for k, quotes in enumerate(plan):
        if k == 2:                                   # another profile in between
            other = store.begin_scan("arb")
            store.write_events([EV])
            store.write_quotes(other, [q("fanatics", 1.5, "z")])
            store.finish_scan(other)
            store.conn.execute("UPDATE scan SET started_at=? WHERE id=?",
                               (_iso(t0 + timedelta(minutes=k)), other))
        sid = store.begin_scan("dfs_nba")
        store.write_events([EV])
        store.write_quotes(sid, quotes)
        store.finish_scan(sid)
        store.conn.execute("UPDATE scan SET started_at=? WHERE id=?",
                           (_iso(t0 + timedelta(minutes=k)), sid))
        ids.append(sid)
    return ids


@pytest.fixture
def store(tmp_path):
    s = OddsStore(tmp_path / "odds.db")
    yield s
    s.close()


def _rows(rows):
    return sorted((r["book"], r["side"], r["subject"], r["decimal"], r["captured_at"]) for r in rows)


def test_archived_scans_rebuild_exactly_and_the_hot_rows_go(store, tmp_path):
    ids = scans(store)
    before = {sid: _rows(store.quotes(sid)) for sid in ids}
    (tmp_path / "arch").mkdir()
    done = store.archive_old(hot_days=14, root=tmp_path / "arch", verify_sample=0, log=lambda m: None)
    assert done["scans"] == 5 and done["quotes"] == 13
    assert done["spans"] < done["quotes"]                         # runs, not rows
    assert store.conn.execute("SELECT COUNT(*) FROM quote").fetchone()[0] == 0
    for sid in ids:
        got = _rows(store.quotes(sid))
        # every column identical; captured_at exact at each run's first and last scan
        assert [r[:4] for r in got] == [r[:4] for r in before[sid]]
    # DK over held at 1.91 for scans 1-2: scan 1 and 2 keep their own times
    assert {r[4] for r in _rows(store.quotes(ids[0]))} == {"a1", "b1", "c1"}
    assert {r[4] for r in _rows(store.quotes(ids[1]))} == {"a2", "b2", "c2"}
    # FanDuel missed scan 3, so its run broke and scan 3 has no FanDuel row
    assert not [r for r in store.quotes(ids[2]) if r["book"] == "fanduel"]
    assert [e["event_id"] for e in store.events(ids[3])] == ["ev1"]
    assert store.scan(ids[0])["ok"] == 1                           # scan rows stay hot


def test_history_spans_the_archive_and_the_hot_store(store, tmp_path):
    ids = scans(store)
    (tmp_path / "arch").mkdir()
    store.archive_old(hot_days=14, root=tmp_path / "arch", verify_sample=0, log=lambda m: None)
    fresh = store.begin_scan("dfs_nba")                            # a new hot scan
    store.write_events([EV])
    store.write_quotes(fresh, [q("draftkings", 1.70, "a5")])
    store.finish_scan(fresh)
    gk = "ev1|player_points|Jayson Tatum|24.5"
    hist = store.history(gk, side="over", book="draftkings")
    assert [r["decimal"] for r in hist] == [1.91, 1.91, 1.80, 1.80, 1.70]
    first, last = store.opening_and_latest(gk, "over", "draftkings")
    assert first["decimal"] == 1.91 and last["decimal"] == 1.70


def test_a_wrong_count_stops_the_move_and_keeps_the_hot_rows(store, tmp_path, monkeypatch):
    ids = scans(store)
    (tmp_path / "arch").mkdir()
    monkeypatch.setattr(archive, "verify_counts", lambda hot, i, p: [i[0]])
    with pytest.raises(RuntimeError, match="hot rows kept"):
        store.archive_old(hot_days=14, root=tmp_path / "arch", log=lambda m: None)
    assert store.conn.execute("SELECT COUNT(*) FROM quote").fetchone()[0] == 13


def test_recent_scans_stay_hot_and_a_missing_drive_is_a_skip(store, tmp_path):
    sid = store.begin_scan("dfs_nba")
    store.write_events([EV])
    store.write_quotes(sid, [q("draftkings", 1.9, "x")])
    store.finish_scan(sid)
    assert "skipped" in store.archive_old(root=tmp_path / "not-mounted")
    (tmp_path / "arch").mkdir()
    assert store.archive_old(root=tmp_path / "arch", log=lambda m: None)["scans"] == 0


def test_iter_quotes_reads_hot_and_archived_alike(store, tmp_path):
    ids = scans(store)
    full = sorted(_rows(store.iter_quotes("basketball_nba", ["player_points"])))
    (tmp_path / "arch").mkdir()
    store.archive_old(hot_days=14, root=tmp_path / "arch", verify_sample=0, log=lambda m: None)
    after = sorted(_rows(store.iter_quotes("basketball_nba", ["player_points"])))
    assert [r[:4] for r in after] == [r[:4] for r in full] and len(full) == 13


def test_prune_is_retired(store):
    with pytest.raises(RuntimeError, match="archive_old"):
        store.prune(30)
