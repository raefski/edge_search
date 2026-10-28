"""The NBA DFS build: slots, optimiser, simulation, ownership, projection pieces and
the lineup snapshot (edge/dfs_opt_nba.py, dfs_nba_theory.py, nba_project.py,
nba_minutes.py, nba_lineups.py, dfs_run_nba.py)."""
from __future__ import annotations

import json

import numpy as np
import pytest

from edge import dfs_nba_theory as T, dfs_opt_nba as O, nba_lineups as L, nba_minutes as NM
from edge import nba_project as NP
from edge import dfs_run_nba as R


def _p(name, pos, team, salary, proj, game=None):
    return {"name": name, "dk_pos": pos, "elig": O.eligible(pos), "team": team,
            "game": game or ("g1" if team in ("NYK", "PHI") else "g2"), "salary": salary,
            "proj": proj, "own": 10.0, "pos": sorted(O.eligible(pos))[0]}


# ── slots ──────────────────────────────────────────────────────────────────
def test_multi_position_players_are_matched_to_the_eight_slots():
    rows = [_p("a", "PG", "NYK", 5000, 30), _p("b", "SG/SF", "NYK", 5000, 30),
            _p("c", "SF", "PHI", 5000, 30), _p("d", "PF/C", "PHI", 5000, 30),
            _p("e", "C", "BOS", 5000, 30), _p("f", "PG/SG", "BOS", 5000, 30),
            _p("g", "SF/PF", "DET", 5000, 30), _p("h", "C", "DET", 5000, 30)]
    slots = O.assign_slots(rows)
    assert slots is not None and [s for s, _ in slots] == list(O.SLOTS)
    assert all(p["elig"] & O.SLOT_OK[s] for s, p in slots)


def test_four_centres_cannot_fill_a_lineup():
    rows = [_p(str(i), "C", "NYK", 5000, 30) for i in range(4)] + \
           [_p(str(i), "PG", "PHI", 5000, 30) for i in range(4, 8)]
    assert O.assign_slots(rows) is None             # C, UTIL hold two centres; F needs SF/PF


def test_the_optimiser_returns_a_legal_lineup_under_the_cap():
    teams = ["NYK", "PHI", "BOS", "DET"]
    pos = ["PG", "SG", "SF", "PF", "C", "PG/SG", "SF/PF", "PF/C"]
    pool = [_p(f"{t}{i}", pos[i % 8], t, 3000 + 700 * ((i * 7 + k) % 10), 15 + 3 * ((i * 5 + k) % 9))
            for k, t in enumerate(teams) for i in range(10)]
    sim = T.simulate(pool, n_sims=500, seed=1)
    T.summarise(sim, pool)
    for mode in ("cash", "gpp"):
        res = O.optimize(pool, sim, mode=mode, iters=15, seed=0)
        assert res and res["salary"] <= O.SALARY_CAP and len(res["slots"]) == 8
        assert len({p["team"] for p in res["lineup"]}) >= 2 and len({p["game"] for p in res["lineup"]}) >= 2


# ── simulation and ownership ───────────────────────────────────────────────
def test_simulated_players_keep_their_mean_and_the_measured_spread():
    pool = [{"proj": 40.0}, {"proj": 8.0}]
    sim = T.simulate(pool, n_sims=40000, seed=0)
    assert sim[:, 0].mean() == pytest.approx(40.0, rel=0.02)
    assert sim[:, 0].std() == pytest.approx(T.sd_for(40.0), rel=0.03)
    assert (sim >= 0).all()


def test_ownership_fills_each_positions_share_of_the_slots():
    # the old per-position prior (weights=) still fills its position's slots
    pool = [{"pos": "C", "proj": 20 + i, "salary": 4000 + 300 * i} for i in range(12)]
    T.add_ownership(pool, weights=(T.VALUE_WEIGHT, T.SALARY_WEIGHT))
    assert sum(p["own"] for p in pool) == pytest.approx(100 * T.GROUP_SLOTS["C"], rel=0.01)
    assert all(0 <= p["own"] <= T.MAX_OWN for p in pool)


