from edge.nba import actual_points, norm, TEAM_NAME_TO_ABBR


def test_actual_points_hand_verified_triple_double():
    # Real 2024-25 game (Keaton Wallace): PTS 15, REB 11, AST 15, STL 5,
    # BLK 0, TOV 3, 3PM 1 -- independently cross-checked against
    # stats.nba.com's own FANTASY_PTS (62.7; the +0.55 delta here is DK's
    # own weights differing slightly from NBA.com's generic formula).
    st = {"PTS": 15, "REB": 11, "AST": 15, "STL": 5, "BLK": 0, "TOV": 3, "FG3M": 1}
    # 15 + 0.5*1 + 1.25*11 + 1.5*15 + 2*5 + 2*0 - 0.5*3 = 15+.5+13.75+22.5+10+0-1.5 = 60.25
    # 3 categories >=10 (PTS,REB,AST) -> triple-double bonus +3
    assert actual_points(st) == 63.25


def test_actual_points_double_double_bonus_not_triple():
    st = {"PTS": 20, "REB": 12, "AST": 3, "STL": 1, "BLK": 0, "TOV": 2, "FG3M": 2}
    base = 20 + 0.5 * 2 + 1.25 * 12 + 1.5 * 3 + 2 * 1 + 2 * 0 - 0.5 * 2
    assert actual_points(st) == round(base + 1.5, 2)  # double-double, not triple


def test_actual_points_no_bonus_below_two_double_digit_categories():
    st = {"PTS": 20, "REB": 5, "AST": 3, "STL": 1, "BLK": 0, "TOV": 2, "FG3M": 0}
    base = 20 + 1.25 * 5 + 1.5 * 3 + 2 * 1 - 0.5 * 2
    assert actual_points(st) == round(base, 2)  # only 1 category >=10 (PTS) -> no bonus


def test_actual_points_bonus_uses_the_five_dk_categories_not_just_pra():
    # DK's double-double covers {pts,reb,ast,blk,stl} -- confirm blocks/
    # steals count toward it, not just the traditional pts/reb/ast trio.
    st = {"PTS": 8, "REB": 5, "AST": 2, "STL": 10, "BLK": 10, "TOV": 1, "FG3M": 0}
    base = 8 + 1.25 * 5 + 1.5 * 2 + 2 * 10 + 2 * 10 - 0.5 * 1
    assert actual_points(st) == round(base + 1.5, 2)  # STL+BLK both >=10 -> double-double


def test_team_name_to_abbr_covers_all_30_teams():
    assert len(TEAM_NAME_TO_ABBR) == 30
    assert len(set(TEAM_NAME_TO_ABBR.values())) == 30


def test_norm_reexported_from_shared_module():
    from edge.names import norm as shared_norm
    assert norm is shared_norm
