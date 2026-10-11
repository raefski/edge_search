"""Ranking NFL GPP stacks for a given contest: the field it simulates and the
pieces that turn simulated finishes into a payout."""
from __future__ import annotations

import numpy as np

from edge import dfs_nfl_contest as nc
from edge import dfs_opt_nfl as opt
from edge.dfs_contest import contest_id, expected_field
from tests.test_dfs_opt_nfl import pool


def owned_pool():
    ps = pool()
    for i, p in enumerate(ps):
        p["own"] = 5.0 + i % 7
    return ps


def test_contest_id_from_link_or_number():
    assert contest_id("https://www.draftkings.com/draft/contest/196420173") == "196420173"
    assert contest_id(" 196420173 ") == "196420173"
    assert contest_id("draft group 154468") is None
    assert contest_id("") is None


def test_expected_field_uses_cap_unless_uncapped():
    assert expected_field({"field": 300, "max_entries": 17835}) == 17835
    assert expected_field({"field": 46410, "max_entries": 999999999}) == 46410


def test_field_lineups_are_legal_and_stacked():
    ps = owned_pool()
    field = nc.sample_field(ps, 200, seed=1)
    assert len(field) == 200
    assert all(opt._valid([ps[i] for i in lu]) for lu in field)
    stacked = sum(any(ps[j]["team"] == ps[lu[0]]["team"] and ps[j]["pos"] & opt.CATCHERS
                      for j in lu[1:]) for lu in field)
    assert stacked / len(field) > 0.5


def test_tail_never_ranks_a_field_beater_first_of_everyone():
    rng = np.random.default_rng(0)
    totals = rng.normal(120, 10, (50, 1000))
    top = totals.max(1)
    share = nc.share_above(top + 1.0, totals)
    assert (share > 0).all() and (share < nc.TAIL_K / 1000).all()
    mid = nc.share_above(np.full(50, 120.0), totals)
    assert np.allclose(mid, 0.5, atol=0.06)


def test_rank_returns_one_row_per_candidate():
    ps = owned_pool()
    cands = [opt.optimize(ps, mode="gpp", stack_qb=q, iters=60, seed=0) for q in ("BUF QB", "KC QB")]
    res = nc.rank(ps, cands, 5000, entry_fee=5.0, n_sims=300, n_field=200)
    assert len(res) == 2
    assert all(r["ev"] >= 0 and 0 <= r["p_cash"] <= 1 for r in res)
