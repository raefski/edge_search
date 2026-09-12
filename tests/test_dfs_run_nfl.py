"""Slate resolution and game-line matching for NFL DFS.

The centre of gravity here is one bug, because it was silent and it was live.
On 2026-09-12 a scraped `dfs_nfl` scan held 27 NFL events -- week 2 AND week 3,
because books post next week's lines days early. The builder walked all of them
into a `{team: line}` dict, so for any team playing in both weeks the later
event overwrote the earlier one: 21 of 28 slate teams ended up carrying the
wrong opponent and the wrong game total. Every defence was projected off the
wrong game, and the "a DST never faces your own lineup" rule silently stopped
working, because a wrong opponent makes the comparison pass rather than fail.

So these tests are mostly about REFUSING data, which is the part that has no
visible symptom when it breaks.
"""
from __future__ import annotations

import pytest

from edge import dfs_nfl_theory as theory
from edge import dfs_run_nfl as nfl


def draftable(name, team, salary, position, game, matchup, start):
    return {"name": name, "team": team, "salary": salary, "position": position,
            "game": game, "matchup": matchup, "start": start, "dk_fppg": None}


WEEK2 = "2026-09-13T17:00:00.0000000Z"
WEEK3 = "2026-09-20T17:00:00.0000000Z"


def salaries():
    """A two-game slate, in DraftKings' own shape."""
    return {
        "a": draftable("QB One", "LAC", 6000, "QB", 101, "ARI @ LAC", WEEK2),
        "b": draftable("WR One", "ARI", 5000, "WR", 101, "ARI @ LAC", WEEK2),
        "c": draftable("RB One", "DET", 7000, "RB", 102, "NO @ DET", WEEK2),
        "d": draftable("WR Two", "NO", 6000, "WR", 102, "NO @ DET", WEEK2),
    }


class FakeClient:
    """Serves whatever events it is given, in the order given."""

    def __init__(self, events):
        self._events = events

    def get_featured_odds(self, sport, markets, regions):
        return self._events


def event(home, away, commence, total=45.5, spread_home=-3.0):
    return {
        "home_team": home, "away_team": away, "commence_time": commence,
        "bookmakers": [{"key": "draftkings", "markets": [
            {"key": "totals", "outcomes": [{"name": "Over", "point": total}]},
            {"key": "spreads", "outcomes": [{"name": home, "point": spread_home}]},
        ]}],
    }


# --- the slate is DraftKings' description of itself ---------------------------

def test_slate_games_are_read_off_the_draftables():
    games = nfl.slate_games(salaries())
    assert set(games) == {101, 102}
    assert games[101]["home"] == "LAC" and games[101]["away"] == "ARI"
    assert games[102]["home"] == "DET" and games[102]["away"] == "NO"


def test_every_team_gets_its_opponent_from_dk_not_from_the_odds_feed():
    opp = nfl.team_opponents(nfl.slate_games(salaries()))
    assert opp["LAC"] == ("ARI", 101)
    assert opp["ARI"] == ("LAC", 101)
    assert opp["NO"] == ("DET", 102)
    assert opp["DET"] == ("NO", 102)


def test_dk_seven_digit_fractional_seconds_parse():
    """fromisoformat takes at most six. Returning None here would make every
    game look unmatched and empty the board."""
    assert nfl._parse_time(WEEK2) is not None
    assert nfl._parse_time("2026-09-13T17:01:00+00:00") is not None
    assert nfl._parse_time("") is None
    assert nfl._parse_time("not a time") is None


# --- THE BUG ------------------------------------------------------------------

def test_a_later_week_never_overwrites_this_weeks_game_line():
    """The measured failure: 21 of 28 teams took a week-3 line. The week-3
    event here is the SAME team pair, listed after the real one, which is
    exactly the case that used to win by being iterated last."""
    games = nfl.slate_games(salaries())
    client = FakeClient([
        event("Los Angeles Chargers", "Arizona Cardinals", "2026-09-13T17:01:00+00:00",
              total=47.5, spread_home=-9.5),
        # next week, same pair -- must be refused
        event("Los Angeles Chargers", "Arizona Cardinals", "2026-09-20T17:01:00+00:00",
              total=41.0, spread_home=+2.0),
        event("Detroit Lions", "New Orleans Saints", "2026-09-13T17:01:00+00:00",
              total=50.0, spread_home=-7.0),
    ])
    lines, report = nfl.game_lines(client, games)

    assert lines[101]["total"] == 47.5, "took next week's total"
    assert lines[101]["spread_home"] == -9.5
    assert lines[102]["total"] == 50.0
    assert report["unmatched_events"] == 1
    assert report["conflicts"] == []
    assert report["missing_games"] == []


def test_an_event_that_is_not_on_the_slate_is_discarded_not_counted_as_a_game():
    games = nfl.slate_games(salaries())
    client = FakeClient([
        event("Los Angeles Chargers", "Arizona Cardinals", "2026-09-13T17:01:00+00:00"),
        event("Buffalo Bills", "Houston Texans", "2026-09-13T17:01:00+00:00"),
    ])
    lines, report = nfl.game_lines(client, games)
    assert set(lines) == {101}
    assert report["unmatched_events"] == 1
    assert report["missing_games"] == ["NO @ DET"]


def test_a_second_event_for_one_game_is_reported_rather_than_preferred():
    """Silently taking one of two disagreeing prices is how the original bug
    looked from the outside: a number, with no sign anything was wrong."""
    games = nfl.slate_games(salaries())
    client = FakeClient([
        event("Los Angeles Chargers", "Arizona Cardinals", "2026-09-13T17:01:00+00:00",
              total=47.5),
        event("Los Angeles Chargers", "Arizona Cardinals", "2026-09-13T17:05:00+00:00",
              total=44.0),
    ])
    lines, report = nfl.game_lines(client, games)
    assert lines[101]["total"] == 47.5
    assert report["conflicts"] == ["ARI @ LAC"]


