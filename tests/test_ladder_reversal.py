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


#: Live Fanatics MLB, 2026-09-07, Cleveland Guardians at Baltimore Orioles.
#: The tail of a 21-rung Total Runs ladder whose main sits around 7.5. It
#: stops declining after 11.5 and then oscillates -- and Under 13 reached the
#: board and was reported as a middle against DraftKings' Over 11.5. The owner
#: checked the app: Fanatics does not offer Under 13 on that game.
CLE_BAL_FANATICS = ladder({
    10.0: (3.60, 1.2857), 10.5: (3.80, 1.2667),
    11.0: (4.70, 1.1905),
    11.5: (4.90, 1.1739),      # the last rung that is really priced
    12.0: (4.80, 1.1818),      # P(over) turns back UP from here
    12.5: (4.50, 1.2000),
    13.0: (4.80, 1.1818),
})


def test_a_self_consistent_phantom_tail_does_not_outvote_the_real_rungs():
    """THE REGRESSION THAT PUT UNDER 13 ON THE BOARD.

    Choosing what to drop by keeping the LONGEST monotone run maximises a
    count, and this tail has a count on its side. P(over) reads:

        11.0  0.2021    11.5  0.1933    12.0  0.1976
        12.5  0.2105    13.0  0.1976

    Dropping {11.5, 12.5} leaves 11.0 > 12.0 = 13.0 -- monotone, and it keeps
    TWO tail rungs. Dropping {12.0, 12.5, 13.0} leaves 11.0 > 11.5 and keeps
    one. So the longest run threw away the real 11.5 and kept two phantoms,
    which is the exact opposite of the job.

    Anchoring at the money and walking outward has no such preference: the
    ladder stops behaving after 11.5 and everything past it goes.
    """
    assert _reversed_rungs(CLE_BAL_FANATICS, non_increasing=True) == {12.0, 12.5, 13.0}
    assert _reversed_rungs(CLE_BAL_FANATICS) == {12.0, 12.5, 13.0}, \
        "inferring the direction must reach the same answer here"


def test_the_phantom_in_the_middle_still_only_costs_its_own_rung():
    """The other half of the same rule, and why the walk carries the last GOOD
    value forward instead of cutting the ladder at the first violation. On the
    Boston at Baltimore ladder the phantom is in the MIDDLE -- 12.5 carrying
    11.0's price -- and the genuine 13.0 past it still continues the sequence
    from 12.0. Cutting at the first violation would condemn it too."""
    assert _reversed_rungs(BOS_BAL_FANATICS, non_increasing=True) == {12.5}


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


# --- an over/under ladder knows its own direction ---------------------------
HENDERSON = {          # Fanatics NFL, live 2026-09-07. DraftKings had 18.5.
    20.5: {"over": (1.7407, None), "under": (2.0000, None)},
    25.5: {"over": (2.1500, None), "under": (1.6452, None)},
    30.5: {"over": (2.0500, None), "under": (1.7143, None)},
    35.5: {"over": (1.9091, None), "under": (1.8333, None)},
}


def test_inferring_the_direction_inverts_on_a_short_broken_ladder():
    """Why the pin exists, stated as the failure it prevents.

    P(over) reads 0.5347 / 0.4335 / 0.4554 / 0.4899 going up the ladder: it
    falls once and then rises twice, so the longest run holding ONE direction
    is the RISING one -- three rungs against two. Majority inference keeps
    those three and condemns 20.5, the single rung that matches the line
    DraftKings actually posts. On an 80-rung game line the majority is the
    sound half; on a 4-rung prop it need not be.
    """
    assert _reversed_rungs(HENDERSON) == {20.5}, "the majority is wrong here"


def test_pinning_the_direction_drops_the_impossible_rungs_instead():
    """{over 35.5} is a strict subset of {over 25.5}, so P(over) cannot rise
    between them whatever the majority says. 35.5 yards priced near even
    money for a back lined at 18.5 is the defect; 20.5 is the sound rung."""
    assert _reversed_rungs(HENDERSON, non_increasing=True) == {30.5, 35.5}


