"""The cross-book league catalog, and the guards that came with it.

Scanning every sport the three books offer rests on two things being right:
the catalog agreeing that "English Premier League", "English Premier League"
and `/us/soccer/premier-league` are one league, and the market map refusing the
period markets that arrive once a feed is no longer filtered down to three bet
types. Both fail silently -- a wrong join produces no matches, a missing period
guard produces a fake arbitrage -- so both are tested here rather than left to
a live scan to reveal.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.arb import catalog                              # noqa: E402
from edge.arb.marketmap import canonical_market, is_full_game   # noqa: E402


# --- the three-book join ----------------------------------------------------
@pytest.mark.parametrize("fd_sport,fd_name,dk_sport,dk_name,path,expected", [
    ("baseball", "MLB", "baseball", "MLB", "/us/baseball/mlb/mets-at-braves",
     "baseball_mlb"),
    ("americanfootball", "NFL", "americanfootball", "NFL",
     "/us/football/nfl/patriots-at-seahawks", "americanfootball_nfl"),
    ("americanfootball", "NCAA Football Games", "americanfootball",
     "College Football", "/us/football/college-football/akron-at-wake-forest",
     "americanfootball_ncaaf"),
    ("soccer", "English Premier League", "soccer", "English Premier League",
     "/us/soccer/premier-league/aston-villa-v-arsenal", "soccer_epl"),
    ("soccer", "Spanish La Liga", "soccer", "Spain - La Liga",
     "/us/soccer/la-liga-primera/real-v-barca", "soccer_spain_la_liga"),
    ("icehockey", "NHL", "icehockey", "NHL", "/us/hockey/nhl/panthers-at-canes",
     "icehockey_nhl"),
])
def test_all_three_books_land_on_one_sport_key(fd_sport, fd_name, dk_sport,
                                               dk_name, path, expected):
    """sport_key is the join: match_event only merges two events that carry the
    same one. If the three books disagree here, three feeds sit on one game and
    never meet, which looks exactly like the books not offering the market."""
    assert catalog.fanduel_league(fd_sport, fd_name).key == expected
    assert catalog.draftkings_league(dk_sport, dk_name).key == expected
    assert catalog.fanatics_league(path).key == expected


def test_a_name_only_matches_within_its_own_sport():
    """"Championship" names a soccer competition, a golf tournament and a darts
    event. Without the sport check the first pattern to match would claim all
    three and file darts fixtures under EFL Championship."""
    assert catalog.draftkings_league("soccer", "England - Championship") is not None
    assert catalog.draftkings_league("darts", "England - Championship") is None
    assert catalog.draftkings_league("snooker", "England - Championship") is None


def test_tournament_sports_claim_their_whole_display_group():
    """Golf and tennis are a league PER TOURNAMENT, and the tournament names
    rotate weekly. They match on the sport alone and collapse onto one key --
    which is the same thing models.field_event does, for the same reason: the
    books do not agree on what a golf event is."""
    assert catalog.fanduel_league("golf", "LPGA FM Championship 2026").key == "golf_pga"
    assert catalog.fanduel_league("tennis", "Plovdiv Challenger 2026").key == "tennis_atp"
    # ...but only within their own sport: the catch-all must not reach out of it.
    assert catalog.fanduel_league("soccer", "PGA Tour Championship 2026") is None


def test_an_unknown_league_is_not_guessed_at():
    assert catalog.fanduel_league("soccer", "Bulgarian A PFG") is None
    assert catalog.draftkings_league("soccer", "Faroe Islands - Betri Deildin") is None
    assert catalog.fanatics_league("/us/soccer/somewhere-new/a-v-b") is None
    assert catalog.fanatics_league("") is None


def test_the_longest_matching_path_wins():
    """`/us/boxing-mma/ufc-mma` and a bare `/us/boxing-mma` would both match a
    UFC fixture; the specific one has to win or every fight lands on boxing."""
    assert catalog.fanatics_league(
        "/us/boxing-mma/ufc-mma/benouaich-v-montenegro").key == "mma_ufc"


def test_a_path_prefix_must_end_on_a_segment_boundary():
    """`/us/soccer/premier-league` must not claim `/us/soccer/premier-league-2`
    -- a different competition whose name merely starts the same way."""
    assert catalog.fanatics_league("/us/soccer/premier-league-2/a-v-b") is None


def test_uncatalogued_leagues_get_distinct_keys_per_sport():
    """Two books can still meet on a league nobody curated, but a bare
    competition name is not enough: "Championship" in golf and in soccer would
    collide into one board."""
    assert catalog.generic_key("soccer", "Bulgarian A PFG") != \
           catalog.generic_key("golf", "Bulgarian A PFG")
    assert catalog.generic_key("soccer", "Bulgarian A PFG") == \
           catalog.generic_key("soccer", "Bulgarian A PFG")


@pytest.mark.parametrize("sport,wrong_tier", [
    ("soccer", "Belgium - Challenger Pro League"),
    ("soccer", "Turkey - 1. Lig"),
    ("soccer", "Denmark - 1st Division"),
    ("soccer", "Sweden - Superettan"),
    ("soccer", "Sweden - 1st North"),
    ("soccer", "Netherlands - Eerste Divisie"),
    ("soccer", "Mexico - Liga MX (W)"),
    ("soccer", "Mexico - Liga de Expansion"),
    ("soccer", "Scotland - Championship"),
    ("soccer", "USA - MLS NEXT Pro"),
    ("soccer", "Europa Conference League"),
    ("soccer", "USA - USL Championship"),
    ("basketball", "Mexico - LNBP"),
])
def test_a_second_tier_is_not_mistaken_for_the_top_one(sport, wrong_tier):
    """Caught live: `r"Belgium"` matched "Belgium - Challenger Pro League"
    before it reached "Belgium - Jupiler Pro League", and the second tier's id
    404s on the feed -- a wasted request AND the real league lost. A country
    name is never specific enough; DraftKings lists two Turkish, two Danish,
    three Swedish and four Mexican competitions."""
    got = catalog.draftkings_league(sport, wrong_tier)
    assert got is None, f"{wrong_tier} matched {got.key if got else None}"


@pytest.mark.parametrize("sport,name,expected", [
    ("soccer", "Belgium - Jupiler Pro League", "soccer_belgium_first_div"),
    ("soccer", "Turkey - Super Lig", "soccer_turkey_super_lig"),
    ("soccer", "Denmark - Superligaen", "soccer_denmark_superliga"),
    ("soccer", "Sweden - Allsvenskan", "soccer_sweden_allsvenskan"),
    ("soccer", "Netherlands - Eredivisie", "soccer_netherlands_eredivisie"),
    ("soccer", "Mexico - Liga MX", "soccer_mexico_ligamx"),
    ("soccer", "England - League One", "soccer_england_league_one"),
    ("soccer", "England - League Two", "soccer_england_league_two"),
    ("soccer", "Champions League", "soccer_uefa_champs_league"),
    ("soccer", "Europa League", "soccer_uefa_europa"),
    ("soccer", "Portugal - Primeira Liga", "soccer_portugal_primeira_liga"),
    ("soccer", "Greece - Super League", "soccer_greece_super_league"),
    ("soccer", "Austria - Bundesliga", "soccer_austria_bundesliga"),
])
def test_the_top_tier_still_resolves(sport, name, expected):
    """Read off the live DraftKings catalog page on 2026-08-30. Anchoring the
    patterns is only safe if they still match the real names."""
    got = catalog.draftkings_league(sport, name)
    assert got is not None and got.key == expected, name


def test_every_catalog_key_is_unique():
    keys = [lg.key for lg in catalog.LEAGUES]
    assert len(keys) == len(set(keys))


def test_catalog_sports_exist_in_both_books():
    """A typo in fd_sport or dk_sport silently disables a league, because the
    lookup that resolves it returns nothing and nothing reports it."""
    from edge.arb.draftkings_league import DISPLAY_GROUPS
    from edge.arb.fanduel import SPORT_EVENT_TYPES
    for lg in catalog.LEAGUES:
        if lg.fd_sport:
            assert lg.fd_sport in SPORT_EVENT_TYPES, lg.key
        if lg.dk_sport:
            assert lg.dk_sport in DISPLAY_GROUPS, lg.key


# --- the full-game guard ----------------------------------------------------
@pytest.mark.parametrize("name", [
    "1st Quarter Point Spread", "Total Points - 1st Half", "1st Half Point Spread",
    "1st Inning Result", "1st Innings Runs", "1st 7 Innings Total Runs",
    "Half Time", "Half Time Including Draw", "Moneyline - Halves (3-Way)",
    "Correct Score", "Double Chance", "Total Goals Odd/Even", "Total Home Goals",
    "Total Away Goals", "Team Total Runs", "Race To 4 Runs", "Total Corners",
    "Most Cards", "Total Bookings", "To Win Either Half", "Winning Margin",
    "First Touchdown Scorer", "1st Set Winner", "Total Goals (Bands)",
])
def test_markets_that_are_not_the_whole_game_are_refused(name):
    """GroupKey carries no period and no team, so "1st Quarter Point Spread
    -1.5" and the full-game "Point Spread -1.5" produce the SAME key -- two
    different bets in one group, which is how a fake arbitrage is invented.

    This became load-bearing when the Fanatics feed stopped being filtered to
    three bet types: that request returns seventeen, most of them periods."""
    assert not is_full_game(name)
    assert canonical_market(name) is None


@pytest.mark.parametrize("name,expected", [
    ("Moneyline", "h2h"), ("Point Spread", "spreads"), ("Win Market", "h2h"),
    ("Total Runs", "totals"), ("Total Goals Over/Under", "totals"),
    ("Run Line", "spreads"), ("Puck Line", "spreads"),
    # An alternate ladder is the SAME bet as the main line at the same number,
    # so it deliberately shares the `spreads` key -- that is what lets a book's
    # alt -2.5 pair against another book's main -2.5.
    ("Alternate Run Line", "spreads"), ("Alternate Total Runs", "totals"),
    ("2 Ball (Round 3)", "golf_2ball"),
    ("Tournament Matchups", "golf_matchup"),
])
def test_full_game_markets_still_map(name, expected):
    assert is_full_game(name)
    assert canonical_market(name) == expected


@pytest.mark.parametrize("name", [
    "To Win To Nil", "Clean Sheet", "To Score In Both Halves",
    "To Score 2 Or More Goals", "Team To Score First", "To Score A Hat-Trick",
    "Anytime Goalscorer", "First Goalscorer", "Last Goalscorer",
])
def test_moneyline_lookalikes_are_refused(name):
    """Caught live, and it is the whole reason the market map is written to
    drop rather than guess. "To Win To Nil" is a moneyline plus a clean sheet,
    and it mapped to h2h off a bare `to win` in the rule. Fanatics priced it
    Eintracht Frankfurt 4.1 / Augsburg 7.0; filed as a three-way beside
    FanDuel's real Draw at 4.1 it summed to 0.63 and was reported as a 58%
    arbitrage on a Bundesliga match. Every number in that report was
    arithmetically correct."""
    assert canonical_market(name) is None


def test_a_real_moneyline_still_maps():
    for name in ("Moneyline", "Win Market", "Match Betting", "Match Result",
                 "To Win Match", "Head To Head", "1X2"):
        assert canonical_market(name) == "h2h", name


@pytest.mark.parametrize("name,expected", [
    ("Sets Handicap", "spreads_sets"), ("Games Handicap", "spreads_games"),
    ("Total Sets", "totals_sets"), ("Total Games", "totals_games"),
])
def test_tennis_counts_three_things_and_calls_them_all_handicaps(name, expected):
    """A set handicap of -1.5 and a games handicap of -1.5 are different bets.
    On one `spreads` key at one point they would be one group, and the pair
    would look like an arbitrage between two books that priced neither."""
    assert canonical_market(name) == expected


def test_set_betting_is_a_correct_score_not_a_handicap():
    """Its "lines" are 2-0 and 2-1 and its bets are player names. Claimed as a
    set handicap it lost its line to float("2-0"), fell through to the
    team-moneyline rule, and every rung collapsed onto one keyless group --
    last rung wins, leaving Joint 2-1 at 6.8 against Samsonova 2-1 at 4.0 and
    an arb_sum of 0.397."""
    assert canonical_market("Set Betting") is None


def test_a_spread_without_a_line_is_not_a_moneyline():
    """The second half of the same bug. Rule 3 only fired when a point was
    present, so a spread whose line failed to parse fell through to rule 4 and
    was read as a team moneyline -- which silently drops the line that makes a
    spread a spread."""
    from edge.arb.normalize import normalize_outcome
    assert normalize_outcome("spreads_sets", "Maya Joint", None, None,
                             "Maya Joint", "Liudmila Samsonova") is None
    assert normalize_outcome("spreads", "Kennesaw State", None, None,
                             "Kennesaw State", "West Georgia") is None
    # ...but with a line it still folds onto the home number
    assert normalize_outcome("spreads", "West Georgia", 23.5, None,
                             "Kennesaw State", "West Georgia") == ("away", None, -23.5)


def test_tennis_keys_still_read_as_spreads_and_totals():
    """normalize keys team-vs-Over/Under handling off the market name, so a new
    spread-like key has to still look like a spread to it."""
    from edge.arb.normalize import is_spread_market, is_team_side
    assert is_spread_market("spreads_sets") and is_team_side("spreads_sets")
    assert is_spread_market("spreads_games") and is_team_side("spreads_games")
    assert not is_spread_market("totals_sets")


def test_the_period_guard_is_not_bypassed_by_a_player():
    """`_first_match` cannot tell "matched a rule mapping to None" from "no rule
    matched", so a None claimed inside RULES falls straight through to
    PLAYER_STATS. The guard has to sit outside both lists -- with a player in
    context, "1st Quarter Player Points" would otherwise map to player_points
    and meet a full-game player line."""
    assert canonical_market("1st Quarter Points", player="Nikola Jokic") is None
    assert canonical_market("Points", player="Nikola Jokic") == "player_points"


# --- DraftKings catalog + main lines ---------------------------------------
CATALOG_SNIPPET = (
    '[{"displayGroupId":1,"eventGroupId":40253,"eventGroupName":"English Premier League"},'
    '{"displayGroupId":1,"eventGroupId":89345,"eventGroupName":"MLS"},'
    '{"displayGroupId":7,"eventGroupId":84240,"eventGroupName":"MLB"},'
    '{"displayGroupId":43,"eventGroupId":9034,"eventGroupName":"UFC"}]')


def test_the_whole_book_is_read_off_one_page():
    """One request returns every league DraftKings lists. There is no API that
    does -- see HANDOFF.md §5 -- so this page is the only route, and reading it
    per sport would mean a request per sport for data already in hand."""
    from edge.arb.draftkings_league import parse_catalog
    got = parse_catalog(CATALOG_SNIPPET)
    assert got[1] == {40253: "English Premier League", 89345: "MLS"}
    assert got[7] == {84240: "MLB"}
    assert got[43] == {9034: "UFC"}


@pytest.mark.parametrize("html", ["", None, "<html>nothing</html>"])
def test_catalog_parsing_fails_soft(html):
    from edge.arb.draftkings_league import parse_catalog
    assert parse_catalog(html) == {}


def test_main_lines_are_ordered_not_taken_as_listed():
    """DraftKings' subcategory order is arbitrary, and a cap applied to it
    drops markets by position -- the same trap prop_subcategories documents.
    Soccer is the case that matters: its spreads and totals live here, not in
    the league feed, so a cap that lost them would leave a soccer league as a
    moneyline and nothing else."""
    from edge.arb.draftkings_league import main_line_subcategories
    payload = {"subcategories": [
        {"categoryId": 653, "id": 17968, "name": "Asian Handicap"},
        {"categoryId": 544, "id": 11273, "name": "Moneyline - Halves (3-Way)"},
        {"categoryId": 543, "id": 17865, "name": "Total Corners"},
        {"categoryId": 490, "id": 13171, "name": "Total Goals"},
        {"categoryId": 758, "id": 12980, "name": "Team Total Runs"},
        {"categoryId": 490, "id": 13170, "name": "Spread"},
    ]}
    got = main_line_subcategories(payload, limit=4)
    assert got == [(490, 13170, "Spread"), (490, 13171, "Total Goals")]


def test_main_lines_respect_the_limit():
    from edge.arb.draftkings_league import main_line_subcategories
    payload = {"subcategories": [
        {"categoryId": 490, "id": 1, "name": "Spread"},
        {"categoryId": 490, "id": 2, "name": "Total"},
        {"categoryId": 490, "id": 3, "name": "Alternate Spread"},
        {"categoryId": 490, "id": 4, "name": "Alternate Total"},
    ]}
    assert len(main_line_subcategories(payload, limit=2)) == 2


# --- Fanatics id discovery --------------------------------------------------
def test_outright_containers_are_not_treated_as_leagues():
    """`/us/basketball/nba` resolves to 1554, "NBA Championship" -- a futures
    field with no second side to price against. Keeping it would put a
    30-runner outright on the board in place of fixtures."""
    from edge.arb.oddschecker_discover import resolve
    rows = [{"event_id": 1554, "name": "NBA Championship", "type": "OUTRIGHT",
             "url_path": "/us/basketball/nba/nba-championship", "subevents": 1}]
    assert resolve(rows) == []


def test_the_fuller_of_two_containers_wins():
    """Oddschecker keeps a thinned duplicate of some leagues under the same
    path -- 9934 "German Bundesliga" beside 9935 "German Bundesliga Matches"."""
    from edge.arb.oddschecker_discover import resolve
    rows = [
        {"event_id": 9934, "name": "German Bundesliga", "type": "MATCH",
         "url_path": "/us/soccer/bundesliga/a-v-b", "subevents": 1},
        {"event_id": 9935, "name": "German Bundesliga Matches", "type": "MATCH",
         "url_path": "/us/soccer/bundesliga/c-v-d", "subevents": 9},
    ]
    got = resolve(rows)
    assert [r["event_id"] for r in got] == [9935]


def test_a_tournament_sport_keeps_every_container():
    """The men's and women's US Open are separate ids under one collapsed
    sport_key. Deduplicating them the way a fixed league is deduplicated would
    drop a whole draw -- 63 matches."""
    from edge.arb.oddschecker_discover import resolve
    rows = [
        {"event_id": 23733, "name": "Mens US Open Matches", "type": "MATCH",
         "url_path": "/us/tennis/mens-us-open/a-v-b", "subevents": 3},
        {"event_id": 23743, "name": "Womens US Open Matches", "type": "MATCH",
         "url_path": "/us/tennis/womens-us-open/c-v-d", "subevents": 3},
    ]
    assert sorted(r["event_id"] for r in resolve(rows)) == [23733, 23743]


def test_a_missing_cache_costs_coverage_not_the_scan():
    from edge.arb.oddschecker_discover import load, resolve
    assert load("data/definitely-not-here.json") == []
    assert resolve(path="data/definitely-not-here.json") == []


# --- Fanatics player props --------------------------------------------------
def _oddschecker_payload(market_name, bets):
    soon = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    return {"subevents": [{
        "id": 1, "name": "New England Patriots at Seattle Seahawks",
        "startTime": soon, "inRunning": False,
        "homeTeam": {"name": "Seattle Seahawks"},
        "awayTeam": {"name": "New England Patriots"},
        "markets": [{"betTypeId": 1, "name": market_name, "bets": [
            {"name": n, "line": ({"name": str(ln)} if ln is not None else None),
             "odds": [{"bookmakerCode": "FNP", "status": "ACTIVE", "decimal": d}]}
            for n, ln, d in bets]}],
    }]}


def test_a_players_line_is_keyed_by_the_player():
    """The player is inside the bet name -- "Drake Maye Over" at line 220.5 --
    and there is no `description` field to carry it. Left to normalize_outcome
    the subject is None, so every quarterback's passing line lands in ONE group
    keyed only by the yardage: the collapse that put 106 prop groups on
    `totals`."""
    from edge.arb.books import ingest_oddschecker
    from edge.arb.models import Board
    board = Board()
    ingest_oddschecker(board, _oddschecker_payload("Player Passing Yards", [
        ("Drake Maye Over", 220.5, 1.77), ("Drake Maye Under", 220.5, 2.0),
        ("Sam Darnold Over", 180.5, 1.9), ("Sam Darnold Under", 180.5, 1.9),
    ]), book="fanatics", sport_key="americanfootball_nfl", strict_match=False)
    subjects = {g.key.subject for g in board.groups.values()}
    assert subjects == {"Drake Maye", "Sam Darnold"}
    for g in board.groups.values():
        assert set(g.quotes) == {"over", "under"}, g.key


def test_alternate_rungs_of_one_players_ladder_stay_separate():
    from edge.arb.books import ingest_oddschecker
    from edge.arb.models import Board
    board = Board()
    ingest_oddschecker(board, _oddschecker_payload("Player Passing Yards", [
        ("Drake Maye Over", 220.5, 1.77), ("Drake Maye Under", 220.5, 2.0),
        ("Drake Maye Over", 215.5, 1.77), ("Drake Maye Under", 215.5, 2.0),
    ]), book="fanatics", sport_key="americanfootball_nfl", strict_match=False)
    assert sorted(g.key.point for g in board.groups.values()) == [215.5, 220.5]


def test_a_field_of_bare_player_names_is_not_folded_into_one_group():
    """"Anytime Touchdown Scorer" lists twenty players and no opposite side.
    Keyed the way a two-sided line is, all twenty become "sides" of a single
    group -- an outright field assembled from one book's partial list, which
    `test_truncated_outright_field_is_refused` already exists to prevent."""
    from edge.arb.books import ingest_oddschecker
    from edge.arb.models import Board
    board = Board()
    st = ingest_oddschecker(board, _oddschecker_payload("Anytime Touchdown Scorer", [
        ("Jadarian Price", None, 1.9), ("Rhamondre Stevenson", None, 2.25),
        ("A.J. Brown", None, 2.85),
    ]), book="fanatics", sport_key="americanfootball_nfl", strict_match=False)
    assert not board.groups
    assert any("no player" in u for u in st["markets_unmapped"])


# --- FanDuel: sports beyond the seven with a page slug ----------------------
@pytest.mark.parametrize("market_type,expected", [
    ("WIN-DRAW-WIN", "h2h"),        # soccer's three-way
    ("HEAD_TO_HEAD", "h2h"),        # boxing
    ("MATCH_BETTING", "h2h"),       # tennis and MMA
    ("MONEY_LINE", "h2h"),
    ("MATCH_HANDICAP_(2-WAY)", "spreads"),
    ("TOTAL_POINTS_(OVER/UNDER)", "totals"),
])
def test_fanduel_classifies_the_other_sports_moneylines(market_type, expected):
    """WIN-DRAW-WIN reaches none of the tolerant word tests -- they split on
    "_" and it is hyphenated -- so without an explicit entry every soccer
    moneyline was dropped, which is most of what soccer prices."""
    from edge.arb.fanduel import classify
    assert classify(market_type)[0] == expected


def test_fanduel_lists_vs_fixtures_as_well_as_at_fixtures():
    """A fixture is "Away @ Home" in a team sport and "A vs B" in an individual
    one. Requiring "@" was right while only the US leagues were scanned and
    silently dropped every soccer, tennis, MMA and boxing event once they were
    added."""
    from edge.arb.fanduel import FanDuelScrape
    payload = {"attachments": {"events": {
        "1": {"name": "Mets @ Braves", "openDate": "2026-09-01T23:00:00.000Z"},
        "2": {"name": "Man Utd v Ipswich", "openDate": "2026-09-01T15:30:00.000Z"},
        "3": {"name": "Alcaraz vs Sinner", "openDate": "2026-09-01T18:00:00.000Z"},
        "4": {"name": "MLB Player Markets", "openDate": "2099-01-01T00:00:00.000Z"},
    }}}
    got = FanDuelScrape.list_events(FanDuelScrape.__new__(FanDuelScrape),
                                   "baseball_mlb", payload)
    assert sorted(e[0] for e in got) == ["1", "2", "3"]


def test_one_sport_payload_splits_into_leagues():
    """`page=SPORT` returns 146 soccer events across 108 competitions in one
    call. They must not share a sport_key: match_event joins on it, so filing
    all of soccer under "soccer" would let a Bundesliga fixture match a Serie A
    one on team-name similarity alone."""
    from edge.arb.fanduel import FanDuelScrape
    from edge.arb.models import Board
    soon = (datetime.now(timezone.utc) + timedelta(hours=6)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")
    def moneyline(eid, a, b):
        return {"eventId": eid, "marketType": "WIN-DRAW-WIN", "marketStatus": "OPEN",
                "runners": [{"runnerName": n, "runnerStatus": "ACTIVE", "handicap": 0,
                             "winRunnerOdds": {"trueOdds": {"decimalOdds":
                                                            {"decimalOdds": 2.0}}}}
                            for n in (a, b)]}

    payload = {"attachments": {
        "events": {
            "1": {"name": "Man Utd v Ipswich", "openDate": soon, "competitionId": 11},
            "2": {"name": "Napoli v Como", "openDate": soon, "competitionId": 22},
            "3": {"name": "Sofia v Levski", "openDate": soon, "competitionId": 33},
        },
        "markets": {"m1": moneyline("1", "Man Utd", "Ipswich"),
                    "m2": moneyline("2", "Napoli", "Como"),
                    "m3": moneyline("3", "Sofia", "Levski")},
    }}
    comps = {"11": {"name": "English Premier League"},
             "22": {"name": "Italian Serie A"},
             "33": {"name": "Bulgarian A PFG"}}

    def sport_key_of(ev):
        name = (comps.get(str(ev.get("competitionId"))) or {}).get("name") or ""
        league = catalog.fanduel_league("soccer", name)
        return league.key if league else catalog.generic_key("soccer", name)

    board = Board()
    FanDuelScrape.ingest_event(FanDuelScrape.__new__(FanDuelScrape), board,
                               payload, "soccer", strict_match=False,
                               sport_key_of=sport_key_of)
    keys = sorted(e.sport_key for e in board.events.values())
    assert keys == ["soccer_bulgarian_a_pfg", "soccer_epl", "soccer_italy_serie_a"]


def test_an_event_the_router_declines_is_skipped():
    from edge.arb.fanduel import FanDuelScrape
    from edge.arb.models import Board
    soon = (datetime.now(timezone.utc) + timedelta(hours=6)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")
    payload = {"attachments": {
        "events": {"1": {"name": "A v B", "openDate": soon, "competitionId": 9}},
        "markets": {}}}
    board = Board()
    st = FanDuelScrape.ingest_event(FanDuelScrape.__new__(FanDuelScrape), board,
                                    payload, "soccer", strict_match=False,
                                    sport_key_of=lambda ev: None)
    assert st["skipped_events"] == 1
    assert not board.events


# --- team markets filed as game markets -------------------------------------
# Reported from the DraftKings app on 2026-08-31: the scanner showed
# DraftKings Over 6.5 at +330 on SD Padres @ CIN Reds and called it a free
# middle *and* a straight arbitrage. The book's own app showed -310. The +330
# was the CINCINNATI REDS' team total for 6.5 runs, filed as the GAME total.
_TEAM_TOTAL = {
    "markets": [{"id": "1", "eventId": "9",
                 "marketType": {"name": "Alternate Team Total Runs"},
                 "name": "Alternate CIN Reds Total Runs"}],
    "selections": [
        {"marketId": "1", "label": "Over", "points": 6.5, "trueOdds": 4.30,
         "participants": [{"name": "CIN Reds", "type": "Team"}]},
        {"marketId": "1", "label": "Under", "points": 6.5, "trueOdds": 1.20,
         "participants": [{"name": "CIN Reds", "type": "Team"}]},
    ],
}
_GAME_TOTAL = {
    "markets": [{"id": "2", "eventId": "9",
                 "marketType": {"name": "Total Alternate"},
                 "name": "Total Alternate"}],
    "selections": [
        {"marketId": "2", "label": "Over", "points": 6.5, "trueOdds": 1.3226},
        {"marketId": "2", "label": "Under", "points": 6.5, "trueOdds": 3.25},
    ],
}


def _fold(payload):
    from edge.arb.draftkings_nash import ingest_sportscontent
    from edge.arb.models import Board, EventMeta
    ev = EventMeta("9", "baseball_mlb", "MLB",
                   datetime.now(timezone.utc) + timedelta(hours=6),
                   "CIN Reds", "SD Padres")
    board = Board()
    st = ingest_sportscontent(board, payload, sport_key="baseball_mlb",
                              strict_match=False, event=ev)
    return board, st


def test_a_refused_market_is_not_resurrected_by_its_display_name():
    """The guard worked and the fallback undid it. `is_full_game` says "never
    map this"; `canonical_market` returning None says only "no rule matched".
    Treating them alike meant marketType "Alternate Team Total Runs" was
    refused, then market name "Alternate CIN Reds Total Runs" -- which names no
    team the pattern knows -- was accepted as `totals`."""
    board, st = _fold(_TEAM_TOTAL)
    assert not board.groups
    assert any("not the full game" in u for u in st["markets_unmapped"])


def test_the_game_total_itself_still_lands():
    board, _ = _fold(_GAME_TOTAL)
    (key, group), = board.groups.items()
    assert key.market == "totals" and key.point == 6.5
    assert round(group.quotes["over"]["draftkings"].decimal, 4) == 1.3226


def test_a_team_total_never_shares_a_group_with_the_game_total():
    """The actual damage: both are "Over 6.5", GroupKey carries no team, so
    best() took the 4.30 and paired it against another book's under."""
    payload = {"markets": _TEAM_TOTAL["markets"] + _GAME_TOTAL["markets"],
               "selections": _TEAM_TOTAL["selections"] + _GAME_TOTAL["selections"]}
    board, _ = _fold(payload)
    assert len(board.groups) == 1
    (_key, group), = board.groups.items()
    assert set(group.quotes["over"]) == {"draftkings"}
    assert round(group.quotes["over"]["draftkings"].decimal, 4) == 1.3226, \
        "the game total, not the team's 4.30"


