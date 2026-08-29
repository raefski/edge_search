import math
import pytest
from edge.arb import oddsmath as om


def test_american_decimal_roundtrip():
    for a in (-500, -250, -110, -101, 100, 110, 250, 1200):
        assert om.decimal_to_american(om.american_to_decimal(a)) == pytest.approx(a, abs=1)


def test_minus_110_is_canonical():
    assert om.american_to_decimal(-110) == pytest.approx(1.9090909, rel=1e-6)
    assert om.implied_prob(om.american_to_decimal(-110)) == pytest.approx(0.5238, abs=1e-4)


def test_commission_reduces_only_net_winnings():
    assert om.net_of_commission(3.0, 0.02) == pytest.approx(2.96)
    assert om.net_of_commission(3.0, 0.0) == 3.0


@pytest.mark.parametrize("method", ["multiplicative", "additive", "power", "shin", "worst_case"])
def test_devig_methods_are_sane(method):
    probs = om.devig([0.5238, 0.5238], method=method)
    assert sum(probs) == pytest.approx(1.0, abs=1e-6) or method == "worst_case"
    assert all(0 < p < 1 for p in probs)
    # a symmetric market must devig to a coin flip under every method
    assert probs[0] == pytest.approx(probs[1], abs=1e-6)


def test_devig_asymmetric_keeps_ordering():
    fair = om.devig([0.70, 0.35], method="power")
    assert sum(fair) == pytest.approx(1.0, abs=1e-8)
    assert fair[0] > fair[1]


def test_additive_falls_back_when_it_would_go_negative():
    fair = om.devig([0.97, 0.09], method="additive")
    assert all(p > 0 for p in fair)


def test_arb_sum_and_allocation():
    ds = [2.10, 2.05]
    assert om.arb_sum(ds) < 1.0
    a = om.allocate(ds, bankroll=1000, round_to=1.0)
    assert a.total == pytest.approx(1000, abs=2)
    assert a.worst_profit > 0
    assert a.worst_profit_pct == pytest.approx(3.73, abs=0.05)
    # every leg must return roughly the same amount -- that is the whole point
    assert max(a.payouts) - min(a.payouts) < 2.0


def test_no_arb_returns_negative_worst_profit():
    a = om.allocate([1.91, 1.91], bankroll=1000, round_to=1.0)
    assert a.worst_profit < 0


def test_rounding_can_erase_a_thin_edge():
    thin = om.allocate([2.01, 2.01], bankroll=20, round_to=5.0)
    assert thin.worst_profit_pct <= 0.5


def test_book_limit_shrinks_total_not_balance():
    a = om.allocate([2.10, 2.05], bankroll=10_000, round_to=1.0, max_stakes=[500, None])
    assert a.capped
    assert a.stakes[0] <= 501
    assert a.worst_profit > 0
    assert max(a.payouts) - min(a.payouts) < 3.0


def test_anchor_sizes_second_leg_off_a_placed_first_leg():
    a = om.allocate([2.10, 2.05], bankroll=0, round_to=0.0, anchor=0, anchor_stake=100.0)
    assert a.stakes[0] == pytest.approx(100.0, abs=0.01)
    assert a.payouts[0] == pytest.approx(a.payouts[1], abs=0.5)


def test_ev_and_kelly():
    assert om.ev_pct(2.10, 0.50) == pytest.approx(5.0)
    assert om.ev_pct(1.90, 0.50) == pytest.approx(-5.0)
    assert om.kelly_fraction(2.10, 0.50) == pytest.approx(0.0454, abs=1e-3)
    assert om.kelly_fraction(1.90, 0.50) == 0.0


# --- parlays ---------------------------------------------------------------
def test_a_straight_parlay_is_the_product_of_its_legs():
    from edge.arb import oddsmath as om
    assert om.parlay_decimal([2.0, 3.0]) == pytest.approx(6.0)
    assert om.parlay_decimal([1.5, 1.5, 1.5]) == pytest.approx(3.375)
    assert om.parlay_decimal([]) == 1.0


def test_a_parlay_can_never_be_hedged_without_a_boost():
    """The whole point. Back a parlay, bet the opposite of each leg, and the
    test is 1/parlay + sum(1/opposite) < 1. For two fair legs that works out to
    1 + (1-p)(1-q), which exceeds 1 for any p and q below 1 -- the parlay's own
    compounding vig is exactly what makes it unhedgeable."""
    from edge.arb import oddsmath as om
    for p in (0.2, 0.5, 0.7, 0.9, 0.95):
        for q in (0.2, 0.5, 0.7, 0.9, 0.95):
            par = om.parlay_decimal([1 / p, 1 / q])
            s = om.parlay_hedge_sum(par, [1 / (1 - p), 1 / (1 - q)])
            assert s > 1.0, f"p={p} q={q} gave {s}"
            assert s == pytest.approx(1 + (1 - p) * (1 - q), abs=1e-9)


def test_a_boost_can_push_a_parlay_under_one():
    """Only on short-priced legs: a favourite has a long opposite and so a
    cheap hedge. Coin-flip legs cost about as much to hedge as they add."""
    from edge.arb import oddsmath as om
    def hedged(p, n, boost):
        par = om.parlay_decimal([1 / p] * n)
        return om.parlay_hedge_sum(om.boosted(par, boost), [1 / (1 - p)] * n)
    assert hedged(0.50, 2, 0.5) > 1.0      # coin flips: no
    assert hedged(0.70, 2, 0.5) < 1.0      # favourites: yes
    assert hedged(0.90, 3, 0.0) > 1.0      # no boost: never
    assert hedged(0.90, 3, 0.5) < 1.0


def test_the_hedge_test_is_just_arb_sum_over_the_parlay_and_the_opposites():
    """The N+1 binding outcomes -- all legs win, or exactly one loses -- behave
    like N+1 mutually exclusive legs, so this is the existing test, not a new
    one. Worth pinning: if they ever diverge, one of them is wrong."""
    from edge.arb import oddsmath as om
    par, opps = 2.5, [3.0, 4.0]
    assert om.parlay_hedge_sum(par, opps) == pytest.approx(om.arb_sum([par] + opps))


def test_every_losing_outcome_is_covered_by_the_hedges():
    """Simulate all 2^n settlements and check the position never loses. This is
    what the algebra claims; simulating it is what proves the claim covers the
    cases, including the several-legs-lose ones the derivation set aside as
    dominated."""
    from edge.arb import oddsmath as om
    import itertools as it
    # p=0.9 not 0.8: three legs at 0.8 with a 50% boost sums to 1.0116 and does
    # NOT arb. The margin is thin and the parameters matter, which is the point
    # of simulating rather than trusting the algebra to have been applied right.
    p = 0.9
    n = 3
    back, opp = 1 / p, 1 / (1 - p)
    par = om.parlay_decimal([back] * n)
    boosted = om.boosted(par, 0.5)
    legs = [boosted] + [opp] * n
    assert om.arb_sum(legs) < 1.0
    a = om.allocate(legs, bankroll=10_000.0, round_to=0.01)
    stake_par, stake_hedges = a.stakes[0], a.stakes[1:]
    for outcome in it.product([True, False], repeat=n):
        ret = stake_par * boosted if all(outcome) else 0.0
        ret += sum(s * opp for s, won in zip(stake_hedges, outcome) if not won)
        assert ret - a.total > -0.02, f"{outcome} lost {ret - a.total:.4f}"
