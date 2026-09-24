"""The college football build: scoring, roster, theory and the optimiser.

These are the invariants that would otherwise be checked by eye on a live
board, where a wrong one looks like a plausible lineup. The measured constants
themselves are re-derivable with scripts/ncaaf_fit.py; what is tested here is
that the code USES them the way the theory says it does.
"""
from __future__ import annotations

import pytest

from edge import dfs_opt_ncaaf as opt
from edge import dfs_ncaaf_theory as theory
from edge import dfs_sport, ncaaf


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def test_scoring_matches_dk_for_a_worked_line():
    """8 catches, 120 yards, 1 TD = 8 + 12 + 6 + 3 (100-yard bonus) = 29."""
    assert ncaaf.actual_points(
        {"rec": 8, "rec_yds": 120, "rec_td": 1}) == pytest.approx(29.0)


def test_the_three_bonuses_are_independent():
    """A back with 100 rushing AND 100 receiving yards earns both.

    Written as a test because the natural way to code three thresholds is an
    if/elif chain, which silently pays only the first.
    """
    both = ncaaf.actual_points({"rush_yds": 100, "rec_yds": 100, "rec": 5})
    bare = (0.1 * 100) + (0.1 * 100) + 5
    assert both == pytest.approx(bare + 6.0)


def test_passing_is_scored_at_four_points_a_touchdown_not_six():
    """DK CFB is DK NFL scoring. A passing TD is 4, a rushing TD is 6.

    Validated against DraftKings' own published FPPG -- see edge/ncaaf.py.
    Six would overstate every quarterback by ~2.2 DK points a game.
    """
    assert ncaaf.actual_points({"pass_td": 1}) == pytest.approx(4.0)
    assert ncaaf.actual_points({"rush_td": 1}) == pytest.approx(6.0)


def test_full_ppr_not_half():
    assert ncaaf.actual_points({"rec": 6}) == pytest.approx(6.0)


# ---------------------------------------------------------------------------
# roster
# ---------------------------------------------------------------------------
def test_tight_ends_are_receivers_in_college():
    """DraftKings lists college tight ends as WR; there is no TE slot."""
    assert ncaaf.base_position("TE") == "WR"
    assert "TE" not in opt.SLOTS


def test_there_is_no_defence_slot():
    assert not any(s in opt.SLOTS for s in ("DST", "DEF", "D"))


def test_a_quarterback_is_eligible_for_the_sflex_but_not_the_flex():
    """The whole roster-construction question of the sport, as a type check."""
    qb = opt.eligible_slots("QB")
    assert "S-FLEX" in qb and "FLEX" not in qb
    rb = opt.eligible_slots("RB")
    assert {"RB", "FLEX", "S-FLEX"} <= rb


def test_roster_shape_matches_draftkings_published_rules():
    """Read off api.draftkings.com/lineups/v1/gametypes/94/rules, not an article."""
    assert opt.SLOTS == ["QB", "RB", "RB", "WR", "WR", "WR", "FLEX", "S-FLEX"]
    assert opt.CAP == 50000
    assert dfs_sport.get("americanfootball_ncaaf").salary_cap == 50000


# ---------------------------------------------------------------------------
# the theory, as the optimiser actually applies it
# ---------------------------------------------------------------------------
def _p(name, pos, team, game, proj=15.0, salary=6000, opp=None):
    return {"name": name, "dk_pos": pos, "team": team, "game": game,
            "opp_team": opp, "proj": proj, "salary": salary,
            "pos": opt.eligible_slots(pos)}


def test_two_quarterbacks_from_one_team_is_correlated_negatively():
    """-0.098. Two college QBs on one roster compete for the same snaps."""
    a = _p("A", "QB", "OSU", 1)
    b = _p("B", "QB", "OSU", 1)
    assert theory.rho(a, b) == pytest.approx(theory.R_QB_OWN_QB)
    assert theory.rho(a, b) < 0


def test_teammate_receivers_are_positively_correlated_unlike_the_nfl():
    """The sign flip that makes a college stack compound.

    edge/dfs_opt_nfl.py reasons from a NEGATIVE teammate correlation that a
    second pass-catcher is worth less than the first, and stacks 2. Here the
    sign is positive and the default is 3. If this test ever fails, the
    stack size default is wrong too.
    """
    a = _p("A", "WR", "OSU", 1)
    b = _p("B", "WR", "OSU", 1)
    assert theory.rho(a, b) > 0
    assert theory.STACK_N == 3


