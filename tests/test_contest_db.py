"""scripts/contest_db.py: parsing, payouts, matching and idempotency, on
synthetic exports (no network, no dependence on which real exports exist)."""
from __future__ import annotations

import csv
import json
import sqlite3

import pytest

from scripts import contest_db as cdb

HEADER = ["Rank", "EntryId", "EntryName", "TimeRemaining", "Points", "Lineup", "",
          "Player", "Roster Position", "%Drafted", "FPTS"]


def write_export(path, entries, board, time_remaining="0"):
    """entries: [(rank, id, name, points, lineup)], board: [(player, slot, pct, fpts)]"""
    n = max(len(entries), len(board))
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.writer(fh)
        w.writerow(HEADER)
        for i in range(n):
            e = entries[i] if i < len(entries) else None
            b = board[i] if i < len(board) else None
            row = ([e[0], e[1], e[2], time_remaining, e[3], e[4]] if e else [""] * 6) + [""]
            row += [b[0], b[1], f"{b[2]}%", b[3]] if b else [""] * 4
            w.writerow(row)


# ------------------------------------------------------------------ parsing
def test_parse_entry_name():
    assert cdb.parse_entry_name("TNeidert (3/3)") == ("TNeidert", 3, 3)
    assert cdb.parse_entry_name("gorillabiscuit") == ("gorillabiscuit", None, None)


def test_parse_lineup_handles_slot_name_collisions_and_dst():
    slots = cdb.SLOTS["NFL"]
    known = {cdb.norm(x) for x in ("Jaguars", "Josh Allen", "Bijan Robinson", "Te Smith")}
    lu = cdb.parse_lineup("DST Jaguars  QB Josh Allen RB Bijan Robinson TE Te Smith", slots, known)
    assert lu == [("DST", "Jaguars"), ("QB", "Josh Allen"), ("RB", "Bijan Robinson"), ("TE", "Te Smith")]
    # NHL: a name token equal to a slot code ("C") is kept inside the name when the board knows it
    nhl = cdb.SLOTS["NHL"]
    known = {cdb.norm("Joe C Doe"), cdb.norm("Cale Makar")}
    assert cdb.parse_lineup("C Joe C Doe D Cale Makar", nhl, known) == [("C", "Joe C Doe"), ("D", "Cale Makar")]
    # showdown captain
    mma = cdb.SLOTS["MMA"]
    known = {cdb.norm("Raul Rosas Jr."), cdb.norm("Tina Black")}
    assert cdb.parse_lineup("CPT Raul Rosas Jr. F Tina Black", mma, known) == [
        ("CPT", "Raul Rosas Jr."), ("F", "Tina Black")]
    assert cdb.parse_lineup("", slots, set()) == []
    assert cdb.parse_lineup("XX Nobody", slots, set()) is None


def test_tie_payouts_split_the_places_they_share():
    tiers = [(1, 1, 100.0), (2, 2, 50.0), (3, 5, 10.0)]
    pay = cdb.tie_payouts([1, 2, 2, 4, 5, 5, 7], tiers)
    assert pay[1] == 100.0
    assert pay[2] == pytest.approx((50.0 + 10.0) / 2)      # places 2 and 3
    assert pay[4] == 10.0
    assert pay[5] == pytest.approx((10.0 + 0.0) / 2)       # places 5 and 6
    assert pay[7] == 0.0


# ------------------------------------------------------------------ the build
@pytest.fixture
def tiny(tmp_path):
    exp = tmp_path / "contest-standings-900000001.csv"
    entries = [(1, 11, "alice", 150.5, "C Aa One W Bb Two"),
               (2, 12, "bob (1/2)", 140.0, "C Aa One W Cc Three"),
               (2, 13, "bob (2/2)", 140.0, "C Aa One W Cc Three"),
               (4, 14, "gorillabiscuit", 90.0, "C Dd Four W Bb Two"),
               (5, 15, "ghost", 0, "")]
    board = [("Aa One", "C", 75.0, 30.0), ("Bb Two", "W", 25.0, 20.0), ("Bb Two", "UTIL", 25.0, 20.0),
             ("Cc Three", "W", 50.0, 25.0), ("Dd Four", "C", 25.0, 5.0)]
    write_export(exp, entries, board)
    meta = {"900000001": {"type": "cash", "sport": "NHL", "name": "NHL $1 Double Up", "field": 5,
                          "entry_fee": 1, "places_paid": 2, "prize_pool": 4.0, "draft_group": 999999901,
                          "slate": "2026-10-01 (gid 999999901)", "guaranteed": True}}
    mp = tmp_path / "meta.json"
    mp.write_text(json.dumps(meta))
    return {"db": tmp_path / "t.db", "files": [str(exp)], "meta": mp, "exp": exp}


def _build(t):
    return cdb.build(t["db"], files=t["files"], meta_path=t["meta"], quiet=True)


