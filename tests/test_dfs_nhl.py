import math
from collections import Counter

import numpy as np
import pytest

from edge import dfs_nhl_theory as theory
from edge import dfs_opt_nhl as opt
from edge import nhl, nhl_sim


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def test_skater_scoring_with_every_bonus():
    # 1 goal, 2 assists, 5 shots, 3 blocks: 8.5 + 10 + 7.5 + 3.9, plus the
    # 5-shot, 3-block and 3-point bonuses.
    assert nhl.skater_points(1, 2, 5, 3) == pytest.approx(38.9)


def test_hat_trick_bonus_stacks_with_three_points():
    assert nhl.skater_points(3, 0, 3, 0) == pytest.approx(25.5 + 4.5 + 3 + 3)


def test_goalie_scoring():
    assert nhl.goalie_points(35, 1, 1, 0, 0) == pytest.approx(24.5 - 3.5 + 6 + 3)
    assert nhl.goalie_points(30, 0, 1, 0, 1) == pytest.approx(21 + 6 + 4)
    assert nhl.goalie_points(30, 3, 0, 1, 0) == pytest.approx(21 - 10.5 + 2)


def test_slots():
    assert nhl.slots_for("LW") == {"W", "UTIL"}
    assert nhl.slots_for("C") == {"C", "UTIL"}
    assert nhl.slots_for("D") == {"D", "UTIL"}
    assert nhl.slots_for("G") == {"G"}


# ---------------------------------------------------------------------------
# market -> means
# ---------------------------------------------------------------------------
def test_poisson_mean_inverts_an_even_half_goal_line():
    assert nhl_sim.poisson_mean(0.5, 0.5) == pytest.approx(math.log(2), abs=1e-4)


def test_market_mean_needs_both_sides():
    assert nhl_sim.market_mean({"Over": 1.9, "point": 2.5}) is None
    assert nhl_sim.market_mean({"Over": 1.9, "Under": 1.9, "point": 2.5}) > 2.5


def test_implied_goals_split_follows_the_moneyline():
    even = nhl_sim.implied_goals(6.0, 0.5, 0.5)
    assert even[0] == pytest.approx(even[1], rel=1e-3)
    fav = nhl_sim.implied_goals(6.0, 0.5, 0.65)
    assert fav[0] > fav[1] and sum(fav) == pytest.approx(sum(even), rel=1e-6)


# ---------------------------------------------------------------------------
# the simulation
# ---------------------------------------------------------------------------
def _team(t, opp, lam_scale=1.0):
    rows = []
    for line in ("F1", "F2", "F3"):
        for k in range(3):
            rows.append({"name": f"{t}{line}{k}", "team": t, "opp": opp, "game": "g",
                         "pos": "C" if k == 0 else "W", "line": line,
                         "pp": "PP1" if line == "F1" else None, "salary": 5000,
                         "means": {"goals": 0.3 * lam_scale, "assists": 0.45 * lam_scale,
                                   "shots": 2.5, "blocks": 0.4}})
    for line in ("D1", "D2"):
        for k in range(2):
            rows.append({"name": f"{t}{line}{k}", "team": t, "opp": opp, "game": "g",
                         "pos": "D", "line": line, "pp": None, "salary": 4000,
                         "means": {"goals": 0.08, "assists": 0.3, "shots": 1.8, "blocks": 1.5}})
    rows.append({"name": f"{t}G", "team": t, "opp": opp, "game": "g", "pos": "G",
                 "line": "G1", "pp": None, "salary": 8000, "means": {"saves": 27}})
    return rows


def test_linemates_score_together():
    pool = _team("AAA", "BBB") + _team("BBB", "AAA")
    sim = nhl_sim.simulate(pool, {"g": {"home": "AAA", "away": "BBB",
                                        "lam_home": 3.2, "lam_away": 3.0}}, n_sims=4000)
    ix = {p["name"]: i for i, p in enumerate(pool)}
    same = np.corrcoef(sim[:, ix["AAAF10"]], sim[:, ix["AAAF11"]])[0, 1]
    other = np.corrcoef(sim[:, ix["AAAF10"]], sim[:, ix["AAAF31"]])[0, 1]
    assert same > other + 0.05
    goalie_vs_opp = np.corrcoef(sim[:, ix["AAAG"]], sim[:, ix["BBBF10"]])[0, 1]
    assert goalie_vs_opp < 0


def test_skaters_never_score_negative_and_favourites_score_more():
    pool = _team("AAA", "BBB") + _team("BBB", "AAA")
    sim = nhl_sim.simulate(pool, {"g": {"home": "AAA", "away": "BBB",
                                        "lam_home": 3.8, "lam_away": 2.4}}, n_sims=2000)
    sk = [i for i, p in enumerate(pool) if p["pos"] != "G"]
    assert sim[:, sk].min() >= 0
    a = [i for i in sk if pool[i]["team"] == "AAA"]
    b = [i for i in sk if pool[i]["team"] == "BBB"]
    assert sim[:, a].mean() > sim[:, b].mean()


