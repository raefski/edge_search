"""Each sport's leverage adapter (edge/dfs_leverage_sports.py) on its own test
board: candidates are legal lineups of that sport, the sweep reaches lower
ownership than the chalk lineup, and a contest re-ranks without a rebuild."""
from __future__ import annotations

import numpy as np

from edge import dfs_leverage as lev
from edge import dfs_leverage_sports as LS


def _check(a, roster):
    cands = a["cands"]
    assert cands and a["field"] and len(a["field_idx"]) > 50
    assert all(len(c["idx"]) == roster and len(set(c["idx"])) == roster for c in cands)
    assert cands[0]["fade"] == 0.0
    assert min(c["own"] for c in cands) <= cands[0]["own"]
    assert any(c["frontier"] for c in cands)


def test_nhl():
    from edge import dfs_opt_nhl as opt
    from tests.test_dfs_nhl import _slate
    pool, sim = _slate()
    chalk = opt.optimize(pool, sim, mode="gpp", iters=15, seed=1)
    a = LS.nhl({"pool": pool, "sim": sim, "gpp": chalk}, None, iters=10, n_field=200)
    _check(a, 9)
    for c in a["cands"]:
        assert opt._valid(pool, c["idx"], opt.GPP_STACKS)
    b = lev.with_contest(a, sim, {"field": 5000, "fee": 5.0})
    assert b["cands"][b["best"]]["rel"] == 100.0


def test_nascar():
    from edge import dfs_nascar_theory as theory, dfs_opt_nascar as opt, nascar_sim
    from tests.test_dfs_nascar import _field, _race
    drivers = _field()
    sim = nascar_sim.simulate(drivers, _race(), n_sims=400, seed=6)
    pool = nascar_sim.summarise(sim, drivers)
    theory.add_ownership(pool)
    for d in pool:
        d.setdefault("team", "")
    chalk = opt.optimize(pool, sim, mode="gpp", iters=40, seed=1)
    a = LS.nascar({"pool": pool, "sim": sim, "gpp": chalk}, {"field": 300, "fee": 3.0},
                  iters=30, n_field=200)
    _check(a, 6)
    assert all(sum(pool[i]["salary"] for i in c["idx"]) <= opt.CAP for c in a["cands"])
    assert a["contest"]["field"] == 300 and "ev" in a["cands"][0]


def test_ncaaf():
    from edge import dfs_ncaaf_theory as theory, dfs_opt_ncaaf as opt
    from tests.test_dfs_ncaaf import _board
    pool = _board()
    theory.add_ownership(pool)
    chalk = opt.optimize(pool, mode="gpp", iters=40, seed=0)
    a = LS.ncaaf({"pool": pool, "gpp": chalk}, None, iters=30, n_sims=500, n_field=150)
    _check(a, 8)
    for c in a["cands"]:
        assert opt._valid([pool[i] for i in c["idx"]])


def test_nfl_takes_extra_stacks():
    from edge import dfs_opt_nfl as opt
    from tests.test_dfs_nfl_contest import owned_pool
    pool = owned_pool()
    chalk = opt.optimize(pool, mode="gpp", iters=40, seed=0)
    other = opt.optimize(pool, mode="gpp", stack_qb="DEN QB", iters=40, seed=0)
    a = LS.nfl({"pool": pool, "gpp": chalk}, None, iters=30, extra=[other, None],
               n_sims=500, n_field=150)
    _check(a, 9)
    assert any("DEN" in (c.get("tag") or "") for c in a["cands"])
    for c in a["cands"]:
        assert opt._valid([pool[i] for i in c["idx"]])


def test_nfl_own_weight_moves_the_search_toward_lower_ownership():
    from edge import dfs_opt_nfl as opt
    from tests.test_dfs_nfl_contest import owned_pool
    pool = owned_pool()
    base = opt.optimize(pool, mode="gpp", iters=60, seed=0)
    faded = opt.optimize(pool, mode="gpp", iters=60, seed=0, own_weight=200.0)
    assert faded["own"] < base["own"]


def _mlb_pool():
    """Two games, four teams: nine hitters and a starter each."""
    slot_pos = {1: "C", 2: "1B", 3: "2B", 4: "3B", 5: "SS", 6: "OF", 7: "OF", 8: "OF", 9: "OF"}
    pool = []
    for g, (a, b) in enumerate((("AAA", "BBB"), ("CCC", "DDD")), start=1):
        for team, opp, home in ((a, b, True), (b, a, False)):
            for slot in range(1, 10):
                proj = 8.0 - 0.3 * slot + (0.6 if team in ("AAA", "CCC") else 0.0)
                pool.append({"name": f"{team}h{slot}", "pos": {slot_pos[slot]}, "team": team,
                             "opp_team": opp, "slot": slot, "home": home, "game": g,
                             "proj": proj, "ceiling": proj * 2.2, "salary": 2200 + 200 * slot,
                             "own": 4.0 + (12 - slot) * (1.5 if team == "AAA" else 0.6)})
            pool.append({"name": f"{team}P", "pos": {"P"}, "team": team, "opp_team": opp,
                         "game": g, "proj": 15.0, "ceiling": 24.0, "salary": 7800,
                         "outs_mean": 17.0, "k_mean": 5.5, "own": 30.0})
    return pool


def test_mlb_sweeps_from_pure_ceiling_and_keeps_the_app_lineup():
    from edge import dfs_opt
    pool = _mlb_pool()
    for p in pool:
        p["lev"] = round(p["ceiling"] - 0.3 * p["own"], 1)
    app = dfs_opt.optimize(pool, mode="gpp", stack_team="AAA", stack_n=5, iters=60,
                           stack2_team="CCC", stack2_n=3)
    a = LS.mlb({"pool": pool, "gpp": app}, None, iters=40, n_sims=400, n_field=120)
    _check(a, 10)
    assert sum(c["app"] for c in a["cands"]) == 1
    assert all(dfs_opt._valid([pool[i] for i in c["idx"]]) for c in a["cands"])
    # the sweep restores the production fade it borrowed
    assert all(p["lev"] == round(p["ceiling"] - 0.3 * p["own"], 1) for p in pool)


def test_nhl_field_builds_its_stacks_in():
    from collections import Counter
    from edge import dfs_opt_nhl as opt
    from tests.test_dfs_nhl import _slate
    pool, _sim = _slate()

    def rate(field):
        return np.mean([max(Counter(pool[i]["team"] for i in lu if pool[i]["pos"] != "G").values()) >= 3
                        for lu in field])

    stacked = LS.nhl_gpp_field(pool, 300, 0.73, seed=2)
    loose = LS.nhl_gpp_field(pool, 300, 0.0, seed=2)
    assert len(stacked) == 300 and all(opt._valid(pool, lu) for lu in stacked)
    # six teams make chance stacks common on this board; deliberate ones add more
    assert rate(stacked) > rate(loose) + 0.05
