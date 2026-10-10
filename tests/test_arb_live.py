"""edge/arb/live.py -- one live game, DraftKings and FanDuel, offline.

The payload shapes below are trimmed from the Cowboys @ Giants reads of
2026-09-13, the only live game this module has been run against.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from edge.arb import engine, live
from edge.arb.config import ArbConfig
from edge.arb.models import Board, EventMeta, GroupKey, Quote

NOW = datetime(2026, 9, 14, 2, 30, tzinfo=timezone.utc)


def _live_event(minutes_ago=120):
    return EventMeta("fd:35596943", "americanfootball_nfl", "americanfootball_nfl",
                     NOW - timedelta(minutes=minutes_ago),
                     "New York Giants", "Dallas Cowboys")


def _dk_event(**over):
    ev = {"id": "34118096", "name": "DAL Cowboys @ NY Giants",
          "startEventDate": "2026-09-14T00:24:50.0000000Z", "status": "STARTED",
          "liveGameState": {"period": "3rd Quarter", "gameTime": 715, "isClockRunning": True},
          "participants": [{"name": "NY Giants", "venueRole": "Home", "sortOrder": 2},
                           {"name": "DAL Cowboys", "venueRole": "Away", "sortOrder": 1}],
          "eventScorecard": {"mainScorecard": {"firstTeamScore": "7",
                                               "secondTeamScore": "14"}}}
    ev.update(over)
    return ev


# ------------------------------------------------------------- DraftKings side

def test_only_event_keeps_one_games_markets_and_selections():
    payload = {"events": [{"id": "1"}, {"id": "2"}],
               "markets": [{"id": "m1", "eventId": "1"}, {"id": "m2", "eventId": "2"}],
               "selections": [{"marketId": "m1"}, {"marketId": "m2"}, {"marketId": "m1"}],
               "subcategories": ["kept"]}
    cut = live.only_event(payload, "1")
    assert [e["id"] for e in cut["events"]] == ["1"]
    assert [m["id"] for m in cut["markets"]] == ["m1"]
    assert len(cut["selections"]) == 2 and all(s["marketId"] == "m1" for s in cut["selections"])
    assert cut["subcategories"] == ["kept"]


def test_dk_event_found_by_mascots_despite_abbreviated_cities():
    payload = {"events": [_dk_event(id="other", participants=[
                              {"name": "PHI Eagles"}, {"name": "NY Giants"}]),
                          _dk_event()]}
    start = datetime(2026, 9, 14, 0, 26, tzinfo=timezone.utc)
    assert live.dk_event_for(payload, "Dallas Cowboys @ New York Giants", start) == "34118096"


def test_dk_event_refuses_the_same_teams_on_another_day():
    payload = {"events": [_dk_event()]}
    rematch = datetime(2026, 12, 20, 0, 20, tzinfo=timezone.utc)
    assert live.dk_event_for(payload, "Dallas Cowboys @ New York Giants", rematch) is None


def test_game_state_maps_scores_through_sort_order():
    st = live.game_state(_dk_event())
    assert (st.away, st.away_score, st.home, st.home_score) == ("DAL Cowboys", "7", "NY Giants", "14")
    assert st.period == "3rd Quarter" and st.clock_seconds == 715


def test_break_is_a_quarter_boundary_not_any_stopped_clock():
    def state(period, secs, running):
        return live.game_state(_dk_event(liveGameState={
            k: v for k, v in (("period", period), ("gameTime", secs),
                              ("isClockRunning", running)) if v is not None}))
    # read live: DraftKings sent no isClockRunning at all at the top of the 4th
    assert state("4th Quarter", 900, None).between_periods
    assert state("3rd Quarter", 0, False).between_periods
    assert state("Halftime", None, None).between_periods
    assert not state("3rd Quarter", 715, False).between_periods    # an incomplete pass
    assert not state("4th Quarter", 900, True).between_periods     # kicked off


def test_live_read_skips_prop_tabs_that_cannot_pair():
    # both spellings of the per-half tab were served, two minutes apart
    tabs = ["Rec Yards O/U", "Rec Yards - 1H O/U", "Pass Yards - 1Q O/U",
            "Each Player Rush Yards in Each Quarter", "Player Pass Yds in Each Half",
            "Either Player Rec Yards", "Combined Pass Yards", "Most Receptions",
            "1st Reception", "Race to X Receptions", "Longest Rush"]
    league = {"subcategories": [{"categoryId": 1342, "id": i + 1, "name": n}
                                for i, n in enumerate(tabs)]}
    kept = [n for _c, _s, n in live.live_prop_subcategories(
        SimpleNamespace(prop_subcategories=lambda p: [(1342, s["id"], s["name"])
                                                      for s in p["subcategories"]]),
        league, 40)]
    assert kept == ["Rec Yards O/U", "Longest Rush"]


# --------------------------------------------------------------- FanDuel side

def test_cache_age_reads_the_header_in_any_casing():
    assert live.cache_age({"Age": "29"}) == 29.0
    assert live.cache_age({"age": "11"}) == 11.0
    assert live.cache_age({"X-Cache": "Miss from cloudfront"}) == 0.0
    assert live.cache_age({"age": "soon"}) == 0.0


def test_backdate_restamps_only_quotes_written_since():
    board, ev = Board(), _live_event()
    g = board.group(GroupKey(ev.event_id, "totals", None, 44.5), ev)
    g.add(Quote("fanduel", "over", 1.85, 44.5, NOW - timedelta(seconds=5)))
    g.add(Quote("draftkings", "under", 1.90, 44.5, NOW))
    g.add(Quote("fanduel", "under", 1.89, 44.5, NOW))
    fetched_at = NOW - timedelta(seconds=40)
    assert live.backdate(board, "fanduel", NOW, fetched_at) == 1
    assert g.quotes["under"]["fanduel"].last_update == fetched_at
    assert g.quotes["under"]["draftkings"].last_update == NOW
    assert g.quotes["over"]["fanduel"].last_update == NOW - timedelta(seconds=5)


# ------------------------------------------------------------------ detecting

def _arb_board(fd_age_seconds=0.0, minutes_ago=120):
    board, ev = Board(), _live_event(minutes_ago)
    g = board.group(GroupKey(ev.event_id, "spreads", None, -7.5), ev)
    g.add(Quote("fanduel", "home", 2.58, -7.5, NOW - timedelta(seconds=fd_age_seconds)))
    g.add(Quote("draftkings", "away", 1.667, 7.5, NOW))
    g.add(Quote("fanatics", "away", 1.90, 7.5, NOW))
    return board


def test_live_config_reports_an_arb_in_a_game_two_hours_old():
    """The pregame defaults drop it (skip_live); the live config must not."""
    assert engine.find_arbitrages(_arb_board(), ArbConfig(), now=NOW) == []
    opps = engine.find_arbitrages(_arb_board(), live.live_config(ArbConfig()), now=NOW)
    assert len(opps) == 1
    assert {l.book for l in opps[0].legs} == {"fanduel", "draftkings"}


def test_live_config_never_pairs_a_book_it_cannot_bet_live():
    cfg = live.live_config(ArbConfig())
    assert cfg.books.bettable == ["draftkings", "fanduel"]
    assert not cfg.detect.ev_enabled


def test_a_cdn_aged_fanduel_leg_past_the_cap_is_refused():
    cfg = live.live_config(ArbConfig(), max_age_seconds=45)
    assert engine.find_arbitrages(_arb_board(fd_age_seconds=30), cfg, now=NOW)
    assert engine.find_arbitrages(_arb_board(fd_age_seconds=60), cfg, now=NOW) == []


def _opp(market, legs, pct=1.0, subject=None):
    return SimpleNamespace(market=market, subject=subject, profit_pct=pct,
                           legs=[SimpleNamespace(book=b, side=s, point=p) for b, s, p in legs])


def test_confirm_keeps_what_survives_and_shows_the_second_reads_price():
    first = [_opp("spreads", [("fanduel", "home", -7.5), ("draftkings", "away", 7.5)], 1.14),
             _opp("player_receptions", [("fanduel", "over", 2.5), ("draftkings", "under", 2.5)],
                  25.4, subject="Darnell Mooney")]
    second = [_opp("spreads", [("draftkings", "away", 7.5), ("fanduel", "home", -7.5)], 0.61)]
    kept, gone = live.confirm(first, second)
    assert [o.profit_pct for o in kept] == [0.61]
    assert [o.subject for o in gone] == ["Darnell Mooney"]


def test_confirm_does_not_accept_the_same_market_at_a_different_line():
    first = [_opp("spreads", [("fanduel", "home", -7.5), ("draftkings", "away", 7.5)])]
    second = [_opp("spreads", [("fanduel", "home", -8.5), ("draftkings", "away", 8.5)])]
    kept, gone = live.confirm(first, second)
    assert kept == [] and len(gone) == 1
