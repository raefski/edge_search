"""The milestone-ladder engine, edge/dfs_ladder.py.

The centrepiece is test_round_trip_recovers_a_known_mean: build a ladder FROM a
distribution whose mean is known in closed form, price every rung with a known
overround, and require the estimator to get the mean back. That is a real test
of the whole chain -- devig, isotonic, fit, integrate -- rather than a check
that the code runs.
"""
from __future__ import annotations

import math
from statistics import NormalDist

import pytest

from edge import dfs_ladder as L

_N = NormalDist()


def _price(p: float, overround: float) -> float:
    """Decimal odds a book would post for a true probability p."""
    return 1.0 / (p * (1.0 + overround))


def _lognormal_ladder(mu, sigma, thresholds, overround=0.0, mass=1.0):
    out = []
    for t in thresholds:
        p = mass * _N.cdf((mu - math.log(t)) / sigma)
        if 1e-4 < p < 0.999:
            out.append((float(t), _price(p, overround)))
    return out


# ---------------------------------------------------------------------------
# monotonicity
# ---------------------------------------------------------------------------
def test_pava_leaves_a_monotone_sequence_alone():
    assert L._pava([0.9, 0.7, 0.5, 0.2]) == pytest.approx([0.9, 0.7, 0.5, 0.2])


def test_pava_pools_an_inversion_into_its_mean():
    """Two adjacent rungs priced out of order are replaced by their mean.

    A real board does this: adjacent rungs are quoted by different market
    makers moments apart, and a stale one inverts the pair. Left alone the
    inversion is a NEGATIVE bucket mass, which is not a small error but an
    impossible one, and it can drag the fitted curve anywhere.
    """
    out = L._pava([0.8, 0.4, 0.6, 0.2])
    assert out == pytest.approx([0.8, 0.5, 0.5, 0.2])
    assert all(a >= b - 1e-12 for a, b in zip(out, out[1:]))


def test_survival_is_monotone_even_for_a_scrambled_ladder():
    rungs = [(10, 1.2), (20, 1.1), (30, 2.0), (40, 1.9), (50, 4.0)]
    pts = L.survival(rungs)
    probs = [p for _, p in pts]
    assert all(a >= b - 1e-12 for a, b in zip(probs, probs[1:]))


# ---------------------------------------------------------------------------
# the devig
# ---------------------------------------------------------------------------
def test_overround_pushes_the_projection_down():
    """More assumed vig => less implied mean. The sign is the whole point.

    edge/dfs_ladder's docstring argues that a ladder CANNOT devig itself
    because the bucket masses telescope to 1 by algebra. This is the test that
    the correction is actually applied and applied the right way round.
    """
    rungs = _lognormal_ladder(4.0, 0.8, range(20, 200, 10), overround=0.0)
    lo = L.mean_continuous(rungs, overround=0.0)["mean"]
    hi = L.mean_continuous(rungs, overround=0.15)["mean"]
    assert hi < lo


def test_round_trip_recovers_a_known_mean():
    """Price a ladder off a known lognormal, then read the mean back out.

    E[X] for LogNormal(mu, sigma) is exp(mu + sigma^2/2). With mu=4.0 and
    sigma=0.8 that is 75.2. The estimator sees only decimal odds.
    """
    mu, sigma = 4.0, 0.8
    truth = math.exp(mu + sigma * sigma / 2.0)
    overround = 0.08
    rungs = _lognormal_ladder(mu, sigma, range(15, 260, 10), overround=overround)
    got = L.mean_continuous(rungs, overround=overround)["mean"]
    assert got == pytest.approx(truth, rel=0.06), f"{got} vs {truth}"