def test_missing_games_are_named_so_a_thin_board_is_distinguishable_from_a_bug():
    games = nfl.slate_games(salaries())
    lines, report = nfl.game_lines(FakeClient([]), games)
    assert lines == {}
    assert sorted(report["missing_games"]) == ["ARI @ LAC", "NO @ DET"]


# --- slate choice -------------------------------------------------------------

def groups():
    return [
        {"DraftGroupId": 1, "GameTypeId": 1, "GameCount": 14,
         "ContestStartTimeSuffix": " (Sun-Mon)", "StartDate": WEEK2,
         "StartDateEst": "", "DraftGroupTag": ""},
        {"DraftGroupId": 2, "GameTypeId": 1, "GameCount": 12,
         "ContestStartTimeSuffix": None, "StartDate": WEEK2,
         "StartDateEst": "", "DraftGroupTag": "Featured"},
        {"DraftGroupId": 3, "GameTypeId": 189, "GameCount": 12,
         "ContestStartTimeSuffix": "", "StartDate": WEEK2,
         "StartDateEst": "", "DraftGroupTag": "Featured"},
        {"DraftGroupId": 4, "GameTypeId": 96, "GameCount": 1,
         "ContestStartTimeSuffix": " (NO @ DET)", "StartDate": WEEK2,
         "StartDateEst": "", "DraftGroupTag": ""},
    ]


def test_only_classic_groups_are_offered():
    """96 is Showdown, 189 is a different game entirely. A 12-game 'NFL Tiers'
    group is not a slate this optimizer can build for."""
    gids = {g["gid"] for g in nfl.classic_groups(groups())}
    assert gids == {1, 2}


def test_the_default_slate_is_main_not_the_biggest():
    """dfs.pick_priced_group's rule is 'biggest', which on an NFL lobby is the
    14-game Sun-Mon slate (and, some weeks, a 16-game season-long tournament).
    Main is the one the big tournaments and the deepest cash games run on."""
    gid, meta = nfl.resolve_slate(None, groups())
    assert gid == 2
    assert meta["label"] == "Main" and meta["games"] == 12


def test_an_explicit_draft_group_is_honoured():
    gid, _ = nfl.resolve_slate(1, groups())
    assert gid == 1


# --- the theory module's own contracts ----------------------------------------

def test_flex_is_a_slot_not_a_position_for_the_variance_lookup():
    """DK writes "RB/FLEX". Keying the spread on FLEX would silently price
    every flex-eligible player as a wide receiver."""
    assert theory.base_position({"dk_pos": "RB/FLEX"}) == "RB"
    assert theory.base_position({"dk_pos": "WR"}) == "WR"
    assert theory.base_position({"dk_pos": "DST"}) == "DST"
    assert theory.base_position({"dk_pos": "FB"}) == "RB"


def test_a_quarterback_is_more_reliable_per_point_than_a_tight_end():
    """The measured shape that gives cash mode its position preference, and the
    reason it is not folk wisdom: sd ~ 7.98 + 0.038*proj for a QB against
    2.21 + 0.480*proj for a TE."""
    qb = {"dk_pos": "QB", "proj": 20.0}
    te = {"dk_pos": "TE", "proj": 20.0}
    assert theory.player_sd(qb) / 20.0 < theory.player_sd(te) / 20.0


def test_correlation_is_zero_between_different_games():
    a = {"dk_pos": "QB", "team": "LAC", "game": 101, "proj": 20}
    b = {"dk_pos": "WR", "team": "DET", "game": 102, "proj": 15}
    assert theory.rho(a, b) == 0.0


def test_a_stack_raises_the_lineup_spread_and_that_is_the_whole_mechanism():
    qb = {"dk_pos": "QB", "team": "LAC", "opp_team": "ARI", "game": 101, "proj": 20}
    mate = {"dk_pos": "WR", "team": "LAC", "opp_team": "ARI", "game": 101, "proj": 15}
    apart = {"dk_pos": "WR", "team": "DET", "opp_team": "NO", "game": 102, "proj": 15}
    assert theory.lineup_sd([qb, mate]) > theory.lineup_sd([qb, apart])
    assert theory.score([qb, mate], "gpp") > theory.score([qb, apart], "gpp")
    assert theory.score([qb, mate], "cash") < theory.score([qb, apart], "cash")


def test_no_player_is_ever_given_an_impossible_ownership():
    """An unclipped softmax handed a $2,900 value outlier 99.1% on the live
    2026-09-13 board. No NFL main-slate player has ever been 99% owned."""
    pool = [{"dk_pos": "TE", "proj": 10.1, "salary": 2900, "name": "outlier"}]
    pool += [{"dk_pos": "TE", "proj": 6.0, "salary": 4500, "name": f"te{i}"}
             for i in range(12)]
    theory.add_ownership(pool)
    assert all(p["own"] <= theory.MAX_OWN + 1e-6 for p in pool)
    # Tolerance is sized by the ROUNDING, not by slack in the normalisation:
    # `own` is published to one decimal, so n players carry up to n*0.05 of
    # accumulated rounding error and 13 of them can legitimately miss by 0.65.
    assert sum(p["own"] for p in pool) == pytest.approx(
        100.0 * theory.SLOTS_BY_POSITION["TE"], abs=0.05 * len(pool))
