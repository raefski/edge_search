import math
from collections import Counter

import numpy as np
import pytest

from edge import dfs_nhl_theory as theory
from edge import dfs_opt_nhl as opt
from edge import nhl, nhl_sim


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def test_skater_scoring_with_every_bonus():
    # 1 goal, 2 assists, 5 shots, 3 blocks: 8.5 + 10 + 7.5 + 3.9, plus the
    # 5-shot, 3-block and 3-point bonuses.
    assert nhl.skater_points(1, 2, 5, 3) == pytest.approx(38.9)


def test_hat_trick_bonus_stacks_with_three_points():
    assert nhl.skater_points(3, 0, 3, 0) == pytest.approx(25.5 + 4.5 + 3 + 3)


def test_goalie_scoring():
    assert nhl.goalie_points(35, 1, 1, 0, 0) == pytest.approx(24.5 - 3.5 + 6 + 3)
    assert nhl.goalie_points(30, 0, 1, 0, 1) == pytest.approx(21 + 6 + 4)
    assert nhl.goalie_points(30, 3, 0, 1, 0) == pytest.approx(21 - 10.5 + 2)


def test_slots():
    assert nhl.slots_for("LW") == {"W", "UTIL"}
    assert nhl.slots_for("C") == {"C", "UTIL"}
    assert nhl.slots_for("D") == {"D", "UTIL"}
    assert nhl.slots_for("G") == {"G"}


# ---------------------------------------------------------------------------
# market -> means
# ---------------------------------------------------------------------------
def test_poisson_mean_inverts_an_even_half_goal_line():
    assert nhl_sim.poisson_mean(0.5, 0.5) == pytest.approx(math.log(2), abs=1e-4)


def test_market_mean_needs_both_sides():
    assert nhl_sim.market_mean({"Over": 1.9, "point": 2.5}) is None
    assert nhl_sim.market_mean({"Over": 1.9, "Under": 1.9, "point": 2.5}) > 2.5


def test_implied_goals_split_follows_the_moneyline():
    even = nhl_sim.implied_goals(6.0, 0.5, 0.5)
    assert even[0] == pytest.approx(even[1], rel=1e-3)
    fav = nhl_sim.implied_goals(6.0, 0.5, 0.65)
    assert fav[0] > fav[1] and sum(fav) == pytest.approx(sum(even), rel=1e-6)


# ---------------------------------------------------------------------------
# the simulation
# ---------------------------------------------------------------------------
def _team(t, opp, lam_scale=1.0):
    rows = []
    for line in ("F1", "F2", "F3"):
        for k in range(3):
            rows.append({"name": f"{t}{line}{k}", "team": t, "opp": opp, "game": "g",
                         "pos": "C" if k == 0 else "W", "line": line,
                         "pp": "PP1" if line == "F1" else None, "salary": 5000,
                         "means": {"goals": 0.3 * lam_scale, "assists": 0.45 * lam_scale,
                                   "shots": 2.5, "blocks": 0.4}})
    for line in ("D1", "D2"):
        for k in range(2):
            rows.append({"name": f"{t}{line}{k}", "team": t, "opp": opp, "game": "g",
                         "pos": "D", "line": line, "pp": None, "salary": 4000,
                         "means": {"goals": 0.08, "assists": 0.3, "shots": 1.8, "blocks": 1.5}})
    rows.append({"name": f"{t}G", "team": t, "opp": opp, "game": "g", "pos": "G",
                 "line": "G1", "pp": None, "salary": 8000, "means": {"saves": 27}})
    return rows


def test_linemates_score_together():
    pool = _team("AAA", "BBB") + _team("BBB", "AAA")
    sim = nhl_sim.simulate(pool, {"g": {"home": "AAA", "away": "BBB",
                                        "lam_home": 3.2, "lam_away": 3.0}}, n_sims=4000)
    ix = {p["name"]: i for i, p in enumerate(pool)}
    same = np.corrcoef(sim[:, ix["AAAF10"]], sim[:, ix["AAAF11"]])[0, 1]
    other = np.corrcoef(sim[:, ix["AAAF10"]], sim[:, ix["AAAF31"]])[0, 1]
    assert same > other + 0.05
    goalie_vs_opp = np.corrcoef(sim[:, ix["AAAG"]], sim[:, ix["BBBF10"]])[0, 1]
    assert goalie_vs_opp < 0


def test_skaters_never_score_negative_and_favourites_score_more():
    pool = _team("AAA", "BBB") + _team("BBB", "AAA")
    sim = nhl_sim.simulate(pool, {"g": {"home": "AAA", "away": "BBB",
                                        "lam_home": 3.8, "lam_away": 2.4}}, n_sims=2000)
    sk = [i for i, p in enumerate(pool) if p["pos"] != "G"]
    assert sim[:, sk].min() >= 0
    a = [i for i in sk if pool[i]["team"] == "AAA"]
    b = [i for i in sk if pool[i]["team"] == "BBB"]
    assert sim[:, a].mean() > sim[:, b].mean()


