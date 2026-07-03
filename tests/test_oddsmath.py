import math

from edge.oddsmath import (
    american_to_decimal,
    decimal_to_american,
    devig,
    two_way_fair,
)


def approx(a, b, tol=1e-4):
    return abs(a - b) < tol


def test_american_decimal_roundtrip():
    assert approx(american_to_decimal(-110), 1.909090, 1e-5)
    assert approx(american_to_decimal(+100), 2.0)
    assert approx(american_to_decimal(+150), 2.5)
    assert decimal_to_american(2.0) == 100
    assert decimal_to_american(1.5) == -200


def test_multiplicative_symmetric():
    # -110 / -110 is a perfectly symmetric market -> 50/50 fair.
    d = american_to_decimal(-110)
    over, under = two_way_fair(d, d, method="multiplicative")
    assert approx(over, 0.5)
    assert approx(under, 0.5)


def test_multiplicative_hand_checked():
    # Over +100 (dec 2.0, q=0.5), Under -120 (dec 1.8333, q=0.545454)
    # sum q = 1.045454; fair_over = 0.5/1.045454 = 0.478261
    over, under = two_way_fair(2.0, american_to_decimal(-120), method="multiplicative")
    assert approx(over, 0.478261)
    assert approx(under, 0.521739)
    assert approx(over + under, 1.0)


def test_all_methods_are_distributions():
    d = [american_to_decimal(-150), american_to_decimal(+130)]
    for method in ("multiplicative", "power", "shin"):
        p = devig(d, method=method)
        assert approx(sum(p), 1.0), method
        assert all(0.0 < pi < 1.0 for pi in p), method


def test_methods_remove_vig():
    # Raw implied probs sum to >1; fair probs must sum to exactly 1 and each
    # fair prob must be below its raw implied prob (vig stripped out).
    d = [american_to_decimal(-110), american_to_decimal(-110)]
    raw = [1 / x for x in d]
    assert sum(raw) > 1.0
    for method in ("multiplicative", "power", "shin"):
        p = devig(d, method=method)
        assert approx(sum(p), 1.0)
        assert all(pi <= ri + 1e-9 for pi, ri in zip(p, raw)), method


def test_shin_shrinks_favourite_less_than_multiplicative():
    # On a lopsided market Shin should keep the favourite higher than the
    # plain multiplicative split (its defining property).
    d = [american_to_decimal(-400), american_to_decimal(+300)]
    mult = devig(d, method="multiplicative")
    shin = devig(d, method="shin")
    fav_idx = 0  # the -400 side
    assert shin[fav_idx] >= mult[fav_idx] - 1e-6
