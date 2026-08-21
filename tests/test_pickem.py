from edge.pickem import (
    ats_result, favorite_flipped, key_number_crossed, make_pick, win_prob,
)


def test_win_prob_matches_normal_approx_hand_calc():
    # Phi(3.0/13.45): erf(0.2230/sqrt(2)) = erf(0.1577) ~ 0.1765 (Taylor approx)
    # -> 0.5*(1+0.1765) = 0.588 -- matches the real Week 1 Bills/Texans read.
    assert abs(win_prob(3.0) - 0.588) < 0.001


def test_win_prob_symmetric_in_sign_and_monotonic():
    assert win_prob(2.0) == win_prob(-2.0)
    assert win_prob(0.0) == 0.5
    assert win_prob(1.0) < win_prob(2.0) < win_prob(4.0)


def test_key_number_crossed_only_when_strictly_between():
    assert key_number_crossed(-2.5, -4.5) is True    # crosses -3
    assert key_number_crossed(-2.5, -2.5) is False   # no movement
    assert key_number_crossed(-9.5, -10.5) is False  # moves but crosses nothing
    assert key_number_crossed(-6.5, -7.5) is True    # crosses -7


def test_favorite_flipped_requires_opposite_signs():
    assert favorite_flipped(-1.5, 1.5) is True
    assert favorite_flipped(-1.5, -4.5) is False
    assert favorite_flipped(0, 3.5) is False  # pick-em open, not a real flip


def test_make_pick_flipped_favorite_is_always_strong():
    # Real Week 1 case: CBS froze Texans -1.5, market flipped to Bills -1.5.
    p = make_pick('Bills', 'Texans', pool_line=-1.5, live_line=1.5)
    assert p.flipped is True
    assert p.tier == 'STRONG'
    assert p.side == 'away'          # Bills, the road team, now favored
    assert p.side_line == 1.5        # Bills get the points CBS still has them at
    assert abs(p.prob - 0.588) < 0.001


def test_make_pick_below_move_floor_is_coin_flip_and_defaults_to_market_favorite():
    p = make_pick('Bengals', 'Browns', pool_line=5.5, live_line=5.5)
    assert p.tier == 'COIN FLIP'
    assert p.side == 'away'          # Bengals -5.5 live, i.e. the market favorite
    assert p.prob == 0.5             # no validated edge below MOVE_FLOOR -- don't overclaim


def test_make_pick_tier_thresholds():
    assert make_pick('A', 'B', 0, -0.4).tier == 'COIN FLIP'
    assert make_pick('A', 'B', 0, -0.5).tier == 'LEAN'
    assert make_pick('A', 'B', 0, -1.5).tier == 'SOLID'
    assert make_pick('A', 'B', 0, -3.0).tier == 'STRONG'


def test_ats_result_push_and_both_sides():
    assert ats_result(home_margin=3, pool_line=-3, side='home') == 'P'
    assert ats_result(home_margin=4, pool_line=-3, side='home') == 'W'
    assert ats_result(home_margin=2, pool_line=-3, side='home') == 'L'
    assert ats_result(home_margin=2, pool_line=-3, side='away') == 'W'
