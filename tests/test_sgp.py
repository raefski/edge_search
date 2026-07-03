import math

from edge.sgp import (
    joint_prob, fair_parlay_decimal, implied_phi, sgp_edge, phi_bounds,
)


def approx(a, b, tol=1e-4):
    return abs(a - b) < tol


def test_independent_parlay_multiplies():
    # two coin-flips, zero correlation -> 0.25, fair odds 4.0 (= 2.0 x 2.0)
    assert approx(joint_prob(0.5, 0.5, 0.0), 0.25)
    assert approx(fair_parlay_decimal(0.5, 0.5, 0.0), 4.0)


def test_positive_correlation_shortens_fair_odds():
    # phi=0.3 makes both legs more likely to hit together -> shorter than 4.0
    j = joint_prob(0.5, 0.5, 0.3)
    assert approx(j, 0.325)               # 0.25 + 0.3*0.25
    assert approx(fair_parlay_decimal(0.5, 0.5, 0.3), 1 / 0.325)


def test_implied_phi_inverts_the_price():
    # DK prices the SGP at 3.5 -> implied joint 0.2857 -> implied phi 0.1429
    phi = implied_phi(3.5, 0.5, 0.5)
    assert approx(phi, (1 / 3.5 - 0.25) / 0.25)
    assert approx(phi, 0.142857)


def test_under_correlation_is_positive_ev():
    # true phi 0.30 but DK only prices in ~0.14 -> we should see +EV
    r = sgp_edge(dk_sgp_decimal=3.5, p=0.5, q=0.5, phi_true=0.30)
    assert r["dk_implied_phi"] < r["phi_true"]      # DK under-correlated
    assert r["ev"] > 0
    assert approx(r["ev"], 0.325 * 3.5 - 1)         # +13.75%


def test_over_correlation_is_negative_ev():
    # if DK over-adjusts (prices shorter than true), taking it is -EV
    r = sgp_edge(dk_sgp_decimal=2.8, p=0.5, q=0.5, phi_true=0.30)
    assert r["dk_implied_phi"] > r["phi_true"]
    assert r["ev"] < 0


def test_phi_bounds_keep_joint_valid():
    lo, hi = phi_bounds(0.5, 0.5)
    # at the bounds the joint hits its min/max feasible value
    assert approx(joint_prob(0.5, 0.5, lo), 0.0)
    assert approx(joint_prob(0.5, 0.5, hi), 0.5)


def test_grade_quote_flags_under_correlation():
    from edge.sgp import grade_quote, lookup_phi
    table = [
        {"combo": "OutsOver + OppUnder", "opp_line": "4.5", "phi_true": "0.34"},
        {"combo": "OutsOver + OppUnder", "opp_line": "3.5", "phi_true": "0.35"},
    ]
    # opp line 4.5 should select the 0.34 row, not the 3.5 row
    assert approx(lookup_phi(table, {"starter_outs_over", "opp_total_under"}, 4.5), 0.34)

    quote = {
        "game_id": "t", "dk_decimal": 3.5,
        "legs": [
            {"type": "starter_outs_over", "taken_dec": 1.91, "opp_dec": 1.91},
            {"type": "opp_total_under", "line": 4.5, "taken_dec": 1.91, "opp_dec": 1.91},
        ],
    }
    r = grade_quote(quote, table)
    # both legs ~0.5, phi_true 0.34 -> joint 0.335, fair 2.99; DK @3.5 is +EV
    assert r["verdict"] == "graded"
    assert r["dk_implied_phi"] < 0.34          # DK under-correlating
    assert r["ev"] > 0 and r["bet"] is True


def test_grade_quote_no_phi_for_unknown_pair():
    from edge.sgp import grade_quote
    quote = {"dk_decimal": 3.0, "legs": [
        {"type": "mystery_a", "taken_dec": 2.0, "opp_dec": 1.9},
        {"type": "mystery_b", "taken_dec": 2.0, "opp_dec": 1.9}]}
    assert grade_quote(quote, [])["verdict"] == "no_phi"
