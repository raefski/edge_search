#!/usr/bin/env python3
"""Replay lineup-CONSTRUCTION variants over the logged 2026 slates.

For each date with logged projections+slots (data/dfs_proj_log.csv), rebuild
the exact player pool the optimizer saw (proj/ceiling/own/salary/slot), then
run construction variants and score each built lineup with REAL DK points
(data/actuals_cache/<date>.json) and, where a contest export matches the date,
its percentile in the REAL field.

This is the only construction backtest DK's data supports (historical salaries
aren't served), so n = number of logged slates. Report accordingly.

Usage: python3 scripts/dfs_construction_replay.py
"""
import csv
import glob
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs, dfs_opt  # noqa: E402
from edge.dfs import norm  # noqa: E402
from scripts.dfs_calibration import parse_contest_file, load_proj_log, infer_date_by_ground_truth  # noqa: E402
from scripts.dfs_roi_backtest import parse_leaderboard, rank_for_score  # noqa: E402

SCHED_CACHE = ROOT / "data" / "replay_sched_cache.json"


def team_maps(date):
    """{team_abbr: (gamePk, opp_abbr)} for `date` (cached)."""
    cache = json.loads(SCHED_CACHE.read_text()) if SCHED_CACHE.exists() else {}
    if date not in cache:
        s = dfs._get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}")
        id2ab = {str(t["id"]): dfs._STATSAPI_TO_DK_ABBR.get(t["abbreviation"], t["abbreviation"])
                 for t in dfs._get("https://statsapi.mlb.com/api/v1/teams?sportId=1")["teams"]}
        m = {}
        for d in s.get("dates", []):
            for g in d.get("games", []):
                if g.get("gameType", "R") != "R":
                    continue
                h = id2ab.get(str(g["teams"]["home"]["team"]["id"]))
                a = id2ab.get(str(g["teams"]["away"]["team"]["id"]))
                if h and a:
                    m.setdefault(h, (g["gamePk"], a))
                    m.setdefault(a, (g["gamePk"], h))
        cache[date] = {k: list(v) for k, v in m.items()}
        SCHED_CACHE.write_text(json.dumps(cache))
    return {k: tuple(v) for k, v in cache[date].items()}


def build_pool(date, rows):
    tm = team_maps(date)
    pool = []
    for r in rows.values():
        try:
            salary = int(r["salary"]); proj = float(r["proj"])
        except (ValueError, TypeError):
            continue
        ceil = float(r["ceiling"]) if r.get("ceiling") else proj
        own = float(r["own"]) if r.get("own") not in ("", None) else 0.0
        pos = dfs.parse_pos(r["pos"]) if "P" not in r["pos"].split("/") else {"P"}
        pos = dfs.parse_pos(r["pos"]) or ({"P"} if "P" in r["pos"] else set())
        if not pos:
            continue
        game, opp = tm.get(r["team"], (None, None))
        slot = None
        if r["conf"].startswith("H-slot"):
            slot = int(r["conf"][6:].split("*")[0])
        pool.append({"name": r["player"], "pos": pos, "salary": salary, "proj": proj,
                     "ceiling": ceil, "own": own, "floor": proj, "team": r["team"],
                     "game": game, "opp_team": opp, "slot": slot,
                     "lev": round(ceil - 0.1 * own, 1)})
    return pool


def score_lineup(lineup, act):
    return sum(act.get(norm(p["name"]), 0.0) for p, _ in lineup)


def gpp_variant(pool, stack_by, stack_n, fade, iters=800, seed=0, stack2_n=0):
    """stack_by: 'chalk' (max team proj) or 'lev' (max team ceiling - fade*own).
    stack2_n>0 adds a secondary stack from the next-best team by the same rule."""
    team_proj = defaultdict(float)
    team_lev = defaultdict(float)
    hitters = [p for p in pool if "P" not in p["pos"]]
    for h in hitters:
        team_proj[h["team"]] += h["proj"]
        team_lev[h["team"]] += h["ceiling"] - fade * h["own"]
    if not team_proj:
        return None, None
    rank = sorted(team_proj if stack_by == "chalk" else team_lev,
                  key=(team_proj if stack_by == "chalk" else team_lev).get, reverse=True)
    stack_team = rank[0]
    stack2_team = rank[1] if stack2_n and len(rank) > 1 else None
    for p in pool:
        p["lev"] = round(p["ceiling"] - fade * p["own"], 1)
    r = dfs_opt.optimize(pool, mode="gpp", stack_team=stack_team, stack_n=stack_n, iters=iters, seed=seed,
                         stack2_team=stack2_team, stack2_n=stack2_n)
    return r, stack_team


