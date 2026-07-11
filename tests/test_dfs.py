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


def test_max_five_hitters_per_team_enforced():
    # DK MLB rule: max 5 hitters from one team (pitchers don't count). Built to
    # FAIL pre-fix: team AAA's hitters are strictly better than everyone else's,
    # so an unconstrained optimizer rosters 8 of them.
    from edge import dfs_opt
    flex = {"C", "1B", "2B", "3B", "SS", "OF"}
    pool = []
    for s in range(1, 10):
        pool.append({"name": f"AAA{s}", "team": "AAA", "pos": set(flex), "salary": 3000,
                     "proj": 20.0, "ceiling": 22.0, "slot": s, "game": "g1"})
    for s in range(1, 10):
        pool.append({"name": f"BBB{s}", "team": "BBB", "pos": set(flex), "salary": 2800,
                     "proj": 5.0, "ceiling": 6.0, "slot": s, "game": "g2"})
    for i in range(3):
        pool.append({"name": f"P{i}", "team": f"T{i}", "pos": {"P"}, "salary": 7000,
                     "proj": 15.0, "ceiling": 15.0, "game": f"pg{i % 2}"})
    for mode in ("cash", "gpp"):
        res = dfs_opt.optimize(pool, mode=mode, stack_team="AAA" if mode == "gpp" else None, iters=400)
        assert res is not None
        n_aaa = sum(1 for p, _ in res["lineup"] if p["team"] == "AAA" and "P" not in p["pos"])
        assert n_aaa <= dfs_opt.MAX_HITTERS_PER_TEAM, f"{mode}: {n_aaa} hitters from one team"


def test_gpp_secondary_stack():
    # 5-3 double stack: primary consecutive 5 from AAA + >=2 correlated hitters
    # from the secondary team BBB, never breaking the max-5 rule.
    from edge import dfs_opt
    flex = {"C", "1B", "2B", "3B", "SS", "OF"}
    pool = []
    for s in range(1, 10):
        pool.append({"name": f"AAA{s}", "team": "AAA", "pos": set(flex), "salary": 3000,
                     "proj": 7.0 + 0.3 * s, "ceiling": 9.0 + 0.3 * s, "slot": s, "game": "g1"})
    for s in range(1, 10):
        pool.append({"name": f"BBB{s}", "team": "BBB", "pos": set(flex), "salary": 2800,
                     "proj": 6.0, "ceiling": 7.0, "slot": s, "game": "g2"})
    for s in range(1, 10):
        pool.append({"name": f"CCC{s}", "team": "CCC", "pos": set(flex), "salary": 2700,
                     "proj": 5.0, "ceiling": 6.0, "slot": s, "game": "g3"})
    for i in range(3):
        pool.append({"name": f"P{i}", "team": f"T{i}", "pos": {"P"}, "salary": 7000,
                     "proj": 15.0, "ceiling": 15.0, "game": f"pg{i % 2}"})
    res = dfs_opt.optimize(pool, mode="gpp", stack_team="AAA", stack_n=5,
                           stack2_team="BBB", stack2_n=3, iters=400)
    assert res is not None
    n_aaa = sum(1 for p, _ in res["lineup"] if p["team"] == "AAA")
    n_bbb = sum(1 for p, _ in res["lineup"] if p["team"] == "BBB")
    assert n_aaa == 5, f"primary stack size {n_aaa}"
    assert n_bbb >= 2, f"secondary stack size {n_bbb}"


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


def test_pooled_skill_rates_shrinkage(monkeypatch):
    # shrink_k: every player gets an EB-shrunk rate instead of the min_pa
    # cutoff -- a 40-PA player lands most of the way toward league average,
    # a high-PA player keeps nearly his raw rate, and nobody is dropped.
    from edge import dfs

    def fake_get(url):
        return {"stats": [{"splits": [
            {"player": {"id": 1}, "stat": {"plateAppearances": 600, "hits": 180, "doubles": 30,
                                           "triples": 2, "homeRuns": 30, "rbi": 100, "runs": 100,
                                           "baseOnBalls": 60, "hitByPitch": 5, "stolenBases": 10}},
            {"player": {"id": 2}, "stat": {"plateAppearances": 40, "hits": 20, "doubles": 10,
                                           "triples": 0, "homeRuns": 5, "rbi": 15, "runs": 15,
                                           "baseOnBalls": 5, "hitByPitch": 0, "stolenBases": 0}},
        ]}]}

    monkeypatch.setattr(dfs, "_get", fake_get)
    rates_cut, lg = dfs.pooled_skill_rates((2025,), min_pa=120)
    assert "2" not in rates_cut                      # cutoff behavior: small sample dropped
    rates_eb, lg2 = dfs.pooled_skill_rates((2025,), shrink_k=60)
    assert "2" in rates_eb                           # shrinkage keeps everyone
    raw2 = dfs.actual_hitter_points(fake_get("")["stats"][0]["splits"][1]["stat"]) / 40
    assert lg2 < rates_eb["2"] < raw2                # shrunk BETWEEN league avg and raw
    # high-PA player barely moves
    raw1 = dfs.actual_hitter_points(fake_get("")["stats"][0]["splits"][0]["stat"]) / 600
    assert abs(rates_eb["1"] - raw1) < abs(rates_eb["2"] - raw2)