def test_build_contest_board_entries_and_features(tiny):
    s = _build(tiny)
    assert s["contests"] == {"new": 1}
    c = sqlite3.connect(str(tiny["db"]))
    live, final, n_ent = c.execute("SELECT live_entries, final, n_entrants FROM contest").fetchone()
    assert (live, final, n_ent) == (4, 1, 4)
    # ownership summed over roster slots
    assert c.execute("SELECT pct_drafted FROM player_result WHERE player_norm='bbtwo'").fetchone()[0] == 50.0
    f = {r[0]: r[1:] for r in c.execute(
        "SELECT entry_id, own_sum, dup_count, payout, cash, win, is_adam FROM entry_feature")}
    assert f[11][0] == pytest.approx(125.0)            # 75 + 50
    assert f[12][1] == 2 and f[13][1] == 2             # bob's two identical lineups
    # flat double-up $2 for places 1-2; bob's tie at rank 2 shares places 2-3 -> $1 each
    assert f[11][2] == pytest.approx(2.0) and f[12][2] == pytest.approx(1.0)
    assert f[14][2] == 0.0 and f[14][5] == 1           # Adam flagged, no cash
    assert f[15][2] is None                            # empty lineup: never played
    assert c.execute("SELECT source FROM payout").fetchone()[0] == "cash_flat"


def test_rebuild_is_idempotent_and_picks_up_changes(tiny):
    _build(tiny)
    c = sqlite3.connect(str(tiny["db"]))
    before = c.execute("SELECT COUNT(*) FROM entry_player").fetchone()[0]
    c.close()
    assert _build(tiny)["contests"] == {"skip": 1}
    c = sqlite3.connect(str(tiny["db"]))
    assert c.execute("SELECT COUNT(*) FROM entry_player").fetchone()[0] == before
    assert c.execute("SELECT COUNT(*) FROM contest").fetchone()[0] == 1
    c.close()
    with open(tiny["exp"], "a", encoding="utf-8") as fh:   # a re-download with one more row
        fh.write("6,16,carol,10,C Dd Four W Cc Three,,,,,\n")
    assert _build(tiny)["contests"] == {"updated": 1}
    c = sqlite3.connect(str(tiny["db"]))
    assert c.execute("SELECT COUNT(*) FROM entry").fetchone()[0] == 6


def test_unfinished_export_has_no_outcomes(tiny):
    write_export(tiny["exp"], [(1, 11, "alice", 50, "C Aa One W Bb Two")],
                 [("Aa One", "C", 100.0, 30.0), ("Bb Two", "W", 100.0, 20.0)], time_remaining="12.5")
    _build(tiny)
    c = sqlite3.connect(str(tiny["db"]))
    assert c.execute("SELECT final, unfinished_entries FROM contest").fetchone() == (0, 1)
    assert c.execute("SELECT payout, cash, win FROM entry_feature").fetchone() == (None, None, None)


def test_matching_is_by_draft_group_and_date_matches_are_checked(tiny):
    _build(tiny)
    c = sqlite3.connect(str(tiny["db"]))

    def add(gid, date, name, team, own):
        c.execute("INSERT INTO projection VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("NHL", gid, date, cdb.norm(name), name, team, None, "C", 5000, 10.0, None, None, None,
                   own, None, "ours", "live", "x", None, "{}"))
    # a log for ANOTHER draft group on the same night must not be used
    c.execute("DELETE FROM projection")
    add(999999902, "2026-10-01", "Aa One", "AAA", 50)
    m = cdb.match_contests(c)[900000001]
    assert m["method"] == "none" and not m["ok_points"]
    # the right draft group matches
    for nm in ("Aa One", "Bb Two", "Cc Three", "Dd Four"):
        add(999999901, "2026-10-01", nm, "AAA", 20)
    m = cdb.match_contests(c)[900000001]
    assert m["method"] == "draft_group" and m["ok_points"] and m["ok_own"]
    assert m["own_coverage"] == pytest.approx(1.0)


def test_date_matched_main_log_rejects_other_slates(tiny):
    meta = json.loads(tiny["meta"].read_text())
    meta["900000001"]["sport"] = "MLB"
    meta["900000001"]["type"] = "gpp"
    tiny["meta"].write_text(json.dumps(meta))
    _build(tiny)
    c = sqlite3.connect(str(tiny["db"]))
    c.execute("DELETE FROM projection")

    def add(name, team):
        c.execute("INSERT INTO projection VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  ("MLB", None, "2026-10-01", cdb.norm(name), name, team, None, "OF", 4000, 8.0, None, None,
                   None, 10.0, None, "ours", "main", "x", None, "{}"))
    # the main log covers the board but also carries two teams nobody drafted: a bigger slate
    for nm in ("Aa One", "Bb Two", "Cc Three", "Dd Four"):
        add(nm, "AAA")
    add("Xx Five", "BBB")
    add("Yy Six", "CCC")
    m = cdb.match_contests(c)[900000001]
    assert m["method"] == "date(main log)"
    assert m["ok_points"] == 1 and m["ok_own"] == 0      # points yes, ownership no
    # a log that covers too little of the board is rejected outright
    c.execute("DELETE FROM projection")
    add("Dd Four", "AAA")
    m = cdb.match_contests(c)[900000001]
    assert m["ok_points"] == 0 and "rejected" in m["note"]