def test_round_trip_survives_a_ladder_that_starts_high():
    """The head is the exposed part, so test a ladder with a big unpriced head.

    Starting the rungs at 60 on a distribution whose mean is 75 leaves most of
    the head unpriced. The answer should still land, and `band` should widen to
    say so.
    """
    mu, sigma = 4.0, 0.8
    truth = math.exp(mu + sigma * sigma / 2.0)
    deep = L.mean_continuous(_lognormal_ladder(mu, sigma, range(10, 260, 10)), 0.0)
    shallow = L.mean_continuous(_lognormal_ladder(mu, sigma, range(60, 260, 10)), 0.0)
    assert deep["mean"] == pytest.approx(truth, rel=0.06)
    assert shallow["mean"] == pytest.approx(truth, rel=0.15)
    assert shallow["band"] > deep["band"]


# ---------------------------------------------------------------------------
# counts: the exact identity
# ---------------------------------------------------------------------------
def test_count_mean_is_the_exact_sum_when_the_ladder_covers_everything():
    """E[N] = sum over k>=1 of P(N>=k), exactly, when every k is priced.

    A ladder that starts at 1+ and runs past the support leaves nothing to
    model: `modelled` must be ~0 and the mean must be the posted sum.
    """
    survivals = {1: 0.90, 2: 0.65, 3: 0.40, 4: 0.20, 5: 0.08, 6: 0.02}
    rungs = [(k, _price(p, 0.0)) for k, p in survivals.items()]
    res = L.mean_count(rungs, overround=0.0)
    assert res["priced"] == pytest.approx(sum(survivals.values()), rel=1e-6)
    assert res["modelled"] < 0.05
    assert res["mean"] == pytest.approx(sum(survivals.values()), abs=0.05)
    assert res["band"] == 0.0        # nothing below the lowest rung to assume


def test_count_band_widens_when_the_ladder_starts_late():
    """A receptions ladder starting at 4+ cannot see P(N>=1..3).

    The band must be exactly (k_lo - 1) * (1 - P(N >= k_lo)) -- a fact about
    the posted prices, not an output of the fit.
    """
    res = L.mean_count([(4, _price(0.70, 0.0)), (5, _price(0.45, 0.0)),
                        (6, _price(0.25, 0.0)), (7, _price(0.10, 0.0))],
                       overround=0.0)
    assert res["band"] == pytest.approx(3 * (1 - 0.70), abs=1e-6)
    # and the mean must sit inside its own bracket
    assert res["priced"] + 3 * 0.70 <= res["mean"] + 1e-9
    assert res["mean"] <= res["priced"] + 3 * 1.0 + 0.5


def test_count_estimator_never_goes_non_monotone_on_a_bad_fit():
    """A two-rung ladder has no degrees of freedom for the fit to use.

    fit_lognormal returns None below three rungs, and the head terms must then
    fall back to the clamped neighbour rather than to whatever a degenerate fit
    produced. The mean must still be finite and at least the posted sum.
    """
    res = L.mean_count([(3, _price(0.5, 0.0)), (4, _price(0.3, 0.0))], overround=0.0)
    assert res["mean"] is not None and math.isfinite(res["mean"])
    assert res["mean"] >= res["priced"]


def test_project_market_dispatches_on_the_kind_of_stat():
    """A count goes to the sum, a yardage goes to the integral.

    Worth a test because getting it backwards produces a PLAUSIBLE number
    rather than an error, and only in one direction. On a receptions ladder
    (4+..7+) the two estimators differ by about 24% -- wrong, but not obviously
    so. On a yardage ladder they differ by about 3x -- a ladder whose true mean
    is 75 reads as 24 through the count estimator -- because summing survival
    over thresholds 20,30,...,200 counts each rung once instead of weighting it
    by the 10 yards it spans.
    """
    rec = [(4, _price(0.70, 0.0)), (5, _price(0.45, 0.0)),
           (6, _price(0.25, 0.0)), (7, _price(0.10, 0.0))]
    assert (L.project_market(rec, "player_receptions")["mean"]
            == pytest.approx(L.mean_count(
                rec, L.LADDER_OVERROUND["player_receptions"],
                L.HEAD_FILL["player_receptions"],
                market_hint="player_receptions")["mean"]))

    yards = _lognormal_ladder(4.0, 0.8, range(20, 210, 10))
    assert (L.project_market(yards, "player_rush_yds")["mean"]
            == pytest.approx(L.mean_continuous(
                yards, L.LADDER_OVERROUND["player_rush_yds"],
                L.HEAD_FILL["player_rush_yds"],
                L.TAIL_EXCESS["player_rush_yds"])["mean"]))

    as_yards = L.mean_continuous(yards, overround=0.0)["mean"]
    as_count = L.mean_count(yards, overround=0.0)["mean"]
    assert as_yards > 2.5 * as_count, "the two estimators must not be interchangeable"


