"""The guard on every sportsbook-prop -> DK-player join (edge/dfs_slate).

A prop names a player and nothing else, and a scan holds every event a book
has posted. Joined by name alone, NCAAF put Missouri QB Austin Simmons' line
on a $3,000 Washington namesake on 2026-10-16 -- both model lineups played
him and the cash entry missed the line -- and every flat {name: markets} walk
(NFL, NHL, NCAAB, MLB pitchers) could carry a player's line from ANOTHER
night's game. These pin the rule each sport now goes through.
"""
from __future__ import annotations

from edge import dfs_slate

TONIGHT = "2026-10-17T23:00:00Z"
TOMORROW = "2026-10-18T23:00:00Z"
GAMES = {"g1": {"start": dfs_slate.parse_time(TONIGHT)},
         "g2": {"start": dfs_slate.parse_time(TONIGHT)}}
BOARD = {"Austin Simmons": {"name": "Austin Simmons", "game": "g1"},
         "A1": {"name": "A1", "game": "g1"}, "A2": {"name": "A2", "game": "g1"},
         "B1": {"name": "B1", "game": "g2"}, "B2": {"name": "B2", "game": "g2"}}


def test_an_off_slate_namesake_is_refused():
    events = {"slate": {"A1", "A2", "walk-on"},
              "off": {"Austin Simmons", "X1", "X2", "X3", "X4"}}
    owner, refused = dfs_slate.subject_events(events, BOARD.get, {}, GAMES)
    assert owner == {"A1": "slate", "A2": "slate"}
    assert refused == ["Austin Simmons"]


def test_tomorrows_line_for_the_same_teams_is_refused():
    """NHL/NBA scans hold two nights: same players, same teams, 24h later."""
    events = {"tonight": {"A1", "A2"}, "tomorrow": {"A1", "A2"}}
    kick = {"tonight": TONIGHT, "tomorrow": TOMORROW}
    owner, refused = dfs_slate.subject_events(events, BOARD.get, kick, GAMES)
    assert owner == {"A1": "tonight", "A2": "tonight"} and refused == []
    markets, _ = dfs_slate.own_game_markets(
        {"tonight": {"A1": {"shots": 2.5}}, "tomorrow": {"A1": {"shots": 3.5}}},
        kick, BOARD.get, GAMES)
    assert markets["A1"] == {"shots": 2.5}


def test_a_player_in_his_own_game_is_kept_and_unknown_names_pass_through():
    markets, refused = dfs_slate.own_game_markets(
        {"e1": {"B1": {"pts": 1}, "B2": {"pts": 2}, "Nobody": {"pts": 3}}},
        {"e1": TONIGHT}, BOARD.get, GAMES)
    assert set(markets) == {"B1", "B2", "Nobody"} and refused == []


def test_a_doubleheader_keeps_the_earlier_game():
    events = {"late": {"A1", "A2"}, "early": {"A1", "A2"}}
    kick = {"early": "2026-10-17T22:00:00Z", "late": "2026-10-18T00:30:00Z"}
    owner, _ = dfs_slate.subject_events(events, BOARD.get, kick, GAMES)
    assert owner["A1"] == "early"


def test_a_same_named_second_player_on_one_board_is_not_dropped(monkeypatch):
    """fetch_draftables keyed by name alone, so a second Sebastian Aho vanished."""
    from edge import dfs
    raw = [{"displayName": "Sebastian Aho", "playerId": 1, "teamAbbreviation": "CAR",
            "salary": 7000, "position": "C", "competition": {"name": "CAR @ NYI", "competitionId": 9}},
           {"displayName": "Sebastian Aho", "playerId": 1, "teamAbbreviation": "CAR",
            "salary": 7000, "position": "UTIL", "competition": {"name": "CAR @ NYI", "competitionId": 9}},
           {"displayName": "Sebastian Aho", "playerId": 2, "teamAbbreviation": "NYI",
            "salary": 3500, "position": "D", "competition": {"name": "CAR @ NYI", "competitionId": 9}}]
    monkeypatch.setattr(dfs, "_draftables_raw", lambda gid: raw)
    sal = dfs.fetch_draftables(1)
    assert sorted((v["team"], v["salary"]) for v in sal.values()) == [("CAR", 7000), ("NYI", 3500)]
    # A name-only lookup cannot pick between them.
    assert dfs.SalaryLookup(sal).bare.get("sebastianaho") is None