# ---------------------------------------------------------------------------
# the optimiser
# ---------------------------------------------------------------------------
def _slate():
    pool = []
    for gname, (h, a) in {"g1": ("AAA", "BBB"), "g2": ("CCC", "DDD"), "g3": ("EEE", "FFF")}.items():
        for t, o in ((h, a), (a, h)):
            for p in _team(t, o):
                p["game"] = gname
                pool.append(p)
    games = {g: {"home": h, "away": a, "lam_home": 3.1, "lam_away": 3.0}
             for g, (h, a) in {"g1": ("AAA", "BBB"), "g2": ("CCC", "DDD"),
                               "g3": ("EEE", "FFF")}.items()}
    rng = np.random.default_rng(3)
    for p in pool:
        p["salary"] = int(rng.integers(30, 90)) * 100 if p["pos"] != "G" else 7500
    sim = nhl_sim.simulate(pool, games, n_sims=800)
    nhl_sim.summarise(sim, pool)
    theory.add_ownership(pool)
    return pool, sim


@pytest.mark.parametrize("mode", ["cash", "gpp"])
def test_every_lineup_is_legal(mode):
    pool, sim = _slate()
    res = opt.optimize(pool, sim, mode=mode, iters=15, seed=1)
    rows = res["lineup"]
    pos = Counter(r["pos"] for r in rows)
    assert len(rows) == 9 and pos["G"] == 1 and pos["C"] >= 2 and pos["W"] >= 3 and pos["D"] >= 2
    assert sum(r["salary"] for r in rows) <= nhl.SALARY_CAP
    assert len({r["team"] for r in rows if r["pos"] != "G"}) >= 3   # skaters only
    assert len({r["game"] for r in rows}) >= 2
    goalie = next(r for r in rows if r["pos"] == "G")
    assert not any(r["team"] == goalie["opp"] for r in rows if r["pos"] != "G")
    assert [s for s, _ in res["slots"]] == ["C", "C", "W", "W", "W", "D", "D", "G", "UTIL"]
    if mode == "gpp":
        sizes = sorted(Counter(r["team"] for r in rows if r["pos"] != "G").values(), reverse=True)
        assert sizes[0] >= 3 and sizes[1] >= 3 and sizes[0] <= opt.MAX_FROM_TEAM


def test_ownership_fills_each_position_group():
    pool, _ = _slate()
    for pos, slots in theory.GROUP_SLOTS.items():
        total = sum(p["own"] for p in pool if p["pos"] == pos)
        assert total == pytest.approx(100 * slots, abs=1.5)


def test_default_slate_is_next_main_not_biggest():
    from edge import dfs_run_nhl as R
    rows = [{"gid": 154690, "label": "Main", "games": 3, "start": "2026-10-07T23:30:00Z"},
            {"gid": 154693, "label": "Late", "games": 1, "start": "2026-10-08T02:00:00Z"},
            {"gid": 154696, "label": "Main", "games": 10, "start": "2026-10-08T23:00:00Z"}]
    assert rows[R.default_slate(rows)]["gid"] == 154690
    assert R.default_slate([{"gid": 1, "label": "Late", "games": 2, "start": "x"}]) == 0


def test_cash_field_is_legal_and_tracks_ownership():
    from edge import dfs_nhl_field as field
    pool, sim = _slate()
    own = field.cash_ownership(pool)
    lineups = field.sample_field(pool, own, n=200, seed=2)
    assert lineups.shape == (200, 9)
    assert all(opt._valid(pool, list(lu)) for lu in lineups)
    seen = np.bincount(lineups.ravel(), minlength=len(pool)) / len(lineups)
    assert np.corrcoef(seen, own)[0, 1] > 0.8
    line = field.cash_line(sim, lineups)
    totals = sim[:, lineups].sum(axis=2)
    assert line.shape == (sim.shape[0],)
    assert (line >= totals.min(axis=1)).all() and (line <= totals.max(axis=1)).all()


def test_field_aware_cash_clears_the_line_at_least_as_often_as_the_floor():
    from edge import dfs_nhl_field as field
    pool, sim = _slate()
    own = field.cash_ownership(pool)
    line = field.cash_line(sim, field.sample_field(pool, own, n=200, seed=2))
    fa = opt.optimize(pool, sim, mode="cash", iters=15, seed=1, line=line)
    fl = opt.optimize(pool, sim, mode="cash", iters=15, seed=1)

    def p(res):
        return (sim[:, [pool.index(r) for r in res["lineup"]]].sum(axis=1) >= line).mean()
    assert opt._valid(pool, [pool.index(r) for r in fa["lineup"]])
    assert p(fa) >= p(fl) - 0.01


