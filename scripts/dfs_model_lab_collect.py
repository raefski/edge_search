#!/usr/bin/env python3
"""Collect per-hitter-game FEATURES for model experiments (leak-free, free).

One row per starter hitter-game in the window, from data/bt_boxscores plus
statsapi game logs (all strictly-before-date, so no lookahead):

  date, pid, slot, home, park, actual,
  pa23/pts23, pa24/pts24, pa25/pts25 (to date),
  opp starter: k9_24, era24, k25/outs25/er25 (to date), hand,
  opp bullpen k9 (2024), hitter vs-hand 2024 split (pts/pa + PA)

Variants (EB shrinkage, platoon weights, ERA blends, home/away PA) are then
pure arithmetic over the saved rows -- see dfs_model_lab_eval.py. Reuses
data/backtest_cache.json so game logs fetched by earlier backtests are free.

Usage: python3 scripts/dfs_model_lab_collect.py 2025-04-15 2025-07-31
Writes data/model_lab_rows.json
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs  # noqa: E402
from scripts.dfs_hitter_backtest import (  # noqa: E402
    load_cache, save_cache, gamelog_upto, person_hand, bullpen_k9,
)

BOX = ROOT / "data" / "bt_boxscores"
OUT = ROOT / "data" / "model_lab_rows.json"


def season_raw_hitting(season, cache):
    """{pid: (pts, pa)} raw DK-points + PA for a full season (one bulk call)."""
    key = f"rawhit:{season}"
    if key not in cache:
        sp = dfs._get(f"https://statsapi.mlb.com/api/v1/stats?stats=season&season={season}"
                      "&group=hitting&sportId=1&limit=3000&playerPool=All")["stats"][0]["splits"]
        d = {}
        for s in sp:
            st = s["stat"]; pa = st.get("plateAppearances", 0) or 0
            if pa:
                d[str(s["player"]["id"])] = (dfs.actual_hitter_points(st), pa)
        cache[key] = d
    return cache[key]


def season_pitching_2024(cache):
    """{pid: {k9, era, outs}} full 2024 season."""
    key = "rawpitch:2024"
    if key not in cache:
        sp = dfs._get("https://statsapi.mlb.com/api/v1/stats?stats=season&season=2024"
                      "&group=pitching&sportId=1&limit=2000&playerPool=All")["stats"][0]["splits"]
        d = {}
        for s in sp:
            st = s["stat"]
            try:
                ip = float(st.get("inningsPitched") or 0)
            except (TypeError, ValueError):
                continue
            if ip < 10:
                continue
            k = st.get("strikeOuts", 0) or 0
            er = st.get("earnedRuns", 0) or 0
            d[str(s["player"]["id"])] = {"k9": 9 * k / ip, "era": 9 * er / ip, "outs": ip * 3}
        cache[key] = d
    return cache[key]


def platoon_split_raw(player_id, season, cache):
    """{code: (pts_per_pa, pa)} for vl/vr splits (cached like platoon_rate)."""
    key = f"platoon:{player_id}:{season}"
    if key not in cache:
        try:
            sp = dfs._get(f"https://statsapi.mlb.com/api/v1/people/{player_id}/stats?stats=statSplits"
                          f"&group=hitting&season={season}&sitCodes=vl,vr")["stats"][0]["splits"]
        except Exception:
            sp = []
        cache[key] = {s["split"]["code"]: s["stat"] for s in sp}
    out = {}
    for code, st in cache[key].items():
        pa = st.get("plateAppearances", 0) or 0
        if pa:
            out[code] = (dfs.actual_hitter_points(st) / pa, pa)
    return out


def er_outs_upto(player_id, cutoff_date, cache):
    """Opp starter's 2025 ER + outs strictly before cutoff (reuses plog cache)."""
    key = f"plog:{player_id}:2025"
    if key not in cache:
        gamelog_upto(player_id, 2025, cutoff_date, "pitching", cache, "plog")
    er = outs = 0.0
    for d, st in cache.get(key, []):
        if d < cutoff_date:
            try:
                ip = float(st.get("inningsPitched") or 0)
            except (TypeError, ValueError):
                continue
            outs += ip * 3
            er += st.get("earnedRuns", 0) or 0
    return er, outs


def main():
    start, end = sys.argv[1], sys.argv[2]
    cache = load_cache()
    raw23 = season_raw_hitting(2023, cache)
    raw24 = season_raw_hitting(2024, cache)
    pitch24 = season_pitching_2024(cache)
    park = dfs.park_runs(2025)
    save_cache(cache)

    files = sorted(BOX.glob("*.json"))
    games = [json.loads(f.read_text()) for f in files]
    games = [g for g in games if start <= g["date"] <= end]
    print(f"{len(games)} games {start}..{end}", flush=True)

    rows = []
    for i, g in enumerate(games):
        for side, other in (("home", "away"), ("away", "home")):
            team, opp = g[side], g[other]
            sp = opp["starter_id"]
            pk_val = park.get(str(g["home"]["team_id"]), 1.0)
            sp_hand = person_hand(sp, True, cache) if sp else None
            p24 = pitch24.get(str(sp), {})
            k25, outs25 = gamelog_upto(sp, 2025, g["date"], "pitching", cache, "plog") if sp else (0, 0)
            er25, _outs25b = er_outs_upto(sp, g["date"], cache) if sp else (0, 0)
            bp = bullpen_k9(opp["team_id"], 2024, sp, cache)
            for pid, name, slot in team["lineup"]:
                st = team["stats"].get(str(pid))
                if st is None:
                    continue
                pts25, pa25 = gamelog_upto(pid, 2025, g["date"], "hitting", cache, "hlog")
                splits = platoon_split_raw(pid, 2024, cache)
                code = "vl" if sp_hand == "L" else "vr"
                plat = splits.get(code, (None, 0))
                r23 = raw23.get(str(pid), (0, 0))
                r24 = raw24.get(str(pid), (0, 0))
                rows.append({
                    "date": g["date"], "pid": pid, "slot": slot, "home": side == "home",
                    "park": pk_val, "actual": st["pts"], "pa_game": st["pa"],
                    "pts23": r23[0], "pa23": r23[1], "pts24": r24[0], "pa24": r24[1],
                    "pts25": pts25, "pa25": pa25,
                    "sp_k9_24": p24.get("k9"), "sp_era24": p24.get("era"), "sp_outs24": p24.get("outs", 0),
                    "sp_k25": k25, "sp_outs25": outs25, "sp_er25": er25, "sp_hand": sp_hand,
                    "bp_k9": bp,
                    "plat_rate": plat[0], "plat_pa": plat[1],
                })
        if (i + 1) % 25 == 0:
            save_cache(cache)
            print(f"  {i+1}/{len(games)} games, {len(rows)} rows", flush=True)

    save_cache(cache)
    OUT.write_text(json.dumps(rows))
    print(f"TOTAL {len(rows)} rows -> {OUT}", flush=True)


if __name__ == "__main__":
    main()