def test_one_team_across_every_selection_is_refused_whatever_it_is_called():
    """The backstop, independent of any name: a game total carries no
    participants and a moneyline carries both teams, so a single distinct team
    across two or more selections is the shape of a team market -- which is
    what catches the next label nobody anticipated."""
    payload = {"markets": [{"id": "3", "eventId": "9",
                            "marketType": {"name": "Total Runs"},
                            "name": "Total Runs"}],
               "selections": [
                   {"marketId": "3", "label": "Over", "points": 6.5, "trueOdds": 4.30,
                    "participants": [{"name": "CIN Reds", "type": "Team"}]},
                   {"marketId": "3", "label": "Under", "points": 6.5, "trueOdds": 1.20,
                    "participants": [{"name": "CIN Reds", "type": "Team"}]}]}
    board, st = _fold(payload)
    assert not board.groups
    assert any("one team" in u for u in st["markets_unmapped"])


def test_a_moneyline_naming_both_teams_is_not_mistaken_for_a_team_market():
    payload = {"markets": [{"id": "4", "eventId": "9",
                            "marketType": {"name": "Moneyline"}, "name": "Moneyline"}],
               "selections": [
                   {"marketId": "4", "label": "CIN Reds", "trueOdds": 1.8,
                    "participants": [{"name": "CIN Reds", "type": "Team"}]},
                   {"marketId": "4", "label": "SD Padres", "trueOdds": 2.1,
                    "participants": [{"name": "SD Padres", "type": "Team"}]}]}
    board, _ = _fold(payload)
    (_key, group), = board.groups.items()
    assert set(group.quotes) == {"home", "away"}


