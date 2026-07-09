from edge.dfs import project_hitter_skill, actual_hitter_points, actual_pitcher_points, SLOT_PA, LG_K9


def approx(a, b, tol=0.05):
    return abs(a - b) < tol


def test_skill_x_opportunity_base():
    # 1.7 DKpts/PA, leadoff (4.65 PA), neutral park/matchup
    assert approx(project_hitter_skill(1.7, 1), round(1.7 * 4.65, 1))


def test_slot_opportunity_monotonic():
    # same skill, leadoff projects higher than the 9-hole (more PA)
    assert project_hitter_skill(1.7, 1) > project_hitter_skill(1.7, 9)


def test_matchup_lowers_vs_high_k_pitcher():
    base = project_hitter_skill(1.7, 3)
    tough = project_hitter_skill(1.7, 3, opp_k9=LG_K9 * 1.4)   # high-K SP
    weak = project_hitter_skill(1.7, 3, opp_k9=LG_K9 * 0.6)    # low-K SP
    assert tough < base < weak


def test_team_total_environment():
    base = project_hitter_skill(1.7, 3)
    hi = project_hitter_skill(1.7, 3, team_total=5.5)   # high implied offense
    lo = project_hitter_skill(1.7, 3, team_total=3.0)
    assert lo < base < hi


def test_actual_points_hitter_and_pitcher():
    # 2-run HR + a single: 1B(3) + HR(10) + 2 RBI(4) + 1 run(2) = 19
    bat = {"hits": 2, "doubles": 0, "triples": 0, "homeRuns": 1, "rbi": 2, "runs": 1,
           "baseOnBalls": 0, "hitByPitch": 0, "stolenBases": 0}
    assert approx(actual_hitter_points(bat), 19.0)
    # 6 IP (18 outs), 7 K, 1 ER, 4 H, 1 BB, win:
    # 0.75*18 + 2*7 + 4 - 2*1 - 0.6*4 - 0.6*1 = 13.5+14+4-2-2.4-0.6 = 26.5
    pit = {"inningsPitched": "6.0", "strikeOuts": 7, "earnedRuns": 1, "hits": 4, "baseOnBalls": 1}
    assert approx(actual_pitcher_points(pit, won=True), 26.5)


def _cyclic_consecutive(slots, total=9, n=4):
    s = set(slots)
    return any({((start - 1 + i) % total) + 1 for i in range(n)} == s for start in range(1, total + 1))


def test_consecutive_runs_cyclic():
    from edge.dfs_opt import _consecutive_runs
    team = [{"name": f"h{s}", "team": "AAA", "pos": {"OF"}, "slot": s, "proj": 8.0, "ceiling": 8.0 + s}
            for s in range(1, 10)]
    runs = _consecutive_runs(team, "AAA", 4)
    assert len(runs) == 9                       # cyclic: one run per start slot
    rs = [sorted(p["slot"] for p in r) for r in runs]
    assert [2, 3, 4, 5] in rs and sorted([9, 1, 2, 3]) in rs   # includes a wrap run
    # runs are pre-sorted by ceiling so the top is the highest-slot tail
    assert all(_cyclic_consecutive([p["slot"] for p in r]) for r in runs)


def test_gpp_stack_is_consecutive():
    from edge import dfs_opt
    flex = {"C", "1B", "2B", "3B", "SS", "OF"}
    pool = []
    for s in range(1, 10):                      # stack team, fully position-flexible
        pool.append({"name": f"AAA{s}", "team": "AAA", "pos": set(flex), "salary": 3000,
                     "proj": 7.0 + 0.3 * s, "ceiling": 9.0 + 0.3 * s, "slot": s, "game": "g1"})
    for s in range(1, 10):                       # filler team for the other slots
        pool.append({"name": f"BBB{s}", "team": "BBB", "pos": set(flex), "salary": 2800,
                     "proj": 6.0, "ceiling": 7.0, "slot": s, "game": "g2"})
    for i in range(3):                           # pitchers across 2 games
        pool.append({"name": f"P{i}", "team": f"T{i}", "pos": {"P"}, "salary": 7000,
                     "proj": 15.0, "ceiling": 15.0, "game": f"pg{i % 2}"})
    res = dfs_opt.optimize(pool, mode="gpp", stack_team="AAA", stack_n=4, iters=400)
    stack_slots = [p["slot"] for p, _ in res["lineup"] if p["team"] == "AAA"]
    assert len(stack_slots) >= 4
    assert _cyclic_consecutive(sorted(stack_slots)[:4]) or _cyclic_consecutive(stack_slots)


