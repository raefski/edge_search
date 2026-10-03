"""Platoon-aware projected lineups, and postseason games reaching the pool."""
from edge import dfs
from edge import dfs_lineup_projection as lp

REG = [(i, f"Reg{i}") for i in range(1, 8)]          # seven everyday regulars
HANDS = {"1": {"throw": "R"}, "2": {"throw": "L"}}   # str(sp id) -> hand


def _games(n=14):
    """Alternating RHP/LHP opponents. A righty-masher (P) starts only against
    righties; his platoon partner (Q) starts only against lefties; a bench bat
    (B) never starts. Most recent game first, as team_games() returns them."""
    rows = []
    for k in range(n):
        vs_lefty = k % 2 == 0
        extra = [(20, "Q")] if vs_lefty else [(10, "P")]
        rows.append({"order": REG + extra + [(30, "Fill")], "opp_sp": 2 if vs_lefty else 1,
                     "type": "R"})
    return rows


def test_platoon_bat_is_in_against_his_hand_and_out_against_the_other():
    games = _games()
    vs_r = {p["id"] for p in lp.predict_lineup(games, "R", HANDS)}
    vs_l = {p["id"] for p in lp.predict_lineup(games, "L", HANDS)}
    assert 10 in vs_r and 20 not in vs_r
    assert 20 in vs_l and 10 not in vs_l


def test_projection_is_nine_ordered_slots_with_probabilities():
    out = lp.predict_lineup(_games(), "R", HANDS)
    assert [p["slot"] for p in out] == list(range(1, 10))
    assert all(0.0 < p["p_start"] <= 1.0 for p in out)
    assert all(p["p_start"] > 0.9 for p in out if p["id"] in range(1, 8))


def test_hand_blind_when_the_starter_is_unknown():
    # no opponent hand: a platoon bat is a coin flip, not a lock
    out = {p["id"]: p["p_start"] for p in lp.predict_lineup(_games(), None, HANDS)}
    platoon = [v for k, v in out.items() if k in (10, 20)]
    assert platoon and all(0.4 < v < 0.6 for v in platoon)


def test_no_history_projects_nothing():
    assert lp.predict_lineup([], "R", HANDS) == []


def test_postseason_games_outweigh_regular_season_ones():
    # Same number of starts for X (regular season) and Y (postseason): the
    # playoff order is the manager's real lineup, so Y ranks above X.
    reg = {"order": REG[:6] + [(40, "X"), (30, "Fill")], "opp_sp": 1, "type": "R"}
    post = {"order": REG[:6] + [(41, "Y"), (30, "Fill")], "opp_sp": 1, "type": "D"}
    games = [post, reg]
    out = {p["id"]: p["p_start"] for p in lp.predict_lineup(games, "R", HANDS)}
    assert out[41] > out[40]


def test_lineups_for_date_includes_postseason_games(monkeypatch):
    # Regression, 2026-10-03: only gameType "R" was kept, so the first Division
    # Series day returned ZERO hitters and no pool could be built.
    def fake_get(url):
        return {"dates": [{"games": [{
            "gamePk": 7, "gameType": "D", "gameDate": "2026-10-03T17:00:00Z",
            "status": {"abstractGameState": "Preview"},
            "teams": {"home": {"team": {"id": 114}, "probablePitcher": {"id": 1}},
                      "away": {"team": {"id": 145}, "probablePitcher": {"id": 2}}},
            "lineups": {"homePlayers": [{"id": 5, "fullName": "Home Guy"}],
                        "awayPlayers": [{"id": 6, "fullName": "Away Guy"}]}}]}]}

    monkeypatch.setattr(dfs, "_get", fake_get)
    out = dfs.lineups_for_date("2026-10-03", project=False)
    assert {v["name"] for v in out.values()} == {"Home Guy", "Away Guy"}
    assert all(v["confirmed"] and v["p_start"] == 1.0 for v in out.values())


def test_unposted_lineup_gets_the_platoon_projection(monkeypatch):
    # an unposted game falls back to _projected_nine, with p_start < 1
    def fake_get(url):
        return {"dates": [{"games": [{
            "gamePk": 8, "gameType": "D", "gameDate": "2026-10-03T20:00:00Z",
            "status": {"abstractGameState": "Preview"},
            "teams": {"home": {"team": {"id": 119}, "probablePitcher": {"id": 2}},
                      "away": {"team": {"id": 144}, "probablePitcher": {"id": 1}}},
            "lineups": {}}]}]}

    monkeypatch.setattr(dfs, "_get", fake_get)
    monkeypatch.setattr(dfs, "_projected_nine", lambda tid, date, opp: [
        {"id": 9, "name": "Proj Guy", "slot": 1, "p_start": 0.8}])
    out = dfs.lineups_for_date("2026-10-03")
    assert {v["name"] for v in out.values()} == {"Proj Guy"}
    assert all(not v["confirmed"] and v["p_start"] == 0.8 for v in out.values())
