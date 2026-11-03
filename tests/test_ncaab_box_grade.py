"""The college box-score grader: ESPN parsing, team-scoped joins, the undrafted, namesakes."""
import pytest

from edge.dfs_contest import parse_contest_file
from edge.dfs_run_ncaab import SALARY_LINE, no_prop_points
from scripts import ncaab_box_grade as g


def _athlete(name, minutes, pts, reb=0, three="0-0", starter=True):
    return {"athlete": {"displayName": name}, "starter": starter,
            "stats": [str(minutes), str(pts), three, str(reb), "0", "0", "0", "0"]}


KEYS = ["minutes", "points", "threePointFieldGoalsMade-threePointFieldGoalsAttempted", "rebounds",
        "assists", "turnovers", "steals", "blocks"]


def _summary(school, athletes):
    return {"displayName": school}, {"keys": KEYS, "athletes": athletes}


def _game(*sides):
    return {"boxscore": {"players": [{"team": t, "statistics": [tbl]} for t, tbl in sides]}}


def _row(player, team, opp="X", proj=20.0, source="blend", salary=5000):
    return {"gid": "1", "date": "2026-11-02", "player": player, "team": team, "opp": opp, "proj": proj,
            "proj_source": source, "salary": salary, "own": 1.0, "starter": "0", "status": ""}


def test_box_rows_score_dk_and_tag_the_right_team():
    summary = _game(_summary("UCLA Bruins", [_athlete("Trent Perry", 25, 20, reb=4, three="2-5")]),
                    _summary("Arizona Wildcats", [{"athlete": {"displayName": "Sat Out"}, "stats": []}]))
    rows = g.box_rows(summary, ("ARIZ", "UCLA"))
    assert rows[0]["team"] == "UCLA" and rows[0]["fp"] == 26.0 and rows[0]["min"] == 25
    assert rows[1]["team"] == "ARIZ" and rows[1]["fp"] == 0.0 and rows[1]["min"] == 0


def test_two_players_one_name_get_their_own_box_line():
    rows = [_row("Trent Perry", "UCLA"), _row("Trent Perry", "KU")]
    box = [{"team": "UCLA", "name": "Trent Perry", "min": 25, "started": True, "fp": 35.25},
           {"team": "KU", "name": "Trent Perry", "min": 6, "started": False, "fp": 7.0}]
    rows, unclaimed = g.join(rows, box)
    assert [r["act"] for r in rows] == [35.25, 7.0] and not unclaimed


def test_logged_player_with_no_box_row_scored_zero_and_stray_box_row_is_reported():
    rows, unclaimed = g.join([_row("Benched Kid", "UK")],
                             [{"team": "UK", "name": "Somebody Else", "min": 12, "started": False, "fp": 9.0}])
    assert rows[0]["act"] == 0.0 and rows[0]["min"] == 0.0
    assert [b["name"] for b in unclaimed] == ["Somebody Else"]


def test_salary_line_recovers_a_known_line():
    a, b = g.fit_line([{"salary": s, "act": 2 + 1.5 * s / 1000} for s in (3000, 6000, 9000)])
    assert (a, b) == pytest.approx((2.0, 1.5))


def test_no_history_no_prop_uses_the_results_line_not_the_market_line():
    fp, source = no_prop_points(10000, None, None, True, None)
    assert source == "salary"
    assert fp == pytest.approx(16.36)
    assert fp < SALARY_LINE[0] + SALARY_LINE[1] * 10


def _export(tmp_path, rows):
    path = tmp_path / "contest-standings-1.csv"
    path.write_text("Rank,EntryId,EntryName,TimeRemaining,Points,Lineup,,Player,Roster Position,%Drafted,FPTS\n"
                    + "".join(f",,,,,,,{n},{s},{p}%,{f}\n" for n, s, p, f in rows))
    return path


def test_namesakes_with_different_scores_are_left_out_and_said(tmp_path, capsys):
    out = parse_contest_file(_export(tmp_path, [("Trent Perry", "G", 2.0, 35.25), ("Trent Perry", "G", 0.7, 7.0),
                                                ("Ethan Brown", "G", 10.68, 38.75)]))
    assert "trentperry" not in out and "ethanbrown" in out
    assert "Trent Perry" in capsys.readouterr().err


def test_one_player_in_two_slots_is_still_summed(tmp_path, capsys):
    out = parse_contest_file(_export(tmp_path, [("Ethan Brown", "G", 10.68, 38.75), ("Ethan Brown", "UTIL", 1.62, 38.75)]))
    assert out["ethanbrown"]["pct_drafted"] == pytest.approx(12.30)
    assert capsys.readouterr().err == ""


def test_captain_row_at_one_and_a_half_times_is_not_a_namesake(tmp_path, capsys):
    out = parse_contest_file(_export(tmp_path, [("Brady Hiestand", "CPT", 5.0, 132.79), ("Brady Hiestand", "FLEX", 9.0, 88.53)]))
    assert out["bradyhiestand"]["fpts"] == 88.53 and capsys.readouterr().err == ""
