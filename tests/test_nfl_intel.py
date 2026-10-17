"""edge/nfl_intel.py (our Fantasy-Guru-style tools) and how they reach the
NFL model: the ownership terms in dfs_nfl_theory.add_ownership, and the
ruled-out removal in dfs_run_nfl.attach_intel.

No network: every nflverse / ESPN read is replaced with a tiny fixture.
"""
from __future__ import annotations

import gzip
import io
import json

import pytest

from edge import dfs_nfl_theory as theory
from edge import dfs_run_nfl as nfl
from edge import nfl_intel as ni


def pg(week, player, pos, team, opp, dk, targets=0, carries=0, season=2026, snap=None):
    from edge.names import norm
    return {"week": week, "pid": player, "player": player, "norm": norm(player), "pos": pos,
            "team": team, "opp": opp, "dk": dk, "targets": targets, "carries": carries,
            "attempts": 0, "air_yards": 0, "snap_pct": snap}


@pytest.fixture
def games(monkeypatch):
    """Week 1-2 of a toy league. DEF1 faces OFF_A in wk1 and OFF_B in wk2;
    OFF_A's WRs score 20 against everyone else, 30 against DEF1."""
    rows = [
        pg(1, "A WR", "WR", "OFFA", "DEF1", 30, targets=10),
        pg(2, "A WR", "WR", "OFFA", "XX", 20, targets=10),
        pg(1, "B WR", "WR", "OFFB", "YY", 10, targets=5),
        pg(2, "B WR", "WR", "OFFB", "DEF1", 10, targets=5),
        pg(1, "A RB", "RB", "OFFA", "DEF1", 12, targets=2, carries=18, snap=0.7),
        pg(2, "A RB", "RB", "OFFA", "XX", 14, targets=2, carries=18, snap=0.6),
        pg(1, "A QB", "QB", "OFFA", "DEF1", 20, carries=4),
        pg(2, "A QB", "QB", "OFFA", "XX", 18, carries=4),
    ]
    memo = {("pg", 2026): rows, ("pg", 2025): []}
    monkeypatch.setattr(ni, "_MEMO", memo)
    return rows


def test_matchup_credits_a_defence_only_beyond_what_the_offence_scores_elsewhere(games):
    m = ni.matchup_table(2026, 3)
    # wk1: OFFA scored 30 vs its 20 elsewhere (+10); wk2: OFFB 10 vs 10 (0).
    # weights 0.9 (wk1) and 1.0 (wk2), shrunk by MATCHUP_K phantom games.
    expected = (0.9 * 10 + 1.0 * 0) / (0.9 + 1.0 + ni.MATCHUP_K)
    assert m[("DEF1", "WR")] == pytest.approx(expected, abs=0.01)


def test_matchup_never_reads_the_week_it_is_rating(games):
    # rating week 2 may only see week 1
    m = ni.matchup_table(2026, 2)
    assert ("DEF1", "WR") not in m or m[("DEF1", "WR")] >= 0   # wk2's 0 is invisible
    assert ni.matchup_table(2026, 1) == {}


def test_usage_shares_are_of_the_teams_volume(games):
    u = ni.usage_table(2026, 3)
    assert u["awr"]["tgt_share"] == pytest.approx(20 / 24, abs=0.001)
    assert u["arb"]["carry_share"] == pytest.approx(36 / 44, abs=0.001)
    assert u["awr"]["dk_last"] == 20
    assert u["arb"]["snap_avg"] == pytest.approx(0.65)


def test_vacated_ignores_quarterbacks_and_doubtful_is_not_ruled_out(games):
    u = ni.usage_table(2026, 3)
    inj = {"aqb": {"player": "A QB", "status": "Out"},
           "arb": {"player": "A RB", "status": "Doubtful"}}
    vac = ni.vacated(u, inj)
    assert vac["OFFA"]["rush"] == pytest.approx(u["arb"]["carry_share"])  # QB's carries excluded
    assert not ni.is_ruled_out("Doubtful") and ni.is_out("Doubtful")
    assert ni.is_ruled_out("Injured Reserve") and ni.is_ruled_out("OUT")
    assert not ni.is_ruled_out("Questionable") and not ni.is_ruled_out("")