# ---------------------------------------------------------------------------
# the optimiser
# ---------------------------------------------------------------------------
def _slate():
    pool = []
    for gname, (h, a) in {"g1": ("AAA", "BBB"), "g2": ("CCC", "DDD"), "g3": ("EEE", "FFF")}.items():
        for t, o in ((h, a), (a, h)):
            for p in _team(t, o):
                p["game"] = gname
                pool.append(p)
    games = {g: {"home": h, "away": a, "lam_home": 3.1, "lam_away": 3.0}
             for g, (h, a) in {"g1": ("AAA", "BBB"), "g2": ("CCC", "DDD"),
                               "g3": ("EEE", "FFF")}.items()}
    rng = np.random.default_rng(3)
    for p in pool:
        p["salary"] = int(rng.integers(30, 90)) * 100 if p["pos"] != "G" else 7500
    sim = nhl_sim.simulate(pool, games, n_sims=800)
    nhl_sim.summarise(sim, pool)
    theory.add_ownership(pool)
    return pool, sim


@pytest.mark.parametrize("mode", ["cash", "gpp"])
def test_every_lineup_is_legal(mode):
    pool, sim = _slate()
    res = opt.optimize(pool, sim, mode=mode, iters=15, seed=1)
    rows = res["lineup"]
    pos = Counter(r["pos"] for r in rows)
    assert len(rows) == 9 and pos["G"] == 1 and pos["C"] >= 2 and pos["W"] >= 3 and pos["D"] >= 2
    assert sum(r["salary"] for r in rows) <= nhl.SALARY_CAP
    assert len({r["team"] for r in rows if r["pos"] != "G"}) >= 3   # skaters only
    assert len({r["game"] for r in rows}) >= 2
    goalie = next(r for r in rows if r["pos"] == "G")
    assert not any(r["team"] == goalie["opp"] for r in rows if r["pos"] != "G")
    assert [s for s, _ in res["slots"]] == ["C", "C", "W", "W", "W", "D", "D", "G", "UTIL"]
    if mode == "gpp":
        sizes = sorted(Counter(r["team"] for r in rows if r["pos"] != "G").values(), reverse=True)
        assert sizes[0] >= 3 and sizes[1] >= 3 and sizes[0] <= opt.MAX_FROM_TEAM


def test_ownership_fills_each_position_group():
    pool, _ = _slate()
    for pos, slots in theory.GROUP_SLOTS.items():
        total = sum(p["own"] for p in pool if p["pos"] == pos)
        assert total == pytest.approx(100 * slots, abs=1.5)


def test_default_slate_is_next_main_not_biggest():
    from edge import dfs_run_nhl as R
    rows = [{"gid": 154690, "label": "Main", "games": 3, "start": "2026-10-07T23:30:00Z"},
            {"gid": 154693, "label": "Late", "games": 1, "start": "2026-10-08T02:00:00Z"},
            {"gid": 154696, "label": "Main", "games": 10, "start": "2026-10-08T23:00:00Z"}]
    assert rows[R.default_slate(rows)]["gid"] == 154690
    assert R.default_slate([{"gid": 1, "label": "Late", "games": 2, "start": "x"}]) == 0


def test_cash_field_is_legal_and_tracks_ownership():
    from edge import dfs_nhl_field as field
    pool, sim = _slate()
    own = field.cash_ownership(pool)
    lineups = field.sample_field(pool, own, n=200, seed=2)
    assert lineups.shape == (200, 9)
    assert all(opt._valid(pool, list(lu)) for lu in lineups)
    seen = np.bincount(lineups.ravel(), minlength=len(pool)) / len(lineups)
    assert np.corrcoef(seen, own)[0, 1] > 0.8
    line = field.cash_line(sim, lineups)
    totals = sim[:, lineups].sum(axis=2)
    assert line.shape == (sim.shape[0],)
    assert (line >= totals.min(axis=1)).all() and (line <= totals.max(axis=1)).all()


def test_field_aware_cash_clears_the_line_at_least_as_often_as_the_floor():
    from edge import dfs_nhl_field as field
    pool, sim = _slate()
    own = field.cash_ownership(pool)
    line = field.cash_line(sim, field.sample_field(pool, own, n=200, seed=2))
    fa = opt.optimize(pool, sim, mode="cash", iters=15, seed=1, line=line)
    fl = opt.optimize(pool, sim, mode="cash", iters=15, seed=1)

    def p(res):
        return (sim[:, [pool.index(r) for r in res["lineup"]]].sum(axis=1) >= line).mean()
    assert opt._valid(pool, [pool.index(r) for r in fa["lineup"]])
    assert p(fa) >= p(fl) - 0.01