def test_players_in_different_games_are_uncorrelated():
    """Which is why a floor-maximising lineup spreads across games unasked."""
    assert theory.rho(_p("A", "QB", "OSU", 1), _p("B", "WR", "MICH", 2)) == 0.0


def test_a_stack_raises_the_spread_so_the_two_objectives_disagree():
    """The whole design, in one assertion.

    The same eight players, rearranged from spread-out to stacked, must score
    BETTER for GPP and WORSE for cash. Nothing tells either objective to
    prefer or avoid a stack -- it falls out of the correlation term.
    """
    spread = [_p(f"S{i}", "WR", f"T{i}", i) for i in range(8)]
    stacked = ([_p("QB1", "QB", "OSU", 1)]
               + [_p(f"W{i}", "WR", "OSU", 1) for i in range(3)]
               + [_p(f"S{i}", "WR", f"T{i}", i + 2) for i in range(4)])
    assert theory.lineup_sd(stacked) > theory.lineup_sd(spread)
    assert theory.score(stacked, "gpp") > theory.score(spread, "gpp")
    assert theory.score(stacked, "cash") < theory.score(spread, "cash")


def test_quarterbacks_are_the_most_volatile_position_in_college():
    """The NFL conclusion inverts, and cash roster construction depends on it.

    edge/dfs_nfl_theory.py's measured NFL spreads make a quarterback the
    SAFEST slot per point. College quarterbacks run, throw interceptions and
    get pulled in blowouts, and the measured coefficient of variation puts
    them last. Asserted so that a future refit that reverses it is noticed
    rather than quietly changing what cash pays up for.
    """
    cv = {}
    for pos in ("QB", "RB", "WR"):
        a, b = theory.SD_FIT[pos]
        cv[pos] = (a + 20 * b) / 20.0
    assert cv["QB"] > cv["RB"] > cv["WR"]


# ---------------------------------------------------------------------------
# the optimiser's hard rules
# ---------------------------------------------------------------------------
def _board():
    """A board deep enough to build from, with two QBs on one team."""
    pool = []
    for g, (home, away) in enumerate([("AAA", "BBB"), ("CCC", "DDD"),
                                      ("EEE", "FFF")], start=1):
        for team, opp in ((home, away), (away, home)):
            pool.append(_p(f"{team}-QB1", "QB", team, g, 24.0, 8000, opp))
            pool.append(_p(f"{team}-QB2", "QB", team, g, 23.5, 7900, opp))
            for i in range(4):
                pool.append(_p(f"{team}-WR{i}", "WR", team, g, 14.0 - i, 5000, opp))
            for i in range(2):
                pool.append(_p(f"{team}-RB{i}", "RB", team, g, 13.0 - i, 5000, opp))
    return pool


def test_the_optimiser_never_rosters_two_quarterbacks_from_one_team():
    """A hard refusal, not a preference -- the analogue of the NFL's DST rule.

    The board is built so that the two highest-projected quarterbacks are
    TEAM-MATES, which is exactly the lineup a mean-maximiser would reach for.
    """
    res = opt.optimize(_board(), mode="cash", iters=120, seed=1)
    assert res is not None
    teams = [p["team"] for p, _ in res["lineup"]
             if ncaaf.base_position(p["dk_pos"]) == "QB"]
    assert len(teams) == len(set(teams)), res["qbs"]


def test_every_built_lineup_is_legal():
    for mode in ("cash", "gpp"):
        res = opt.optimize(_board(), mode=mode, iters=120, seed=2)
        assert res is not None, mode
        players = [p for p, _ in res["lineup"]]
        assert len(players) == 8
        assert res["salary"] <= opt.CAP
        assert len({p["game"] for p in players}) >= opt.MIN_GAMES
        assert len({p["name"] for p in players}) == 8