def test_espn_report_is_parsed_and_rams_become_lar(monkeypatch):
    payload = {"injuries": [{"displayName": "Los Angeles Rams", "injuries": [
        {"athlete": {"displayName": "Puka Nacua", "position": {"abbreviation": "WR"}},
         "status": "Questionable", "details": {"type": "Ankle"}, "shortComment": "c"}]}]}
    body = gzip.compress(json.dumps(payload).encode())

    class Resp(io.BytesIO):
        headers = {"Content-Encoding": "gzip"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(ni.urllib.request, "urlopen", lambda req, timeout=0: Resp(body))
    monkeypatch.setattr(ni, "_ESPN_MEMO", [])
    out = ni.injuries()
    assert out["pukanacua"]["status"] == "Questionable"
    assert out["pukanacua"]["team"] == "LAR"


# --- the ownership terms ------------------------------------------------------

def _wrs():
    return [{"dk_pos": "WR", "salary": 6000, "proj": 15.0, "name": f"w{i}"} for i in range(30)]


def test_pool_without_intel_fields_gets_the_old_model_exactly():
    a, b = _wrs(), _wrs()
    theory.add_ownership(a)
    theory.add_ownership(b, intel={})
    assert [p["own"] for p in a] == [p["own"] for p in b]


def test_questionable_tag_lowers_ownership_and_matchup_raises_it():
    pool = _wrs()
    for p in pool:
        p.update(inj_status="", matchup=0.0, implied=22.0, dk_last=15.0)
    pool[0]["inj_status"] = "Questionable"
    pool[1]["matchup"] = 6.0
    theory.add_ownership(pool)
    flat = sorted(p["own"] for p in pool[2:])
    assert pool[0]["own"] < flat[0]
    assert pool[1]["own"] > flat[-1]


def test_a_player_missing_a_feature_is_treated_as_average_not_as_zero():
    """A missing implied total must not read as a 0-point team."""
    pool = _wrs()
    for i, p in enumerate(pool):
        p["implied"] = 20.0 + i
    pool[0]["implied"] = None
    theory.add_ownership(pool)
    mid = sorted(p["own"] for p in pool[1:])
    assert mid[0] <= pool[0]["own"] <= mid[-1]


def test_dsts_get_no_intel_terms():
    pool = [{"dk_pos": "DST", "salary": 3000, "proj": 7.0, "name": f"d{i}", "implied": 20.0 + 5 * i}
            for i in range(4)]
    theory.add_ownership(pool)
    assert len({p["own"] for p in pool}) == 1


# --- the slate build ----------------------------------------------------------

def test_attach_intel_removes_ruled_out_players_and_says_so(monkeypatch):
    pool = [{"name": "Out Guy", "team": "PHI", "dk_pos": "RB"},
            {"name": "Q Guy", "team": "PHI", "dk_pos": "WR"},
            {"name": "Fine", "team": "PHI", "dk_pos": "WR"},
            {"name": "Eagles", "team": "PHI", "dk_pos": "DST"}]

    def fake_attach(pool, date):
        status = {"Out Guy": "Out", "Q Guy": "Questionable"}
        for p in pool:
            p["inj_status"] = status.get(p["name"], "")
        return {"ok": True, "injury_source": "espn"}
    monkeypatch.setattr(ni, "attach", fake_attach)
    stats = {}
    out = nfl.attach_intel(pool, {"start": "2026-10-18T17:00:00Z"}, stats)
    assert [p["name"] for p in out] == ["Q Guy", "Fine", "Eagles"]
    assert stats["ruled_out"] == ["Out Guy (PHI, Out)"]


def test_attach_intel_failure_never_stops_a_build(monkeypatch):
    def boom(*a, **kw):
        raise OSError("github down")
    monkeypatch.setattr(ni, "attach", boom)
    stats = {}
    pool = [{"name": "A", "team": "PHI", "dk_pos": "WR"}]
    assert nfl.attach_intel(pool, {}, stats) == pool
    assert stats["intel"]["ok"] is False


def test_injury_on_another_team_with_the_same_name_is_ignored(games):
    pool = [{"name": "A WR", "team": "OFFZ", "opp_team": "DEF1", "dk_pos": "WR"}]
    ni.attach(pool, "2026-09-27", live_injuries={"awr": {"player": "A WR", "team": "OFFA",
                                                          "status": "Out"}})
    assert pool[0]["inj_status"] == ""
