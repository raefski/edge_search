from edge.clv import log_open_bets, grade, summary, load
from edge.oddsmath import devig


def _dk_event(eid, over, under):
    return {"id": eid, "home_team": "Fever", "away_team": "Sparks",
            "bookmakers": [{"key": "draftkings", "markets": [{"key": "player_threes",
                "outcomes": [
                    {"name": "Over", "description": "Rook", "price": over, "point": 1.5},
                    {"name": "Under", "description": "Rook", "price": under, "point": 1.5}]}]}]}


def _flag(eid):
    return {"event_id": eid, "commence": "2026-06-27T23:00:00Z", "event": "Sparks @ Fever",
            "market": "player_threes", "subject": "Rook", "side": "Over", "point": 1.5,
            "dec": 2.62, "american": 162, "fair_consensus": 0.40, "ev": 0.05}


def test_log_and_dedupe(tmp_path):
    p = tmp_path / "clv.csv"
    scan_event = _dk_event("g1", 2.62, 1.45)
    added = log_open_bets([_flag("g1")], [scan_event], "basketball_wnba", p)
    assert len(added) == 1
    assert added[0]["opp_dec"] == 1.45  # captured DK's other side for later devig
    # re-logging the same bet is idempotent
    again = log_open_bets([_flag("g1")], [scan_event], "basketball_wnba", p)
    assert again == []
    assert len(load(p)) == 1


def test_grade_positive_clv(tmp_path):
    p = tmp_path / "clv.csv"
    log_open_bets([_flag("g1")], [_dk_event("g1", 2.62, 1.45)], "basketball_wnba", p)
    # DK closes shorter on the Over (2.62 -> 2.40): we beat the close.
    changed = grade(p, [_dk_event("g1", 2.40, 1.55)])
    r = changed[0]
    assert r["status"] == "graded"
    assert r["beat_close"] is True
    assert abs(float(r["price_clv_pct"]) - (2.62 / 2.40 - 1)) < 1e-3  # stored rounded to 4dp
    # line moved toward the Over -> positive probability CLV
    assert float(r["prob_clv"]) > 0
    s = summary(p)
    assert s["graded"] == 1 and s["beat"] == 1 and s["pct_positive"] == 100.0


def test_grade_line_moved_off_number(tmp_path):
    p = tmp_path / "clv.csv"
    log_open_bets([_flag("g1")], [_dk_event("g1", 2.62, 1.45)], "basketball_wnba", p)
    # At close DK only offers the 2.5 line, not 1.5 -> bet's number is gone.
    moved = _dk_event("g1", 2.40, 1.55)
    for o in moved["bookmakers"][0]["markets"][0]["outcomes"]:
        o["point"] = 2.5
    changed = grade(p, [moved])
    assert changed[0]["status"] == "no_close_line"