def test_project_hitter_skill_home_away_and_era():
    from edge import dfs
    # away lineups get more PA than home at every slot -> higher projection
    p_home = dfs.project_hitter_skill(2.0, 1, home=True)
    p_away = dfs.project_hitter_skill(2.0, 1, home=False)
    p_legacy = dfs.project_hitter_skill(2.0, 1)
    assert p_away > p_home
    assert p_legacy == round(2.0 * dfs.SLOT_PA[1], 1)   # back-compat: None keeps flat table
    # opposing starter's ERA: facing a bad (high-ERA) pitcher raises the projection
    p_bad = dfs.project_hitter_skill(2.0, 1, opp_era=6.0)
    p_good = dfs.project_hitter_skill(2.0, 1, opp_era=2.5)
    assert p_bad > p_legacy > p_good
    # clamped
    assert dfs.project_hitter_skill(2.0, 1, opp_era=99.0) <= round(2.0 * dfs.SLOT_PA[1] * 1.15, 1)


def test_actual_pitcher_points_hbp_and_cg():
    from edge.dfs import actual_pitcher_points
    base = {"inningsPitched": "9.0", "strikeOuts": 10, "earnedRuns": 0, "hits": 3, "baseOnBalls": 1}
    # DK: -0.6 per hit batsman; +2.5 CG and +2.5 CG-shutout stack
    no_extras = actual_pitcher_points(dict(base))
    hbp = actual_pitcher_points(dict(base, hitBatsmen=2))
    assert approx(no_extras - hbp, 1.2)
    cgso = actual_pitcher_points(dict(base, completeGames=1, shutouts=1))
    assert approx(cgso - no_extras, 5.0)


def test_date_all_final_rejects_midslate(monkeypatch):
    # Regression for the 2026-07-08 stale-actuals-cache bug: a cache written
    # while games were still in progress served a 25-player stub forever.
    # date_all_final must be False while any game is unfinished.
    import scripts.dfs_grade as grade
    from edge import dfs

    def fake_get(url):
        return {"dates": [{"games": [
            {"gameType": "R", "status": {"abstractGameState": "Final"}},
            {"gameType": "R", "status": {"abstractGameState": "Live"}},
        ]}]}
    monkeypatch.setattr(dfs, "_get", fake_get)
    assert grade.date_all_final("2026-07-08") is False

    def fake_get_done(url):
        return {"dates": [{"games": [
            {"gameType": "R", "status": {"abstractGameState": "Final"}},
            {"gameType": "A", "status": {"abstractGameState": "Preview"}},  # All-Star game ignored
        ]}]}
    monkeypatch.setattr(dfs, "_get", fake_get_done)
    assert grade.date_all_final("2026-07-08") is True

    monkeypatch.setattr(dfs, "_get", lambda url: {"dates": []})
    assert grade.date_all_final("2026-11-01") is False  # no games at all != complete


def test_inactive_players_flags_40man_not_active(monkeypatch):
    from edge import dfs

    def fake_get(url):
        if "rosterType=active" in url:
            return {"roster": [{"person": {"id": 1, "fullName": "Active Guy"}}]}
        return {"roster": [
            {"person": {"id": 1, "fullName": "Active Guy"}},
            {"person": {"id": 2, "fullName": "Hurt Guy"}},
        ]}

    monkeypatch.setattr(dfs, "_get", fake_get)
    out = dfs.inactive_players(118)
    assert out == {dfs.norm("Hurt Guy")}