def _swap_pool():
    # confirmed hitters on team AAA (posted) + BBB replacements (posted), game g1/g2 not locked
    hh = []
    for s in range(1, 10):
        hh.append({"name": f"AAA{s}", "team": "AAA", "pos": {"OF"}, "salary": 4000,
                   "proj": 6.0 + 0.2 * s, "ceiling": 9.0, "own": 5.0, "game": "g1", "confirmed": True})
    for s in range(1, 6):
        hh.append({"name": f"BBB{s}", "team": "BBB", "pos": {"OF"}, "salary": 3500 + 100 * s,
                   "proj": 5.0 + 0.5 * s, "ceiling": 8.0, "own": 3.0, "game": "g2", "confirmed": True})
    return hh


def test_swap_out_suggests_fitting_replacements():
    from edge.dfs_swap import suggest_swaps
    hh = _swap_pool()
    # entered a projected player "Ghost" on AAA who is NOT in the posted AAA order -> OUT
    entered = [{"player": "Ghost", "team": "AAA", "salary": 4000, "pos": "OF", "game": "gX", "conf": "H-slot6*PROJ"}]
    started = {"g1": False, "g2": False, "gX": False}
    recs = suggest_swaps(entered, hh, started, mode="cash", cap=50000, top=3)
    assert len(recs) == 1 and recs[0]["status"] == "out" and not recs[0]["locked"]
    sug = recs[0]["suggestions"]
    assert sug and all(s["salary"] <= recs[0]["max_salary"] for s in sug)
    assert sug[0]["val"] >= sug[-1]["val"]                 # ranked best-first
    assert not any(s["name"] == "Ghost" for s in sug)      # never suggests an entered player


def test_swap_confirmed_and_hold_and_locked():
    from edge.dfs_swap import suggest_swaps
    hh = _swap_pool()
    entered = [
        {"player": "AAA3", "team": "AAA", "salary": 4000, "pos": "OF", "game": "g1", "conf": "H-slot3*PROJ"},  # confirmed in
        {"player": "Zzz",  "team": "CCC", "salary": 4000, "pos": "OF", "game": "g3", "conf": "H-slot4*PROJ"},  # CCC not posted -> hold
        {"player": "Ghost","team": "AAA", "salary": 4000, "pos": "OF", "game": "g1", "conf": "H-slot6*PROJ"},  # OUT but own game locked
    ]
    started = {"g1": True, "g2": False, "g3": False}
    recs = {r["player"]: r for r in suggest_swaps(entered, hh, started, mode="cash")}
    assert recs["AAA3"]["status"] == "confirmed"
    assert recs["Zzz"]["status"] == "hold"
    assert recs["Ghost"]["status"] == "out" and recs["Ghost"]["locked"] and recs["Ghost"]["suggestions"] == []


def test_pinned_entry_round_trip(tmp_path):
    from edge.dfs_swap import save_pinned_entry, load_pinned_entry, entry_path
    rows = [{"player": "AAA1", "team": "AAA", "salary": 4000, "pos": "OF", "game": "g1", "conf": "H-slot1"}]
    assert load_pinned_entry(tmp_path, "2026-07-06", "cash") is None
    p = save_pinned_entry(tmp_path, "2026-07-06", "cash", rows)
    assert p == entry_path(tmp_path, "2026-07-06", "cash") and p.exists()
    loaded = load_pinned_entry(tmp_path, "2026-07-06", "cash")
    assert loaded and loaded[0]["player"] == "AAA1" and loaded[0]["salary"] == "4000"
    # a different mode/date is untouched
    assert load_pinned_entry(tmp_path, "2026-07-06", "gpp") is None
    assert load_pinned_entry(tmp_path, "2026-07-07", "cash") is None


