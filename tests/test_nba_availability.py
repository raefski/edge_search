"""edge/nba_availability.py: who was really out (critique cycle 3)."""
from edge import nba_availability as A


def _box():
    return {"boxScoreTraditional": {
        "homeTeam": {"teamTricode": "DET", "players": [
            {"personId": 1, "comment": ""},
            {"personId": 2, "comment": "DNP - Coach's Decision"},
            {"personId": 3, "comment": "Inactive - G League - Two-Way"}]},
        "awayTeam": {"teamTricode": "BOS", "players": [
            {"personId": 4, "comment": "Inactive - Injury/Illness"}]}}}


def test_the_box_score_comment_separates_played_coach_dnp_and_inactive():
    av = A.parse_box(_box())
    assert av["1"]["status"] == "played" and av["2"]["status"] == "dnp"
    assert av["3"]["status"] == "inactive" and av["4"]["status"] == "inactive" and av["4"]["team"] == "BOS"


def test_apply_overrides_the_lineup_flags_and_a_player_missing_from_the_box_did_not_dress(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "CACHE", tmp_path)
    (tmp_path / "0022600001.json").write_text(__import__("json").dumps(A.parse_box(_box())))
    lineup = [{"id": i, "inactive": False} for i in (1, 2, 3, 4, 5)]
    out = {p["id"]: p["inactive"] for p in A.apply(lineup, "0022600001")}
    assert out == {1: False, 2: False, 3: True, 4: True, 5: True}
    assert A.apply(lineup, "0022600099") == lineup            # no box cached: flags unchanged


def test_espns_out_list_maps_team_codes_and_skips_day_to_day():
    feed = {"injuries": [{"injuries": [
        {"status": "Out", "athlete": {"displayName": "Mouhamed Gueye", "team": {"abbreviation": "ATL"}}},
        {"status": "Out", "athlete": {"displayName": "Stephen Curry", "team": {"abbreviation": "GS"}}},
        {"status": "Day-To-Day", "athlete": {"displayName": "Someone Else", "team": {"abbreviation": "UTAH"}}}]}]}
    out = A.tonight_out(fetch=lambda: feed)
    assert out == [("Mouhamed Gueye", "ATL"), ("Stephen Curry", "GSW")]
    assert A.LAST_SOURCE.startswith("espn") and "(2 out)" in A.LAST_SOURCE
    assert A.tonight_out(fetch=lambda: (_ for _ in ()).throw(OSError("down"))) == []
    assert A.LAST_SOURCE.startswith("failed")