def test_subcategories_are_gated_on_their_category_not_just_their_name():
    """MLB category 1674 is "Team Totals" and its subcategories are called, in
    full, "Total Runs" and "Alternate Total Runs" -- identical by name to the
    game totals in category 493 "Game Lines". The subcategory name alone cannot
    tell them apart, so matching on it pulled one team's runs into the scan."""
    from edge.arb.draftkings_league import main_line_subcategories
    payload = {
        "categories": [{"id": 493, "name": "Game Lines"},
                       {"id": 1674, "name": "Team Totals"},
                       {"id": 1024, "name": "1st Inning"}],
        "subcategories": [
            {"categoryId": 1674, "id": 16209, "name": "Total Runs"},
            {"categoryId": 1674, "id": 16208, "name": "Alternate Total Runs"},
            {"categoryId": 1024, "id": 111, "name": "Total Runs"},
            {"categoryId": 493, "id": 13169, "name": "Alternate Total Runs"},
        ],
    }
    assert main_line_subcategories(payload, 4) == [(493, 13169, "Alternate Total Runs")]


@pytest.mark.parametrize("name", ["Team Totals", "Alt Team Totals",
                                  "Alternate Team Total Runs", "Team Total Runs"])
def test_team_total_is_refused_in_singular_and_plural(name):
    """`\\bteam total\\b` did not match "Team Totals" -- the plural's "s" kills
    the word boundary -- and "Team Totals" is exactly what the CATEGORY is
    called."""
    assert not is_full_game(name)


