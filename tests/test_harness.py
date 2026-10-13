"""The harness (harness/): catalog coverage, the run ledger, test selection,
the monitor's change detection and the NHL late swap."""
from __future__ import annotations

import json
import sqlite3
import sys

import numpy as np

from harness import late_swap, monitor, registry, runner, verify
from harness.registry import ROOT


# ── catalog ─────────────────────────────────────────────────────────────────
def test_every_script_module_and_page_has_a_role():
    missing = [r["path"] for r in registry.catalog() if r["role"] not in registry.ROLES]
    assert not missing, f"add a JOB or a RULE in harness/registry.py for: {missing}"


def test_jobs_point_at_real_scripts_and_timers():
    for j in registry.JOBS.values():
        assert j.role in registry.ROLES
        if len(j.argv) > 1 and j.argv[1].endswith(".py"):
            assert (ROOT / j.argv[1]).exists(), j.name
        if j.timer:
            assert (ROOT / "deploy" / j.timer).exists(), j.name


# ── runner ──────────────────────────────────────────────────────────────────
def test_run_records_exit_code_and_tail(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "STATE", tmp_path)
    monkeypatch.setattr(runner, "LEDGER", tmp_path / "runs.jsonl")
    monkeypatch.setitem(runner.JOBS, "t-ok", registry.Job("t-ok", "monitor",
                                                          (sys.executable, "-c", "print('hello')")))
    monkeypatch.setitem(runner.JOBS, "t-bad", registry.Job("t-bad", "monitor",
                                                           (sys.executable, "-c", "import sys; sys.exit(3)")))
    assert runner.run("t-ok", quiet=True) == 0
    assert runner.run("t-bad", quiet=True) == 3
    assert runner.run("no-such-job", quiet=True) == 127
    led = runner.runs()
    assert [(e["job"], e["rc"]) for e in led] == [("t-ok", 0), ("t-bad", 3)]
    assert led[0]["tail"] == ["hello"] and (ROOT / led[0]["log"]).exists()


# ── verify ──────────────────────────────────────────────────────────────────
def test_tests_are_chosen_by_what_they_import():
    t = verify.tests_for(["edge/dfs_leverage.py"])
    assert "tests/test_dfs_leverage.py" in t and "tests/test_dfs_leverage_sports.py" in t
    assert "tests/test_pickem.py" not in t
    assert set(verify.PAGE_TESTS) <= set(verify.tests_for(["pages/8_🏒_NHL_DFS.py"]))
    assert "tests/test_harness_guard.py" in verify.tests_for([".claude/hooks/guard_bash.py"])
    assert verify.tests_for(["tests/test_names.py"]) == ["tests/test_names.py"]


def test_compile_errors_are_caught(tmp_path, monkeypatch):
    monkeypatch.setattr(verify, "ROOT", tmp_path)
    (tmp_path / "bad.py").write_text("def f(:\n")
    (tmp_path / "ok.py").write_text("x = 1\n")
    assert verify.compile_errors(["ok.py"]) == []
    assert verify.compile_errors(["bad.py"])[0].startswith("bad.py")
    assert verify.fingerprint(["ok.py"]) == verify.fingerprint(["ok.py"])


# ── monitor ─────────────────────────────────────────────────────────────────
def test_goalie_changes_alert_once_per_name_change():
    seen = {"DAL": {"goalie": "Jake Oettinger", "status": "Likely"},
            "ANA": {"goalie": "Lukas Dostal", "status": None}}
    now = {"DAL": {"goalie": "Casey DeSmith", "status": "Confirmed"},
           "ANA": {"goalie": "Lukas Dostal", "status": "Confirmed"},
           "BOS": {"goalie": "Jeremy Swayman", "status": None}}          # first sighting
    a = monitor.goalie_changes(now, seen)
    assert [(x["team"], x["priority"]) for x in a] == [("ANA", "low"), ("DAL", "high")]
    assert "DeSmith" in a[1]["title"] and "late-swap" in a[1]["body"]


def test_consensus_is_the_median_over_books_and_devigs_the_moneyline():
    rows = [("e", "totals", 6.5, "over", "dk", 1.9), ("e", "totals", 6.5, "under", "dk", 1.9),
            ("e", "totals", 6.0, "over", "fd", 1.8), ("e", "totals", 5.5, "over", "mgm", 1.8),
            ("e", "h2h", None, "home", "dk", 1.8), ("e", "h2h", None, "away", "dk", 2.1)]
    c = monitor.consensus(rows)
    assert c[("e", "totals")] == 6.0
    ph, pa = 1 / 1.8, 1 / 2.1
    assert abs(c[("e", "h2h")] - ph / (ph + pa)) < 1e-12