# ---------------------------------------------------------------------------
# thresholds -- what the NFL path cannot do honestly
# ---------------------------------------------------------------------------
def test_exceed_returns_the_posted_price_at_a_posted_rung():
    """No distribution is assumed when DK prices the bonus threshold outright.

    This is the claim in the module docstring: edge/dfs_sport.py records that
    scoring the NFL 100-yard bonus off a fitted normal under-predicts the real
    tail by 1.3-1.7 points of probability. A ladder with a 100+ rung simply
    reads it off.
    """
    rungs = [(50, _price(0.80, 0.0)), (100, _price(0.42, 0.0)),
             (150, _price(0.15, 0.0))]
    assert L.exceed(rungs, 100, overround=0.0) == pytest.approx(0.42, rel=1e-6)


def test_exceed_interpolates_between_rungs_and_stays_bracketed():
    rungs = [(50, _price(0.80, 0.0)), (100, _price(0.40, 0.0))]
    got = L.exceed(rungs, 75, overround=0.0)
    assert 0.40 < got < 0.80


def test_exceed_beyond_the_top_rung_never_exceeds_it():
    rungs = [(50, _price(0.80, 0.0)), (100, _price(0.40, 0.0)),
             (150, _price(0.12, 0.0))]
    assert L.exceed(rungs, 200, overround=0.0) <= 0.12 + 1e-9


# ---------------------------------------------------------------------------
# reading DK's own shape
# ---------------------------------------------------------------------------
def test_half_points_are_folded_back_to_inclusive_thresholds():
    """DK's '50+' is stored as Over 49.5 so it can meet another book's line.

    The ladder arithmetic wants the inclusive integer back: P(X >= 50) and
    P(X > 49.5) are the same number, and it is the integer the count identity
    sums over.
    """
    outcomes = [{"name": "Over", "point": 49.5, "price": 2.0, "description": "A"},
                {"name": "Over", "point": 3.5, "price": 1.5, "description": "A"}]
    got = dict(L.rungs_from_outcomes(outcomes, "A"))
    assert 50.0 in got and 4.0 in got


def test_other_players_and_non_over_sides_are_ignored():
    outcomes = [{"name": "Over", "point": 50, "price": 2.0, "description": "A"},
                {"name": "Over", "point": 60, "price": 3.0, "description": "B"},
                {"name": "Under", "point": 50, "price": 1.8, "description": "A"}]
    assert L.rungs_from_outcomes(outcomes, "A") == [(50.0, 2.0)]


def test_a_repeated_threshold_is_deterministic():
    outcomes = [{"name": "Over", "point": 50, "price": 2.4, "description": "A"},
                {"name": "Over", "point": 50, "price": 2.0, "description": "A"}]
    assert L.rungs_from_outcomes(outcomes, "A") == [(50.0, 2.0)]


# ---------------------------------------------------------------------------
# degenerate input must degrade, not explode
# ---------------------------------------------------------------------------
def test_an_empty_ladder_projects_nothing_rather_than_zero():
    """None is the signal to leave the player out of the pool entirely.

    Zero would put him on the board at the cheapest salary with the worst
    projection, which is a different and much worse failure -- the same
    contract dfs_project.project has always had.
    """
    assert L.project_market([], "player_receptions")["mean"] is None
    assert L.project_market([], "player_rush_yds")["mean"] is None


