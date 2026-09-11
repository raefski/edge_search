"""Tennis, which was on the board and never in the results.

A live tennis scan on 2026-09-07 returned 148 events and 1,033 quotes from
three books, and produced no arbitrage, no middle and no +EV bet -- because
only 24 of those events were ever recognised as the same match by two books,
and the deepest market on the sport was split across two keys. Four separate
defects, each enough on its own; these are the regressions for all four.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from edge.arb.draftkings_league import main_line_subcategories
from edge.arb.fanduel import classify
from edge.arb.marketmap import canonical_market
from edge.arb.matching import match_event, start_tolerance_minutes
from edge.arb.models import Board, EventMeta


# --- 1. a match has no start time, only an estimate -------------------------
def _board(when: datetime, sport: str) -> Board:
    b = Board()
    b.events["e1"] = EventMeta("e1", sport, sport, when, "Sidharth Rawat",
                               "Kasidit Samrej")
    return b


def test_two_books_eighty_minutes_apart_are_one_tennis_match():
    """The live failure, with its real numbers. A tennis match is "third on
    Court 5" -- it starts when the one before it ends -- so books publish
    estimates and the estimates diverge down the order of play. DraftKings ran
    70-100 minutes behind FanDuel on fixtures whose names matched at 1.00, and
    the 30-minute default dropped every one: 0 of 84 DraftKings tennis events
    found their FanDuel twin.
    """
    fd_time = datetime.now(timezone.utc) + timedelta(hours=3)
    dk_time = fd_time + timedelta(minutes=80)
    board = _board(fd_time, "tennis_atp")
    hit = match_event(board, "Sidharth Rawat", "Kasidit Samrej", dk_time, "tennis_atp")
    assert hit is not None and hit.event_id == "e1"


def test_a_scheduled_fixture_keeps_the_tight_window():
    """Baseball starts when the schedule says. Widening tennis must not widen
    a sport where a start time is an appointment and an 80-minute disagreement
    means the books are talking about different games."""
    fd_time = datetime.now(timezone.utc) + timedelta(hours=3)
    board = _board(fd_time, "baseball_mlb")
    assert match_event(board, "Sidharth Rawat", "Kasidit Samrej",
                       fd_time + timedelta(minutes=80), "baseball_mlb") is None
    assert match_event(board, "Sidharth Rawat", "Kasidit Samrej",
                       fd_time + timedelta(minutes=20), "baseball_mlb") is not None


def test_the_widened_window_still_refuses_a_different_day():
    """The only thing on the other side of this window is the same two players
    meeting again, which is another day's card. Six hours covers a full order
    of play and stops well short of that."""
    fd_time = datetime.now(timezone.utc) + timedelta(hours=3)
    board = _board(fd_time, "tennis_atp")
    assert match_event(board, "Sidharth Rawat", "Kasidit Samrej",
                       fd_time + timedelta(hours=20), "tennis_atp") is None


@pytest.mark.parametrize("sport,expected", [
    ("tennis_atp", 360.0), ("mma_mixed_martial_arts", 360.0),
    ("boxing_boxing", 360.0), ("baseball_mlb", 30.0),
    ("americanfootball_nfl", 30.0), (None, 30.0),
])
def test_only_the_sports_without_appointments_are_widened(sport, expected):
    """MMA and boxing are the same shape -- a card's bouts start when the
    previous one finishes."""
    assert start_tolerance_minutes(sport) == expected


# --- 2. tennis counts games and sets and calls both a handicap --------------
@pytest.mark.parametrize("name,want", [
    ("Handicaps", "spreads_games"),        # Oddschecker/Fanatics: bare, no unit
    ("Spread", "spreads_games"),
    ("Games Handicap", "spreads_games"),
    ("Set Spread", "spreads_sets"),        # DraftKings: says spread, not handicap
    ("Sets Handicap", "spreads_sets"),
    ("Total Games", "totals_games"),
    ("Total", "totals_games"),
    ("Total Sets", "totals_sets"),
    ("Win Market", "h2h"),                 # no tennis rule: falls through
    ("Moneyline", "h2h"),
])
def test_a_tennis_market_is_keyed_by_the_unit_it_counts(name, want):
    assert canonical_market(name, sport_key="tennis_atp") == want


def test_the_bare_handicap_split_one_market_across_two_keys():
    """THE REGRESSION THAT COST THE MOST COVERAGE. Fanatics names the games
    handicap "Handicaps" and FanDuel names it "Games Handicap". The first fell
    through to the generic `spreads` rule and the second did not, so 21 rungs
    naming the same bet at the same line sat in different groups and could
    never pair -- on a board with 39 two-book tennis groups in total."""
    assert canonical_market("Handicaps") == "spreads"          # generic, unchanged
    assert (canonical_market("Handicaps", sport_key="tennis_atp")
            == canonical_market("Games Handicap", sport_key="tennis_atp"))


def test_a_set_handicap_never_shares_a_key_with_a_games_handicap():
    """The other half, and the dangerous one. A set favourite at -1.5 prices
    around 2.5-4.0 where a games favourite at -1.5 prices around 1.3-1.5, so
    merging them is not a near miss -- it is a manufactured arbitrage. Both
    "Set Spread" and "Handicaps" used to key bare `spreads`."""
    assert (canonical_market("Set Spread", sport_key="tennis_atp")
            != canonical_market("Handicaps", sport_key="tennis_atp"))


def test_a_set_moneyline_is_not_the_match_moneyline():
    """"Moneyline - Listed Set" is the winner of ONE set. It carries no ordinal
    and no digit, so every period pattern stepped over it and the bare
    `money ?line` rule filed a set winner as the match h2h."""
    assert canonical_market("Moneyline - Listed Set") is None
    assert canonical_market("Moneyline - Listed Set", sport_key="tennis_atp") is None


def test_other_sports_are_untouched_by_the_tennis_rules():
    for name, want in (("Point Spread", "spreads"), ("Total Points", "totals"),
                       ("Run Line", "spreads"), ("Moneyline", "h2h")):
        assert canonical_market(name) == want


# --- 3. FanDuel's own fallback had the same blind spot ----------------------
@pytest.mark.parametrize("market_type,want", [
    ("MATCH_HANDICAP_(2-WAY)", "spreads_games"),     # from the explicit table
    ("ALTERNATE_MATCH_HANDICAP", "spreads_games"),   # ditto
    ("SOME_UNKNOWN_HANDICAP", "spreads_games"),      # from the tolerant fallback
    ("ALTERNATIVE_MATCH_GAME_HANDICAP", "spreads_games"),
    ("MATCH_TOTAL_GAMES", "totals_games"),
])
def test_a_bare_fanduel_tennis_key_is_re_keyed_onto_its_unit(market_type, want):
    """Both routes that produce a bare key are covered, which is why the
    re-key runs after classification rather than inside it: the explicit table
    maps two real tennis market types to `spreads`, and the tolerant fallback
    keys anything containing HANDICAP the same way. Three FanDuel tennis
    handicaps went to a key no other book writes, where they paired with
    nothing."""
    assert classify(market_type, "tennis_atp")[0] == want


def test_fanduel_classification_is_unchanged_off_tennis():
    assert classify("RUN_LINE")[0] == "spreads"
    assert classify("RUN_LINE", "baseball_mlb")[0] == "spreads"
    assert classify("TOTAL_RUNS", "baseball_mlb")[0] == "totals"


# --- 4. DraftKings keeps tennis handicaps in a subcategory ------------------
def test_a_tennis_subcategory_is_recognised_as_a_main_line():
    """DraftKings' tennis LEAGUE FEED is moneyline and nothing else -- measured
    on Challenger - Cassis: 13 events, 13 markets, 26 selections, all
    "Moneyline". The handicaps live in subcategories, exactly as soccer's main
    lines do. Every MAIN_LINE_ORDER pattern is anchored, so "Set Spread"
    matched none of them and this returned [] for every tennis league; the
    scan therefore had nothing to fetch and DraftKings contributed 101 tennis
    h2h groups and zero handicaps to a live board."""
    payload = {
        "categories": [{"id": 488, "name": "Match Lines"},
                       {"id": 534, "name": "Sets"}],
        "subcategories": [
            {"id": 11127, "categoryId": 534, "name": "Set Spread"},
            {"id": 6360, "categoryId": 534, "name": "Moneyline - Listed Set"},
            {"id": 9535, "categoryId": 534, "name": "Set Betting"},
            {"id": 6364, "categoryId": 488, "name": "Moneyline"},
        ],
    }
    got = main_line_subcategories(payload, 4)
    assert (534, 11127, "Set Spread") in got
    names = {n for _c, _s, n in got}
    assert "Set Betting" not in names, "a correct score is not a handicap"
    assert "Moneyline - Listed Set" not in names, "a set winner is not the match"
    assert "Moneyline" not in names, "the league feed already carries it"