# ── projection pieces ──────────────────────────────────────────────────────
def test_a_player_with_few_minutes_is_mostly_the_position_prior():
    rookie = [(10.0, False, 20.0)]                       # 2.0 per minute over 10 minutes
    vet = [(30.0, True, 30.0)] * 30                      # 1.0 per minute over 900
    assert NP.rate(rookie, "G") < 1.3                    # pulled toward 0.95
    assert NP.rate(vet, "G") == pytest.approx(1.0, abs=0.02)


def test_too_many_roles_are_shared_so_a_star_keeps_more_than_proportional():
    team = [{"id": "star", "position": "G", "inactive": False, "starter": None,
             "history": [(36.0, True)] * 10}]
    team += [{"id": str(i), "position": "F", "inactive": False, "starter": None,
              "history": [(24.0, False)] * 10} for i in range(11)]          # 300 minutes of roles
    out = NM.project(team)
    assert out["star"]["minutes"] > 36 * 240 / 300 + 2                # far more than proportional (28.8)
    assert sum(p["minutes"] for p in out.values()) == pytest.approx(240, abs=0.6)


# ── the lineup snapshot ────────────────────────────────────────────────────
def _raw(ts, inactive=False):
    return {"games": [{"gameId": "1", "gameStatusText": "7:30 pm ET",
                       "homeTeam": {"teamAbbreviation": "NYK", "players": [
                           {"personId": 1, "playerName": "A", "position": "PG", "lineupStatus": "Expected",
                            "rosterStatus": "Inactive" if inactive else "Active", "timestamp": ts}]},
                       "awayTeam": {"teamAbbreviation": "TOR", "players": []}}]}


def test_the_snapshot_commits_lineup_changes_not_nba_coms_five_minute_timestamps(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "SNAPSHOT", tmp_path / "snap.json")
    assert L.write_snapshot("2026-10-15", _raw("15:36:32"), "t1")
    assert not L.write_snapshot("2026-10-15", _raw("15:41:32"), "t2")       # timestamp only
    assert L.write_snapshot("2026-10-15", _raw("15:46:32", inactive=True), "t3")
    assert json.loads((tmp_path / "snap.json").read_text())["fetched_at"] == "t3"


# ── slates ─────────────────────────────────────────────────────────────────
def test_the_main_classic_slate_is_the_default_and_team_codes_match_nba_com():
    groups = [{"DraftGroupId": 2, "GameTypeId": 70, "ContestStartTimeSuffix": " (Night)", "GameCount": 2,
               "StartDate": "2026-10-20T23:00:00"},
              {"DraftGroupId": 1, "GameTypeId": 70, "ContestStartTimeSuffix": None, "GameCount": 3,
               "StartDate": "2026-10-20T19:00:00"},
              {"DraftGroupId": 3, "GameTypeId": 81, "ContestStartTimeSuffix": " (BOS @ DET)", "GameCount": 1,
               "StartDate": "2026-10-20T19:00:00"}]
    gid, meta = R.resolve_slate(None, groups)
    assert gid == 1 and meta["label"] == "Main" and len(R.classic_groups(groups)) == 2
    assert R.team("PHO") == "PHX" and R.team("NYK") == "NYK"


# ── late swap ──────────────────────────────────────────────────────────────
def _swap_slate():
    from datetime import datetime, timezone
    teams = {"NYK": "g1", "PHI": "g1", "BOS": "g2", "DET": "g2"}
    tip = {"g1": "2026-10-20T19:00:00Z", "g2": "2026-10-21T01:30:00Z"}
    pos = ["PG", "SG", "SF", "PF", "C", "PG/SG", "SF/PF", "PF/C"]
    pool = []
    for k, (t, g) in enumerate(teams.items()):
        for i in range(10):
            p = _p(f"{t}{i}", pos[i % 8], t, 3000 + 700 * ((i * 7 + k) % 10), 15 + 3 * ((i * 5 + k) % 9), g)
            p["start"] = tip[g]
            pool.append(p)
    sim = T.simulate(pool, n_sims=400, seed=2)
    T.summarise(sim, pool)
    priced = {p["name"]: {k: p[k] for k in ("salary", "dk_pos", "team", "start", "game")} for p in pool}
    lu = O.optimize(pool, sim, mode="gpp", iters=15, seed=0)
    entered = [p["name"] for _, p in lu["slots"]]
    between = datetime(2026, 10, 20, 21, 0, tzinfo=timezone.utc)   # g1 tipped, g2 not yet
    return pool, sim, priced, entered, between


