#!/usr/bin/env python3
"""Validate SIM-EV-DRIVEN GPP construction against the incumbent (5-3 leverage)
on every replayable logged slate — the DFS_IMPROVEMENT_PLAN §4 gate: the
selector ships only if it beats the incumbent's percentile-in-REAL-field
across slates and multiple optimizer seeds.

Method, per (date x meta-seed):
  incumbent = the §18 production construction (5-stack + secondary 3, leverage-
              picked stack teams, fade 0.3) at that seed
  candidates = incumbent + optimize() runs across the top-K leverage stack
              teams x several seeds (the diverse-lineup pool production would
              generate for free from its randomized restarts)
  selector  = pick_by_sim_ev: one shared simulated world-set + one shared
              ownership-modeled field; candidates ranked by expected payout
              under a synthetic top-heavy GPP curve sized to the REAL contest
  score     = REAL DK points (actuals cache) -> percentile in the REAL field
              (contest standings), for BOTH the incumbent's lineup and the
              selector's pick

Only GPP contest fields are used (cash fields don't price differentiation).

Usage: python3 scripts/dfs_sim_ev_replay.py [--sims 2500] [--seeds 3]
"""
import argparse
import glob
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs, dfs_opt, dfs_sim  # noqa: E402
from edge.dfs import norm  # noqa: E402
from scripts.dfs_calibration import (  # noqa: E402
    parse_contest_file, load_proj_log, infer_date_by_ground_truth, load_contest_meta,
)
from scripts.dfs_roi_backtest import parse_leaderboard, rank_for_score  # noqa: E402
from scripts.dfs_construction_replay import build_pool, team_maps, gpp_variant  # noqa: E402
from edge.dfs_run import gpp_candidates  # noqa: E402  (single source with production)

HOME_CACHE = ROOT / "data" / "replay_home_cache.json"


def home_map(date):
    """{team_abbr: is_home} for `date` (cached; same schedule feed team_maps uses)."""
    cache = json.loads(HOME_CACHE.read_text()) if HOME_CACHE.exists() else {}
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
                    m.setdefault(h, True)
                    m.setdefault(a, False)
        cache[date] = m
        HOME_CACHE.write_text(json.dumps(cache))
    return cache[date]


def season_rate_lookup():
    try:
        raw = json.loads((ROOT / "data/dfs_season_hitting.json").read_text())
    except Exception:
        return {}
    return {norm(k.split("|", 1)[1]): v for k, v in raw.items()
            if isinstance(v, dict) and "|" in k}


def lineup_names(r):
    return [p["name"] for p, _slot in r["lineup"]]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sims", type=int, default=2500)
    ap.add_argument("--seeds", type=int, default=3)
    args = ap.parse_args()

    by_date = load_proj_log()
    candidate_dates = sorted(by_date.keys())
    boards = {}
    for f in sorted(glob.glob(str(ROOT / "data/contest-standings-*.csv"))):
        meta = load_contest_meta(f)
        if meta["type"] != "gpp":
            continue
        contest = parse_contest_file(f)
        if not contest:
            continue
        date, frac, _ = infer_date_by_ground_truth(contest, candidate_dates)
        if frac >= 0.85:
            boards.setdefault(date, f)

    sh = season_rate_lookup()
    rows_out = []
    for date in sorted(boards):
        rows = by_date[date]
        pool = build_pool(date, rows)
        hmap = home_map(date)
        for p in pool:
            p["home"] = hmap.get(p["team"], False)
        ph = [p for p in pool if "P" in p["pos"]]
        hh = [p for p in pool if "P" not in p["pos"] and p.get("slot")]
        actp = ROOT / f"data/actuals_cache/{date}.json"
        if len(ph) < 2 or len(hh) < 8 or not actp.exists():
            continue
        act = json.loads(actp.read_text())
        lb = parse_leaderboard(boards[date])
        entries = len(lb)
        season_rates = {p["name"]: sh.get(norm(p["name"])) for p in pool}
        payouts = dfs_sim.synthetic_gpp_payouts(entries)

        for ms in range(args.seeds):
            meta_seed = ms * 100
            inc, _ = gpp_variant(pool, "lev", 5, 0.3, iters=800, seed=meta_seed, stack2_n=3)
            if not inc:
                continue
            cands = gpp_candidates(pool, meta_seed=meta_seed)
            keys = {tuple(sorted(lineup_names(c))) for c in cands}
            if tuple(sorted(lineup_names(inc))) not in keys:
                cands.append(inc)
            ix_by_name = {p["name"]: i for i, p in enumerate(pool)}
            cand_ix = [[ix_by_name[nm] for nm in lineup_names(c)] for c in cands]

            scores, _m = dfs_sim.simulate_slate(pool, n_sims=args.sims, seed=meta_seed + 7,
                                                season_rates=season_rates)
            rng = np.random.default_rng(meta_seed + 11)
            field = dfs_sim.generate_field(pool, min(3 * entries, 900), rng=rng)
            if len(field) < 50:
                continue
            best_i, evs = dfs_sim.pick_by_sim_ev(scores, cand_ix, field, payouts, entries)

            def real_pct(r):
                s = sum(act.get(norm(nm), 0.0) for nm in lineup_names(r))
                return s, rank_for_score(s, lb)[1]

            s_inc, pct_inc = real_pct(inc)
            s_sel, pct_sel = real_pct(cands[best_i])
            rows_out.append((date, ms, s_inc, pct_inc, s_sel, pct_sel, len(cands),
                             evs[best_i]["ev"]))
            print(f"{date} seed{ms}: incumbent {s_inc:6.1f} ({pct_inc:5.1f}%)  "
                  f"sim-EV pick {s_sel:6.1f} ({pct_sel:5.1f}%)  [{len(cands)} cands]", flush=True)

    if not rows_out:
        print("no replayable GPP slates")
        return
    inc_p = [r[3] for r in rows_out]
    sel_p = [r[5] for r in rows_out]
    print(f"\n{len(rows_out)} (date x seed) replays on {len({r[0] for r in rows_out})} slates")
    print(f"incumbent  mean percentile {statistics.mean(inc_p):.1f}%  median {statistics.median(inc_p):.1f}%")
    print(f"sim-EV     mean percentile {statistics.mean(sel_p):.1f}%  median {statistics.median(sel_p):.1f}%")
    wins = sum(1 for a, b in zip(sel_p, inc_p) if a > b)
    ties = sum(1 for a, b in zip(sel_p, inc_p) if a == b)
    print(f"sim-EV beats incumbent on {wins}/{len(rows_out)} replays ({ties} ties -- same lineup picked)")


if __name__ == "__main__":
    main()
