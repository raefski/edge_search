"""The generic projection engine, and the proof it did not change MLB.

THE LOAD-BEARING TEST IS test_matches_project_pitcher. edge/dfs.py's
project_pitcher is backtested code with real money behind it; edge/dfs_project
claims to be the same arithmetic with the constants moved into a table. The
only honest way to make that claim is to run both over the same payloads and
require the answers to agree, which is what these do. project_pitcher is
deliberately NOT deleted -- it stays as the reference the generalisation is
measured against.
"""
from __future__ import annotations

import pytest

from edge import dfs, dfs_project
from edge.dfs_sport import MLB_PITCHER, NFL, get


def pm(**markets) -> dict:
    """Build a player_markets dict the way edge/dfs.player_markets does."""
    out = {}
    for key, spec in markets.items():
        market = key.replace("__", ".")
        out[market] = spec
    return out


# --- the equivalence proof --------------------------------------------------

FULL = {
    "pitcher_outs": {"Over": 1.90, "Under": 1.90, "point": 17.5},
    "pitcher_strikeouts": {"Over": 1.95, "Under": 1.87, "point": 6.5},
    "pitcher_earned_runs": {"Over": 1.85, "Under": 1.95, "point": 2.5},
    "pitcher_hits_allowed": {"Over": 1.91, "Under": 1.91, "point": 5.5},
    "pitcher_walks": {"Over": 2.05, "Under": 1.78, "point": 1.5},
    "pitcher_record_a_win": {"Yes": 2.10, "No": 1.75},
}
CORE_ONLY = {k: FULL[k] for k in ("pitcher_outs", "pitcher_strikeouts")}
ACE = {
    "pitcher_outs": {"Over": 1.88, "Under": 1.94, "point": 18.5},
    "pitcher_strikeouts": {"Over": 1.90, "Under": 1.92, "point": 9.5},
}


@pytest.mark.parametrize("markets", [FULL, CORE_ONLY, ACE],
                         ids=["all-markets", "core-only", "ace-imputed"])
def test_matches_project_pitcher(markets):
    """The generic engine must reproduce the backtested MLB projection."""
    reference = dfs.project_pitcher(markets)
    generic = dfs_project.project(markets, MLB_PITCHER, require_all=True)
    assert generic["proj"] == pytest.approx(reference["proj"], abs=0.15), (
        f"generic {generic['proj']} vs reference {reference['proj']}\n"
        f"  generic components: {generic['components']}\n"
        f"  reference components: {reference['components']}")


@pytest.mark.parametrize("markets", [FULL, CORE_ONLY, ACE])
def test_imputes_the_same_components_as_the_reference(markets):
    reference = dfs.project_pitcher(markets)
    generic = dfs_project.project(markets, MLB_PITCHER, require_all=True)
    # Reference labels them ER/hit/bb/win; the Sport table uses its own names.
    assert bool(reference["imputed"]) == bool(generic["imputed"])
    assert len(reference["imputed"]) == len(generic["imputed"])


def test_missing_a_required_market_projects_nobody():
    """The contract that keeps a half-priced pitcher out of the pool entirely,
    rather than in it with a quietly wrong number."""
    assert dfs.project_pitcher({"pitcher_outs": FULL["pitcher_outs"]})["proj"] is None
    assert dfs_project.project({"pitcher_outs": FULL["pitcher_outs"]},
                               MLB_PITCHER, require_all=True)["proj"] is None


# --- the arithmetic itself --------------------------------------------------

def test_implied_mean_is_the_line_when_the_price_is_even():
    assert dfs_project.implied_mean(1.91, 1.91, 20.5, 5.0) == pytest.approx(20.5, abs=1e-6)


def test_a_shorter_over_price_implies_a_mean_above_the_line():
    assert dfs_project.implied_mean(1.50, 2.60, 20.5, 5.0) > 20.5
    assert dfs_project.implied_mean(2.60, 1.50, 20.5, 5.0) < 20.5


def test_one_sided_price_cannot_produce_an_infinite_mean():
    """inv_cdf(0) is -inf; without the clamp one lopsided quote builds a whole
    lineup around a player projected at minus infinity (or plus)."""
    m = dfs_project.implied_mean(1.001, 1000.0, 20.5, 5.0)
    assert 20.5 < m < 100.0


# --- NFL --------------------------------------------------------------------

