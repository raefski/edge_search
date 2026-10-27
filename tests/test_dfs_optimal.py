"""edge/dfs_optimal.py: Optimal % is the share of simulated nights a player is in
that night's exactly-solved best legal lineup."""
import numpy as np

from edge import dfs_optimal as D, dfs_opt_nba as O


def _pool():
    pos = ["PG", "SG", "SF", "PF", "C", "PG", "SF", "C", "PG/SG", "SF/PF"]
    return [{"name": f"p{i}", "dk_pos": p, "elig": O.eligible(p), "salary": 5000 + 100 * i,
             "game": f"g{i % 3}", "team": f"t{i % 6}", "own": 10.0} for i, p in enumerate(pos)]


def test_shares_sum_to_roster_and_stars_always_in():
    pool = _pool()
    rng = np.random.default_rng(0)
    sim = rng.uniform(10, 20, size=(60, len(pool)))
    sim[:, 0] += 100                         # p0 dominates every night
    opt = D.optimal_pct(pool, sim, O.SLOTS, O.SLOT_OK, 60000, n=60, workers=1)
    assert abs(opt.sum() - 800) < 1e-6       # 8 slots, every night solved
    assert opt[0] == 100


def test_cap_binds():
    pool = _pool()
    sim = np.full((5, len(pool)), 10.0)
    sim[:, 9] = 1000                         # best player, but the cap excludes any lineup with him...
    pool[9]["salary"] = 49000                # ...plus seven others at >= $5,000
    opt = D.optimal_pct(pool, sim, O.SLOTS, O.SLOT_OK, 50000, n=5, workers=1)
    assert opt[9] == 0


def test_annotate_columns():
    pool = _pool()
    sim = np.random.default_rng(1).uniform(10, 40, size=(30, len(pool)))
    D.annotate(pool, sim, O.SLOTS, O.SLOT_OK, 60000, n=30, workers=1)
    for p in pool:
        assert {"optimal", "opt_leverage", "boom", "bust"} <= set(p)
        assert p["opt_leverage"] == round(p["optimal"] - 10.0, 1)