def test_inactive_players_ignores_stale_status_field(monkeypatch):
    # Regression: MLB's own roster `status` text field can say "Active" for
    # a player who is genuinely not on the active roster -- membership in
    # the active-roster id set (not the status field) is the source of truth.
    from edge import dfs

    def fake_get(url):
        if "rosterType=active" in url:
            return {"roster": []}
        return {"roster": [{"person": {"id": 5, "fullName": "Stale Status Guy"},
                            "status": {"code": "A", "description": "Active"}}]}

    monkeypatch.setattr(dfs, "_get", fake_get)
    out = dfs.inactive_players(118)
    assert dfs.norm("Stale Status Guy") in out


def test_inactive_players_cache_respects_max_age(tmp_path, monkeypatch):
    import json
    import os
    import time
    from edge import dfs

    cache_path = tmp_path / "inactive.json"
    cache_path.write_text(json.dumps(["stale name"]))
    old_time = time.time() - 1000
    os.utime(cache_path, (old_time, old_time))

    called = []

    def fake_get(url):
        called.append(url)
        return {"roster": []}

    monkeypatch.setattr(dfs, "_get", fake_get)
    dfs.inactive_players(118, cache_path=str(cache_path), max_age=10)
    assert called, "stale cache (past max_age) should have triggered a refetch"

    called.clear()
    os.utime(cache_path, (time.time(), time.time()))
    out = dfs.inactive_players(118, cache_path=str(cache_path), max_age=10000)
    assert not called, "fresh cache (within max_age) should be reused, not refetched"
    assert out == set()  # first call's refetch already overwrote the cache with a fresh (empty) result


def test_inactive_players_survives_api_failure(monkeypatch):
    from edge import dfs

    def fake_get(url):
        raise Exception("network error")

    monkeypatch.setattr(dfs, "_get", fake_get)
    assert dfs.inactive_players(118) == set()


def test_validate_pearson_spearman_agree_on_linear_data():
    from edge.dfs_validate import pearson, spearman
    xs = [1, 2, 3, 4, 5]
    ys = [2, 4, 6, 8, 10]
    assert approx(pearson(xs, ys), 1.0, tol=0.01)
    assert approx(spearman(xs, ys), 1.0, tol=0.01)


def test_validate_spearman_robust_to_outlier_pearson_is_not():
    from edge.dfs_validate import pearson, spearman
    # a monotonic relationship with one huge outlier that Pearson overweights
    xs = [1, 2, 3, 4, 5]
    ys = [1, 2, 3, 4, 100]
    # still perfectly monotonic -> spearman is exactly 1.0 regardless of the outlier's size
    assert approx(spearman(xs, ys), 1.0, tol=0.01)


def test_validate_fisher_ci_widens_with_smaller_n():
    from edge.dfs_validate import fisher_ci
    lo_big, hi_big = fisher_ci(0.5, 1000)
    lo_small, hi_small = fisher_ci(0.5, 20)
    assert (hi_small - lo_small) > (hi_big - lo_big)


def test_validate_cross_slate_summary_separates_pooled_from_per_slate():
    from edge.dfs_validate import cross_slate_summary
    # two slates, each with a real relationship, but different slopes/noise
    rows = []
    for i in range(20):
        rows.append({"date": "d1", "x": i, "y": i + 1})
    for i in range(20):
        rows.append({"date": "d2", "x": i, "y": -i})   # inverted relationship
    out = cross_slate_summary(rows, "date", "x", "y")
    assert out["n_slates"] == 2 and out["n_rows"] == 40
    assert out["per_slate"]["d1"]["corr"] > 0.9
    assert out["per_slate"]["d2"]["corr"] < -0.9
    # pooled masks the per-slate signal since slopes cancel
    assert abs(out["pooled_corr"]) < 0.5


def test_validate_incremental_baseline_test_detects_real_signal():
    from edge.dfs_validate import incremental_baseline_test
    import random
    rng = random.Random(0)
    baseline = [rng.uniform(0, 10) for _ in range(200)]
    model = [rng.uniform(0, 10) for _ in range(200)]   # independent of baseline
    # y depends on BOTH baseline and model with real coefficients
    y = [2 * b + 3 * m + rng.gauss(0, 0.5) for b, m in zip(baseline, model)]
    out = incremental_baseline_test(y, baseline, model)
    assert out["significant_at_5pct"]
    assert out["incremental_r2"] > 0.1