def test_late_swap_replaces_only_the_scratched_player_and_keeps_locked_games():
    pool, sim, priced, entered, now = _swap_slate()
    late = next(n for n in entered if priced[n]["game"] == "g2")
    board = [p for p in pool if p["name"] != late]                 # ruled out after the build
    sim_b = np.delete(sim, [i for i, p in enumerate(pool) if p["name"] == late], axis=1)
    sw = R.late_swap(board, sim_b, entered, priced, mode="gpp", now=now, iters=15)
    assert sw["error"] is None and sw["replace"] == [late] and not sw["stuck"]
    final = [p["name"] for _, p in sw["lineup"]["slots"]]
    assert late not in final and len(sw["swaps"]) == 1 and sw["swaps"][0][0] == late
    assert set(entered) - {late} <= set(final)                     # everyone else kept
    assert set(sw["locked"]) == {n for n in entered if priced[n]["game"] == "g1"}
    assert sw["lineup"]["salary"] <= O.SALARY_CAP


def test_a_scratched_player_whose_game_already_tipped_is_stuck_in_his_slot():
    pool, sim, priced, entered, now = _swap_slate()
    early = next(n for n in entered if priced[n]["game"] == "g1")
    board = [p for p in pool if p["name"] != early]
    sim_b = np.delete(sim, [i for i, p in enumerate(pool) if p["name"] == early], axis=1)
    sw = R.late_swap(board, sim_b, entered, priced, mode="gpp", now=now, iters=15, improve=True)
    assert sw["stuck"] == [early] and sw["error"] is None
    final = [p["name"] for _, p in sw["lineup"]["slots"]]
    assert early in final                                          # holds his slot at 0 points
    assert {n for n in entered if priced[n]["game"] == "g1"} <= set(final)   # locked ones stay


# ── buzz ───────────────────────────────────────────────────────────────────
def test_buzz_raises_a_named_players_ownership_and_none_means_the_prior():
    pool = [_p(f"g{i}", "PG", "NYK", 4000 + 300 * i, 20 + i) for i in range(60)]
    T.add_ownership(pool)
    prior = {p["name"]: p["own"] for p in pool}
    for p in pool:
        p["buzz"] = 40 if p["name"] == "g1" else 0
    T.add_ownership(pool)
    assert pool[1]["own"] > prior["g1"]
    for p in pool:
        p["buzz"] = None                                     # no collection tonight
    T.add_ownership(pool)
    assert {p["name"]: p["own"] for p in pool} == prior


def test_attach_buzz_reads_only_tonight(tmp_path, monkeypatch):
    from edge import repo_files
    f = tmp_path / "buzz_nba.csv"
    f.write_text("date,player,team,pos,mentions,videos,channels,reach\n"
                 "2026-10-21,Jalen Brunson,NYK,PG,30,4,3,9000\n2026-10-20,Joel Embiid,PHI,C,50,5,4,9000\n")
    monkeypatch.setattr(repo_files, "read", lambda p: type("F", (), {"data": f.read_text()})())
    pool = [{"name": "Jalen Brunson"}, {"name": "Joel Embiid"}]
    assert R.attach_buzz(pool, "2026-10-21") == 1
    assert pool[0]["buzz"] == 30 and pool[1]["buzz"] == 0
    assert R.attach_buzz(pool, "2026-10-22") == 0 and pool[0]["buzz"] is None