def test_log_forward_test_writes_proj_log_and_lineups(tmp_path):
    from edge.dfs_run import log_forward_test
    pool = [{"name": "AAA1", "team": "AAA", "pos": {"OF"}, "salary": 4000, "proj": 8.0,
            "ceiling": 12.0, "own": 5.0, "conf": "H-slot1"}]
    cash = {"lineup": [(pool[0], "OF")], "proj": 8.0, "ceil": 12.0, "salary": 4000}
    logged = log_forward_test(tmp_path, "2026-07-06", True, 123, pool, cash, None)
    assert logged["logged_projections"] and logged["n"] == 1
    plog = tmp_path / "data/dfs_proj_log.csv"
    assert plog.exists() and "AAA1" in plog.read_text()
    lf = tmp_path / logged["lineup_file"]
    assert lf.exists() and "cash" in lf.read_text() and "AAA1" in lf.read_text()
    # re-running for a later date preserves the earlier date's row
    log_forward_test(tmp_path, "2026-07-07", True, 123, [], None, None)
    assert "2026-07-06" in plog.read_text() and "AAA1" in plog.read_text()
    # a sub-slate (is_main=False) must not touch the main proj log
    before = plog.read_text()
    log_forward_test(tmp_path, "2026-07-08", False, 999, pool, None, None)
    assert plog.read_text() == before


def test_lineups_for_date_doubleheader_prefers_unfinished_game(monkeypatch):
    # Regression: a finished game 1 of a doubleheader must not leak its
    # (correctly) confirmed lineup into a still-upcoming game 2 for the same
    # team/date — game 2 can start someone completely different (e.g. a rested
    # catcher). Caught live 2026-07-07: Brewers/Cardinals DH had William
    # Contreras (game 1, Final) wrongly shown as game 2's confirmed catcher
    # instead of Gary Sanchez (game 2's actual starter).
    from edge import dfs

    def game(pk, state, hour, away_starter):
        filler = [{"id": 900 + i, "fullName": f"Filler{i}"} for i in range(8)]
        return {
            "gamePk": pk, "status": {"abstractGameState": state},
            "gameDate": f"2026-07-07T{hour}:00:00Z",
            "teams": {"home": {"team": {"id": 100}, "probablePitcher": {"id": 1}},
                     "away": {"team": {"id": 200}, "probablePitcher": {"id": 2}}},
            "lineups": {"awayPlayers": [{"id": 10, "fullName": away_starter}] + filler,
                       "homePlayers": [{"id": 300 + i, "fullName": f"Home{i}"} for i in range(9)]},
        }

    fake_schedule = {"dates": [{"date": "2026-07-07", "games": [
        game(1001, "Final", 18, "William Contreras"),
        game(1002, "Preview", 23, "Gary Sanchez"),
    ]}]}
    monkeypatch.setattr(dfs, "_get", lambda url: fake_schedule)

    out = dfs.lineups_for_date("2026-07-07", project=False)
    assert dfs.norm("gary sanchez") in out and out[dfs.norm("gary sanchez")]["game"] == 1002
    assert dfs.norm("william contreras") not in out


def test_optimizer_never_rosters_pitcher_against_own_hitters():
    # Regression: caught live 2026-07-08 — a build stacked White Sox hitters
    # while also rostering the opposing Boston starter, i.e. betting the Red
    # Sox pitcher dominates AND that the White Sox hitters he faces do well.
    # NOTE the match field is "opp_team" (a team abbreviation), not "game":
    # pitcher pool entries carry a DK/Odds-API game id and hitter entries carry
    # a statsapi gamePk, two id spaces that never coincide even for the same
    # real matchup, so "game" can't detect this (an earlier version of this
    # test used matching "game" strings and passed without exercising the real
    # bug at all — this version mirrors the real pool shape from
    # edge/dfs_run.py, where only team/opp_team line up across the two sources).
    from edge import dfs_opt
    flex = {"C", "1B", "2B", "3B", "SS", "OF"}
    pool = []
    for s in range(1, 10):                       # stack team CWS, statsapi game g1 vs BOS
        pool.append({"name": f"CWS{s}", "team": "CWS", "opp_team": "BOS", "pos": set(flex), "salary": 3000,
                     "proj": 7.0 + 0.3 * s, "ceiling": 9.0 + 0.3 * s, "slot": s, "game": "g1"})
    for s in range(1, 10):                       # filler team, different statsapi game
        pool.append({"name": f"BBB{s}", "team": "BBB", "opp_team": "CCC", "pos": set(flex), "salary": 2800,
                     "proj": 6.0, "ceiling": 7.0, "slot": s, "game": "g2"})
    # BOS pitcher's own pool "game" is a DK/Odds-API id (never equal to a statsapi
    # gamePk), and he's projected far better than any alternative, so he'd be
    # picked if the opposing-matchup constraint weren't enforced correctly.
    pool.append({"name": "BOS_ace", "team": "BOS", "opp_team": "CWS", "pos": {"P"}, "salary": 9000,
                "proj": 30.0, "game": "dk-competition-1"})
    pool.append({"name": "P_other1", "team": "T1", "opp_team": "T2", "pos": {"P"}, "salary": 7000,
                "proj": 15.0, "game": "dk-competition-2"})
    pool.append({"name": "P_other2", "team": "T2", "opp_team": "T1", "pos": {"P"}, "salary": 7000,
                "proj": 14.0, "game": "dk-competition-3"})

    res = dfs_opt.optimize(pool, mode="gpp", stack_team="CWS", stack_n=4, iters=400)
    assert res is not None
    names = {p["name"] for p, _ in res["lineup"]}
    assert "BOS_ace" not in names, "rostered the opposing team's pitcher against its own stacked hitters"
    stack_team_hitters = {p["name"] for p, _ in res["lineup"] if p.get("team") == "CWS"}
    assert len(stack_team_hitters) >= 4     # the stack itself is still built


