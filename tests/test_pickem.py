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


# --- key-number bonus (added 2026-08-22) -----------------------------------

def test_effective_edge_applies_the_configured_key_bonus():
    from edge.pickem import KEY_BONUS, effective_edge
    # -2.5 -> -3.5 passes through 3, so it earns whatever the bonus is set to
    assert effective_edge(-2.5, -3.5) == 1.0 + KEY_BONUS
    # -8.0 -> -10.0 is a bigger move but crosses neither 3 nor 7
    assert effective_edge(-8.0, -10.0) == 2.0
    # landing exactly ON 3 is not crossing it
    assert effective_edge(-2.5, -3.0) == 0.5


def test_key_bonus_ships_disabled_pending_clean_validation():
    """Guards a deliberate decision, not an implementation detail: the
    dev-fitted 3.6 overshot the holdout and made calibration worse. If this
    is ever re-enabled it must be re-validated -- see PICKEM_MODEL.md 5d."""
    from edge.pickem import KEY_BONUS
    assert KEY_BONUS == 0.0


def test_effective_edge_ignores_sub_floor_moves():
    # a move below MOVE_FLOOR carries no signal, so it earns no bonus even
    # if the two lines happen to straddle a key number
    from edge.pickem import effective_edge
    assert effective_edge(-3.0, -3.0) == 0.0


def test_key_number_flag_still_reported_even_with_the_bonus_off():
    # the effect is real and we keep surfacing it, we just don't price it
    crossing = make_pick('A', 'B', -2.5, -3.5)
    plain = make_pick('A', 'B', -8.0, -10.0)
    assert crossing.key_number is True
    assert plain.key_number is False


# --- totals-drift coin-flip tiebreak ---------------------------------------

def test_coinflip_without_totals_keeps_the_old_market_favorite_behaviour():
    p = make_pick('A', 'B', -3.0, -3.0)
    assert p.tier == 'COIN FLIP'
    assert p.side == 'home'          # home is the live favorite


def test_coinflip_falling_total_takes_the_underdog():
    # lower-scoring game compresses margins -> helps whoever gets points
    p = make_pick('A', 'B', -3.0, -3.0, total_open=47.0, total_close=44.0)
    assert p.side == 'away'


def test_coinflip_rising_total_takes_the_favorite():
    p = make_pick('A', 'B', -3.0, -3.0, total_open=47.0, total_close=49.0)
    assert p.side == 'home'


def test_coinflip_ignores_totals_drift_below_the_floor():
    # the floor is inclusive -- exactly 0.5 counts, so use a smaller drift
    p = make_pick('A', 'B', -3.0, -3.0, total_open=47.0, total_close=46.75)
    assert p.side == 'home'


def test_coinflip_totals_floor_is_inclusive():
    # guards the boundary the backtest actually validated (abs(drift) >= 0.5)
    p = make_pick('A', 'B', -3.0, -3.0, total_open=47.0, total_close=46.5)
    assert p.side == 'away'


def test_totals_do_not_affect_games_that_actually_moved():
    # the tiebreak is for no-movement games only; a real move still wins
    a = make_pick('A', 'B', -3.0, -5.0)
    b = make_pick('A', 'B', -3.0, -5.0, total_open=50.0, total_close=40.0)
    assert a.side == b.side == 'home'