def test_line_moves_need_the_sport_threshold(tmp_path, monkeypatch):
    db = tmp_path / "odds.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE scan (id INTEGER PRIMARY KEY, profile TEXT, ok INTEGER);
        CREATE TABLE event (event_id TEXT, sport_key TEXT, home_team TEXT, away_team TEXT,
                            commence_time TEXT);
        CREATE TABLE quote (scan_id INT, event_id TEXT, market TEXT, subject TEXT, point REAL,
                            side TEXT, book TEXT, decimal REAL);""")
    from datetime import datetime, timedelta, timezone
    start = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat()
    con.execute("INSERT INTO event VALUES ('g', 'icehockey_nhl', 'DAL', 'ANA', ?)", (start,))

    def scan(sid, total):
        con.execute("INSERT INTO scan VALUES (?, 'dfs_nhl', 1)", (sid,))
        for book in ("dk", "fd", "mgm"):
            con.execute("INSERT INTO quote VALUES (?, 'g', 'totals', NULL, ?, 'over', ?, 1.9)",
                        (sid, total, book))
        con.commit()

    seen: dict = {}
    scan(1, 6.0)
    assert monitor.line_moves(db, 12, seen) == []            # baseline
    scan(2, 6.25)
    assert monitor.line_moves(db, 12, seen) == []            # under NHL's 0.5
    scan(3, 6.5)
    assert monitor.line_moves(db, 12, seen) == []            # 3h out: tracked, not pushed
    assert seen["g|totals"] == 6.5
    con.execute("UPDATE event SET commence_time = ?",
                ((datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),))
    con.commit()
    scan(4, 7.0)
    moves = monitor.line_moves(db, 12, seen)
    assert len(moves) == 1 and "6.5 → 7" in moves[0]["title"]   # inside 15 min: pushed
    assert monitor.line_moves(db, 12, seen) == []            # alerted once


# ── late swap ───────────────────────────────────────────────────────────────
def _nhl():
    from edge import dfs_opt_nhl as opt
    from tests.test_dfs_nhl import _slate
    pool, sim = _slate()
    lu = opt.optimize(pool, sim, mode="gpp", iters=15, seed=1)
    return pool, sim, [p["name"] for p in lu["lineup"]]


def test_a_benched_goalie_is_replaced_by_a_starter_and_nothing_else_moves():
    from edge import dfs_opt_nhl as opt
    pool, sim, names = _nhl()
    goalie = next(p for p in pool if p["name"] in names and p["pos"] == "G")
    board = [p for p in pool if p["name"] != goalie["name"]]           # he is not starting
    keep = [i for i, p in enumerate(pool) if p["name"] != goalie["name"]]
    r = late_swap.swap(board, sim[:, keep], names, set(),
                       dk_info={late_swap._norm(goalie["name"]): {"salary": goalie["salary"],
                                                                 "pos": "G", "team": goalie["team"]}})
    assert r["out"] == [goalie["name"]] and len(r["swaps"]) == 1
    new = r["swaps"][0][1]
    assert next(p for p in board if p["name"] == new)["pos"] == "G"
    assert opt._valid(board, [board.index(p) for p in r["lineup"]])
    assert r["salary"] <= 50_000


def test_started_games_lock_and_strand():
    pool, sim, names = _nhl()
    skater = next(p for p in pool if p["name"] in names and p["pos"] != "G")
    board = [p for p in pool if p["name"] != skater["name"]]
    keep = [i for i, p in enumerate(pool) if p["name"] != skater["name"]]
    r = late_swap.swap(board, sim[:, keep], names, {skater["team"]},
                       dk_info={late_swap._norm(skater["name"]): {"salary": skater["salary"],
                                                                 "pos": skater["pos"],
                                                                 "team": skater["team"]}})
    assert r["stuck"] == [skater["name"]] and not r["swaps"]
    assert all(p["team"] == skater["team"] for p in board
               if p["name"] in r["locked"])


def test_swap_chains_collapse():
    assert late_swap._chain([("A", "B", "out"), ("B", "C", "better"), ("D", "E", "better")]) == \
        [("A", "C", "out"), ("D", "E", "better")]