def test_a_single_rung_still_returns_a_finite_number():
    res = L.project_market([(50, 2.0)], "player_rush_yds")
    assert res["mean"] is not None and math.isfinite(res["mean"])
    assert res["fit"] is None            # one rung cannot determine a curve


def test_a_certainty_priced_rung_cannot_produce_an_infinite_mean():
    """1.001 decimal is ~100%; an unclamped probit of 1.0 is +inf.

    dfs_project guards the same way and for the same reason: one mispriced
    rung would otherwise build the entire lineup around that player.
    """
    res = L.mean_continuous([(10, 1.0001), (20, 1.0001), (30, 1.0001)], 0.0)
    assert math.isfinite(res["mean"])


# ---------------------------------------------------------------------------
# regressions: two bugs that produced plausible numbers rather than errors
# ---------------------------------------------------------------------------
def test_a_flat_top_pair_cannot_run_the_count_tail_away():
    """Equal probabilities on the top two rungs must not invent a touchdown.

    The first version of mean_count decayed the tail geometrically at the rate
    the last two rungs implied, clamped to a 0.9 ceiling. A book really does
    post two rungs at the same price, and the isotonic step above produces one
    whenever it pools an inversion -- and then the ratio is 1.0, clamps to 0.9,
    and compounds: 0.083 * 0.9 / (1 - 0.9) = 0.75 of a touchdown from nothing.
    Graded against real distributions that was +33.7% on passing touchdowns.

    The measured mean excess above the top rung is 0.000 for passing
    touchdowns and 0.062 for receptions, so the tail here must be tiny.
    """
    flat = [(1, _price(0.25, 0.0)), (2, _price(0.083, 0.0)), (3, _price(0.083, 0.0))]
    res = L.mean_count(flat, overround=0.0, market_hint="player_pass_tds")
    assert res["modelled"] < 0.05, res
    assert res["mean"] == pytest.approx(res["priced"], abs=0.05)


def test_head_fill_interpolates_between_the_two_hard_bounds():
    """c=0 is the rectangle, c=1 is the box, and both are real bounds.

    Monotonicity guarantees the true head lies between t_min*S(t_min) and
    t_min, whatever the distribution. This is the test that HEAD_FILL is a
    position inside that bracket rather than a free multiplier that could
    leave it.
    """
    rungs = _lognormal_ladder(4.0, 0.8, range(40, 210, 10))
    t_min = 40.0
    p_min = 1.0 / rungs[0][1]

    lo = L.mean_continuous(rungs, 0.0, head_fill=0.0, tail_excess=0.0)
    hi = L.mean_continuous(rungs, 0.0, head_fill=1.0, tail_excess=0.0)
    mid = L.mean_continuous(rungs, 0.0, head_fill=0.5, tail_excess=0.0)

    assert lo["head"] == pytest.approx(t_min * p_min, rel=1e-9)
    assert hi["head"] == pytest.approx(t_min, rel=1e-9)
    assert lo["head"] < mid["head"] < hi["head"]
    assert hi["mean"] - lo["mean"] == pytest.approx(lo["band"], rel=1e-9)


def test_the_priced_region_is_untouched_by_either_constant():
    """Only the head and tail are modelled; the ladder's own span is data.

    If a change to HEAD_FILL or TAIL_EXCESS moved `priced`, the module would
    have stopped doing the thing it exists to do.
    """
    rungs = _lognormal_ladder(4.0, 0.8, range(20, 210, 10))
    a = L.mean_continuous(rungs, 0.0, head_fill=0.1, tail_excess=0.1)
    b = L.mean_continuous(rungs, 0.0, head_fill=0.9, tail_excess=0.9)
    assert a["priced"] == pytest.approx(b["priced"], rel=1e-12)
