"""GPP leverage (edge/dfs_leverage.py): the chalk -> contrarian sweep, the
frontier, and expected payout in a contest of a given size."""
from __future__ import annotations

import numpy as np

from edge import dfs_leverage as lev


def test_own_weights_scale_to_the_chalk_lineup():
    w = lev.own_weights(150.0, 120.0, fades=(0.0, 0.1))
    assert w[0] == 0.0
    # at fade 0.1 the chalk lineup's own ownership costs 10% of its ceiling
    assert abs(w[1] * 120.0 / 100.0 - 15.0) < 1e-9


def test_sweep_drops_duplicates_and_keeps_slot_order():
    calls = []

    def build(w):
        calls.append(w)
        return {"idx": [3, 1, 2] if w < 20 else [5, 4, 6]}

    out = lev.sweep(build, lambda r: r["idx"], {"idx": [2, 3, 1]}, 100.0, 50.0,
                    fades=(0.0, 0.05, 0.3))
    assert [c["idx"] for c in out] == [[2, 3, 1], [5, 4, 6]]   # [3,1,2] is the chalk again
    assert out[0]["fade"] == 0.0 and len(calls) == 2


def test_pareto_flags_dominated_points():
    pts = [{"ceil": 160, "own": 70}, {"ceil": 164, "own": 50}, {"ceil": 150, "own": 30},
           {"ceil": 149, "own": 40}]
    assert lev.pareto(pts) == [False, True, True, False]


def test_payout_tiers_pin_a_synthetic_curve_to_the_prize_pool():
    tiers = lev.payout_tiers(1000, None, entry_fee=5.0, prize_pool=4000.0)
    total = sum((hi - lo + 1) * amt for lo, hi, amt in tiers)
    assert abs(total - 4000.0) / 4000.0 < 0.02
    real = lev.payout_tiers(100, [(1, 1, 50.0), (2, 10, 5.0)])
    assert real == [(1, 1, 50.0), (2, 10, 5.0)]


def test_tail_never_ranks_a_field_beater_first_of_everyone():
    rng = np.random.default_rng(0)
    srt = np.sort(rng.normal(120, 10, (50, 1000)), axis=1)
    share = lev.share_above(srt[:, -1] + 1.0, srt)
    assert (share > 0).all() and (share < lev.TAIL_K / 1000).all()
    assert np.allclose(lev.share_above(np.full(50, 120.0), srt), 0.5, atol=0.06)
    assert np.isfinite(lev.share_above(np.full(50, -1e6), srt)).all()


def _toy(n_players=12, n_sims=3000, seed=0):
    """Two candidates with the same mean: one shares the field's players
    (duplicated), one does not. The field all plays players 0-2."""
    rng = np.random.default_rng(seed)
    scores = rng.normal(10, 4, (n_sims, n_players))
    field = [[0, 1, 2]] * 200 + [[3, 4, 5]] * 50
    return scores, field


def test_contrarian_gains_in_a_big_field():
    scores, field = _toy()
    chalk, contrarian = [0, 1, 2], [9, 10, 11]
    small = lev.contest_ev(scores, [chalk, contrarian], field, 10, entry_fee=1.0)
    big = lev.contest_ev(scores, [chalk, contrarian], field, 50_000, entry_fee=1.0)
    # every field lineup that matches the chalk ties it; the same mean
    # unshared is worth more, and more so when first place is 1-in-50,000
    assert big[1]["ev"] > big[0]["ev"]
    assert big[1]["rel"] == 100.0
    assert all(0 <= r["p_cash"] <= 1 for r in small + big)


def test_analyze_then_with_contest_reuses_candidates():
    scores, field = _toy()
    own = np.array([30.0] * 3 + [10.0] * 3 + [1.0] * 6)
    cands = [{"idx": [0, 1, 2], "fade": 0.0, "result": {}},
             {"idx": [9, 10, 11], "fade": 0.3, "result": {}}]
    a = lev.analyze(scores, own, cands, field, 95.0)
    assert [c["own"] for c in a["cands"]] == [90.0, 3.0]
    assert a["contest"] is None and len(a["field"]) == 250 and len(a["field_idx"]) == 250
    b = lev.with_contest(a, scores, {"field": 50_000, "fee": 1.0})
    assert b["best"] == 1 and "ev" in b["cands"][0] and "ev" not in a["cands"][0]


def test_tilt_never_duplicates_a_lineup():
    """With replacement, a 1,000-lineup NHL field held 145 + 101 copies of
    its two best-projected draws (2026-10-13)."""
    raw = [[i, i + 1] for i in range(3000)]
    proj = np.linspace(0, 100, 3000)
    field = lev.tilt(raw, proj, 1000, seed=1)
    assert len(field) == 1000 and len({tuple(x) for x in field}) == 1000
    top = sum(1 for x in field if x[0] >= 2700)          # the best-projected tenth
    assert top > 150             # still tilted: uniform picking would take ~100 of these 300
