from edge.nfl import actual_offense_points, actual_dst_points, norm, TEAM_NAME_TO_ABBR


def test_actual_offense_points_hand_verified():
    # Ja'Marr Chase, real 2024 week 10 game: 264 rec yds, 3 rec TDs, 11 rec,
    # 0 pass/rush -- verified directly against nflverse (which independently
    # reported fantasy_points_ppr=55.4; the +3.0 delta here is exactly DK's
    # 100+ receiving yard bonus that standard PPR scoring doesn't have).
    st = {"passing_yards": 0, "passing_tds": 0, "passing_interceptions": 0,
          "rushing_yards": 0, "rushing_tds": 0,
          "receiving_yards": 264, "receiving_tds": 3, "receptions": 11}
    # 0.1*264 + 6*3 + 1*11 = 26.4 + 18 + 11 = 55.4, +3 bonus (100+ rec yds) = 58.4
    assert actual_offense_points(st) == 58.4


def test_actual_offense_points_stacks_multiple_yardage_bonuses():
    # A player can clear BOTH the 100-rush and 100-rec bonus in one game
    # (rare, but real -- e.g. a receiving back) -- each bonus is independent.
    st = {"rushing_yards": 110, "receiving_yards": 105, "receptions": 4}
    base = 0.1 * 110 + 0.1 * 105
    assert actual_offense_points(st) == round(base + 4 * 1 + 3 + 3, 2)


def test_actual_offense_points_penalizes_turnovers():
    base = actual_offense_points({"passing_yards": 200})
    with_int = actual_offense_points({"passing_yards": 200, "passing_interceptions": 1})
    with_fumble = actual_offense_points({"passing_yards": 200, "rushing_fumbles_lost": 1})
    assert with_int == round(base - 1, 2)
    assert with_fumble == round(base - 1, 2)


def test_actual_dst_points_matches_hand_calc():
    # 3 sacks, 1 INT, 1 fumble recovery, 1 defensive TD, opponent scored 10
    # (falls in the 7-13 tier, +4): 3*1 + 1*2 + 1*2 + 1*6 + 4 = 3+2+2+6+4=17
    st = {"def_sacks": 3, "def_interceptions": 1, "fumble_recovery_opp": 1,
          "def_tds": 1, "special_teams_tds": 0, "def_safeties": 0}
    assert actual_dst_points(st, points_allowed=10) == 17.0


def test_actual_dst_points_allowed_tiers_in_order():
    st = {}  # no defensive plays, isolate the points-allowed tier itself
    assert actual_dst_points(st, 0) == 10
    assert actual_dst_points(st, 6) == 7
    assert actual_dst_points(st, 7) == 4
    assert actual_dst_points(st, 13) == 4
    assert actual_dst_points(st, 14) == 1
    assert actual_dst_points(st, 27) == 0
    assert actual_dst_points(st, 28) == -1
    assert actual_dst_points(st, 34) == -1
    assert actual_dst_points(st, 35) == -4
    assert actual_dst_points(st, 50) == -4


def test_team_name_to_abbr_covers_all_32_teams():
    assert len(TEAM_NAME_TO_ABBR) == 32
    assert len(set(TEAM_NAME_TO_ABBR.values())) == 32  # no two teams collide on abbr


def test_norm_reexported_from_shared_module():
    # edge/nfl.py re-exports edge.names.norm rather than defining its own --
    # regression guard against silently drifting back to a local copy.
    from edge.names import norm as shared_norm
    assert norm is shared_norm
