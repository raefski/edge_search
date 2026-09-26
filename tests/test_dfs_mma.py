"""The MMA build: scoring, the market de-biasing, the simulator's invariants,
the field model, the optimiser, and the three real-card failure modes.

No network, no UFCStats download: everything here is synthetic or a fixed
anchor, so it runs anywhere the other suites run.
"""
from __future__ import annotations

import itertools
import math

import numpy as np
import pytest

from edge import dfs_mma_theory as theory
from edge import dfs_opt_mma as opt
from edge import dfs_run_mma as R
from edge import mma, mma_market as mk, mma_sim as sim


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def test_the_win_bonus_table():
    assert mma.win_bonus(True, "KO", 1, 120) == 90
    assert mma.win_bonus(True, "SUB", 2, 10) == 70
    assert mma.win_bonus(True, "KO", 3, 299) == 45
    assert mma.win_bonus(True, "KO", 4, 100) == 40
    assert mma.win_bonus(True, "SUB", 5, 100) == 40
    assert mma.win_bonus(True, "DEC", 3, 300) == 30
    assert mma.win_bonus(True, "DEC", 5, 300) == 30      # five rounds pays no more
    assert mma.win_bonus(False, "KO", 1, 10) == 0


def test_the_quick_win_bonus_is_sixty_seconds_of_round_one_only():
    assert mma.win_bonus(True, "KO", 1, 60) == 115
    assert mma.win_bonus(True, "KO", 1, 61) == 90
    assert mma.win_bonus(True, "KO", 2, 30) == 70        # not round two
    assert mma.win_bonus(True, "DEC", 1, 30) == 30       # not a decision


def test_a_significant_strike_is_worth_point_four():
    """'Strikes' is every landed strike and 'Significant Strikes' a second award
    on the significant ones. Reading it as 0.2 once missed DK's own FPPF by 8.6
    points on average; this reading reproduced it exactly for 15 of 18."""
    assert mma.stat_points(strikes=10, sig=10, ctrl_sec=0, td=0, rev=0, kd=0) == pytest.approx(4.0)
    assert mma.stat_points(strikes=10, sig=0, ctrl_sec=0, td=0, rev=0, kd=0) == pytest.approx(2.0)


def test_a_worked_fight_reproduces():
    """Rinya Nakamura, UFC Fight Night 2025-08-02: 12 strikes, all significant,
    2s of control, one knockdown, KO win in round 1 at 1:02 -- two seconds too
    late for the quick-win bonus."""
    s = {"strikes": 12, "sig": 12, "ctrl": 2, "td": 0, "rev": 0, "kd": 1}
    assert mma.dk_points(s, True, "KO", 1, 62) == pytest.approx(104.86)


def test_methods_classify():
    assert mma.classify_method("Decision - Split ") == "DEC"
    assert mma.classify_method("KO/TKO ") == "KO"
    assert mma.classify_method("TKO - Doctor's Stoppage") == "KO"
    assert mma.classify_method("Submission ") == "SUB"
    assert mma.classify_method("Overturned") == "NC"
    assert mma.classify_method("DQ") == "KO"      # DK pays a DQ win by round


def test_colliding_names_split_by_weight():
    assert mma.fighter_key("Bruno Silva", 125) != mma.fighter_key("Bruno Silva", 185)
    assert mma.fighter_key("Raul Rosas Jr.", 135) == "raulrosas"


# ---------------------------------------------------------------------------
# names: three sources, three spellings
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("a,b", [
    ("Heili Alatengheili", "Alateng Heili"),        # DK DFS vs sportsbook/UFCStats
    ("Ilimbek Akylbek", "Ilimbek Akylbek Uulu"),
    ("Raul Rosas Jr.", "Raul Rosas"),
])
def test_spelling_variants_match(a, b):
    assert mma.name_similarity(a, b) >= 0.86


def test_a_transliteration_clears_the_bout_threshold():
    assert mma.name_similarity("Mahammadali Osmanli", "Mehemmedeli Osmanli") >= R.BOUT_MATCH