def test_one_games_starters_move_together_and_the_bench_moves_against_them():
    pool = []
    for t, g in (("NYK", "g1"), ("PHI", "g1")):
        for i in range(9):
            p = _p(f"{t}{i}", "PG", t, 4000, 30 - 2 * i, g)
            p["starter"] = i < 5
            pool.append(p)
    sim = T.simulate(pool, n_sims=40000, seed=3)
    c = np.corrcoef(sim.T)
    ss, sb, opp = c[0, 1], c[0, 6], c[0, 10]                   # NYK0-NYK1, NYK0-NYK6, NYK0-PHI1
    assert 0.03 < ss < 0.08 and -0.12 < sb < -0.05 and 0.0 < opp < 0.04
    assert abs(sim[:, 0].mean() - 30) < 0.4                     # the marginal is untouched


def test_a_first_name_spelled_differently_still_joins_on_team_last_name_and_initial():
    assert R._sig("Alexandre Sarr") == R._sig("Alex Sarr") == ("sarr", "a")
    assert R._sig("Mikel Brown Jr.") == ("brown", "m")
    assert R._sig("Michael Porter Jr.") != R._sig("Mikel Brown Jr.")


def test_games_since_return_counts_from_the_first_game_after_a_five_game_gap():
    tail = [("BOS", 1), ("BOS", 2), ("BOS", 3)]
    assert NM.games_since_return(tail, "BOS", 4, 3, "BOS") is None          # never missed
    assert NM.games_since_return(tail, "BOS", 9, 3, "BOS") == 0             # missed 4-8: tonight is game 1 back
    back = tail + [("BOS", 9), ("BOS", 10)]
    assert NM.games_since_return(back, "BOS", 11, 5, "BOS") == 2            # third game back
    assert NM.games_since_return([], "BOS", 7, 0, "BOS") == 0               # missed the first six games
    assert NM.games_since_return([], "BOS", 7, 0, "NYK") is None            # new team: not an absence
    assert NM.games_since_return([("BOS", 1)], "BOS", 30, 1, "BOS", cap=10) == 0



# ── the 2026-10-21 minutes model ───────────────────────────────────────────
def test_the_role_weights_this_season_against_a_one_game_prior_of_last_season():
    last = [(36.0, True)] * 60                                   # last season: 36 a night
    assert NM.role_v2(last, cur_n=0)["base"] == pytest.approx(36.0)        # night one = last season
    now = last + [(26.0, True)] * 10                             # this season: 26
    r = NM.role_v2(now, cur_n=10)["base"]
    assert 26.0 < r < 28.0                                       # mostly this season (prior worth 1 game)


def test_a_lottery_rookie_starts_from_his_draft_pick_and_a_late_pick_lower():
    def mins(pick):
        team = [{"id": "r", "position": "G", "inactive": False, "starter": None, "history": [], "pick": pick}]
        return NM.project(team)["r"]["minutes"]
    assert mins(1) == pytest.approx(22.19, abs=0.1) and mins(30) < mins(10) < mins(1)


def test_the_first_games_back_from_an_absence_are_ramped():
    team = [{"id": "x", "position": "G", "inactive": False, "starter": None, "history": [(30.0, True)] * 20,
             "since_return": 0}]
    assert NM.project(team)["x"]["minutes"] == pytest.approx(30 * NM.RAMP[0], abs=0.1)


def test_a_short_handed_team_gives_the_extra_minutes_to_the_bench_end_not_the_star():
    """Critique cycle 1: the team's gap to 240 is absorbed by role rank -- stars barely move,
    the 8th-12th men take most of it (edge/nba_minutes_gap.py)."""
    roles = [36, 32, 30, 28, 26, 20, 16, 12, 10, 8, 6, 4]               # 228: 12 short of 240
    team = [{"id": str(i), "position": "G", "inactive": False, "starter": None,
             "history": [(float(m), i < 5)] * 10} for i, m in enumerate(roles)]
    out = NM.project(team)
    assert sum(p["minutes"] for p in out.values()) == pytest.approx(240, abs=0.6)
    gain = {i: out[str(i)]["minutes"] - roles[i] for i in range(12)}
    assert abs(gain[0]) < 1.0 and sum(gain[i] for i in range(7, 12)) > sum(gain[i] for i in range(5))
    assert not hasattr(NM, "SALARY_NUDGE")                              # deleted 10/28: added nothing