def test_validate_incremental_baseline_test_detects_no_signal():
    from edge.dfs_validate import incremental_baseline_test
    import random
    rng = random.Random(0)
    baseline = [rng.uniform(0, 10) for _ in range(200)]
    model = [rng.uniform(0, 10) for _ in range(200)]   # pure noise, no relationship to y
    y = [2 * b + rng.gauss(0, 0.5) for b in baseline]   # y depends only on baseline
    out = incremental_baseline_test(y, baseline, model)
    assert not out["significant_at_5pct"]
    assert out["incremental_r2"] < 0.02


def test_project_ownership_defaults_match_swept_values():
    # Regression: guards the gamma defaults against silent drift back to the
    # pre-fix values (gamma=3.5, pitcher_gamma=6.0), which an external review
    # + out-of-sample sweep (scripts/dfs_ownership_gamma_sweep.py) found were
    # measurably worse on real contest ownership -- see project_ownership's
    # docstring/comment for the numbers.
    import inspect
    from edge.dfs import project_ownership
    params = inspect.signature(project_ownership).parameters
    assert params["gamma"].default == 1.5
    assert params["pitcher_gamma"].default == 7.0


def test_project_ownership_normalizes_within_position():
    # salaries/proj kept away from the punt-chalk (<=3500) and stud (>=9000)
    # salary-tier bonuses so nobody hits the 65% chalk cap and clips the sum.
    from edge.dfs import project_ownership, _OWN_SLOTS
    pool = [{"proj": 6.0 + i * 0.3, "salary": 4000 + i * 300, "pos": {"OF"}} for i in range(5)]
    project_ownership(pool, team_proj=None)
    total = sum(p["own"] for p in pool)
    assert approx(total, _OWN_SLOTS["OF"] * 100, tol=1.0)  # 3 OF slots -> sums to ~300%


def test_project_ownership_pitchers_concentrate_more_than_hitters():
    # pitcher_gamma > gamma: the SAME shape of value spread should produce a
    # more concentrated (higher-variance-of-ownership) distribution for
    # pitchers than for hitters, since the field jams the top 1-2 arms harder.
    from edge.dfs import project_ownership
    def make_pool(pos, n_slots_key):
        return [{"proj": 5.0 + i * 2, "salary": 3000 + i * 800, "pos": {pos}} for i in range(6)]

    pitchers = make_pool("P", "P")
    hitters = make_pool("OF", "OF")
    project_ownership(pitchers, team_proj=None)
    project_ownership(hitters, team_proj=None)
    # same underlying value spread -> pitcher ownership should spread out MORE
    # (top pitcher owns a much bigger share than top OF, proportionally)
    pit_top_share = max(p["own"] for p in pitchers) / sum(p["own"] for p in pitchers)
    hit_top_share = max(p["own"] for p in hitters) / sum(p["own"] for p in hitters)
    assert pit_top_share > hit_top_share


def test_fetch_draftables_handles_dash_fppg(monkeypatch):
    # Regression: DK returns the literal string "-" for dk_fppg (id 408) on
    # players with no game history yet (rookies/callups) -- float("-") crashed
    # the entire draftables pull, not just that one player's row.
    from edge import dfs

    def fake_get(url):
        return {"draftables": [
            {"displayName": "Rookie Guy", "salary": 3000, "position": "OF", "teamAbbreviation": "AAA",
             "competition": {}, "draftStatAttributes": [{"id": 408, "value": "-"}]},
            {"displayName": "Vet Guy", "salary": 5000, "position": "OF", "teamAbbreviation": "AAA",
             "competition": {}, "draftStatAttributes": [{"id": 408, "value": "12.3"}]},
        ]}

    monkeypatch.setattr(dfs, "_get", fake_get)
    out = dfs.fetch_draftables(123)
    assert out[dfs.norm("Rookie Guy")]["dk_fppg"] is None
    assert out[dfs.norm("Vet Guy")]["dk_fppg"] == 12.3