def test_seven_d_lineup_is_read_from_d4_or_eleven_forwards():
    from edge import dfs_run_nhl as R
    lines = {}
    for team, n_f, d4 in (("COL", 11, True), ("DET", 11, False), ("BOS", 12, False)):
        for i in range(n_f):
            lines[(team, f"f{i}")] = {"line": f"F{i // 3 + 1}"}
        for i in range(6):
            lines[(team, f"d{i}")] = {"line": f"D{i // 2 + 1}"}
        if d4:
            lines[(team, "d7")] = {"line": "D4"}
    assert R.seven_d_teams(lines) == {"COL", "DET"}
    assert "d4" in nhl.LINE_GROUPS and nhl.LINE_GROUPS["d4"] == "D4"


def test_seven_d_scale_only_shrinks_and_keeps_keys():
    from edge import dfs_run_nhl as R
    m = {"shots": 2.0, "goals": 0.1, "assists": 0.3, "blocks": 1.5}
    s = R.scale_seven_d(m)
    assert set(s) == set(m)
    assert all(0.8 < s[k] / m[k] <= 1.0 for k in m)
    assert s["blocks"] / m["blocks"] < s["shots"] / m["shots"]   # blocks lose the most


def test_role_ratio_shrinks_this_season_toward_last():
    from edge import dfs_run_nhl as R
    assert R.role_ratio(None, [15.0]) == 1.0                  # rookie: no last season
    assert R.role_ratio(18.0, []) == 1.0                      # yet to play this season
    # two games at 12 min against 18 last season, K=5: (5*18 + 2*12) / 7 / 18
    assert R.role_ratio(18.0, [12.0, 12.0], k=5) == pytest.approx((90 + 24) / 7 / 18)
    # only the last `last` games set the current level; all of them count as n
    assert R.role_ratio(10.0, [30.0] * 5 + [10.0] * 5, k=5, last=5) == pytest.approx(1.0)
    assert R.role_ratio(10.0, [20.0] * 50, k=5) > 1.9


def test_scale_role_touches_only_what_last_season_set():
    from edge import dfs_run_nhl as R
    m = {"shots": 2.0, "goals": 0.2, "assists": 0.3, "blocks": 1.0}
    p = {"goals": 1.0, "assists": 1.0, "shots": 1.0, "blocks": 1.0}
    props = R.scale_role(m, 0.5, [], p)                       # props priced shots + points
    assert props["shots"] == 2.0 and props["goals"] == 0.2 and props["blocks"] == 0.5
    none = R.scale_role(m, 0.5, ["shots", "points"], p)
    assert none == pytest.approx({"shots": 1.0, "goals": 0.1, "assists": 0.15, "blocks": 0.5})
    assert R.scale_role(m, 1.0, ["shots", "points"]) is m
    assert m["shots"] == 2.0                                  # the input is not mutated


def test_group_toi_keeps_namesakes_apart_and_traded_players_together():
    from edge import dfs_run_nhl as R
    from edge.names import norm
    rows = [{"name": "Elias Pettersson", "pos": "C", "date": "2026-10-02", "toi": 19.0},
            {"name": "Elias Pettersson", "pos": "D", "date": "2026-10-02", "toi": 14.0},
            {"name": "Elias Pettersson", "pos": "C", "date": "2026-10-01", "toi": 18.0},
            {"name": "A Trade", "pos": "L", "date": "2026-10-05", "toi": 11.0},
            {"name": "A Trade", "pos": "L", "date": "2026-10-03", "toi": 15.0}]
    g = R.group_toi(rows)
    assert g[(norm("Elias Pettersson"), "F")] == [18.0, 19.0]  # oldest first
    assert g[(norm("Elias Pettersson"), "D")] == [14.0]
    assert g[(norm("A Trade"), "F")] == [15.0, 11.0]


def test_current_toi_unavailable_is_none_not_an_error(monkeypatch):
    from edge import dfs_run_nhl as R

    def down(*a, **k):
        raise OSError("offline")
    monkeypatch.setattr(R.nhl, "skater_game_toi", down)
    assert R.current_toi("2026-10-22") is None


