"""College late scratches read off the prop board (edge/ncaab_scratch.py, NCAAB critique
cycle 11, 2026-11-27): a player whose props are pulled ALONE while his game-mates stay
quoted is cut to PULL_MULT; a whole game closing at tip is not a pull."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from edge import dfs_run_ncaab as RC
from edge import ncaab_scratch as S
from edge.ncaab_names import Rosters

T0 = datetime(2026, 11, 19, 18, 0, tzinfo=timezone.utc)
MATES = frozenset({"A One", "B Two", "C Three", "D Four"})


def _tl(*boards):
    return [(T0 + timedelta(minutes=15 * i), frozenset(b)) for i, b in enumerate(boards)]


def test_lone_pull_is_flagged_at_the_scan_he_vanished():
    tl = _tl(MATES | {"Boogie Fland"}, MATES | {"Boogie Fland"}, MATES, MATES)
    assert S.isolated_pull(tl, {"Boogie Fland"}, T0 + timedelta(hours=2)) == T0 + timedelta(minutes=30)


def test_not_yet_pulled_before_the_scan():
    tl = _tl(MATES | {"Boogie Fland"}, MATES)
    assert S.isolated_pull(tl, {"Boogie Fland"}, T0 + timedelta(minutes=10)) is None


def test_vanishing_with_a_game_mate_is_the_books_closing_the_game():
    tl = _tl(MATES | {"Boogie Fland"}, MATES - {"A One"})
    assert S.isolated_pull(tl, {"Boogie Fland"}, T0 + timedelta(hours=1)) is None


def test_a_blip_that_comes_back_is_not_a_pull():
    tl = _tl(MATES | {"Boogie Fland"}, MATES, MATES | {"Boogie Fland"})
    assert S.isolated_pull(tl, {"Boogie Fland"}, T0 + timedelta(hours=1)) is None


def test_a_thin_board_cannot_tell():
    two = frozenset({"A One", "B Two"})
    tl = _tl(two | {"Boogie Fland"}, two)
    assert S.isolated_pull(tl, {"Boogie Fland"}, T0 + timedelta(hours=1)) is None


def test_never_quoted_is_not_pulled():
    tl = _tl(MATES, MATES)
    assert S.isolated_pull(tl, {"Boogie Fland"}, T0 + timedelta(hours=1)) is None


def test_any_book_spelling_keeps_him_quoted():
    tl = _tl(MATES | {"Boogie Fland"}, MATES | {"Boogie Fland (UF)"})
    assert S.isolated_pull(tl, {"Boogie Fland", "Boogie Fland (UF)"}, T0 + timedelta(hours=1)) is None


def _salaries():
    names = [("Boogie Fland", "UF"), ("A One", "UF"), ("B Two", "UF"),
             ("C Three", "TCU"), ("D Four", "TCU"), ("Boogie Fland", "OTHER")]
    return {f"k{i}": {"name": n, "team": t, "salary": 5000 + i} for i, (n, t) in enumerate(names)}


def test_pulled_players_resolve_inside_the_game_only():
    sal = _salaries()
    tl = {"ev1": _tl(MATES | {"Boogie Fland"}, MATES)}
    got = S.pulled_players(tl, {"ev1": ("UF", "TCU")}, Rosters(sal), T0 + timedelta(hours=1))
    assert set(got) == {"k0"}                      # UF's Fland, never OTHER's namesake


class _Store:
    """The two things store_timelines reads: the scan table and scan_groups."""

    def __init__(self, scans: list[tuple[int, datetime, set]]):
        self.conn = sqlite3.connect(":memory:")
        self.conn.execute("CREATE TABLE scan (id INTEGER, profile TEXT, ok INTEGER, started_at TEXT)")
        self.groups = {}
        for sid, t, subs in scans:
            self.conn.execute("INSERT INTO scan VALUES (?,?,?,?)", (sid, "dfs_ncaab", 1, t.isoformat()))
            self.groups[sid] = {f"ev1|player_points|{s}|10.5" for s in subs} | {"ev1|totals||150.5"}

    def scan_groups(self, ids, sport_key):
        return {i: self.groups[i] for i in ids}


class _Client:
    profile = "dfs_ncaab"

    def __init__(self, store):
        self.store = store


def _pool():
    return [{"name": "Boogie Fland", "team": "UF", "salary": 5000, "source": "blend", "fp": 20.0,
             "status": None},
            {"name": "A One", "team": "UF", "salary": 5001, "source": "props", "fp": 30.0, "status": None}]


def test_board_cuts_a_pulled_non_props_row_and_says_so():
    now = T0 + timedelta(hours=1)
    store = _Store([(1, T0, MATES | {"Boogie Fland"}), (2, T0 + timedelta(minutes=15), MATES)])
    sal = _salaries()
    games = {"g1": {"home": "UF", "away": "TCU"}}
    pool = _pool()
    audit = RC.apply_props_pulled(pool, _Client(store), Rosters(sal), games, sal,
                                  {"ev1": {"k1", "k2"}}, now=now)
    assert pool[0]["fp"] == pytest.approx(20.0 * S.PULL_MULT)
    assert pool[0]["status"] == "PULL" and pool[0]["pulled_at"]
    assert pool[1]["fp"] == 30.0                    # a props row is quoted: untouched
    assert audit["flagged"] and audit["check"].startswith("store")


def test_snapshot_client_says_the_check_did_not_run():
    pool = _pool()
    audit = RC.apply_props_pulled(pool, object(), Rosters(_salaries()), {}, _salaries(), {})
    assert audit["check"].startswith("unavailable") and not audit["flagged"]
    assert pool[0]["fp"] == 20.0


def test_store_timelines_reads_player_markets_only():
    store = _Store([(1, T0, {"Boogie Fland"}), (2, T0 + timedelta(minutes=15), set())])
    tl = S.store_timelines(store, "dfs_ncaab", "basketball_ncaab", now=T0 + timedelta(hours=1))
    assert [subs for _t, subs in tl["ev1"]] == [frozenset({"Boogie Fland"})]


def test_lab_flags_never_raise():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import ncaab_scratch_lab
    assert ncaab_scratch_lab.flags_at_build([{"gid": "x"}]) == set()
