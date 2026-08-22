"""Tests for the CBS scrapers (edge/pickem_cbs.py)."""
from edge.pickem_cbs import (
    NICK_TO_ABBR, parse_odds_tables, parse_pool_text, parse_spread, parse_total,
)


def test_parse_spread_ignores_american_prices():
    # the odds page puts the price on the second line of the same cell, and
    # bare price cells appear in the moneyline column -- neither is a spread
    assert parse_spread("-3.5\n-110") == -3.5
    assert parse_spread("+3.5\n-108") == 3.5
    assert parse_spread("-180") is None          # moneyline, not a spread
    assert parse_spread("+162") is None
    assert parse_spread("PK") == 0.0
    assert parse_spread("") is None


def test_parse_total_handles_over_under_prefixes():
    assert parse_total("o44.5\n-108") == 44.5
    assert parse_total("u44.5") == 44.5
    assert parse_total("") is None


def _tbl(extra_col=False):
    """Odds-page shape. CBS inserts a 'Final' score column during and after
    a game week, which shifts every index -- the regression this guards."""
    hdr = ["Wed Sep 9, 8:20pm"] + (["Final"] if extra_col else []) + \
          ["Open", "Spread", "Moneyline", "Total"]
    away = ["Patriots"] + (["0"] if extra_col else []) + \
           ["o44.5\n-111", "+3.5\n-108", "+162", "o44.5\n-108"]
    home = ["Seahawks"] + (["0"] if extra_col else []) + \
           ["-3.5\n-110", "-4.0\n-110", "-180", "u44.5\n-105"]
    return [hdr, away, home]


def test_parse_odds_tables_reads_columns_by_header_not_position():
    without = parse_odds_tables([_tbl(False)])[0]
    with_final = parse_odds_tables([_tbl(True)])[0]
    for g in (without, with_final):
        assert g.away_abbr == "NE" and g.home_abbr == "SEA"
        assert g.open_line == -3.5
        assert g.current_line == -4.0     # would be the score column if indexed by position
        assert g.total == 44.5


def test_parse_odds_tables_skips_malformed_tables():
    assert parse_odds_tables([[["hdr"]], []]) == []


def test_parse_pool_text_extracts_line_and_community_split():
    g = parse_pool_text("PATRIOTS 0-0 30% +3.5 AT -3.5 70% SEAHAWKS 0-0")[0]
    assert (g["away_abbr"], g["home_abbr"]) == ("NE", "SEA")
    assert g["cbs_line_home"] == -3.5
    assert (g["comm_pct_away"], g["comm_pct_home"]) == (30, 70)


def test_parse_pool_text_handles_an_away_favorite():
    # the sign that is easiest to get backwards: road team laying the points
    g = parse_pool_text("RAVENS 0-0 68% -3.5 AT +3.5 32% COLTS 0-0")[0]
    assert g["home_abbr"] == "IND"
    assert g["cbs_line_home"] == 3.5      # home team RECEIVING points


def test_parse_pool_text_reads_a_multi_game_page():
    txt = ("Wed @ 8:20 PM NBC\nMatchup Analysis\n"
           "PATRIOTS 0-0 30% +3.5 AT -3.5 70% SEAHAWKS 0-0\n"
           "Thu @ 8:35 PM NFLX\n"
           "49ERS 0-0 19% +3.5 AT -3.5 81% RAMS 0-0\n")
    gs = parse_pool_text(txt)
    assert [g["home_abbr"] for g in gs] == ["SEA", "LAR"]
    assert gs[1]["away_abbr"] == "SF"     # '49ERS' must not fall through title-casing


def test_parse_pool_text_returns_nothing_for_an_unrelated_page():
    assert parse_pool_text("Pool Settings Entry Fee $150 Weekly Payout") == []


def test_every_nfl_team_maps_to_a_unique_abbreviation():
    assert len(NICK_TO_ABBR) == 32
    assert len(set(NICK_TO_ABBR.values())) == 32