def test_season_stats_refetch_a_cache_with_repeated_players(monkeypatch, tmp_path):
    # Unsorted 100-row paging once repeated players and dropped others (cycle 2).
    import json
    monkeypatch.setattr(nhl, "CACHE_DIR", tmp_path)
    dup = [{"playerId": 1, "skaterFullName": "A B"}, {"playerId": 1, "skaterFullName": "A B"}]
    for rep in ("summary", "realtime"):
        (tmp_path / f"skater_{rep}_X.json").write_text(json.dumps(dup))
    good = {"data": [{"playerId": 1, "skaterFullName": "A B", "gamesPlayed": 10},
                     {"playerId": 2, "skaterFullName": "C D", "gamesPlayed": 10}], "total": 2}
    urls = []
    monkeypatch.setattr(nhl, "_get", lambda url, **k: urls.append(url) or json.dumps(good).encode())
    assert set(nhl.season_skater_stats("X")) == {1, 2}
    assert len(urls) == 2 and all("limit=-1" in u for u in urls)
    bad = {"data": good["data"][:1], "total": 2}                  # short of the API's total
    monkeypatch.setattr(nhl, "_get", lambda url, **k: json.dumps(bad).encode())
    with pytest.raises(RuntimeError):
        nhl.season_skater_stats("X", refresh=True)


def test_season_rates_leave_a_missing_report_field_missing(monkeypatch):
    from edge import dfs_run_nhl as R
    monkeypatch.setattr(R.nhl, "season_skater_stats", lambda s: {
        1: {"skaterFullName": "Has Both", "gamesPlayed": 10, "goals": 2, "assists": 3, "shots": 20,
            "blockedShots": 0, "timeOnIcePerGame": 900},
        2: {"skaterFullName": "No Realtime", "gamesPlayed": 10, "goals": 2, "assists": 3, "shots": 20,
            "timeOnIcePerGame": 900}})
    from edge.names import norm
    r = R._season_rates()
    assert r[norm("Has Both")]["blocks"] == 0.0                   # a real zero stays zero
    assert "blocks" not in r[norm("No Realtime")]                 # missing -> board default + imputed
    assert r[norm("No Realtime")]["shots"] == 2.0


def test_regress_rates_pull_short_samples_to_ice_time_prior():
    from edge import dfs_run_nhl as R
    rates = {"vet": {"goals": 0.2, "assists": 0.3, "shots": 2.0, "blocks": 0.5, "toi": 15.0, "gp": 80, "pos": "F"},
             "hot": {"goals": 0.5, "assists": 0.3, "shots": 2.0, "toi": 15.0, "gp": 10, "pos": "F"},
             "d": {"goals": 0.05, "assists": 0.3, "shots": 1.5, "blocks": 1.5, "toi": 20.0, "gp": 80, "pos": "D"},
             "no_toi": {"goals": 0.9, "assists": 0.0, "shots": 1.0, "toi": None, "gp": 6}}
    out = R.regress_rates(rates, k=30.0)
    prior_g = (0.2 * 80 + 0.5 * 10) / 90               # F goals a game at 15 min (one toi: b unidentified)
    assert abs(out["hot"]["goals"] - (10 * 0.5 + 30 * prior_g) / 40) < 1e-9
    assert out["hot"]["goals"] < 0.35                                 # 10 hot games mostly regress (0.5 raw)
    assert abs(out["vet"]["goals"] - 0.2) < 0.02                      # 80 games barely move
    assert "blocks" not in out["hot"]                                 # missing stays missing
    assert abs(out["d"]["blocks"] - 1.5) < 1e-9                       # sole D: his own prior
    assert out["no_toi"] is rates["no_toi"]                           # no ice time: untouched
    assert R.regress_rates(rates, k=0) is rates
    # the prior's slope in ice time: per-game shots rising as toi ** 2 recover b = 2
    rows = [{"shots": 0.01 * t ** 2, "toi": t, "gp": 50} for t in (8.0, 12.0, 16.0, 20.0)]
    a, b = R.toi_prior(rows, "shots")
    assert abs(b - 2.0) < 1e-9 and abs(a - 0.01) < 1e-9


def test_name_aliases_match_nicknames_but_not_namesakes():
    from edge import dfs_run_nhl as R
    from edge.names import norm
    rows = [{"id": 1, "team": "PIT", "name": "Nick Robertson"},
            {"id": 2, "team": "WSH", "name": "Aliaksei Protas"},
            {"id": 3, "team": "WSH", "name": "Ilya Protas"},
            {"id": 4, "team": "MTL", "name": "Zachary Bolduc"}]
    rates = {norm("Nick Robertson"): {}, norm("Aliaksei Protas"): {}}
    al = R.name_aliases(rows, rates)
    assert al[R._initial_last("PIT", "Nicholas Robertson")] == norm("Nick Robertson")
    assert R._initial_last("WSH", "Ilya Protas") not in al            # a rookie, not his brother
    assert R._initial_last("MTL", "Zack Bolduc") not in al            # no last-season rates to borrow


