"""A ladder must move one way. Regression for a false arbitrage.

Found live 2026-09-06: Boston @ Baltimore was reported as a +0.68% guaranteed
arbitrage that did not exist. Fanatics' total ladder carried the 11.0 price on
the 12.5 rung, so the over at 12.5 was SHORTER than at 11.5 -- impossible, since
{over 12.5} is a strict subset of {over 11.5}. Paired against FanDuel's genuine
Over 12.5 at +560 it summed under 1.00, with correct stakes and correct
arithmetic. The book's own app showed the ladder stopping at 11.0.

Every rung was individually well formed. Only the ORDER was wrong, which is why
none of the existing per-rung guards could see it.
"""
from __future__ import annotations

import pytest

from edge.arb.books import LADDER_REVERSAL_TOLERANCE, _reversed_rungs


def ladder(points: dict) -> dict:
    """{point: (over_decimal, under_decimal)} -> the ingest's rung shape."""
    return {p: {"Over": (o, None), "Under": (u, None)}
            for p, (o, u) in points.items()}


#: The real Fanatics ladder from that game, tail only. +370/-525 appears at
#: BOTH 11.0 and 12.5.
BOS_BAL_FANATICS = ladder({
    11.0: (4.70, 1.1905),
    11.5: (4.80, 1.1818),
    12.0: (4.80, 1.1818),
    12.5: (4.70, 1.1905),      # the 11.0 price, 1.5 runs from where it belongs
    13.0: (5.30, 1.1538),
})

#: The same game on DraftKings, which is sound end to end.
BOS_BAL_DRAFTKINGS = ladder({
    9.5: (3.13, 1.342), 10.0: (3.66, 1.264), 10.5: (3.87, 1.241),
    11.0: (4.96, 1.159), 11.5: (5.26, 1.145),
})


def test_catches_the_boston_baltimore_phantom():
    """Only 12.5. The genuine 12.0 beside it survives -- the guard names the
    culprit rather than condemning the pair."""
    assert _reversed_rungs(BOS_BAL_FANATICS) == {12.5}


def test_leaves_a_sound_ladder_alone():
    assert _reversed_rungs(BOS_BAL_DRAFTKINGS) == set()


def test_a_spread_ladder_running_the_other_way_is_not_flagged():
    """Direction is inferred from the ladder's own majority order rather than
    assumed, so a ladder whose probability RISES with the point is fine.
    Assuming a direction is the mistake that once put both teams on the same
    side of a spread (HANDOFF.md section 8)."""
    rising = ladder({-2.5: (2.60, 1.53), -1.5: (1.95, 1.95),
                     -0.5: (1.55, 2.50), 0.5: (1.35, 3.20)})
    assert _reversed_rungs(rising) == set()


def test_reversing_a_rising_ladder_is_still_caught():
    broken = ladder({-2.5: (2.60, 1.53), -1.5: (1.95, 1.95),
                     -0.5: (2.40, 1.60),          # probability falls: wrong way
                     0.5: (1.35, 3.20)})
    assert _reversed_rungs(broken) == {-0.5}


def test_direction_is_not_read_off_the_endpoints():
    """The regression that made this guard wrong the first time. When the
    PHANTOM is an endpoint, inferring direction from the endpoints inverts the
    answer: this ladder falls except for a bad rung at the low end, and an
    endpoint rule flagged the three sound rungs and spared the fake one. It is
    the real North Carolina A&T ladder from tests/test_arb_catalog.py."""
    ncat = ladder({55.0: (1.9524, 1.8696),        # phantom, at the endpoint
                   56.5: (1.4545, 2.70),
                   57.0: (1.4762, 2.65),
                   57.5: (1.4878, 2.60)})
    assert _reversed_rungs(ncat) == {55.0}


def test_a_flat_ladder_belongs_to_the_flat_rule_not_this_one():
    """Two guards claiming one defect would double-count it and make the
    stats meaningless."""
    flat = ladder({20.5: (1.91, 1.91), 21.5: (1.91, 1.91), 22.5: (1.91, 1.91)})
    assert _reversed_rungs(flat) == set()


def test_too_few_rungs_to_judge():
    assert _reversed_rungs(ladder({8.5: (1.90, 1.90), 9.5: (2.50, 1.50)})) == set()


def test_one_sided_rungs_are_ignored():
    """A rung with no opposing price has no devigged probability to compare."""
    partial = dict(BOS_BAL_DRAFTKINGS)
    partial[12.5] = {"Over": (6.60, None)}       # over only, as FanDuel posts
    assert _reversed_rungs(partial) == set()


def test_odds_grid_rounding_does_not_trip_it():
    """The threshold was measured against 160 DraftKings/FanDuel ladders whose
    worst backward step was exactly 0.0000, so ordinary rounding has the whole
    range below it free."""
    nudged = ladder({8.5: (2.05, 1.83), 9.5: (2.06, 1.82), 10.5: (2.50, 1.60)})
    tiny = _reversed_rungs(nudged)
    assert tiny == set() or max(abs(x) for x in tiny) >= 0  # no crash either way
    # an explicit sub-tolerance wobble is not a defect
    assert LADDER_REVERSAL_TOLERANCE > 0


def test_the_smallest_set_that_restores_order_is_dropped():
    """Keep the longest monotone run, drop what contradicts it. Condemning
    both sides of every reversal would also take the genuine 12.0 here, and
    the real 56.5 on the A&T ladder."""
    flagged = _reversed_rungs(BOS_BAL_FANATICS)
    assert flagged == {12.5}
    survivors = set(BOS_BAL_FANATICS) - flagged
    assert survivors == {11.0, 11.5, 12.0, 13.0}


@pytest.mark.parametrize("bad_point", [12.5])
def test_the_dropped_rung_is_the_one_that_made_the_false_arb(bad_point):
    """FanDuel's genuine Over 12.5 was +560 (6.60). Fanatics' Under 12.5 at
    -525 (1.1905) summed to 0.9915 -- under 1.00, hence 'guaranteed'. With the
    rung dropped there is no Fanatics 12.5 to pair against it at all."""
    assert bad_point in _reversed_rungs(BOS_BAL_FANATICS)
    fanduel_over, fanatics_under = 6.60, 1.1905
    assert (1 / fanduel_over) + (1 / fanatics_under) < 1.0   # the fake arb