def test_a_replacement_opponent_does_not():
    assert mma.name_similarity("Tina Black", "Valesca Machado") < R.BOUT_MATCH


def _ev(a, b):
    return {"event_id": "1", "name": f"{a} vs {b}", "a": a, "b": b}


def test_match_bout_refuses_a_replacement_opponent():
    """DK DFS had Amaya vs Tina Black while the book still priced Amaya vs
    Valesca Machado (2026-09-26). Those prices are for a different fight."""
    bout = {"fighters": [{"name": "Tina Black"}, {"name": "Melissa Amaya"}]}
    ev, _flip, _s = R.match_bout(bout, {"1": _ev("Melissa Amaya", "Valesca Machado")})
    assert ev is None


def test_match_bout_orients_a_flipped_event():
    bout = {"fighters": [{"name": "Yazmin Jauregui"}, {"name": "Vanessa Demopoulos"}]}
    ev, flip, _s = R.match_bout(bout, {"1": _ev("Vanessa Demopoulos", "Yazmin Jauregui")})
    assert ev is not None and flip


def test_division_from_weigh_in():
    assert R.division(116) == 115
    assert R.division(136) == 135
    assert R.division(205) == 205
    assert R.division(186) == 185


# ---------------------------------------------------------------------------
# the market
# ---------------------------------------------------------------------------
def test_the_decision_correction_raises_the_distance():
    """The one measured bias that matters most to DK: the method market
    underprices decisions in every era, and a finish pays 45-90 to 30."""
    for p in (0.2, 0.35, 0.45):
        assert mk.calibrate_decision(p) > p


def test_rake_hits_both_margins_and_keeps_ko_sub_ratio():
    t = [0.2, 0.1, 0.25, 0.15, 0.1, 0.2]
    out = mk.rake(t, p_a=0.6, p_dec=0.5)
    assert sum(out) == pytest.approx(1.0)
    assert sum(out[:3]) == pytest.approx(0.6, abs=1e-6)
    assert out[2] + out[5] == pytest.approx(0.5, abs=1e-6)
    assert out[0] / out[1] == pytest.approx(2.0, rel=1e-3)


def test_outcome_table_sums_to_one_with_the_draw():
    t = mk.outcome_table(ml=(1.5, 2.8), method=(5, 8, 3.5, 7, 15, 5))
    assert sum(t["cells"].values()) + t["p_draw"] == pytest.approx(1.0)
    assert t["source"] == "moneyline+method"


def test_the_salary_fallback_favours_the_expensive_fighter():
    t = mk.outcome_table(salary_diff=1600, lbs=115, women=True)
    pa, _pb = sim.win_probs(t["cells"])
    assert t["source"] == "salary" and 0.65 < pa < 0.78


def test_no_market_at_all_is_a_coin_flip_and_says_so():
    t = mk.outcome_table()
    assert t["source"] == "none"
    assert sim.win_probs(t["cells"])[0] == pytest.approx(0.5)


def _sel(label, odds, ot, who=None):
    return {"marketId": None, "label": label, "trueOdds": odds, "outcomeType": ot,
            "participants": [{"name": who}] if who else []}


def test_parse_book_reads_moneyline_method_and_props():
    ev = {"id": 7, "name": "Raul Rosas Jr. vs Raoni Barcelos",
          "startEventDate": "2026-09-27T02:15:00Z"}
    markets, sels = [], []

    def add(mid, typ, name, ss):
        markets.append({"id": mid, "eventId": 7, "name": name, "marketType": {"name": typ}})
        for s in ss:
            s["marketId"] = mid
            sels.append(s)

    add("m1", "Moneyline", "Moneyline", [_sel("Rosas", 1.7, "Home"), _sel("Barcelos", 2.2, "Away")])
    for i, (typ, a, b) in enumerate((("KO/TKO/DQ", 8, 5.5), ("Submission", 4.5, 12),
                                     ("Decision", 3, 3.75))):
        add(f"v{i}", typ, typ, [_sel("Rosas", a, "Home"), _sel("Barcelos", b, "Away")])
    add("s1", "[Team1] Total Significant Strikes O/U",
        "Raul Rosas Jr. Total Significant Strikes O/U",
        [_sel("Over 31.5", 1.87, "Over"), _sel("Under 31.5", 1.87, "Under")])
    add("r1", "Round and Method Betting", "Round and Method Betting",
        [_sel("Raul Rosas Jr. to Win by KO/TKO/DQ in Round 4", 46, "Home", "Raul Rosas Jr.")])
    bundle = {"league": {"events": [ev]},
              "subcategories": {"x": {"payload": {"markets": markets, "selections": sels}}}}
    out = mk.parse_book(bundle)["7"]
    assert out["ml"] == (1.7, 2.2)
    assert out["method"] == [8, 4.5, 3, 5.5, 12, 3.75]
    assert out["sig"][0]["line"] == 31.5
    assert out["sched_rounds"] == 5          # a round-4 price means five rounds