def test_gpp_actually_stacks_and_cash_does_not():
    gpp = opt.optimize(_board(), mode="gpp", iters=150, seed=3)
    cash = opt.optimize(_board(), mode="cash", iters=150, seed=3)
    assert gpp is not None and gpp.get("stack")
    assert len(gpp["stack"]["with"]) >= 2

    def biggest_team(res):
        counts: dict = {}
        for p, _ in res["lineup"]:
            counts[p["team"]] = counts.get(p["team"], 0) + 1
        return max(counts.values())

    assert biggest_team(gpp) > biggest_team(cash)


def test_an_empty_or_unprojectable_board_returns_none_not_a_bad_lineup():
    assert opt.optimize([], mode="cash", iters=10) is None
    no_proj = [dict(p, proj=None) for p in _board()]
    assert opt.optimize(no_proj, mode="cash", iters=10) is None


def test_gpp_falls_back_to_a_smaller_stack_rather_than_returning_nothing():
    """A QB + 3 catchers + a bring-back is 5 of 8 slots.

    On a board where no team has three catchers, the full stack is infeasible.
    Returning None there would be wrong -- a GPP lineup still exists -- so the
    optimiser steps the stack down instead.
    """
    thin = []
    for g, (home, away) in enumerate([("AAA", "BBB"), ("CCC", "DDD")], start=1):
        for team, opp in ((home, away), (away, home)):
            thin.append(_p(f"{team}-QB", "QB", team, g, 22.0, 7000, opp))
            thin.append(_p(f"{team}-WR", "WR", team, g, 13.0, 5000, opp))
            for i in range(3):
                thin.append(_p(f"{team}-RB{i}", "RB", team, g, 12.0 - i, 4500, opp))
    res = opt.optimize(thin, mode="gpp", iters=120, seed=4)
    assert res is not None
    assert len(res["stack"]["with"]) < theory.STACK_N


# ---------------------------------------------------------------------------
# imputation
# ---------------------------------------------------------------------------
def test_a_quarterbacks_rushing_touchdowns_convert_faster_than_a_backs():
    """110 yards per score against 130. Separated by a bootstrap, so split."""
    sport = dfs_sport.get("americanfootball_ncaaf")
    qb = sport.impute({"player_rush_yds": 100, "player_pass_yds": 250}, "QB")
    rb = sport.impute({"player_rush_yds": 100}, "RB")
    assert qb["player_rush_tds"] > rb["player_rush_tds"]


def test_receiving_touchdowns_are_NOT_split_by_position():
    """Deliberate: the bootstrap did not separate WR (187-199) from RB (169-284).

    The NFL build DOES split these. Splitting on an unmeasured difference
    costs the same as leaving a real one out and is harder to notice, so this
    stays one rate until a sample says otherwise.
    """
    sport = dfs_sport.get("americanfootball_ncaaf")
    wr = sport.impute({"player_reception_yds": 100}, "WR")
    rb = sport.impute({"player_reception_yds": 100}, "RB")
    assert wr["player_reception_tds"] == pytest.approx(rb["player_reception_tds"])


def test_interceptions_are_imputed_because_college_has_no_market_for_them():
    """And from ATTEMPTS in preference to yards -- an INT is a per-throw risk."""
    sport = dfs_sport.get("americanfootball_ncaaf")
    from_att = sport.impute({"player_pass_yds": 250,
                             "player_pass_attempts": 40}, "QB")
    from_yds = sport.impute({"player_pass_yds": 250}, "QB")
    assert from_att["player_pass_interceptions"] == pytest.approx(40 / 48.5)
    assert from_yds["player_pass_interceptions"] == pytest.approx(250 / 358.2)


def test_a_priced_bonus_overrides_the_fitted_normal():
    """The ladder's own price for '100+' beats a normal's right tail.

    edge/dfs_sport.py measured that normal under-predicting P(100+) by 1.3-1.7
    percentage points. This is the wiring that lets a real price win.
    """
    from edge import dfs_project
    sport = dfs_sport.get("americanfootball_ncaaf")
    means = {"player_reception_yds": 80.0, "player_receptions": 5.0}
    fitted = dfs_project.points_from_means(means, {}, sport, "WR")
    priced = dfs_project.points_from_means(
        means, {}, sport, "WR",
        bonus_probs={"player_reception_yds": 0.60})
    assert priced["bonus"] == pytest.approx(0.60 * 3.0)
    assert priced["bonus"] != fitted["bonus"]