@pytest.mark.parametrize("name", [
    "Either Pitcher Strikeouts Thrown", "Combined Pitcher Strikeouts Thrown",
    "Either Player to have X Receiving Yards", "Combined Receiving Yards Milestones",
    "Combined Player Props", "Either Player Props",
])
def test_multi_subject_markets_are_refused(name):
    """GroupKey carries ONE subject, so a two-player market arrives with
    subject=None and every such market in an event collapses onto one key.
    "Either Pitcher Strikeouts Thrown" (one of the two starters reaches 11.5)
    and "Combined Pitcher Strikeouts Thrown" (their totals added) are different
    bets that both keyed to pitcher_strikeouts / 11.5 / over -- 19.00 against
    1.613 in a single group, on 29 of one night's games.

    ingest_sportscontent already refuses a market whose selections name two
    Players; these do not populate `participants`, so the name is the only
    signal left."""
    assert canonical_market(name) is None


def test_single_subject_prop_markets_still_map():
    for name, expected in [("Strikeouts", "pitcher_strikeouts"),
                           ("Pitcher Strikeouts", "pitcher_strikeouts"),
                           ("Player Receiving Yards", "player_reception_yds")]:
        assert canonical_market(name) == expected, name
    # "Total Bases" is a game total with no player and a prop with one -- the
    # player context decides which rule set wins, not rule ordering.
    assert canonical_market("Total Bases O/U") == "totals"
    assert canonical_market("Total Bases O/U", player="Tatis") == "batter_total_bases"


