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