def test_points_stretch_widens_around_the_reference_and_keeps_the_split():
    from edge import dfs_run_nhl as R
    def stretched(points, assists):
        g, a = max(points - assists, 0.02), assists
        k = R.POINTS_REF * (points / R.POINTS_REF) ** R.POINTS_STRETCH / (g + a)
        return g * k, a * k
    g, a = stretched(R.POINTS_REF, 0.3)
    assert g + a == pytest.approx(R.POINTS_REF)                       # the reference is a fixed point
    g, a = stretched(1.2, 0.7)
    assert g + a > 1.2 and a / (g + a) == pytest.approx(0.7 / 1.2)    # stars rise, split kept
    g, a = stretched(0.3, 0.2)
    assert g + a < 0.3


def _simulated_assists(sim_module, monkeypatch):
    """(simulated assists per game, the targets the solve was given) for one toy
    team, through sim_module.simulate itself."""
    team = [{"line": "F1", "pp": "PP1"}] * 3 + [{"line": f"F{i}", "pp": None} for i in (2, 2, 2, 3, 3, 3)]
    goals = [0.5, 0.3, 0.3, 0.2, 0.2, 0.2, 0.1, 0.1, 0.1]
    assists = [0.9, 0.5, 0.5, 0.4, 0.4, 0.4, 0.2, 0.2, 0.2]
    pool = [{"name": f"h{i}", "team": "HOM", "pos": "C", **t,
             "means": {"goals": g, "assists": a, "shots": 2.0, "blocks": 0.5}}
            for i, (t, g, a) in enumerate(zip(team, goals, assists))]
    pool += [{"name": f"a{i}", "team": "AWY", "pos": "C", "line": f"F{i // 3 + 1}", "pp": None,
              "means": {"goals": 0.2, "assists": 0.3, "shots": 2.0, "blocks": 0.5}} for i in range(9)]
    games = {"g": {"home": "HOM", "away": "AWY", "lam_home": 3.4, "lam_away": 2.8}}
    got = []
    real = nhl.skater_points

    def capture(G, AS, S, B, *a, **k):
        got.append((G.sum(axis=1), AS))
        return real(G, AS, S, B, *a, **k)
    monkeypatch.setattr(nhl, "skater_points", capture)
    sim_module.simulate(pool, games, n_sims=40_000, seed=1)
    team_goals, AS = got[0]
    targets = np.array(assists) * team_goals.mean() * nhl_sim.ASSISTS_PER_GOAL / sum(assists)
    return AS.mean(axis=0), targets, AS.sum() / team_goals.sum()


def test_simulated_assists_hit_their_targets(monkeypatch):
    # Every assisted goal carries 1 + P_TWO assists to OTHER teammates, and each
    # skater gets his target -- the top assister included. Before 2026-10-22 a
    # clashing second assist could be dropped (fewer assists per goal than fitted)
    # and the solve over-credited high-share assisters.
    got, targets, per_goal = _simulated_assists(nhl_sim, monkeypatch)
    assert per_goal == pytest.approx(nhl_sim.ASSISTS_PER_GOAL, rel=0.01)
    assert got == pytest.approx(targets, rel=0.04)
    assert got[0] == pytest.approx(targets[0], rel=0.02)


def test_skater_means_is_the_board_recipe():
    from edge import dfs_run_nhl as R
    base = {"goals": 0.3, "assists": 0.4, "shots": 2.5, "blocks": 0.6, "toi": 18.0}
    # no props: last season x scale, all flagged; role scales them (r = 1 without games)
    m, imp, r = R.skater_means(base, "C", 1.1)
    assert imp == ["shots", "points"] and r == 1.0
    assert m == pytest.approx({"shots": 2.75, "goals": 0.33, "assists": 0.44, "blocks": 0.6})
    m2, _, r2 = R.skater_means(base, "C", 1.1, toi_recent=[12.0] * 10)
    assert r2 < 1 and m2["goals"] < m["goals"] and m2["blocks"] < m["blocks"]
    # props: market means, stretched points, blocks still last season's; nothing imputed
    mp, impp, _ = R.skater_means(base, "C", 1.1, shots=3.0, points=0.9, assists=0.5)
    assert impp == [] and mp["shots"] == 3.0 and mp["goals"] + mp["assists"] > 0.9
    # unknown skater: defaults, blocks flagged too; 7-D shrinks a defenceman
    md, impd, _ = R.skater_means({}, "D", 1.0, seven_d=True)
    assert impd == ["shots", "points", "blocks"]
    assert md["blocks"] == pytest.approx(R.DEFAULT_BLOCKS["D"] * R.SEVEN_D_SCALE["blocks"])