def cash_stack_variant(pool, stack_n, iters=800, seed=0):
    """Cash with a milder forced stack from the top-projected team."""
    team_proj = defaultdict(float)
    for h in pool:
        if "P" not in h["pos"]:
            team_proj[h["team"]] += h["proj"]
    if not team_proj:
        return None
    return dfs_opt.optimize(pool, mode="cash", stack_team=max(team_proj, key=team_proj.get),
                            stack_n=stack_n, iters=iters, seed=seed)


def main():
    by_date = load_proj_log()
    # contest file -> date mapping (ground truth, same as calibration)
    candidate_dates = sorted(by_date.keys())
    boards = {}
    for f in sorted(glob.glob(str(ROOT / "data/contest-standings-*.csv"))):
        contest = parse_contest_file(f)
        if not contest:
            continue
        date, frac, _ = infer_date_by_ground_truth(contest, candidate_dates)
        if frac >= 0.85:
            boards.setdefault(date, f)

    results = defaultdict(list)  # variant -> [(date, actual_pts, pctile)]
    for date in candidate_dates:
        rows = by_date[date]
        pool = build_pool(date, rows)
        ph = [p for p in pool if "P" in p["pos"]]
        hh = [p for p in pool if "P" not in p["pos"] and p.get("slot")]
        if len(ph) < 2 or len(hh) < 8:
            continue
        actp = ROOT / f"data/actuals_cache/{date}.json"
        if not actp.exists():
            continue
        act = json.loads(actp.read_text())
        lb = parse_leaderboard(boards[date]) if date in boards else None

        variants = {}
        variants["cash(proj)"] = dfs_opt.optimize(pool, mode="cash", iters=800)
        variants["cash 3stk"] = cash_stack_variant(pool, 3)
        for name, (sb, sn, fd, s2) in {
            "gpp 4stk chalk f.1": ("chalk", 4, 0.1, 0),
            "gpp 5stk chalk f.1": ("chalk", 5, 0.1, 0),
            "gpp 4stk lev f.3": ("lev", 4, 0.3, 0),
            "gpp 5stk lev f.3": ("lev", 5, 0.3, 0),
            "gpp 5-3 chalk f.1": ("chalk", 5, 0.1, 3),
            "gpp 5-3 lev f.3": ("lev", 5, 0.3, 3),
            "gpp 5-3 lev f.1": ("lev", 5, 0.1, 3),
            "gpp 4stk chalk f0": ("chalk", 4, 0.0, 0),
        }.items():
            variants[name], _ = gpp_variant(pool, sb, sn, fd, stack2_n=s2)

        for name, r in variants.items():
            if not r:
                continue
            s = score_lineup(r["lineup"], act)
            pct = rank_for_score(s, lb)[1] if lb else None
            results[name].append((date, s, pct))

    print(f"{'variant':22} {'n':>3} {'mean pts':>9} {'med pts':>8} {'mean pctile':>12}")
    for name, rs in results.items():
        pts = [x[1] for x in rs]
        pcts = [x[2] for x in rs if x[2] is not None]
        mp = statistics.mean(pcts) if pcts else float("nan")
        print(f"{name:22} {len(rs):>3} {statistics.mean(pts):>9.1f} {statistics.median(pts):>8.1f} {mp:>11.1f}%")
    print("\nper-date detail:")
    for name, rs in results.items():
        det = "  ".join(f"{d[5:]}:{s:.0f}" + (f"({p:.0f}%)" if p is not None else "") for d, s, p in rs)
        print(f"  {name:22} {det}")


if __name__ == "__main__":
    main()
