"""edge/nba_onoff.py (with/without-teammate splits), its backtest, and how
the NBA board applies it -- on synthetic box scores with a planted effect."""
from __future__ import annotations

import random
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

from edge import dfs, dfs_run_nba as R, nba, nba_onoff  # noqa: E402


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


def test_backtest_finds_a_real_effect_and_not_a_fake_one():
    import nba_onoff_backtest as B
    real = B.run(_season(boost=12, n_games=400, seed=3), refresh_days=10)
    assert real["best_k"] < 1e8 and real["rep_best_mae"] < real["rep_base_mae"]
    assert real["diff_ci90"][1] < 0                      # helps, held out
    null = B.run(_season(boost=0, n_games=400, seed=4), refresh_days=10)
    assert null["diff_ci90"][1] > -0.5                   # no real gain to find


# ---------------------------------------------------------------------------
# on the board
# ---------------------------------------------------------------------------
import test_dfs_nba as T  # noqa: E402


@pytest.fixture
def board(monkeypatch):
    """The synthetic NBA slate: 'Nba BOS Player3' is OUT. The table says BOS's
    Player7 (an FPPG player) and Player0 (a props player) both gain without him."""
    sal = T._salaries(T.NBA_TEAMS, T.NBA_POS, "Nba")
    monkeypatch.setattr(dfs, "fetch_draftables", lambda gid: sal)
    monkeypatch.setattr(dfs, "LOBBY_GAME_TYPES", {})
    split = {"n_with": 30, "n_wo": 30, "fp_with": 20.0, "fp_wo": 30.0,
             "min_with": 24.0, "min_wo": 31.0}
    table = {"BOS": {dfs.norm("Nba BOS Player3"): {
        "name": "Nba BOS Player3", "games": 40, "out": 30,
        "mates": {dfs.norm("Nba BOS Player7"): dict(split, name="Nba BOS Player7"),
                  dfs.norm("Nba BOS Player0"): dict(split, name="Nba BOS Player0")}}}}
    monkeypatch.setattr(nba_onoff, "load", lambda path=None: table)
    return sal


def _row(res, name):
    return next(p for p in res["pool"] if p["name"] == name)


def test_fppg_player_is_adjusted_props_player_is_not(board):
    client = T.FakeClient(board, T.NBA_TEAMS, T.BOOK_NAME)      # Player3 has no prop (k<7 has)
    # Player3 is k=3 -> the fake books DO price him: make them stale-free by
    # dropping his lines, as a book does once it re-prices the team
    orig = client.get_event_odds

    def reposted(sport, eid, markets, regions):
        out = orig(sport, eid, markets, regions)
        for m in out["bookmakers"][0]["markets"]:
            m["outcomes"] = [o for o in m["outcomes"] if o["description"] != "Nba BOS Player3"]
        return out
    client.get_event_odds = reposted
    res = R.build_slate(client=client, groups=T._groups(70), n_sims=600, iters=5, persist=False)
    p7, p0 = _row(res, "Nba BOS Player7"), _row(res, "Nba BOS Player0")
    assert p7["source"] == "fppg" and p7["onoff"] and p7["onoff"]["ratio"] > 1.0
    assert "onoff" in p7["imputed"]
    assert p0["source"] == "props" and p0["onoff"] is None
    assert res["stats"]["stale_props_teams"] == []


def test_stale_props_are_adjusted_too(board):
    # the OUT player still has a points line -> the books have not re-posted
    res = R.build_slate(client=T.FakeClient(board, T.NBA_TEAMS, T.BOOK_NAME),
                        groups=T._groups(70), n_sims=600, iters=5, persist=False)
    assert res["stats"]["stale_props_teams"] == ["BOS"]
    p0 = _row(res, "Nba BOS Player0")
    assert p0["source"] == "props" and p0["onoff"]["ratio"] > 1.0


def test_no_table_is_visible_not_silent(monkeypatch, board):
    monkeypatch.setattr(nba_onoff, "load", lambda path=None: {})
    monkeypatch.setattr(nba_onoff, "LAST_SOURCE", "missing")
    res = R.build_slate(client=T.FakeClient(board, T.NBA_TEAMS, T.BOOK_NAME),
                        groups=T._groups(70), n_sims=400, iters=5, persist=False)
    assert res["stats"]["onoff_source"] == "missing"
    assert all(p["onoff"] is None for p in res["pool"])