def test_blend_season_weights_this_season_by_games_played():
    from edge import dfs_run_nhl as R
    m = {"goals": 0.1, "assists": 0.2, "shots": 2.0, "blocks": 1.0}
    k = {"points": 30.0, "shots": 7.0, "blocks": 20.0}
    assert R.blend_season(m, None, 1.0, ["shots", "points"]) is m
    # 10 games: 4 goals, 0 assists, 30 shots, 5 blocks at scale 1.2
    out = R.blend_season(m, (10, 4, 0, 30, 5), 1.2, ["shots", "points"], k)
    w_p, w_s, w_b = 30 / 40, 7 / 17, 20 / 30
    assert out["goals"] == pytest.approx(w_p * 0.1 + (1 - w_p) * 0.4 * 1.2)
    assert out["assists"] == pytest.approx(w_p * 0.2)
    assert out["shots"] == pytest.approx(w_s * 2.0 + (1 - w_s) * 3.0 * 1.2)
    assert out["blocks"] == pytest.approx(w_b * 1.0 + (1 - w_b) * 0.5)    # blocks: no scale
    # a prop skater keeps his market goals / assists / shots; only blocks blend
    props = R.blend_season(m, (10, 4, 0, 30, 5), 1.2, [], k)
    assert {x: props[x] for x in ("goals", "assists", "shots")} == {x: m[x] for x in ("goals", "assists", "shots")}
    assert props["blocks"] == pytest.approx(out["blocks"])


def test_skater_means_blends_this_season_before_the_seven_d_scale():
    from edge import dfs_run_nhl as R
    base = {"goals": 0.1, "assists": 0.2, "shots": 1.5, "blocks": 1.5, "toi": 20.0}
    six, _, _ = R.skater_means(base, "D", 1.0, season=(20, 1, 4, 20, 20))
    seven, _, _ = R.skater_means(base, "D", 1.0, season=(20, 1, 4, 20, 20), seven_d=True)
    assert seven["blocks"] == pytest.approx(six["blocks"] * R.SEVEN_D_SCALE["blocks"])
    assert six["blocks"] < 1.5                                   # 1.0 a game this season pulls it down


def test_lab_shots_power_moves_only_imputed_shots():
    """Cycle 12's lab variant (not shipped): imputed shots x scale ** (e - 1), props
    untouched, the goals + 0.05 floor kept; the shrink keeps the slate x position mean."""
    from scripts import nhl_skater_lab as lab
    from edge import dfs_run_nhl as R
    hi = 1.5 * R.LEAGUE_TEAM_GOALS
    s = {"implied": {"AAA": hi}, "pool": [
        {"pos": "C", "team": "AAA", "imputed": ["shots", "points"],
         "means": {"shots": 2.25, "goals": 0.3, "assists": 0.4, "blocks": 0.5}},
        {"pos": "C", "team": "AAA", "imputed": ["shots", "points"],
         "means": {"shots": 0.30, "goals": 0.29, "assists": 0.1, "blocks": 0.5}},
        {"pos": "W", "team": "AAA", "imputed": [],
         "means": {"shots": 3.0, "goals": 0.5, "assists": 0.5, "blocks": 0.3}}]}
    out = lab.shots_power(s, {"e": 0.5})["pool"]
    assert out[0]["means"]["shots"] == pytest.approx(2.25 * 1.5 ** -0.5)
    assert out[1]["means"]["shots"] == pytest.approx(0.34)          # the floor
    assert out[2]["means"]["shots"] == 3.0                          # a prop: untouched
    before = np.mean([p["means"]["shots"] for p in out[:2]])
    lab.shrink_imputed_shots(s, 0.5)
    assert np.mean([p["means"]["shots"] for p in s["pool"][:2]]) == pytest.approx(before, abs=0.05)


