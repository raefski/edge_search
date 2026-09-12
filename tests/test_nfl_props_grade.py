"""scripts/nfl_props_grade.py -- the props arm, rebuilt forwards.

The happy path cannot run yet: nflverse had not published player_stats_2026
as of 2026-09-12, so there are no actuals to join to. That is precisely why
these tests exist -- the parts that CAN be wrong today are the scan selection,
the actuals filter, and what the script does when there is nothing to grade.

The last one matters most. "No actuals yet" is a normal, expected state that
will persist for weeks, and it must read as waiting rather than as failure --
a script that exits non-zero every Sunday until nflverse catches up is one
that gets ignored on the Sunday it finally has something to say.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest

from scripts import nfl_props_grade as pg


def _store(tmp_path, scans):
    """A minimal store holding only the `scan` rows the selector reads."""
    db = tmp_path / "odds.db"
    con = sqlite3.connect(db)
    con.execute("create table scan (id integer primary key, profile text, "
                "started_at text, finished_at text, ok integer)")
    con.executemany("insert into scan values (?,?,?,?,?)", scans)
    con.commit()
    con.close()
    return db


def test_the_scan_selector_respects_the_time_bound(tmp_path):
    db = _store(tmp_path, [
        (1, "dfs_nfl", "", "2026-09-13T14:00:00+00:00", 1),
        (2, "dfs_nfl", "", "2026-09-13T16:00:00+00:00", 1),   # after kickoff
        (3, "dfs_nfl", "", "2026-09-13T15:00:00+00:00", 1),
    ])
    # A lineup locks with the information available BEFORE kickoff; grading it
    # against a scan taken after is grading it against the answer.
    assert pg.last_scan_before(db, "2026-09-13T15:30:00+00:00") == 3
    assert pg.last_scan_before(db, None) == 2


def test_an_unfinished_scan_is_never_used(tmp_path):
    """ok=0 means finish_scan never committed it -- a partial board looks
    exactly like a thin slate, which is the whole reason that flag exists."""
    db = _store(tmp_path, [
        (1, "dfs_nfl", "", "2026-09-13T14:00:00+00:00", 1),
        (2, "dfs_nfl", "", "2026-09-13T15:00:00+00:00", 0),
    ])
    assert pg.last_scan_before(db, None) == 1


def test_another_profiles_scan_is_never_used(tmp_path):
    """pickem_nfl collects the same league with props OFF. Grading a DFS
    projection off it would silently find no player markets at all."""
    db = _store(tmp_path, [
        (1, "dfs_nfl", "", "2026-09-13T14:00:00+00:00", 1),
        (2, "pickem_nfl", "", "2026-09-13T15:00:00+00:00", 1),
    ])
    assert pg.last_scan_before(db, None) == 1


def test_missing_actuals_reads_as_waiting_not_as_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(pg, "GT", tmp_path)
    assert pg.load_actuals(2026, 1) == {}


def test_actuals_are_filtered_to_regular_season_week_and_skill_positions(tmp_path, monkeypatch):
    rows = [
        {"season_type": "REG", "week": "1", "position": "WR",
         "player_display_name": "Puka Nacua"},
        {"season_type": "POST", "week": "1", "position": "WR",
         "player_display_name": "Playoff Guy"},
        {"season_type": "REG", "week": "2", "position": "WR",
         "player_display_name": "Next Week"},
        {"season_type": "REG", "week": "1", "position": "CB",
         "player_display_name": "A Corner"},
    ]
    (tmp_path / "player_week_2026.json").write_text(json.dumps(rows))
    monkeypatch.setattr(pg, "GT", tmp_path)

    got = pg.load_actuals(2026, 1)

    from edge.names import norm
    assert list(got) == [norm("Puka Nacua")], (
        "POST, other weeks, and non-skill positions must all be excluded")


def test_names_join_through_the_shared_normaliser(tmp_path, monkeypatch):
    """The join bug that cost 4pp of match rate in the original ingest, twice,
    in two sports -- diacritics and generational suffixes. edge/names.py exists
    because of it and this path must use it, not its own str.lower()."""
    (tmp_path / "player_week_2026.json").write_text(json.dumps([
        {"season_type": "REG", "week": "1", "position": "RB",
         "player_display_name": "Travis Etienne Jr."}]))
    monkeypatch.setattr(pg, "GT", tmp_path)

    got = pg.load_actuals(2026, 1)

    from edge.names import norm
    assert norm("Travis Etienne") in got or norm("Travis Etienne Jr.") in got
    assert got, "the normaliser dropped a real player"


def test_it_is_scored_by_the_same_metrics_as_the_skill_backtest():
    """The comparison is only honest if both sides are measured identically.
    Importing the function is what guarantees that, rather than a second copy
    that agrees today."""
    import inspect
    from scripts import nfl_skill_backtest

    assert pg.metrics is nfl_skill_backtest.metrics
    src = inspect.getsource(pg)
    assert "def metrics" not in src, "the grader has grown its own metrics"