# ---------------------------------------------------------------------------
# the simulator
# ---------------------------------------------------------------------------
CELLS = {"A_KO": 0.25, "A_SUB": 0.10, "A_DEC": 0.28, "B_KO": 0.12, "B_SUB": 0.05, "B_DEC": 0.193}


def test_timing_hits_every_cell_exactly():
    tim = sim.timing(CELLS, 3)
    for c in ("A_KO", "A_SUB", "B_KO", "B_SUB"):
        assert tim[c].sum() == pytest.approx(CELLS[c], abs=1e-5)
    assert tim["DEC_TOTAL"] == pytest.approx(1 - sum(CELLS[c] for c in
                                                      ("A_KO", "A_SUB", "B_KO", "B_SUB")), abs=1e-5)


def test_round_one_is_the_most_likely_finish_round():
    tim = sim.timing(CELLS, 3)
    per_round = [tim["A_KO"][r * 10:(r + 1) * 10].sum() for r in range(3)]
    assert per_round[0] > per_round[1] > per_round[2]


def test_a_submission_rarely_comes_in_the_first_minute():
    tim = sim.timing(CELLS, 3)
    ko = tim["A_KO"][:2].sum() / tim["A_KO"][:10].sum()
    sub = tim["A_SUB"][:2].sum() / tim["A_SUB"][:10].sum()
    assert sub < ko


def _bout():
    return {"cells": CELLS, "p_draw": 0.007, "sched": 3, "idx": [(1.1, 1.0), (0.9, 1.0)]}


def test_simulated_fights_have_one_winner_and_only_he_gets_a_bonus():
    out = sim.simulate([_bout()], n_sims=4000, seed=1)
    w, pts = out["winner"][:, 0], out["points"]
    assert set(np.unique(w)) <= {-1, 0, 1}
    # the loser's score is stats only, and stats never reach a 30-point bonus
    # in a fight that ended in round one
    r1 = out["rnd"][:, 0] == 1
    assert (pts[r1 & (w == 0), 0] >= 90).all()
    assert (pts[w == 0, 0] >= 30).all()
    assert (pts >= 0).all()


def test_simulated_win_rate_matches_the_table():
    out = sim.simulate([_bout()], n_sims=20000, seed=2)
    pa = CELLS["A_KO"] + CELLS["A_SUB"] + CELLS["A_DEC"]
    assert (out["winner"][:, 0] == 0).mean() == pytest.approx(pa, abs=0.015)


def test_expected_points_agrees_with_the_sampler():
    b = _bout()
    tim = sim.timing(b["cells"], 3)
    ea, eb = sim.expected_points(b["cells"], b["p_draw"], 3, tim, *b["idx"])
    out = sim.simulate([{**b, "timing": tim}], n_sims=40000, seed=3)["points"]
    assert out[:, 0].mean() == pytest.approx(ea, rel=0.02)
    assert out[:, 1].mean() == pytest.approx(eb, rel=0.02)


