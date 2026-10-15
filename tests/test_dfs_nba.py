"""The NBA DFS build: slots, optimiser, simulation, ownership, projection pieces and
the lineup snapshot (edge/dfs_opt_nba.py, dfs_nba_theory.py, nba_project.py,
nba_minutes.py, nba_lineups.py, dfs_run_nba.py)."""
from __future__ import annotations

import json

import numpy as np
import pytest

from edge import dfs_nba_theory as T, dfs_opt_nba as O, nba_lineups as L, nba_minutes as NM
from edge import nba_project as NP
from edge import dfs_run_nba as R


def _p(name, pos, team, salary, proj, game=None):
    return {"name": name, "dk_pos": pos, "elig": O.eligible(pos), "team": team,
            "game": game or ("g1" if team in ("NYK", "PHI") else "g2"), "salary": salary,
            "proj": proj, "own": 10.0, "pos": sorted(O.eligible(pos))[0]}


# ── slots ──────────────────────────────────────────────────────────────────
def test_multi_position_players_are_matched_to_the_eight_slots():
    rows = [_p("a", "PG", "NYK", 5000, 30), _p("b", "SG/SF", "NYK", 5000, 30),
            _p("c", "SF", "PHI", 5000, 30), _p("d", "PF/C", "PHI", 5000, 30),
            _p("e", "C", "BOS", 5000, 30), _p("f", "PG/SG", "BOS", 5000, 30),
            _p("g", "SF/PF", "DET", 5000, 30), _p("h", "C", "DET", 5000, 30)]
    slots = O.assign_slots(rows)
    assert slots is not None and [s for s, _ in slots] == list(O.SLOTS)
    assert all(p["elig"] & O.SLOT_OK[s] for s, p in slots)


def test_four_centres_cannot_fill_a_lineup():
    rows = [_p(str(i), "C", "NYK", 5000, 30) for i in range(4)] + \
           [_p(str(i), "PG", "PHI", 5000, 30) for i in range(4, 8)]
    assert O.assign_slots(rows) is None             # C, UTIL hold two centres; F needs SF/PF


def test_the_optimiser_returns_a_legal_lineup_under_the_cap():
    teams = ["NYK", "PHI", "BOS", "DET"]
    pos = ["PG", "SG", "SF", "PF", "C", "PG/SG", "SF/PF", "PF/C"]
    pool = [_p(f"{t}{i}", pos[i % 8], t, 3000 + 700 * ((i * 7 + k) % 10), 15 + 3 * ((i * 5 + k) % 9))
            for k, t in enumerate(teams) for i in range(10)]
    sim = T.simulate(pool, n_sims=500, seed=1)
    T.summarise(sim, pool)
    for mode in ("cash", "gpp"):
        res = O.optimize(pool, sim, mode=mode, iters=15, seed=0)
        assert res and res["salary"] <= O.SALARY_CAP and len(res["slots"]) == 8
        assert len({p["team"] for p in res["lineup"]}) >= 2 and len({p["game"] for p in res["lineup"]}) >= 2


# ── simulation and ownership ───────────────────────────────────────────────
def test_simulated_players_keep_their_mean_and_the_measured_spread():
    pool = [{"proj": 40.0}, {"proj": 8.0}]
    sim = T.simulate(pool, n_sims=40000, seed=0)
    assert sim[:, 0].mean() == pytest.approx(40.0, rel=0.02)
    assert sim[:, 0].std() == pytest.approx(T.sd_for(40.0), rel=0.03)
    assert (sim >= 0).all()


def test_ownership_fills_each_positions_share_of_the_slots():
    pool = [{"pos": "C", "proj": 20 + i, "salary": 4000 + 300 * i} for i in range(12)]
    T.add_ownership(pool)
    assert sum(p["own"] for p in pool) == pytest.approx(100 * T.GROUP_SLOTS["C"], rel=0.01)
    assert all(0 <= p["own"] <= T.MAX_OWN for p in pool)


# ── projection pieces ──────────────────────────────────────────────────────
def test_a_player_with_few_minutes_is_mostly_the_position_prior():
    rookie = [(10.0, False, 20.0)]                       # 2.0 per minute over 10 minutes
    vet = [(30.0, True, 30.0)] * 30                      # 1.0 per minute over 900
    assert NP.rate(rookie, "G") < 1.3                    # pulled toward 0.95
    assert NP.rate(vet, "G") == pytest.approx(1.0, abs=0.02)


def test_too_many_roles_are_shared_so_a_star_keeps_more_than_proportional():
    team = [{"id": "star", "position": "G", "inactive": False, "starter": None,
             "history": [(36.0, True)] * 10}]
    team += [{"id": str(i), "position": "F", "inactive": False, "starter": None,
              "history": [(24.0, False)] * 10} for i in range(11)]          # 300 minutes of roles
    shared = NM.project(team)["star"]["minutes"]
    proportional = NM.project(team, {"EXCESS_POW": 1.0})["star"]["minutes"]
    assert proportional == pytest.approx(36 * 240 / 300, abs=0.2) and shared > proportional


# ── the lineup snapshot ────────────────────────────────────────────────────
def _raw(ts, inactive=False):
    return {"games": [{"gameId": "1", "gameStatusText": "7:30 pm ET",
                       "homeTeam": {"teamAbbreviation": "NYK", "players": [
                           {"personId": 1, "playerName": "A", "position": "PG", "lineupStatus": "Expected",
                            "rosterStatus": "Inactive" if inactive else "Active", "timestamp": ts}]},
                       "awayTeam": {"teamAbbreviation": "TOR", "players": []}}]}


def test_the_snapshot_commits_lineup_changes_not_nba_coms_five_minute_timestamps(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "SNAPSHOT", tmp_path / "snap.json")
    assert L.write_snapshot("2026-10-15", _raw("15:36:32"), "t1")
    assert not L.write_snapshot("2026-10-15", _raw("15:41:32"), "t2")       # timestamp only
    assert L.write_snapshot("2026-10-15", _raw("15:46:32", inactive=True), "t3")
    assert json.loads((tmp_path / "snap.json").read_text())["fetched_at"] == "t3"


# ── slates ─────────────────────────────────────────────────────────────────
def test_the_main_classic_slate_is_the_default_and_team_codes_match_nba_com():
    groups = [{"DraftGroupId": 2, "GameTypeId": 70, "ContestStartTimeSuffix": " (Night)", "GameCount": 2,
               "StartDate": "2026-10-20T23:00:00"},
              {"DraftGroupId": 1, "GameTypeId": 70, "ContestStartTimeSuffix": None, "GameCount": 3,
               "StartDate": "2026-10-20T19:00:00"},
              {"DraftGroupId": 3, "GameTypeId": 81, "ContestStartTimeSuffix": " (BOS @ DET)", "GameCount": 1,
               "StartDate": "2026-10-20T19:00:00"}]
    gid, meta = R.resolve_slate(None, groups)
    assert gid == 1 and meta["label"] == "Main" and len(R.classic_groups(groups)) == 2
    assert R.team("PHO") == "PHX" and R.team("NYK") == "NYK"