QB = {
    "player_pass_yds": {"Over": 1.91, "Under": 1.91, "point": 245.5},
    "player_pass_tds": {"Over": 1.95, "Under": 1.87, "point": 1.5},
    "player_pass_interceptions": {"Over": 1.85, "Under": 1.95, "point": 0.5},
    "player_rush_yds": {"Over": 1.90, "Under": 1.90, "point": 22.5},
}
WR = {
    "player_reception_yds": {"Over": 1.91, "Under": 1.91, "point": 62.5},
    "player_receptions": {"Over": 1.87, "Under": 1.95, "point": 4.5},
}


def test_nfl_quarterback_projects_from_passing_markets():
    r = dfs_project.project(QB, NFL)
    assert r["proj"] is not None
    # 245.5 pass yds * 0.04 = 9.8, plus ~1.5 pass TDs * 4
    assert 14.0 < r["proj"] < 26.0
    assert "pass yds" in r["components"] and "pass TD" in r["components"]
    assert r["components"]["INT"] < 0        # interceptions cost points


def test_nfl_receiver_projects_and_gets_a_td_imputed():
    """Rushing and receiving TDs have no two-sided market -- DraftKings prices
    them only as a field. They are imputed from yardage and MUST be flagged,
    because at 6 points each they move a projection more than anything else
    imputed."""
    r = dfs_project.project(WR, NFL)
    assert r["proj"] is not None
    assert "rec TD" in r["imputed"]
    # full PPR: 4.5 receptions is ~4.5 points on its own
    assert r["components"]["rec"] == pytest.approx(4.5, abs=0.6)


def test_nfl_requires_only_one_of_its_position_markets():
    """A QB has no receiving yards and a WR has no passing yards, so demanding
    every required market would project nobody at all."""
    assert dfs_project.project(WR, NFL)["proj"] is not None
    assert dfs_project.project(QB, NFL)["proj"] is not None
    assert dfs_project.project({}, NFL)["proj"] is None


def test_threshold_bonus_is_an_expectation_not_a_step():
    """A back projected at 99 and one at 101 must not differ by the whole 3
    points -- the bonus is P(>= 100) * 3, off the same fitted normal."""
    near = {"player_rush_yds": {"Over": 1.91, "Under": 1.91, "point": 99.0}}
    over = {"player_rush_yds": {"Over": 1.91, "Under": 1.91, "point": 101.0}}
    a = dfs_project.project(near, NFL)["bonus"]
    b = dfs_project.project(over, NFL)["bonus"]
    assert 0.0 < a < 3.0 and 0.0 < b < 3.0
    assert b > a
    assert (b - a) < 0.5           # smooth, not a cliff


def test_a_big_rusher_earns_more_bonus_than_a_small_one():
    small = {"player_rush_yds": {"Over": 1.91, "Under": 1.91, "point": 35.0}}
    big = {"player_rush_yds": {"Over": 1.91, "Under": 1.91, "point": 120.0}}
    assert (dfs_project.project(big, NFL)["bonus"]
            > dfs_project.project(small, NFL)["bonus"])


# --- NFL DST ----------------------------------------------------------------

def test_implied_team_points_sign_convention():
    """spread is negative for a favourite, so a favourite scores MORE. Getting
    this backwards silently inverts every DST on the slate."""
    fav = dfs_project.implied_team_points(total=44.0, spread=-7.0)
    dog = dfs_project.implied_team_points(total=44.0, spread=7.0)
    assert fav == pytest.approx(25.5)
    assert dog == pytest.approx(18.5)
    assert fav + dog == pytest.approx(44.0)


def test_dst_scores_higher_against_a_weaker_offence():
    strong = dfs_project.project_dst(13.0)     # shutting a bad offence down
    weak = dfs_project.project_dst(31.0)       # facing a good one
    assert strong["proj"] > weak["proj"]


def test_dst_points_allowed_is_interpolated_not_snapped():
    a = dfs_project.project_dst(20.0)["components"]["points allowed"]
    b = dfs_project.project_dst(20.6)["components"]["points allowed"]
    assert a != b                      # not the same bucket value
    assert abs(a - b) < 1.0            # and not a whole-tier jump


def test_dst_separates_market_signal_from_the_prior():
    """The tier score is market-derived; the big-play allowance is a guess.
    They are reported separately so the guess stays visible."""
    r = dfs_project.project_dst(20.0)
    assert set(r["components"]) == {"points allowed", "big plays"}
    assert r["imputed"] == ["big plays"]


# --- registry ---------------------------------------------------------------

def test_every_sport_declares_the_markets_it_needs():
    for key in ("baseball_mlb", "americanfootball_nfl", "basketball_nba"):
        sport = get(key)
        assert sport.market_keys()
        for required in sport.required:
            assert sport.stat_for(required) is not None, (key, required)
