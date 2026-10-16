"""edge/nba_onoff.py (with/without-teammate splits), its backtest, and how
the NBA board applies it -- on synthetic box scores with a planted effect."""
from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

from edge import dfs, nba_onoff  # noqa: E402


def _season(boost: float, n_games: int = 70, p_out: float = 0.3, seed: int = 0):
    """Two teams playing each other repeatedly. BOS's star sits a random
    p_out of games; when he does, BOS's 'Backup' scores `boost` more DK points
    (points only, so the DK total moves by exactly boost)."""
    rng = random.Random(seed)
    rows, day = [], date(2024, 10, 22)
    for g in range(n_games):
        day += timedelta(days=2)
        star_out = rng.random() < p_out
        for team in ("BOS", "NYK"):
            for k in range(8):
                name = f"{team} P{k}" if k else f"{team} Star"
                if team == "BOS" and k == 1:
                    name = "BOS Backup"
                if team == "BOS" and k == 0 and star_out:
                    continue
                pts = max(0, round(rng.gauss(24 - 2.5 * k, 6)))
                if team == "BOS" and k == 1 and star_out:
                    pts += round(boost)
                rows.append({"PLAYER_ID": f"{team}{k}", "PLAYER_NAME": name,
                             "TEAM_ABBREVIATION": team, "GAME_ID": f"g{g}",
                             "GAME_DATE": day.isoformat(), "MIN": 34 - 2 * k, "PTS": pts,
                             "REB": rng.randint(2, 9), "AST": rng.randint(1, 6),
                             "STL": rng.randint(0, 2), "BLK": rng.randint(0, 1),
                             "TOV": rng.randint(0, 3), "FG3M": rng.randint(0, 3)})
    return rows


def test_build_finds_the_planted_split():
    t = nba_onoff.build(_season(boost=10, n_games=240))
    split = t["BOS"][dfs.norm("BOS Star")]["mates"][dfs.norm("BOS Backup")]
    assert split["fp_wo"] - split["fp_with"] == pytest.approx(10, abs=3.0)
    assert split["n_wo"] >= 10
    # a player who never missed a game has splits for nobody
    assert not t["NYK"][dfs.norm("NYK Star")]["mates"]


def test_ratio_shrinks_and_is_bounded():
    t = nba_onoff.build(_season(boost=10))
    r_raw, used = nba_onoff.ratio(t, "BOS", "BOS Backup", ["BOS Star"], shrink=0.0)
    r_shr, _ = nba_onoff.ratio(t, "BOS", "BOS Backup", ["BOS Star"], shrink=50.0)
    assert used and r_raw > r_shr > 1.0
    assert nba_onoff.ratio(t, "BOS", "BOS Backup", [], shrink=0.0) == (1.0, [])
    assert nba_onoff.ratio(t, "BOS", "Nobody", ["BOS Star"])[0] == 1.0
    big, _ = nba_onoff.ratio(
        {"X": {"s": {"name": "S", "mates": {"a": {"name": "A", "n_with": 50, "n_wo": 3,
                                                 "fp_with": 10, "fp_wo": 60}}}}},
        "X", "A", ["S"], shrink=0.0)
    assert big == nba_onoff.RATIO_MAX


def test_ratio_is_about_one_when_fppg_already_is_a_without_number():
    """Star out for most of the window: the all-games level is mostly
    without-star games already, so the multiplier on FPPG stays near 1."""
    table = {"BOS": {"s": {"name": "S", "mates": {"a": {"name": "A", "n_with": 3, "n_wo": 40,
                                                         "fp_with": 20.0, "fp_wo": 30.0}}}}}
    r, _ = nba_onoff.ratio(table, "BOS", "A", ["S"], shrink=0.0)
    assert r == pytest.approx(30 / ((20 * 3 + 30 * 40) / 43), abs=1e-6) and r < 1.03


def test_build_is_leak_free():
    rows = _season(boost=10)
    cut = sorted({r["GAME_DATE"] for r in rows})[30]
    t = nba_onoff.build(rows, before=cut)
    split = t["BOS"][dfs.norm("BOS Star")]["mates"][dfs.norm("BOS Backup")]
    early = [r for r in rows if r["GAME_DATE"] < cut]
    assert split["n_with"] + split["n_wo"] == sum(r["PLAYER_NAME"] == "BOS Backup" for r in early)


def test_merge_unions_and_weights_last_season():
    cur = nba_onoff.build(_season(boost=10, n_games=20, seed=1))
    prev = nba_onoff.build(_season(boost=10, seed=2))
    m = nba_onoff.merge(cur, prev)
    s_cur = cur["BOS"][dfs.norm("BOS Star")]["mates"][dfs.norm("BOS Backup")]
    s_prev = prev["BOS"][dfs.norm("BOS Star")]["mates"][dfs.norm("BOS Backup")]
    s = m["BOS"][dfs.norm("BOS Star")]["mates"][dfs.norm("BOS Backup")]
    assert s["n_wo"] == pytest.approx(s_cur["n_wo"] + 0.5 * s_prev["n_wo"], abs=0.11)
    # a trio only last season knows about survives the merge
    only_prev = nba_onoff.merge({}, prev)
    assert dfs.norm("BOS Backup") in only_prev["BOS"][dfs.norm("BOS Star")]["mates"]


def test_rate_ratio_keeps_only_the_per_minute_change():
    """Without the star he plays 31 min instead of 24 and scores 30 instead of
    20: most of that is minutes (the minutes model's job). The per-minute
    change is 0.968 vs 0.833 a minute; ratio() moves the whole points gap."""
    split = {"n_with": 30, "n_wo": 30, "fp_with": 20.0, "fp_wo": 30.0,
             "min_with": 24.0, "min_wo": 31.0, "name": "B"}
    table = {"BOS": {"a": {"name": "A", "games": 60, "out": 30, "mates": {"b": split}}}}
    pts, _ = nba_onoff.ratio(table, "BOS", "B", ["A"], shrink=0.0)
    per_min, used = nba_onoff.rate_ratio(table, "BOS", "B", ["A"], shrink=0.0)
    assert used and pts == pytest.approx(30.0 / 25.0)
    assert per_min == pytest.approx((30 / 31) / (50 / 55))
    assert 1.0 < per_min < pts
    assert nba_onoff.rate_ratio(table, "BOS", "B", [], shrink=0.0) == (1.0, [])