def test_a_regular_starter_left_out_of_the_five_is_cut_and_a_bench_role_reads_the_last_game():
    """Critique cycle 2: a regular starter active but not in tonight's announced five
    plays 0 in most such games (DROP_Q); a bench player's role moves RECENCY_LAM of the
    way to his last game."""
    starter_hist = [(32.0, True)] * 10
    bench_hist = [(18.0, False)] * 9 + [(0.0, False)]                  # a DNP last game
    def one(hist, starter):
        team = [{"id": "x", "position": "G", "inactive": False, "starter": starter, "history": hist,
                 "cur_n": len(hist)}]
        return NM.project(team)["x"]["minutes"]
    assert one(starter_hist, False) < 0.5 * one(starter_hist, True)      # dropped from the five
    bench = one(bench_hist, False)
    role = NM.role_v2(bench_hist, cur_n=10)["mb"]
    assert bench == pytest.approx(role + NM.RECENCY_LAM * (0.0 - role), abs=0.15)
    # no game this season yet: last season's finale must not move him
    quiet = [{"id": "x", "position": "G", "inactive": False, "starter": False, "history": bench_hist, "cur_n": 0}]
    assert NM.project(quiet)["x"]["minutes"] == pytest.approx(NM.role_v2(bench_hist, cur_n=0)["mb"], abs=0.15)


def test_an_expected_five_is_blended_with_no_five_until_confirmed():
    """Critique cycle 4: NBA.com's *Expected* five missed a starter in 7 of 28 team-games
    (2026-10-20/21), so until the team confirms, minutes = EXPECTED_W x the five's
    projection + the rest x no five. Confirmed (or no flag: backtests) is unchanged."""
    roles = [34, 32, 30, 28, 26, 22, 20, 16, 12, 10, 6, 4]
    team = [{"id": str(i), "position": "G", "inactive": False, "starter": i in (0, 1, 2, 3, 5),
             "history": [(float(m), i < 5)] * 10, "cur_n": 10} for i, m in enumerate(roles)]
    hard = NM.project(team)
    assert NM.project([{**p, "confirmed": True} for p in team]) == hard
    none = NM.project([{**p, "starter": None} for p in team])
    exp = NM.project([{**p, "confirmed": False if p["starter"] else None} for p in team])
    w = NM.EXPECTED_W
    for i in exp:
        assert exp[i]["minutes"] == pytest.approx(w * hard[i]["minutes"] + (1 - w) * none[i]["minutes"], abs=0.06)
    # the regular starter left out of the Expected five is cut less than if confirmed
    assert hard["4"]["minutes"] < exp["4"]["minutes"] < none["4"]["minutes"]
    assert exp["5"]["promoted"] is hard["5"]["promoted"]
    assert sum(p["minutes"] for p in exp.values()) == pytest.approx(240, abs=0.6)


def test_the_starting_role_remembers_further_back_than_the_bench_role():
    """Critique cycle 5: a starter's role reads ~10 games and last season as 3 games
    (MS_DECAY, MS_KAPPA), so one cold week moves it less than the shared role."""
    hist = [(34.0, True)] * 60 + [(34.0, True)] * 10 + [(24.0, True)] * 4      # last season 34, now 4 cold games
    long_ = NM.role_v2(hist, cur_n=14)
    short = NM.role_v2(hist, cur_n=14, params={"MS_DECAY": None})
    assert NM.MS_DECAY is not None and short["ms"] < long_["ms"] < 34.0
    assert long_["mb"] == short["mb"] and long_["base"] == short["base"]          # bench / pre-lineup unchanged