def _floor_test_pool(low_floor_ceiling=8.0, low_floor_floor=8.0):
    # LowFloor/HighFloor are BOTH the only two "C" (catcher)-eligible players
    # in the pool -- only 1 catcher slot exists, so the optimizer is forced to
    # pick exactly one of them, never both and never neither. Every other
    # slot is filled by a clearly-lower-value, non-catcher-eligible filler so
    # the C decision is the only thing in play.
    flex = {"1B", "2B", "3B", "SS", "OF"}
    pool = [
        {"name": "LowFloor", "team": "AAA", "pos": {"C"}, "salary": 4000,
         "proj": 8.0, "ceiling": low_floor_ceiling, "floor": low_floor_floor, "game": "g1"},
        {"name": "HighFloor", "team": "AAA", "pos": {"C"}, "salary": 4000,
         "proj": 8.0, "ceiling": 8.0, "floor": 8.8, "game": "g1"},
    ]
    for s in range(7):  # fills 1B/2B/3B/SS/OF/OF/OF -- exactly the 7 non-catcher hitter slots
        # spread fillers over 2 teams: DK's max-5-hitters-per-team rule (now
        # enforced by the optimizer) makes a 7-hitter single-team pool illegal
        pool.append({"name": f"F{s}", "team": "BBB" if s % 2 else "CCC", "pos": set(flex), "salary": 3000,
                     "proj": 5.0, "ceiling": 5.0, "floor": 5.0, "game": "g2"})
    for i in range(2):
        pool.append({"name": f"P{i}", "team": f"T{i}", "pos": {"P"}, "salary": 7000,
                     "proj": 15.0, "ceiling": 15.0, "floor": 15.0, "game": f"pg{i}"})
    return pool


def test_cash_mode_prefers_floor_over_raw_proj_on_a_tie():
    # Regression: cash mode's optimizer objective is "floor" (proj nudged by
    # walk-rate consistency signal), not raw "proj" -- when two players have
    # identical proj but different floor, cash should pick the higher-floor one.
    from edge import dfs_opt
    pool = _floor_test_pool()  # both proj=8.0, HighFloor has the better floor (8.8 vs 8.0)
    res = dfs_opt.optimize(pool, mode="cash", iters=400)
    names = {p["name"] for p, _ in res["lineup"]}
    assert "HighFloor" in names and "LowFloor" not in names


def test_gpp_mode_unaffected_by_floor_differences():
    # GPP's objective is "lev" (ceiling faded by ownership) -- floor must not
    # influence GPP selection at all.
    from edge import dfs_opt
    # LowFloor has a strictly higher ceiling despite a lower floor -- GPP should take it
    pool = _floor_test_pool(low_floor_ceiling=9.0, low_floor_floor=6.0)
    res = dfs_opt.optimize(pool, mode="gpp", iters=400)
    names = {p["name"] for p, _ in res["lineup"]}
    assert "LowFloor" in names and "HighFloor" not in names


def test_bb_floor_weight_constant_is_documented_and_modest():
    # BB_FLOOR_WEIGHT should stay a deliberately small nudge -- guards against
    # someone cranking it up without re-validating on a bigger sample (see the
    # comment in edge/dfs.py for the n=60 validation this rests on).
    from edge.dfs import BB_FLOOR_WEIGHT
    assert 0 < BB_FLOOR_WEIGHT <= 5.0


def test_team_abbrev_map_normalizes_arizona(monkeypatch):
    # Regression: statsapi says "AZ" for Arizona, DK's draftables say "ARI".
    # team_abbrev_map() feeds hitters' pool "team" field while pitchers get
    # DK's abbreviation directly -- without normalizing, Arizona hitters and
    # Arizona's own pitcher never matched on team string, silently breaking
    # the pitcher-vs-own-hitters constraint for this one team.
    from edge import dfs_run, dfs

    monkeypatch.setattr(dfs, "_get", lambda url: {"teams": [
        {"id": 109, "abbreviation": "AZ"}, {"id": 111, "abbreviation": "BOS"}]})
    out = dfs_run.team_abbrev_map()
    assert out["109"] == "ARI"   # normalized to DK's spelling
    assert out["111"] == "BOS"   # unaffected teams pass through unchanged


_FAKE_TEAMS_ENDPOINT = {"teams": [
    {"id": 109, "abbreviation": "AZ"}, {"id": 111, "abbreviation": "BOS"},
    {"id": 121, "abbreviation": "NYM"}, {"id": 144, "abbreviation": "ATL"},
]}


def _fake_schedule_and_teams(schedule_response):
    """Real /schedule responses carry only team id (no abbreviation) --
    confirmed live 2026-07-10, every team, every game. team_game_status now
    resolves id -> abbreviation via a SEPARATE /teams call, so tests must
    mock both endpoints, keyed by team id like the real payload."""
    def fake_get(url):
        return _FAKE_TEAMS_ENDPOINT if "/teams?" in url else schedule_response
    return fake_get