def test_bullpen_k9_excludes_starter_and_starters():
    from edge.dfs import bullpen_k9, LG_K9
    stats = [
        {"pid": 1, "ip": 150.0, "k": 150, "gs": 28, "g": 28},   # a starter -> excluded (gs/g >= 0.5)
        {"pid": 2, "ip": 60.0, "k": 72, "gs": 0, "g": 60},      # reliever, high K9 (=10.8)
        {"pid": 3, "ip": 50.0, "k": 40, "gs": 0, "g": 55},      # reliever, lower K9 (=7.2)
        {"pid": 4, "ip": 5.0, "k": 8, "gs": 0, "g": 5},         # too few IP (<15) -> excluded
    ]
    k9 = bullpen_k9(stats, exclude_pid=1)
    # pooled from pid 2+3 only: (72+40) K over (60+50) IP -> 9*112/110
    assert approx(k9, 9 * 112 / 110)


def test_bullpen_k9_empty_or_all_excluded_falls_back_to_league_average():
    from edge.dfs import bullpen_k9, LG_K9
    assert bullpen_k9([], exclude_pid=None) == LG_K9
    stats = [{"pid": 1, "ip": 100.0, "k": 100, "gs": 20, "g": 20}]  # only a starter, no relievers
    assert bullpen_k9(stats, exclude_pid=None) == LG_K9


def test_pitcher_k9_pools_multiple_seasons(monkeypatch, tmp_path):
    from edge import dfs

    def fake_get(url):
        season = url.split("season=")[1].split("&")[0]
        ip_by_season = {"2024": "100.0", "2025": "50.0"}
        return {"stats": [{"splits": [{"player": {"id": 999},
                "stat": {"inningsPitched": ip_by_season[season], "strikeOuts": 100 if season == "2024" else 60}}]}]}

    monkeypatch.setattr(dfs, "_get", fake_get)
    out = dfs.pitcher_k9((2024, 2025))
    # pooled: (100+60) K over (100+50) IP -> 9*160/150
    assert approx(out["999"], 9 * 160 / 150)


def test_pitcher_k9_single_season_int_still_works(monkeypatch):
    from edge import dfs

    monkeypatch.setattr(dfs, "_get", lambda url: {"stats": [{"splits": [
        {"player": {"id": 1}, "stat": {"inningsPitched": "60.0", "strikeOuts": 60}}]}]})
    out = dfs.pitcher_k9(2024)
    assert approx(out["1"], 9.0)


def test_pooled_skill_rates_max_age_forces_refresh(tmp_path, monkeypatch):
    import json
    import time
    from edge import dfs

    cache_path = tmp_path / "skill.json"
    cache_path.write_text(json.dumps({"rates": {"1": 1.0}, "lg": 1.5}))
    old_time = time.time() - 1000
    import os
    os.utime(cache_path, (old_time, old_time))

    called = []

    def fake_get(url):
        called.append(url)
        return {"stats": [{"splits": []}]}

    monkeypatch.setattr(dfs, "_get", fake_get)
    # max_age shorter than the cache's actual age -> must refetch, not read stale cache
    dfs.pooled_skill_rates((2024,), cache_path=str(cache_path), max_age=10)
    assert called, "stale cache (past max_age) should have triggered a refetch"

    called.clear()
    os.utime(cache_path, (time.time(), time.time()))
    dfs.pooled_skill_rates((2024,), cache_path=str(cache_path), max_age=10000)
    assert not called, "fresh cache (within max_age) should be reused, not refetched"