def test_lineup_lab_replays_a_cached_board_and_scores_it_against_the_field(monkeypatch):
    """Cycle 13 (lineup harness, scripts/nhl_lineup_lab.py): a cached pre-sim board is
    simulated and run through production's three optimizers (legal 9s); each lineup is
    scored in its own contest type only (cash and cash_floor vs cash, gpp vs GPPs), by field
    percentile, cash line and the payout table, on DK points with a box-score fallback."""
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location(
        "nhl_lineup_lab_t", Path(__file__).resolve().parents[1] / "scripts" / "nhl_lineup_lab.py")
    L = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(L)
    pool, _ = _slate()
    for p in pool:
        for k in ("proj", "sd", "floor", "ceil", "p99", "own", "leverage"):
            p.pop(k, None)
    games = {g: {"home": h, "away": a, "lam_home": 3.1, "lam_away": 3.0}
             for g, (h, a) in {"g1": ("AAA", "BBB"), "g2": ("CCC", "DDD"),
                               "g3": ("EEE", "FFF")}.items()}
    board = {"gid": 1, "date": "2026-10-20", "games": games, "pool": pool}
    monkeypatch.setattr(L, "N_SIMS", 400)
    monkeypatch.setattr(L, "ITERS", 10)
    monkeypatch.setattr(L, "_BOARDS", {1: board})
    _, _, _, lus = L._job(("shipped", 1, 0))
    for mode in L.MODES:
        lu = lus[mode]
        assert len(lu) == 9 and sum(p["salary"] for p in lu) <= nhl.SALARY_CAP
        assert Counter(p["pos"] for p in lu)["G"] == 1

    field = np.array([300.0, 250.0, 200.0, 150.0, 100.0])
    monkeypatch.setattr(L, "fields", lambda: {1: [(11, "cash", field, 200.0), (12, "gpp", field, 300.0)]})
    gpp = lus["gpp"]
    monkeypatch.setattr(L, "_DK", {1: {L.norm(p["name"]): 30.0 for p in gpp[1:]}})
    goalie_in_box = gpp[0]
    from scripts.nhl_ownership_signal import box_key
    bk = box_key(goalie_in_box["team"], goalie_in_box["name"])
    if goalie_in_box["pos"] == "G":
        bk = ("G",) + bk
    monkeypatch.setattr(L, "_BOX", [{"2026-10-20": {bk: 10.0}}, box_key])
    monkeypatch.setattr(L, "_PAY", {12: {"fee": 1.0, "tiers": [(1, 3, 2.0)]}})
    out = L.contest_rows(1, "gpp", [gpp], "t")
    assert len(out) == 1 and out[0]["contest"] == 12                      # never the cash contest
    r = out[0]
    assert r["points"] == 250.0 and r["cashed"] == 0.0 and r["ret"] == 2.0   # rank 2 (tie not split)
    assert r["pctile"] == pytest.approx(3 / 5) and r["split"] == "test"
    assert [x["contest"] for x in L.contest_rows(1, "cash_floor", [lus["cash_floor"]], "t")] == [11]


# ---------------------------------------------------------------------------
# starting goalies: P(start) (NHL critique cycle 14, 2026-11-12)
# ---------------------------------------------------------------------------
def test_goalie_start_prob_cells():
    from edge import dfs_run_nhl as R
    assert R.goalie_start_prob("Confirmed", True, False) == 1.0
    assert R.goalie_start_prob("Likely", False, True) == 1.0
    # DFO's no-news default on rest = the goalie who started last game: the warmup switch risk
    assert R.goalie_cell(None, True, False) == "Projected/started_last"
    assert R.goalie_cell("Projected", True, True) == "Projected/started_last"
    # its rotation pick for the second night of a back-to-back
    assert R.goalie_cell(None, False, True) == "Projected/b2b_backup"
    assert R.goalie_cell(None, False, False) == "Projected/other"
    # no box scores reachable -> the status rate; an unknown DFO label is no news
    assert R.goalie_cell(None, None, None) == "Projected"
    assert R.goalie_cell("Unconfirmed", None, None) == "Projected"
    p = R.GOALIE_START_P
    assert p["Projected/started_last"] < p["Projected/other"] < p["Projected/b2b_backup"] < 1.0
    assert all(0.0 < v <= 1.0 for v in p.values())


def test_unsure_goalie_zeroes_only_his_own_draws():
    games = {"g": {"home": "AAA", "away": "BBB", "lam_home": 3.2, "lam_away": 3.0}}
    base = _team("AAA", "BBB") + _team("BBB", "AAA")
    sure = nhl_sim.simulate([dict(p) for p in base], games, n_sims=4000, seed=5)
    pool = [dict(p) for p in base]
    gi = next(i for i, p in enumerate(pool) if p["name"] == "AAAG")
    pool[gi]["p_start"] = 0.6
    unsure = nhl_sim.simulate(pool, games, n_sims=4000, seed=5)
    others = [i for i in range(len(pool)) if i != gi]
    assert np.array_equal(sure[:, others], unsure[:, others])      # skaters + the other goalie
    sat = unsure[:, gi] != sure[:, gi]
    assert np.all(unsure[sat, gi] == 0.0)
    assert 0.35 < sat.mean() < 0.45
    assert unsure[:, gi].mean() < 0.7 * sure[:, gi].mean()
    pool[gi]["p_start"] = 1.0                                      # a certain starter: untouched
    assert np.array_equal(nhl_sim.simulate(pool, games, n_sims=4000, seed=5), sure)