def test_team_game_status_flags_postponed_not_normal_states(monkeypatch):
    from edge import dfs

    fake_get = _fake_schedule_and_teams({"dates": [{"games": [
        {"status": {"detailedState": "Postponed"},
         "teams": {"home": {"team": {"id": 109}}, "away": {"team": {"id": 111}}}},
        {"status": {"detailedState": "Final"},
         "teams": {"home": {"team": {"id": 121}}, "away": {"team": {"id": 144}}}},
    ]}]})

    monkeypatch.setattr(dfs, "_get", fake_get)
    out = dfs.team_game_status("2026-07-09")
    assert out["ARI"] == "Postponed" and out["BOS"] == "Postponed"  # normalized + flagged
    assert out["NYM"] == "" and out["ATL"] == ""                    # normal, unflagged


def test_team_game_status_survives_malformed_game_entry(monkeypatch):
    # Regression: a game entry missing "teams" (or home/away/team within it)
    # used to raise an uncaught KeyError from direct dict indexing, crashing
    # the whole function instead of just skipping that one game.
    from edge import dfs

    fake_get = _fake_schedule_and_teams({"dates": [{"games": [
        {"status": {"detailedState": "Final"}},  # missing "teams" entirely
        {"status": {"detailedState": "Final"}, "teams": {"home": {}}},  # missing "team" under home
        {"status": {"detailedState": "Final"},
         "teams": {"home": {"team": {"id": 121}}, "away": {"team": {"id": 144}}}},
    ]}]})

    monkeypatch.setattr(dfs, "_get", fake_get)
    out = dfs.team_game_status("2026-07-09")  # must not raise
    assert out["NYM"] == "" and out["ATL"] == ""


def test_team_game_status_skips_non_regular_season_games(monkeypatch):
    # Regression: confirmed real 2026-07-14 -- an All-Star/exhibition entry
    # (gameType != "R") uses "AL"/"NL" pseudo-teams instead of real ones and
    # should never show up in this dict.
    from edge import dfs

    fake_get = _fake_schedule_and_teams({"dates": [{"games": [
        {"gameType": "A", "status": {"detailedState": "Final"},
         "teams": {"home": {"team": {"id": 9001}}, "away": {"team": {"id": 9002}}}},  # AL/NL, not real teams
        {"gameType": "R", "status": {"detailedState": "Final"},
         "teams": {"home": {"team": {"id": 121}}, "away": {"team": {"id": 144}}}},
    ]}]})

    monkeypatch.setattr(dfs, "_get", fake_get)
    out = dfs.team_game_status("2026-07-14")
    assert 9001 not in out and 9002 not in out
    assert out["NYM"] == "" and out["ATL"] == ""


def test_team_game_status_missing_abbreviation_in_schedule_payload(monkeypatch):
    # Regression: confirmed live 2026-07-10 -- the plain /schedule response's
    # embedded team objects carry ONLY id/name/link, never "abbreviation".
    # Extracting abbreviation straight from the schedule payload (even
    # defensively) silently returned "" for every team, every time -- this
    # function had never actually worked. Must resolve by id instead.
    from edge import dfs

    fake_get = _fake_schedule_and_teams({"dates": [{"games": [
        {"status": {"detailedState": "Final"}, "teams": {
            "home": {"team": {"id": 121, "name": "New York Mets", "link": "/api/v1/teams/121"}},
            "away": {"team": {"id": 144, "name": "Atlanta Braves", "link": "/api/v1/teams/144"}}}},
    ]}]})

    monkeypatch.setattr(dfs, "_get", fake_get)
    out = dfs.team_game_status("2026-07-10")
    assert out == {"NYM": "", "ATL": ""}


def test_lineups_for_date_skips_non_regular_season_games(monkeypatch):
    from edge import dfs

    def fake_get(url):
        return {"dates": [{"games": [
            {"gamePk": 1, "gameType": "A", "status": {"abstractGameState": "Final"},
             "teams": {"home": {"team": {"id": 999}}, "away": {"team": {"id": 998}}}, "lineups": {}},
        ]}]}

    monkeypatch.setattr(dfs, "_get", fake_get)
    out = dfs.lineups_for_date("2026-07-14", project=False)
    assert out == {}
