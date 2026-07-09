#!/usr/bin/env python3
"""Leak-free backtest: does including current-season data, platoons, and
bullpen quality reduce hitter MAE vs the production model's inputs?

Design (documented so the proxy is honest, not hidden): production today uses
pooled_skill_rates((2024,2025)) and pitcher_k9(2025) while the real season in
progress is 2026 -- i.e. it's frozen on the two seasons BEFORE the current one
and never sees the current season at all. To test "does including the current
season help" without waiting for 2026 to finish (can't validate the future),
this backtest recreates the exact same shape one year earlier: baseline uses
(2023,2024) skill + 2024 pitcher K9 (the two seasons before 2025), tested on
REAL 2025 games, with a "+2025-to-date" variant built from per-player GAME LOGS
filtered to strictly before each test date (no lookahead -- a player's own
game that day is never in their own feature).

Everything here is FREE (statsapi only). Slow (many small calls, cached to
disk), meant to run once and be read, not re-run casually.

Usage: python3 scripts/dfs_hitter_backtest.py 2025-06-01 2025-06-21
"""
import sys
import json
import time
import statistics
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs  # noqa: E402

CACHE = ROOT / "data/backtest_cache.json"
UMP_FILE = ROOT / "data/umpire_games.json"


def load_cache():
    return json.loads(CACHE.read_text()) if CACHE.exists() else {}


def save_cache(c):
    CACHE.write_text(json.dumps(c))


def load_umpire_factors(shrink_k=50):
    """gamePk -> shrunk K-rate multiplier vs league average (1.0 = neutral).
    Empirical-Bayes shrinkage toward 1.0 by sample size, since most umpires only
    have ~20 games in our collected window -- an unshrunk per-umpire rate would
    be mostly noise for the low-n ones."""
    if not UMP_FILE.exists():
        return {}
    rows = json.loads(UMP_FILE.read_text())
    by_ump = defaultdict(lambda: {"k": 0, "pa": 0, "n": 0, "games": []})
    for r in rows:
        u = by_ump[r["ump_name"]]
        u["k"] += r["k"]; u["pa"] += r["pa"]; u["n"] += 1; u["games"].append(r["game"])
    lg_rate = sum(u["k"] for u in by_ump.values()) / sum(u["pa"] for u in by_ump.values())
    game_factor = {}
    for u in by_ump.values():
        raw_rate = u["k"] / u["pa"] if u["pa"] else lg_rate
        shrunk = lg_rate + (u["n"] / (u["n"] + shrink_k)) * (raw_rate - lg_rate)
        mult = shrunk / lg_rate
        for pk in u["games"]:
            game_factor[pk] = mult
    return game_factor


def game_pks_for_range(start, end):
    sched = dfs._get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate={start}&endDate={end}")
    out = []
    for d in sched.get("dates", []):
        date = d["date"]
        for g in d.get("games", []):
            if g.get("status", {}).get("abstractGameState") == "Final" and g.get("gameType") == "R":
                out.append((date, g["gamePk"]))
    return out


