from edge.pickem_live import _parse_events

# Real Odds API v4 shape (trimmed to what _parse_events reads), two books
# disagreeing slightly on the same game -- the kind of case a mean-consensus
# is meant to smooth out.
BILLS_TEXANS = {
    "home_team": "Houston Texans", "away_team": "Buffalo Bills",
    "commence_time": "2026-09-13T17:00:00Z",
    "bookmakers": [
        {"key": "draftkings", "markets": [{"key": "spreads", "outcomes": [
            {"name": "Houston Texans", "point": -1.5, "price": 1.91},
            {"name": "Buffalo Bills", "point": 1.5, "price": 1.91},
        ]}]},
        {"key": "fanduel", "markets": [{"key": "spreads", "outcomes": [
            {"name": "Houston Texans", "point": -2.5, "price": 1.87},
            {"name": "Buffalo Bills", "point": 2.5, "price": 1.95},
        ]}]},
        # a book quoting a totals market only -- must be ignored, not crash
        {"key": "betmgm", "markets": [{"key": "totals", "outcomes": [
            {"name": "Over", "point": 44.5, "price": 1.91},
        ]}]},
    ],
}

NO_MARKET_YET = {
    "home_team": "Denver Broncos", "away_team": "Kansas City Chiefs",
    "commence_time": "2026-09-15T00:15:00Z", "bookmakers": [],
}


def test_parse_events_averages_home_point_across_books():
    games = _parse_events([BILLS_TEXANS])
    assert len(games) == 1
    g = games[0]
    assert g.home == "Houston Texans" and g.away == "Buffalo Bills"
    assert g.home_abbr == "HOU" and g.away_abbr == "BUF"
    assert g.live_line == -2.0  # mean(-1.5, -2.5)


def test_parse_events_ignores_non_spreads_markets():
    # the totals-only book above must not contribute a phantom home point
    games = _parse_events([BILLS_TEXANS])
    assert games[0].live_line == -2.0  # unchanged by the totals-only bookmaker


def test_parse_events_no_bookmakers_yet_gives_none_not_crash():
    games = _parse_events([NO_MARKET_YET])
    assert games[0].live_line is None
    assert games[0].home_abbr == "DEN"


def test_parse_events_unmapped_team_name_falls_back_gracefully():
    ev = {"home_team": "Unmapped Team FC", "away_team": "Also Unmapped",
          "commence_time": "", "bookmakers": []}
    games = _parse_events([ev])
    assert games[0].home_abbr == "UNM"  # first 3 letters, uppercased -- not a crash