def test_one_book_quoting_one_side_twice_is_counted_as_a_conflict():
    """The smoke alarm. Every price bug so far had the same shape -- two
    different bets on one GroupKey -- and Board.add() silently kept one of
    them, which is why they were invisible after the fact. In a one-shot scan
    every quote is seconds old, so a 25% gap is never a price move."""
    from edge.arb.models import GroupKey, MarketGroup, EventMeta, Quote
    now = datetime.now(timezone.utc)
    ev = EventMeta("9", "baseball_mlb", "MLB", now + timedelta(hours=6), "CIN", "SD")
    g = MarketGroup(GroupKey("9", "totals", None, 6.5), ev)
    g.add(Quote("draftkings", "over", 1.3226, 6.5, now))
    g.add(Quote("draftkings", "over", 4.30, 6.5, now))     # the team total
    assert len(g.conflicts) == 1
    side, book, old, new = g.conflicts[0]
    assert (side, book) == ("over", "draftkings")
    assert {round(old, 4), round(new, 4)} == {1.3226, 4.30}


def test_an_ordinary_reprice_is_not_a_conflict():
    from edge.arb.models import GroupKey, MarketGroup, EventMeta, Quote
    now = datetime.now(timezone.utc)
    ev = EventMeta("9", "baseball_mlb", "MLB", now + timedelta(hours=6), "CIN", "SD")
    g = MarketGroup(GroupKey("9", "totals", None, 6.5), ev)
    g.add(Quote("draftkings", "over", 1.90, 6.5, now))
    g.add(Quote("draftkings", "over", 2.05, 6.5, now + timedelta(seconds=30)))
    assert g.conflicts == []
    assert g.quotes["over"]["draftkings"].decimal == 2.05


def test_two_books_disagreeing_is_not_a_conflict():
    """Books disagreeing IS the product. Only the same book quoting the same
    side twice means two markets landed on one key."""
    from edge.arb.models import GroupKey, MarketGroup, EventMeta, Quote
    now = datetime.now(timezone.utc)
    ev = EventMeta("9", "baseball_mlb", "MLB", now + timedelta(hours=6), "CIN", "SD")
    g = MarketGroup(GroupKey("9", "totals", None, 6.5), ev)
    g.add(Quote("draftkings", "over", 1.32, 6.5, now))
    g.add(Quote("fanduel", "over", 4.30, 6.5, now))
    assert g.conflicts == []


# --- team names that look alike inside one fixture --------------------------
@pytest.mark.parametrize("runner,home,away,expected", [
    # normalize_team drops "city" as noise, so "Man City" becomes "man" and
    # _mascot_extension reads "Man Utd" as "man" + a mascot: 0.90 against the
    # HOME team. Home was tested first, so BOTH runners became `home`, the away
    # price overwrote the home price, and the group held a moneyline summing to
    # 0.59 -- two prices for the same side of the same market.
    ("Man Utd", "Man City", "Man Utd", "away"),
    ("Man City", "Man City", "Man Utd", "home"),
    ("Omonia", "Omonia FC Aradippou", "Omonia", "away"),
    ("Omonia FC Aradippou", "Omonia FC Aradippou", "Omonia", "home"),
    # the ordinary cases must keep working
    ("SEA Seahawks", "Seattle Seahawks", "New England Patriots", "home"),
    ("LSU Tigers", "LSU", "Clemson", "home"),
    ("West Georgia", "Kennesaw State", "West Georgia", "away"),
])
def test_the_better_matching_team_wins_not_the_first(runner, home, away, expected):
    from edge.arb.normalize import normalize_outcome
    got = normalize_outcome("h2h", runner, None, None, home, away)
    assert got is not None and got[0] == expected, f"{runner} -> {got}"


def test_a_genuinely_ambiguous_name_is_refused():
    """When a name fits both sides equally there is no evidence to pick one.
    A wrong side is a fake arbitrage; a dropped one is only a gap."""
    from edge.arb.normalize import normalize_outcome, pick_team_side
    assert pick_team_side("Rangers", "Rangers", "Rangers") is None
    # A spread cannot be placed without a side, so it is dropped outright.
    assert normalize_outcome("spreads", "Rangers", 1.5, None, "Rangers", "Rangers") is None
    # A moneyline falls through to the named-outcome rule instead. That is the
    # safe outcome: it will not pair with another book's home/away, so the
    # market goes unmatched rather than landing on the wrong side.
    side, _subj, _pt = normalize_outcome("h2h", "Rangers", None, None,
                                         "Rangers", "Rangers")
    assert side not in ("home", "away")