def test_a_player_who_sat_last_game_gets_expected_minutes_and_his_teammates_a_blend():
    """Critique cycle 6: a stale player (did not dress for his team's latest game, not on
    the Out list) may sit again -- his minutes are AVAIL_P x his minutes if he plays, and
    teammates get AVAIL_W x (he plays) + the rest x (he sits)."""
    roles = [34, 32, 30, 28, 26, 22, 20, 16, 12, 10, 6, 4]
    team = [{"id": str(i), "position": "G", "inactive": False, "starter": None,
             "history": [(float(m), i < 5)] * 10, "cur_n": 10} for i, m in enumerate(roles)]
    plain, none = NM.project_available(team, set(), "inactives")
    assert plain == NM.project(team) and none == {}
    out, avail = NM.project_available(team, {"6"}, "inactives")
    a = NM.project(team)
    b = NM.project([{**p, "inactive": p["id"] == "6"} for p in team])
    w, (lo, hi) = NM.AVAIL_W["inactives"], NM.AVAIL_P["inactives"]
    assert avail == {"6": hi} and out["6"]["minutes"] == pytest.approx(hi * a["6"]["minutes"], abs=0.06)
    for i in a:
        if i != "6":
            assert out[i]["minutes"] == pytest.approx(w * a[i]["minutes"] + (1 - w) * b[i]["minutes"], abs=0.06)
    assert out["7"]["minutes"] > a["7"]["minutes"]                       # the next man up gains
    _, avail = NM.project_available(team, {"11"}, "confirmed")
    assert avail == {"11": NM.AVAIL_P["confirmed"][0]}                   # deep bench: role < AVAIL_ROLE


def test_injury_report_statuses_give_expected_minutes_by_their_chance_to_dress():
    """Critique cycle 7: a Questionable / Doubtful / stale player gets P(dress) x his
    minutes if he plays; each teammate gets A + sum (1 - p_k)(B_k - A) over them."""
    roles = [34, 32, 30, 28, 26, 22, 20, 16, 12, 10, 6, 4]
    team = [{"id": str(i), "position": "G", "inactive": False, "starter": None,
             "history": [(float(m), i < 5)] * 10, "cur_n": 10} for i, m in enumerate(roles)]
    plain, none = NM.project_uncertain(team, {}, "inactives")
    assert plain == NM.project(team) and none == {}
    out, p = NM.project_uncertain(team, {"1": "Questionable", "10": "stale"}, "inactives")
    P = NM.REPORT_P["inactives"]
    assert p == {"1": P[("Questionable", True)], "10": P[("stale", False)]}
    a = NM.project(team)
    b = {k: NM.project([{**x, "inactive": x["id"] == k} for x in team]) for k in ("1", "10")}
    assert out["1"]["minutes"] == pytest.approx(      # x the cycle 16 limit K
        NM.REPORT_K["inactives"]["Questionable"] * p["1"] * (a["1"]["minutes"] + (1 - p["10"]) * (b["10"]["1"]["minutes"] - a["1"]["minutes"])), abs=0.06)
    m7 = a["7"]["minutes"] + sum((1 - p[k]) * (b[k]["7"]["minutes"] - a["7"]["minutes"]) for k in b)
    assert out["7"]["minutes"] == pytest.approx(m7, abs=0.06) and out["7"]["minutes"] > a["7"]["minutes"]
    # expected minutes ~240 less what the limited Questionable player gives up (cycle 16
    # does not re-share it, as the lab's report_score does not)
    gone = (1 - NM.REPORT_K["inactives"]["Questionable"]) * out["1"]["minutes"] / NM.REPORT_K["inactives"]["Questionable"]
    assert sum(v["minutes"] for v in out.values()) == pytest.approx(240 - gone, abs=1.5)