def test_a_five_round_fight_scores_more_for_both_corners():
    b3, b5 = _bout(), {**_bout(), "sched": 5}
    m3 = sim.simulate([b3], n_sims=20000, seed=4)["points"].mean(axis=0)
    m5 = sim.simulate([b5], n_sims=20000, seed=4)["points"].mean(axis=0)
    assert (m5 > m3).all()


def test_dominance_lifts_the_favourite_in_defeat():
    lo = sim.expected_stats("L-KO", 5.0, p_win=0.2)
    hi = sim.expected_stats("L-KO", 5.0, p_win=0.8)
    assert hi > lo


# ---------------------------------------------------------------------------
# the field and the optimiser
# ---------------------------------------------------------------------------
def _toy_card(n_bouts=5, seed=0):
    rng = np.random.default_rng(seed)
    pool, opp = [], []
    for j in range(n_bouts):
        for side in (0, 1):
            pool.append({"name": f"F{j}{side}", "salary": int(rng.integers(66, 99)) * 100,
                         "proj": float(rng.uniform(30, 90)), "dk_fppf": None,
                         "bout": f"B{j}"})
            opp.append(2 * j + 1 - side)
    return pool, np.array(opp)


def test_field_ownership_is_a_board_a_real_field_could_build():
    pool, opp = _toy_card(6)
    sal = np.array([d["salary"] for d in pool])
    L = opt.legal_lineups(sal)
    own, fl, w = theory.field_model(pool, L, opp, 8.0)
    assert own.sum() == pytest.approx(600.0)
    assert (own / 100 * sal).sum() <= 50000 + 1e-6          # the budget
    for j in range(6):                                       # no fight over 100%
        assert own[2 * j] + own[2 * j + 1] <= 100.0 + 1e-6
    field = theory.sample_field(fl, w, n=500)
    assert theory.pair_free(field, opp).all()


def test_legal_lineups_are_all_the_legal_lineups():
    pool, _opp = _toy_card(4)
    sal = np.array([d["salary"] for d in pool])
    L = opt.legal_lineups(sal)
    brute = [c for c in itertools.combinations(range(len(pool)), 6)
             if sal[list(c)].sum() <= 50000]
    assert len(L) == len(brute)
    assert (sal[L].sum(axis=1) <= 50000).all()


def test_optimize_is_the_argmax_of_its_own_objective():
    pool, _opp = _toy_card(4, seed=3)
    sal = np.array([d["salary"] for d in pool])
    rng = np.random.default_rng(5)
    pts = rng.gamma(2.0, 25.0, size=(3000, len(pool)))
    line = np.full(3000, np.percentile(pts.sum(axis=1) * 6 / len(pool), 60))
    res = opt.optimize(pts, sal, {"cash": line, "gpp": line}, "cash")
    L = opt.legal_lineups(sal)
    scores = [(pts[:, l].sum(axis=1) > line).mean() for l in L]
    assert res["objective"] == pytest.approx(max(scores), abs=1e-3)


def test_locked_and_banned_fighters_are_respected():
    pool, _opp = _toy_card(4)
    sal = np.array([d["salary"] for d in pool])
    L = opt.legal_lineups(sal, locked=[0], banned=[1])
    assert (L == 0).any(axis=1).all()
    assert not (L == 1).any()


# ---------------------------------------------------------------------------
# the forward log
# ---------------------------------------------------------------------------
def test_a_past_date_never_overwrites_the_log(tmp_path):
    pool = [{"bout": "A vs B", "name": "A", "key": "a", "salary": 8000, "sched": 3,
             "source": "moneyline+method", "p_win": 0.5, "p_finish": 0.3, "p_r1": 0.1,
             "proj": 60, "sd": 30, "floor": 40, "median": 60, "ceil": 100, "own": 20,
             "own_cash": 20, "dk_fppf": 55, "off": 1.0, "def_opp": 1.0,
             "start": "2020-01-01T21:00:00Z"}]
    res = R.log_forward_test(pool, {}, {}, 1, {"start": "2020-01-01T21:00:00Z"}, {},
                             root=tmp_path)
    assert res.get("skipped_past_date")
    assert not (tmp_path / "data" / "dfs_proj_log_mma.csv").exists()
