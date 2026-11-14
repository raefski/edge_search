"""Polymarket spread ladder -> fair home spread (edge/pickem_polymarket.py)."""
import json

from edge import pickem_polymarket as pm


def _r(code, k, p, flip=False):
    return {"code": code, "k": k, "flip": flip, "hist": [(0, p)]}


def test_home_favourite_interpolates_between_straddling_rungs():
    # home -3.5 covers 55%, home -4.5 covers 45% -> fair line -4.0
    line, n = pm.fair_home_line("DEN", [_r("DEN", 3.5, 0.55), _r("DEN", 4.5, 0.45)], 10)
    assert n == 2 and abs(line - (-4.0)) < 1e-9


def test_away_rungs_are_mirrored_into_home_terms():
    # away -2.5 at 40% means P(home margin > -2.5) = 60%; home -1.5 at 40%
    line, _ = pm.fair_home_line("GB", [_r("DET", 2.5, 0.40), _r("GB", 1.5, 0.40)], 10)
    # points (-2.5, .60), (1.5, .40): crosses .5 at x = -0.5 -> home -(-0.5) = +0.5
    assert abs(line - 0.5) < 1e-9


def test_flip_reads_outcome_zero_as_the_other_side():
    a, _ = pm.fair_home_line("DEN", [_r("DEN", 3.5, 0.55), _r("DEN", 4.5, 0.45)], 10)
    b, _ = pm.fair_home_line("DEN", [_r("DEN", 3.5, 0.45, flip=True),
                                     _r("DEN", 4.5, 0.55, flip=True)], 10)
    assert a == b


def test_extreme_and_future_prices_are_ignored():
    rungs = [_r("DEN", 3.5, 0.55), _r("DEN", 4.5, 0.45), _r("DEN", 20.5, 0.01)]
    assert pm.fair_home_line("DEN", rungs, 10)[1] == 2
    assert pm.fair_home_line("DEN", rungs, -1) == (None, 0)   # before any trade


def test_game_rungs_parses_questions_and_team_codes():
    ev = {"title": "Packers vs. Lions", "markets": [
        {"sportsMarketType": "spreads", "question": "Spread: Lions (-3.5)", "volume": "9000",
         "outcomes": json.dumps(["Packers", "Lions"]), "clobTokenIds": json.dumps(["t0", "t1"]),
         "gameStartTime": "2026-10-25 20:25:00+00"},
        {"sportsMarketType": "spreads", "question": "Spread: Lions (-9.5)", "volume": "10",
         "outcomes": "[]", "clobTokenIds": "[]"},
        {"sportsMarketType": "moneyline", "question": "Packers vs. Lions"}]}
    teams, kick, rungs = pm.game_rungs(ev)
    assert teams == {"GB", "DET"} and kick == pm.parse_ts("2026-10-25T20:25:00Z")
    assert rungs == [{"code": "DET", "k": 3.5, "token": "t0", "flip": True}]