def test_an_injured_namesake_on_another_team_does_not_take_a_player_off_the_board():
    # 2026-10-27: Justin Champagnie (WAS) Out; Julian Champagnie (SAS) played 25 min
    from edge.dfs_run_nba import official_index, _sig
    from edge.names import norm
    rows = [("Champagnie", "Justin", "Out", "foot"), ("Sarr", "Alex", "Out", "ankle")]
    official, sig = official_index(rows, {norm("Justin Champagnie"), norm("Julian Champagnie")})
    assert official[norm("Justin Champagnie")] == "Out"
    assert _sig("Julian Champagnie") not in sig       # a real other player: exact name only
    assert sig[_sig("Alexandre Sarr")] == ["Out"]     # not a known name: initial fallback kept


def test_recent_dk_skips_dnps_and_a_rookies_trailing_preseason_rows():
    from edge.dfs_nba_theory import recent_dk
    h = [(30, 1, 10.0), (30, 1, 20.0), (0, 0, 0.0), (30, 1, 30.0), (30, 1, 40.0), (20, 0, 99.0)]
    assert recent_dk(h, 3, n_pre=1) == 35.0          # this season: 0-min, 30, 40 -> played 30, 40
    assert recent_dk(h[:2], 0) == 15.0               # no games this season: last season's
    assert recent_dk([], 0) is None


def test_slate_ownership_sums_to_eight_slots_and_cheap_recent_value_draws_more():
    from edge import dfs_nba_theory as T
    pool = [{"name": f"p{i}", "pos": "PG", "proj": 25.0, "salary": 5000 + 500 * i, "recent": 25.0}
            for i in range(60)]
    pool[30]["recent"] = 45.0
    T.add_ownership(pool)
    assert abs(sum(p["own"] for p in pool) - 800) < 1
    assert pool[30]["own"] > pool[29]["own"] and pool[30]["own"] > pool[31]["own"]


def test_a_cheap_announced_starter_draws_more_ownership_than_the_same_player_on_the_bench():
    from edge import dfs_nba_theory as T
    pool = [{"name": f"p{i}", "pos": "SF", "proj": 22.0, "salary": 4000 + 100 * i, "recent": 22.0}
            for i in range(60)]
    pool[10]["starter"] = pool[11]["starter"] = True
    pool[11]["salary"] = pool[10]["salary"] = 4900
    pool[11]["salary"] = 9000          # an expensive starter gets no bonus
    T.add_ownership(pool)
    bench = next(p for p in pool if p["salary"] == 4900 and not p.get("starter"))
    assert pool[10]["own"] > bench["own"]


def test_a_starter_listed_twice_once_on_the_roster_keeps_his_starting_row():
    # 2026-10-27 DET ~1 h before tip: starter row then roster row (no position)
    from edge import nba_lineups
    row = {"personId": 1, "playerName": "Paul Reed", "rosterStatus": "Active"}
    raw = {"games": [{"gameId": "g", "homeTeam": {"teamAbbreviation": "DET", "players": [
        {**row, "position": "C", "lineupStatus": "Expected"},
        {**row, "position": "", "lineupStatus": "Confirmed"},
        {"personId": 2, "playerName": "Jalen Duren", "position": "", "rosterStatus": "Inactive"}]},
        "awayTeam": {"teamAbbreviation": "NYK", "players": []}}]}
    ps = nba_lineups.parse(raw)[0]["players"]
    assert len(ps) == 2
    reed = ps[0]
    assert reed["starter"] and reed["position"] == "C" and not reed["confirmed"]


def test_a_questionable_player_who_dresses_is_on_a_minutes_limit():
    # cycle 16: P(dress) x K x minutes for Doubtful / Questionable
    from edge import nba_minutes as NM
    team = [{"id": f"p{i}", "position": "G", "inactive": False, "starter": None,
             "history": [(30.0 - 2 * i, i < 5)] * 20} for i in range(10)]
    a, _ = NM.project_uncertain(team, {}, "inactives")
    b, p = NM.project_uncertain(team, {"p6": "Questionable"}, "inactives")
    assert b["p6"]["minutes"] < a["p6"]["minutes"] * p["p6"]
    assert abs(b["p6"]["minutes"] - a["p6"]["minutes"] * p["p6"] * NM.REPORT_K["inactives"]["Questionable"]) < 0.5