def game_data(pk):
    """-> {home:{team_id, starter_id, lineup:[(pid,name,slot)]}, away:{...}} + actuals {pid: dk_pts}."""
    box = dfs._get(f"https://statsapi.mlb.com/api/v1/game/{pk}/boxscore")
    sides = {}
    actuals = {}
    for side in ("home", "away"):
        t = box["teams"][side]
        starter = None
        lineup = []
        for pid, pl in t["players"].items():
            person = pl["person"]
            bt = pl.get("stats", {}).get("batting", {})
            pit = pl.get("stats", {}).get("pitching", {})
            if pit and pit.get("gamesStarted"):
                starter = person["id"]
            bo = pl.get("battingOrder")
            if bo and int(bo) % 100 == 0:
                lineup.append((person["id"], person["fullName"], int(bo) // 100))
            if bt.get("plateAppearances"):
                actuals[person["id"]] = dfs.actual_hitter_points(bt)
        sides[side] = {"team_id": t["team"]["id"], "starter_id": starter,
                       "lineup": sorted(lineup, key=lambda x: x[2])}
    return sides, actuals


def gamelog_upto(player_id, season, cutoff_date, group, cache, cache_key):
    """Sum a player's dated gameLog entries strictly before cutoff_date. Cached
    per (player,season,group) for the whole run -- cutoff filtering happens
    in-memory per call, so one gameLog pull covers every test date."""
    key = f"{cache_key}:{player_id}:{season}"
    if key not in cache:
        try:
            sp = dfs._get(f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats"
                         f"?stats=gameLog&group={group}&season={season}")["stats"][0]["splits"]
        except Exception:
            sp = []
        cache[key] = [(s["date"], s["stat"]) for s in sp]
    entries = cache[key]
    if group == "hitting":
        pts = pa = 0.0
        for d, st in entries:
            if d < cutoff_date and st.get("plateAppearances"):
                pts += dfs.actual_hitter_points(st); pa += st["plateAppearances"]
        return pts, pa
    else:  # pitching
        outs = k = 0.0
        for d, st in entries:
            if d < cutoff_date:
                try:
                    ip = float(st.get("inningsPitched") or 0)
                except (TypeError, ValueError):
                    continue
                outs += ip * 3; k += st.get("strikeOuts", 0) or 0
        return k, outs / 3.0


def platoon_rate(player_id, season, opp_hand, cache):
    """This player's vs-L or vs-R DK-pts-per-PA for `season` (whichever the
    opposing starter throws), or None if unavailable/too small a sample."""
    key = f"platoon:{player_id}:{season}"
    if key not in cache:
        try:
            sp = dfs._get(f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats?stats=statSplits"
                         f"&group=hitting&season={season}&sitCodes=vl,vr")["stats"][0]["splits"]
        except Exception:
            sp = []
        cache[key] = {s["split"]["code"]: s["stat"] for s in sp}
    code = "vl" if opp_hand == "L" else "vr"
    st = cache[key].get(code)
    if st and st.get("plateAppearances", 0) >= 40:
        return dfs.actual_hitter_points(st) / st["plateAppearances"]
    return None


def person_hand(player_id, is_pitcher, cache):
    key = f"hand:{player_id}"
    if key not in cache:
        try:
            p = dfs._get(f"https://statsapi.mlb.com/api/v1/people/{player_id}")["people"][0]
            cache[key] = (p.get("pitchHand") or p.get("batSide") or {}).get("code")
        except Exception:
            cache[key] = None
    return cache[key]


def bullpen_k9(team_id, season, exclude_pid, cache):
    """Season K/9 pooled across the team's non-starter pitchers (gamesStarted/
    gamesPitched < 0.5), excluding tonight's actual starter. One roster + N
    player-stat calls per team per season (cached)."""
    key = f"bullpen:{team_id}:{season}"
    if key in cache:
        return cache[key]
    try:
        roster = dfs._get(f"https://statsapi.mlb.com/api/v1/teams/{team_id}/roster?rosterType=40Man")["roster"]
    except Exception:
        cache[key] = dfs.LG_K9
        return cache[key]
    tot_k = tot_outs = 0.0
    for p in roster:
        if p["position"]["type"] != "Pitcher" or p["person"]["id"] == exclude_pid:
            continue
        pid = p["person"]["id"]
        pkey = f"pstat:{pid}:{season}"
        if pkey not in cache:
            try:
                st = dfs._get(f"https://statsapi.mlb.com/api/v1/people/{pid}/stats?stats=season"
                             f"&group=pitching&season={season}")["stats"][0]["splits"][0]["stat"]
            except Exception:
                cache[pkey] = None
                continue
            cache[pkey] = st
        st = cache[pkey]
        if not st:
            continue
        try:
            ip = float(st.get("inningsPitched") or 0)
        except (TypeError, ValueError):
            continue
        gs, g = st.get("gamesStarted", 0) or 0, st.get("gamesPitched", 0) or 1
        if ip >= 15 and gs / g < 0.5:
            tot_k += st.get("strikeOuts", 0) or 0; tot_outs += ip * 3
    result = (9 * tot_k / (tot_outs / 3.0)) if tot_outs else dfs.LG_K9
    cache[key] = result
    return result


def main():
    start, end = sys.argv[1], sys.argv[2]
    cache = load_cache()
    print(f"backtest window {start}..{end}", flush=True)

    old_skill, old_lg = dfs.pooled_skill_rates((2023, 2024), cache_path=str(ROOT / "data/bt_skill_2023_2024.json"))
    old_k9 = dfs.pitcher_k9(2024, cache_path=str(ROOT / "data/bt_k9_2024.json"))
    park = dfs.park_runs(2025)
    ump_factors = load_umpire_factors()
    print(f"umpire factors loaded for {len(ump_factors)} games", flush=True)

    games = game_pks_for_range(start, end)
    print(f"{len(games)} completed regular-season games in range", flush=True)

    rows = []  # (date, old_proj, new_proj, platoon_proj, bullpen_proj, actual)
    for i, (date, pk) in enumerate(games):
        try:
            sides, actuals = game_data(pk)
        except Exception:
            continue
        for side, other in (("home", "away"), ("away", "home")):
            team = sides[side]; opp = sides[other]
            opp_starter = opp["starter_id"]
            opp_hand = person_hand(opp_starter, True, cache) if opp_starter else None
            pk_val = park.get(str(team["team_id"]), 1.0) if side == "home" else park.get(str(opp["team_id"]), 1.0)
            for pid, name, slot in team["lineup"]:
                actual = actuals.get(pid)
                if actual is None:
                    continue
                # OLD: production-shaped baseline (2023+2024 skill, 2024 opp K9)
                skill_old = old_skill.get(str(pid), old_lg)
                k9_old = old_k9.get(str(opp_starter), dfs.LG_K9) if opp_starter else dfs.LG_K9
                proj_old = dfs.project_hitter_skill(skill_old, slot, pk_val, k9_old)

                # +2025-to-date: same skill/K9 baseline, PLUS this player's/pitcher's
                # 2025 games strictly before `date` (no lookahead), pooled in.
                pts25, pa25 = gamelog_upto(pid, 2025, date, "hitting", cache, "hlog")
                base_pts = old_skill.get(str(pid), 0) * 0  # unused; pool raw totals instead
                # reconstruct raw pooled pts/pa for this player from the (2023,2024) cache
                # isn't available (pooled_skill_rates only stores the rate) -- approximate
                # by blending the 2023-24 rate with the fresh partial-2025 rate, PA-weighted.
                if pa25 >= 20:
                    w25 = pa25 / (pa25 + 300)  # 300 = rough proxy for 2 pooled prior seasons' PA weight
                    skill_new = (1 - w25) * skill_old + w25 * (pts25 / pa25)
                else:
                    skill_new = skill_old
                k25, outs25 = gamelog_upto(opp_starter, 2025, date, "pitching", cache, "plog") if opp_starter else (0, 0)
                if outs25 >= 15:
                    k9_25 = 9 * k25 / outs25
                    w = outs25 / (outs25 + 180)  # ~60 IP proxy weight for the 2024 prior
                    k9_new = (1 - w) * k9_old + w * k9_25
                else:
                    k9_new = k9_old
                proj_new = dfs.project_hitter_skill(skill_new, slot, pk_val, k9_new)

                # +platoon: swap skill for this player's vs-opp-hand split (2024 season,
                # a full completed season, blended toward it only when PA is sufficient)
                plt = platoon_rate(pid, 2024, opp_hand, cache) if opp_hand else None
                skill_platoon = (0.5 * skill_new + 0.5 * plt) if plt is not None else skill_new
                proj_platoon = dfs.project_hitter_skill(skill_platoon, slot, pk_val, k9_new)

                # +bullpen: blend matchup K9 toward the opposing bullpen's K9 (a hitter
                # sees the actual starter for ~60% of PAs, the pen for the rest -- rough,
                # documented weight, not a precise innings model)
                bp_k9 = bullpen_k9(opp["team_id"], 2024, opp_starter, cache)
                k9_blend = 0.6 * k9_new + 0.4 * bp_k9
                proj_bullpen = dfs.project_hitter_skill(skill_platoon, slot, pk_val, k9_blend)
                # clean bullpen-only variant: same skill as +2025-to-date (NOT skill_platoon),
                # so bullpen's effect isn't confounded with the (dropped) platoon adjustment
                proj_bullpen_clean = dfs.project_hitter_skill(skill_new, slot, pk_val, k9_blend)

                # +umpire: scale the effective matchup K9 by tonight's HP umpire's
                # shrunk K-rate multiplier vs league average (higher-K ump -> tougher
                # effective matchup; lower-K ump -> easier), on top of everything above.
                ump_mult = ump_factors.get(pk, 1.0)
                proj_umpire = dfs.project_hitter_skill(skill_platoon, slot, pk_val, k9_blend * ump_mult)

                rows.append((proj_old, proj_new, proj_platoon, proj_bullpen, proj_umpire, proj_bullpen_clean, actual))
        if (i + 1) % 20 == 0:
            save_cache(cache)
            print(f"  {i+1}/{len(games)} games, {len(rows)} hitter rows so far", flush=True)

    save_cache(cache)
    print(f"\nTOTAL: {len(rows)} hitter-games\n", flush=True)

    def report(label, idx):
        proj = [r[idx] for r in rows]; act = [r[-1] for r in rows]
        mae = statistics.mean(abs(a - p) for p, a in zip(proj, act))
        n = len(rows)
        mp, ma = statistics.mean(proj), statistics.mean(act)
        cov = sum((p - mp) * (a - ma) for p, a in zip(proj, act)) / n
        sp, sa = statistics.pstdev(proj), statistics.pstdev(act)
        corr = cov / (sp * sa) if sp and sa else float("nan")
        print(f"  {label:22} MAE={mae:.3f}  corr={corr:+.3f}")

    report("OLD (2023-24 only)", 0)
    report("+2025-to-date", 1)
    report("+platoon", 2)
    report("+bullpen (w/ platoon)", 3)
    report("+umpire", 4)
    report("+bullpen (NO platoon)", 5)


if __name__ == "__main__":
    main()