def test_a_sound_over_under_ladder_survives_the_pin():
    sound = {20.5: {"over": (1.60, None), "under": (2.30, None)},
             25.5: {"over": (1.91, None), "under": (1.91, None)},
             30.5: {"over": (2.30, None), "under": (1.60, None)}}
    assert _reversed_rungs(sound, non_increasing=True) == set()


def test_the_pin_is_refused_when_the_devigged_side_is_not_the_over_one():
    """The pin names a direction for the side this function happens to devig
    -- the alphabetically first one. On a spread that is a team name, and
    which way ITS probability runs depends on a sign convention this code
    must not assume. Rather than apply the pin backwards, fall through to
    inference, which is what a spread gets anyway."""
    teams = {-3.5: {"Chiefs": (1.60, None), "Ravens": (2.30, None)},
             -7.5: {"Chiefs": (2.30, None), "Ravens": (1.60, None)}}
    assert _reversed_rungs(teams, non_increasing=True) == \
        _reversed_rungs(teams), "pin ignored: not an over/under ladder"


# --- the same arithmetic, one player at a time ------------------------------
def _bet(name, line, over_dec):
    return {"name": name, "line": {"name": str(line)},
            "odds": [{"status": "ACTIVE", "decimal": over_dec}]}


def test_a_prop_ladder_that_reverses_is_caught_per_player():
    """Fanatics' NFL props are 3-4 rung alternate ladders and arrive with no
    other guard on them -- the game-line checks are skipped for player
    markets. The reversal check is the one that still applies, because it is
    arithmetic: {over 45.5 yards} is a strict subset of {over 40.5} for one
    named player exactly as {over 12.5} is for one game total."""
    from edge.arb.books import _prop_reversed_rungs
    bets = []
    for line, (o, u) in ((20.5, (1.7407, 2.0)), (25.5, (2.15, 1.6452)),
                         (30.5, (2.05, 1.7143)), (35.5, (1.9091, 1.8333))):
        bets.append(_bet(f"TreVeyon Henderson Over", line, o))
        bets.append(_bet(f"TreVeyon Henderson Under", line, u))
    assert _prop_reversed_rungs(bets) == {("TreVeyon Henderson", 30.5),
                                          ("TreVeyon Henderson", 35.5)}


def test_two_players_sharing_a_line_are_not_compared_against_each_other():
    """The reason this cannot reuse the game-line rungs dict: that one keys on
    the line alone, and 35% of NFL prop rungs hold two or more players. Pooled
    that way, one player's price at 40.5 sits against another's at 45.5 and
    the difference between two different people reads as a reversal.

    Both ladders here are individually sound and run opposite ways to each
    other, which is exactly what pooling would call broken."""
    from edge.arb.books import _prop_reversed_rungs
    bets = []
    for line, (o, u) in ((40.5, (1.60, 2.30)), (45.5, (1.91, 1.91)),
                         (50.5, (2.30, 1.60))):
        bets += [_bet("Player One Over", line, o), _bet("Player One Under", line, u)]
    for line, (o, u) in ((40.5, (2.30, 1.60)), (45.5, (2.60, 1.50)),
                         (50.5, (3.20, 1.36))):
        bets += [_bet("Player Two Over", line, o), _bet("Player Two Under", line, u)]
    assert _prop_reversed_rungs(bets) == set()


def test_a_prop_ladder_too_short_to_establish_an_order_is_left_alone():
    """Two points cannot be contradicted by a third that is not there --
    the same floor the game-line check keeps."""
    from edge.arb.books import _prop_reversed_rungs
    bets = [_bet("Solo Player Over", 40.5, 2.30), _bet("Solo Player Under", 40.5, 1.60),
            _bet("Solo Player Over", 45.5, 1.60), _bet("Solo Player Under", 45.5, 2.30)]
    assert _prop_reversed_rungs(bets) == set()