# --- FanDuel's parenthesised alternate handicap -----------------------------
def test_the_alternate_handicap_line_is_read_out_of_the_runner_name():
    """ALTERNATE_HANDICAP writes the line inside the name -- "New England
    Patriots (-14.5)" -- and leaves `handicap` at 0. Neither the "Over 44.5"
    nor the "Twins +6.5" form matches that, so the name stayed whole and the
    line came back 0: twenty-two rungs per game, both teams, all on one group
    at line 0, last one winning."""
    from edge.arb.fanduel import TEAM_PAREN_RE
    assert TEAM_PAREN_RE.match("New England Patriots (-14.5)").groups() == \
        ("New England Patriots", "-14.5")
    assert TEAM_PAREN_RE.match("Seattle Seahawks (+3.5)").groups() == \
        ("Seattle Seahawks", "+3.5")
    # the other two forms must not be claimed by it
    assert TEAM_PAREN_RE.match("Minnesota Twins +6.5") is None
    assert TEAM_PAREN_RE.match("Over 44.5") is None


def test_an_alternate_spread_ladder_keeps_its_rungs_apart():
    from edge.arb.fanduel import FanDuelScrape
    from edge.arb.models import Board
    soon = (datetime.now(timezone.utc) + timedelta(hours=6)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")
    runners = [("New England Patriots (-14.5)", 12.0),
               ("New England Patriots (-15.5)", 13.0),
               ("Seattle Seahawks (+14.5)", 1.05)]
    payload = {"attachments": {
        "events": {"1": {"name": "New England Patriots @ Seattle Seahawks",
                         "openDate": soon}},
        "markets": {"m": {"eventId": "1", "marketType": "ALTERNATE_HANDICAP",
                          "marketStatus": "OPEN",
                          "runners": [
                              {"runnerName": n, "runnerStatus": "ACTIVE", "handicap": 0,
                               "winRunnerOdds": {"trueOdds": {"decimalOdds":
                                                              {"decimalOdds": d}}}}
                              for n, d in runners]}},
    }}
    board = Board()
    FanDuelScrape.ingest_event(FanDuelScrape.__new__(FanDuelScrape), board, payload,
                               "americanfootball_nfl", strict_match=False)
    # Patriots are AWAY here, so their -14.5 folds onto the home number +14.5 --
    # which is exactly what puts it in the same group as "Seahawks (+14.5)".
    points = sorted({g.key.point for g in board.groups.values()})
    assert points == [14.5, 15.5], points
    paired = [g for g in board.groups.values() if g.key.point == 14.5][0]
    assert set(paired.quotes) == {"home", "away"}
    assert not any(g.conflicts for g in board.groups.values())


@pytest.mark.parametrize("name", ["Alt Spread (Regular Time)", "Alt Total (Regular Time)",
                                  "Spread (Reg. Time)", "Total Goals 90 Mins"])
def test_a_regular_time_ladder_is_not_merged_with_the_match_spread(name):
    """"(Regular Time)" is a settlement basis, not a decoration. DraftKings
    prices soccer "Spread" and "Alt Spread (Regular Time)" as two ladders on
    one match and they disagree -- Tottenham -2.5 was 7.0 on one and 8.5 on the
    other. Merged onto `spreads` they became two prices for one side of one
    group, 23 of them across EPL, the Champions League and La Liga.

    The qualifier is only in the SUBCATEGORY name, never in the marketType, so
    main_line_subcategories is where it has to be refused."""
    assert not is_full_game(name)


def test_the_plain_soccer_ladders_are_still_taken():
    from edge.arb.draftkings_league import main_line_subcategories
    payload = {"categories": [{"id": 490, "name": "Match Lines"}],
               "subcategories": [
                   {"categoryId": 490, "id": 13170, "name": "Spread"},
                   {"categoryId": 490, "id": 13171, "name": "Total Goals"},
                   {"categoryId": 490, "id": 16289, "name": "Alt Spread (Regular Time)"}]}
    assert main_line_subcategories(payload, 4) == [(490, 13170, "Spread"),
                                                   (490, 13171, "Total Goals")]


# --- a ladder that does not reprice is not a ladder -------------------------
def _ladder_payload(lines_and_prices, market="Point Spread"):
    soon = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    bets = []
    for line, home_d, away_d in lines_and_prices:
        bets.append({"name": "Delaware", "line": {"name": f"-{line}"},
                     "odds": [{"bookmakerCode": "FNP", "status": "ACTIVE",
                               "decimal": home_d}]})
        bets.append({"name": "Merrimack", "line": {"name": f"+{line}"},
                     "odds": [{"bookmakerCode": "FNP", "status": "ACTIVE",
                               "decimal": away_d}]})
    return {"subevents": [{"id": 1, "name": "Merrimack at Delaware",
                           "startTime": soon, "inRunning": False,
                           "homeTeam": {"name": "Delaware"},
                           "awayTeam": {"name": "Merrimack"},
                           "markets": [{"betTypeId": 525, "name": market,
                                        "bets": bets}]}]}


def test_a_ladder_priced_the_same_at_every_rung_is_refused():
    """Seen live on a large minority of NCAAF events: Oddschecker returned the
    Merrimack at Delaware spread at 23.5, 25, 26 and 26.5 with -110 on both
    sides of all four. P(cover -23.5) is not P(cover -26.5), so those rungs
    carry no line-specific information and there is no way to tell which (if
    any) is real.

    Left in, they manufactured four "free middles" on that one game: a -110
    attached to the wrong number beat DraftKings' genuine +27.5 and the pair
    summed under 1.00, so it was reported as a straight arbitrage."""
    from edge.arb.books import ingest_oddschecker
    from edge.arb.models import Board
    board = Board()
    st = ingest_oddschecker(board, _ladder_payload([
        (23.5, 1.9091, 1.9091), (25, 1.9091, 1.9091),
        (26, 1.9091, 1.9091), (26.5, 1.9091, 1.9091)]),
        book="fanatics", sport_key="americanfootball_ncaaf", strict_match=False)
    assert not board.groups
    assert st["flat_ladders"] == 1
    assert any("flat ladder" in u for u in st["markets_unmapped"])


def test_a_ladder_that_reprices_is_kept():
    from edge.arb.books import ingest_oddschecker
    from edge.arb.models import Board
    board = Board()
    st = ingest_oddschecker(board, _ladder_payload([
        (23.5, 1.74, 2.14), (25, 1.87, 1.95),
        (26, 1.95, 1.87), (26.5, 2.00, 1.83)]),
        book="fanatics", sport_key="americanfootball_ncaaf", strict_match=False)
    assert st["flat_ladders"] == 0
    assert len(board.groups) == 4


def test_one_symmetric_rung_is_the_main_line_and_is_kept():
    """A book prices its main line -110 both ways all the time. It is the
    SECOND symmetric rung that is impossible, so one is left alone."""
    from edge.arb.books import ingest_oddschecker
    from edge.arb.models import Board
    board = Board()
    st = ingest_oddschecker(board, _ladder_payload([
        (28.5, 1.9091, 1.9091),      # the main line, symmetric and real
        (29, 1.8696, 1.9524), (29.5, 1.8696, 1.9524), (28, 2.0, 1.8333)]),
        book="fanatics", sport_key="americanfootball_ncaaf", strict_match=False)
    assert st["placeholder_rungs"] == 0
    # spreads fold onto the home axis, so the sign is the home team's
    assert sorted(abs(g.key.point) for g in board.groups.values()) == \
        [28.0, 28.5, 29.0, 29.5]


def test_placeholder_rungs_are_dropped_without_taking_the_real_one():
    """The partial version, which the whole-market check is too blunt to see.
    North Carolina A&T at Georgia State came back with Total Points at 63.5, 56
    and 55.5 all -110/-110 plus a real 64.5 at -105/-115. Three distinct prices
    in the market, so the flat-ladder check did not fire, and Over 55.5 at -110
    went on the board against a genuine DraftKings Under 56.5 and was reported
    as a free middle. Over 55.5 and Over 63.5 cannot both be -110 -- they are
    eight points apart -- and the book's own app had the total at 56.5 with the
    over at -235, a line the feed does not even carry."""
    from edge.arb.books import ingest_oddschecker
    from edge.arb.models import Board
    soon = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    bets = []
    for line, over, under in [(63.5, 1.9091, 1.9091), (56, 1.9091, 1.9091),
                              (55.5, 1.9091, 1.9091), (64.5, 1.9524, 1.8696)]:
        for name, dec in (("Over", over), ("Under", under)):
            bets.append({"name": name, "line": {"name": str(line)},
                         "odds": [{"bookmakerCode": "FNP", "status": "ACTIVE",
                                   "decimal": dec}]})
    payload = {"subevents": [{"id": 1, "name": "North Carolina A&T at Georgia State",
                              "startTime": soon, "inRunning": False,
                              "homeTeam": {"name": "Georgia State"},
                              "awayTeam": {"name": "North Carolina A&T"},
                              "markets": [{"betTypeId": 526, "name": "Total Points",
                                           "bets": bets}]}]}
    board = Board()
    st = ingest_oddschecker(board, payload, book="fanatics",
                            sport_key="americanfootball_ncaaf", strict_match=False)
    assert st["placeholder_rungs"] == 3
    assert sorted(g.key.point for g in board.groups.values()) == [64.5]


def test_a_one_sided_rung_is_not_mistaken_for_a_symmetric_one():
    """Both sides are needed to call a rung symmetric: keying on the price
    alone cannot tell a two-sided -110/-110 from a lone quote."""
    from edge.arb.books import ingest_oddschecker
    from edge.arb.models import Board
    soon = (datetime.now(timezone.utc) + timedelta(hours=6)).isoformat()
    bets = [{"name": "Over", "line": {"name": str(ln)},
             "odds": [{"bookmakerCode": "FNP", "status": "ACTIVE", "decimal": 1.9091}]}
            for ln in (55.5, 56.5, 57.5)]
    payload = {"subevents": [{"id": 1, "name": "A at B", "startTime": soon,
                              "inRunning": False,
                              "homeTeam": {"name": "B"}, "awayTeam": {"name": "A"},
                              "markets": [{"betTypeId": 526, "name": "Total Points",
                                           "bets": bets}]}]}
    board = Board()
    st = ingest_oddschecker(board, payload, book="fanatics",
                            sport_key="americanfootball_ncaaf", strict_match=False)
    assert st["placeholder_rungs"] == 0


# --- FanDuel alternate ladders ----------------------------------------------
def test_alt_line_events_are_queued_for_catalogued_leagues_only():
    """FanDuel's SPORT page carries ONE line per market. Its alternate ladders
    live on the per-event `popular` tab only -- one MLB game returns 19 alt
    total rungs and 15 alt run-line rungs -- so depth costs a request per
    event, and it is spent where another book is actually likely to be."""
    from edge.arb import catalog as cat
    assert "baseball_mlb" in cat.BY_KEY
    assert cat.generic_key("soccer", "Bulgarian A PFG") not in cat.BY_KEY


def test_the_alt_pass_does_not_refetch_what_the_prop_pass_covered():
    """An event in the prop queue already gets `popular`, which is where the
    alternates are; asking again would buy nothing and cost a request."""
    prop_queue = [("baseball_mlb", "1"), ("baseball_mlb", "2")]
    alt_targets = [("baseball_mlb", "1"), ("baseball_mlb", "3")]
    covered = {eid for _k, eid in prop_queue}
    assert [(k, e) for k, e in alt_targets if e not in covered] == [("baseball_mlb", "3")]


def test_alternate_handicap_and_ladders_classify_as_game_markets():
    """These are the market types the extra call exists to collect."""
    from edge.arb.fanduel import classify
    for mt in ("ALTERNATE_HANDICAP", "ALTERNATE_RUN_LINES", "ALTERNATE_SPREAD",
               "ALTERNATE_MATCH_HANDICAP"):
        assert classify(mt)[0] == "spreads", mt
    for mt in ("ALTERNATE_TOTAL_RUNS", "ALTERNATE_TOTAL_POINTS",
               "ALTERNATE_TOTAL_POINTS_(OVER/UNDER)"):
        assert classify(mt)[0] == "totals", mt


def test_the_alt_pass_can_be_turned_off():
    from edge.arb.config import ArbConfig
    cfg = ArbConfig()
    assert cfg.fanduel_alt_line_events > 0, "on by default"
    cfg.fanduel_alt_line_events = 0
    assert cfg.fanduel_alt_line_events == 0


# --- FanDuel reuses one marketType across periods ---------------------------
@pytest.mark.parametrize("market_name", [
    "Set 1 Game Handicap", "Set 2 Game Handicap", "Set 3 Total Games",
    "Quarter 2 Spread", "Period 3 Total",
])
def test_a_period_named_with_a_bare_digit_is_refused(market_name):
    """The ordinal patterns only caught "1st"/"first". FanDuel numbers tennis
    sets "Set 1", and gives all three of a match's set handicaps the SAME
    marketType (MAIN_SET_GAME_HANDICAP) -- the period lives only in
    marketName. All three landed on one `spreads` key at the same numbers:
    67 same-book price conflicts in one scan."""
    assert not is_full_game(market_name)


def test_the_full_match_tennis_markets_are_kept_and_kept_apart():
    from edge.arb.fanduel import classify
    assert is_full_game("Alternative Game Spread")
    assert is_full_game("Match Total Games")
    # sets and games are different axes and must not share a key
    assert classify("ALTERNATIVE_MATCH_GAME_HANDICAP")[0] == "spreads_games"
    assert classify("MATCH_TOTAL_GAMES")[0] == "totals_games"
    assert classify("MATCH_TOTAL_GAMES")[0] != classify("TOTAL_POINTS_(OVER/UNDER)")[0]


def test_a_market_is_refused_on_its_name_even_when_its_type_maps():
    """MAIN_SET_GAME_HANDICAP classifies as `spreads` on its type alone. Only
    the name says it is one set's handicap, so the name has to be checked."""
    from edge.arb.fanduel import FanDuelScrape, classify
    from edge.arb.models import Board
    assert classify("MAIN_SET_GAME_HANDICAP")[0] == "spreads"   # type alone maps
    soon = (datetime.now(timezone.utc) + timedelta(hours=6)).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")

    def market(name):
        return {"eventId": "1", "marketType": "MAIN_SET_GAME_HANDICAP",
                "marketName": name, "marketStatus": "OPEN",
                "runners": [{"runnerName": f"{who} {sign}1.5", "runnerStatus": "ACTIVE",
                             "handicap": 0,
                             "winRunnerOdds": {"trueOdds": {"decimalOdds":
                                                            {"decimalOdds": 1.9}}}}
                            for who, sign in (("Starodubtseva", "-"), ("Seidel", "+"))]}

    payload = {"attachments": {
        "events": {"1": {"name": "Starodubtseva v Seidel", "openDate": soon}},
        "markets": {"a": market("Set 1 Game Handicap"),
                    "b": market("Set 2 Game Handicap"),
                    "c": market("Set 3 Game Handicap")}}}
    board = Board()
    FanDuelScrape.ingest_event(FanDuelScrape.__new__(FanDuelScrape), board, payload,
                               "tennis_atp", strict_match=False)
    assert not board.groups
    assert not any(g.conflicts for g in board.groups.values())


def test_one_players_total_is_not_the_games_total():
    """PLAYER_A_TOTAL_POINTS reached the tolerant TOTAL_WORDS fallback and was
    filed as the GAME total -- the same shape as the DraftKings team total,
    and it also pollutes the totals ladder the middle finder walks."""
    from edge.arb.fanduel import classify
    assert classify("PLAYER_A_TOTAL_POINTS") is None
    assert classify("PLAYER_B_TOTAL_POINTS") is None
    # the trailing underscore matters: PLAYER_A is a prefix of PLAYER_ASSISTS,
    # and the game total itself must survive
    assert classify("TOTAL_POINTS_(OVER/UNDER)")[0] == "totals"
    assert classify("ALTERNATE_TOTAL_RUNS")[0] == "totals"


# --- parlays, three-way variants, and totals with no line -------------------
@pytest.mark.parametrize("name", [
    "Head to Head / Total Points Parlay", "Margin / Total Points Parlay",
    "Line & Total Points Parlay", "Same Game Parlay",
])
def test_parlay_markets_are_refused(name):
    """FanDuel's rugby league page carries these, and their marketTypes contain
    TOTAL_POINTS so they reached the totals rule. The runners are "Bulldogs &
    Over (52.5) Points" -- no line parses out -- so all of them collapsed onto
    one keyless `totals` group: 286 same-book price conflicts in a scan. A
    parlay is two bets and cannot be arbitraged as one regardless."""
    assert not is_full_game(name)


@pytest.mark.parametrize("name", ["HEAD_TO_HEAD/TOTAL_POINTS_DOUBLE",
                                  "LINE_&_TOTAL_POINTS_DOUBLES"])
def test_parlay_market_types_are_refused_too(name):
    assert not is_full_game(name)


def test_a_batters_doubles_are_not_a_parlay():
    """The first version of the parlay guard used a bare \bdoubles?\b and
    killed "Doubles O/U" -- a batter's two-base hits. The parlay marketTypes
    are underscore-joined, so the guard requires the underscore."""
    assert is_full_game("Doubles O/U")
    assert canonical_market("Doubles O/U", player="Tatis") == "batter_doubles"


@pytest.mark.parametrize("name", ["Moneyline (3-Way)", "Spread (3-Way)",
                                  "Total Runs (3-Way)", "1st Quarter Winner 3-Way"])
def test_three_way_variants_are_refused(name):
    """A two-way market pushes where its three-way variant pays a third
    outcome, so they are different bets. FanDuel offers "Moneyline" and
    "Moneyline (3-Way)" on one rugby league match and both wrote to h2h."""
    assert not is_full_game(name)


def test_soccers_three_way_moneyline_is_untouched():
    """Soccer's three-way IS the market, and it is named WIN-DRAW-WIN rather
    than "3-Way" -- so the guard above must not reach it."""
    from edge.arb.fanduel import classify
    assert classify("WIN-DRAW-WIN")[0] == "h2h"
    assert is_full_game("Win Market") and canonical_market("Win Market") == "h2h"


def test_a_total_without_a_line_is_not_a_moneyline():
    """The spread branch already refused this on its own axis. Without the
    same guard on totals, a market keyed `totals` whose line does not parse
    fell through to the team rule and every rung landed on one keyless group."""
    from edge.arb.normalize import normalize_outcome, is_totals_market
    assert is_totals_market("totals") and is_totals_market("totals_games")
    assert is_totals_market("alternate_totals") and not is_totals_market("h2h")
    assert normalize_outcome("totals", "Canterbury Bulldogs", None, None,
                             "Brisbane Broncos", "Canterbury Bulldogs") is None
    # ...but a real total still lands
    assert normalize_outcome("totals", "Over", 52.5, None,
                             "Brisbane Broncos", "Canterbury Bulldogs") == \
        ("over", None, 52.5)


def test_a_squad_qualifier_is_not_treated_as_the_mascot():
    """In a women's fixture every team ends in "Women", so the bare last word
    made the mascot shortcut fire between the two SIDES of one match: "St
    George Illawarra Dragons Women" scored 0.85 against "Brisbane Broncos
    Women", beat its genuine 0.50 against the away name, and both runners were
    filed as `home`."""
    from edge.arb.matching import mascot, team_similarity
    from edge.arb.normalize import pick_team_side
    assert mascot("St George Illawarra Dragons Women") == "dragons"
    assert mascot("Brisbane Broncos Women") == "broncos"
    assert mascot("New York Yankees") == "yankees"          # unchanged
    # the two sides of one women's fixture no longer look alike
    assert team_similarity("St George Illawarra Dragons Women",
                           "Brisbane Broncos Women") < 0.6
    assert pick_team_side("Brisbane Broncos Women", "Brisbane Broncos Women",
                          "St George Illawarra Drag") == "home"


def test_qualifiers_are_stripped_in_the_mascot_not_in_the_name():
    """Dropping "Women" from the name itself would let a women's fixture match
    the men's fixture of the same two clubs -- a worse error than missing one.
    So normalize_team keeps it and only mascot() steps over it."""
    from edge.arb.matching import normalize_team
    assert "women" in normalize_team("Brisbane Broncos Women")


# --- combined stats -----------------------------------------------------
@pytest.mark.parametrize("name,expected", [
    ("Hits + Runs + RBIs", "batter_hits_runs_rbis"),
    ("Player Hits, Runs, RBIs", "batter_hits_runs_rbis"),
    ("Hits Runs RBIs", "batter_hits_runs_rbis"),
    ("Points + Rebounds + Assists", "player_points_rebounds_assists"),
    ("Player Points, Rebounds, Assists", "player_points_rebounds_assists"),
    ("Pts + Reb + Ast", "player_points_rebounds_assists"),
])
def test_a_combined_stat_is_not_read_as_one_of_its_parts(name, expected):
    """The combo patterns required "+" and Fanatics writes commas, so "Player
    Hits, Runs, RBIs" fell past them to the bare \\brbis?\\b rule and became
    `batter_rbis`. Shohei Ohtani's H+R+RBI Under 2.5 was then filed as his RBI
    Under 2.5 and paired against a genuine FanDuel RBI Over 1.5 -- a reported
    free middle between two different stats. His real RBI line was 0.5."""
    assert canonical_market(name, player="Ohtani") == expected


def test_the_single_stats_still_map_to_themselves():
    for name, expected in [("Player RBIs", "batter_rbis"),
                           ("Player Runs", "batter_runs_scored"),
                           ("Player Hits", "batter_hits"),
                           ("Player Points", "player_points"),
                           ("Player Rebounds", "player_rebounds"),
                           ("Player Assists", "player_assists")]:
        assert canonical_market(name, player="X") == expected, name


def test_a_prop_leg_carries_the_line_parsed_out_of_its_name():
    """Fanatics puts the line in the bet name -- "Shohei Ohtani Under 2.5" --
    and leaves the line field empty, so the leg displayed a blank Line. That is
    the cell you would check to notice a market was wrong."""
    from edge.arb.books import ingest_oddschecker
    from edge.arb.models import Board
    soon = (datetime.now(timezone.utc) + timedelta(hours=5)).isoformat()
    payload = {"subevents": [{"id": 1, "name": "St Louis at Los Angeles",
                              "startTime": soon, "inRunning": False,
                              "homeTeam": {"name": "Los Angeles Dodgers"},
                              "awayTeam": {"name": "St Louis Cardinals"},
                              "markets": [{"betTypeId": 1, "name": "Player RBIs",
                                           "bets": [{"name": "Shohei Ohtani Under 2.5",
                                                     "line": None,
                                                     "odds": [{"bookmakerCode": "FNP",
                                                               "status": "ACTIVE",
                                                               "decimal": 1.606}]}]}]}]}
    board = Board()
    ingest_oddschecker(board, payload, book="fanatics", sport_key="baseball_mlb",
                       strict_match=False)
    (key, group), = board.groups.items()
    assert key.point == 2.5 and key.subject == "Shohei Ohtani"
    assert group.quotes["under"]["fanatics"].point == 2.5