def test_last_starts_takes_each_teams_latest_regular_season_starter(monkeypatch):
    week = {"gameWeek": [
        {"date": "2026-11-03", "games": [{"id": 1, "gameType": 2, "awayTeam": {"abbrev": "NSH"},
                                          "homeTeam": {"abbrev": "BOS"}}]},
        {"date": "2026-11-04", "games": [{"id": 2, "gameType": 2, "awayTeam": {"abbrev": "LAK"},
                                          "homeTeam": {"abbrev": "NSH"}}]},
        {"date": "2026-11-05", "games": [{"id": 3, "gameType": 2, "awayTeam": {"abbrev": "LAK"},
                                          "homeTeam": {"abbrev": "BOS"}}]}]}
    boxes = {1: [("NSH", "J. Saros", True), ("BOS", "J. Swayman", True)],
             2: [("LAK", "D. Kuemper", True), ("NSH", "J. Annunen", True), ("NSH", "J. Saros", False)],
             3: [("LAK", "A. Forsberg", True), ("BOS", "J. Korpisalo", True)]}
    monkeypatch.setattr(nhl, "_cached_json", lambda url, path, refresh=False: week)
    monkeypatch.setattr(nhl, "boxscore", lambda gid, refresh=False: {"goalies": [
        {"team": t, "name": n, "starter": s} for t, n, s in boxes[gid]]})
    got = nhl.last_starts("2026-11-05", {"NSH", "LAK", "BOS"})
    assert got["NSH"] == {"date": "2026-11-04", "goalie": "J. Annunen"}     # tonight's games excluded
    assert got["LAK"] == {"date": "2026-11-04", "goalie": "D. Kuemper"}
    assert got["BOS"] == {"date": "2026-11-03", "goalie": "J. Swayman"}


def test_goalie_lab_cells_mirror_production():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
    import nhl_goalie_lab as G
    from edge import dfs_run_nhl as R
    last = ("2026-11-04", G.sig("D. Kuemper"))
    for status in ("Confirmed", "Likely", "Projected"):
        for goalie, date in (("Darcy Kuemper", "2026-11-05"), ("Anton Forsberg", "2026-11-05"),
                             ("Darcy Kuemper", "2026-11-07"), ("Anton Forsberg", "2026-11-07")):
            sl = G.sig(goalie) == last[1]
            b2b = date == "2026-11-05"
            assert G.cell_of(status, goalie, last, date) == R.goalie_cell(status, sl, b2b)
    assert G.cell_of("Projected", "Anton Forsberg", None, "2026-11-05") == "Projected/other"
    # fit: n counts team-games (leads pooled), shrunk toward the status rate
    rows = [{"date": "2026-10-01", "team": "T", "lead": ld, "status": "Projected",
             "cell": "Projected/started_last", "y": k < 6, "pts": 0.0}
            for k in range(10) for ld in G.LEADS]
    fit = G.fit_cells(rows)
    assert fit["_n"]["Projected/started_last"] == 10.0
    assert 0.6 < fit["Projected/started_last"] < 0.67
    assert fit["Confirmed"] == 1.0


def test_lab_pinned_store_keeps_each_querys_rows_in_the_stores_order(tmp_path, monkeypatch):
    """scripts/nhl_skater_lab._PinnedStore (2026-11-12): an archived scan is read from F: once
    per distinct query, then from local disk, rows in the store's own order (a filtered full
    read reordered the 621 h2h/totals rows and moved a skater 10.34 -> 10.58)."""
    from scripts import nhl_skater_lab as L
    monkeypatch.setattr(L, "SCANS", tmp_path)

    class Store:
        calls = 0
        conn = "conn"

        def quotes(self, scan_id, sport_key=None, markets=None, event_ids=None):
            Store.calls += 1
            rows = [{"event_id": e, "market": m, "sport_key": "icehockey_nhl"}
                    for e in ("b", "a") for m in ("totals", "h2h")]
            return [r for r in rows if (not markets or r["market"] in markets)
                    and (not event_ids or r["event_id"] in event_ids)][::-1]   # the store's own order

        def events(self, scan_id, sport_key=None):
            Store.calls += 1
            return [{"event_id": "b"}, {"event_id": "a"}]

    real = Store()
    first = L._PinnedStore(real, 7)
    want = real.quotes(7, "icehockey_nhl", ["h2h"])
    Store.calls = 0
    assert first.quotes(7, "icehockey_nhl", ["h2h"]) == want and Store.calls == 1
    assert first.events(7, "icehockey_nhl") == [{"event_id": "b"}, {"event_id": "a"}] and Store.calls == 2
    again = L._PinnedStore(Store(), 7)                       # a new process: read from disk
    assert again.quotes(7, "icehockey_nhl", ["h2h"]) == want and again.events(7, "icehockey_nhl")
    assert Store.calls == 2
    again.quotes(8, "icehockey_nhl")                         # another scan: the real store
    assert Store.calls == 3 and again.conn == "conn"         # anything else passes through
