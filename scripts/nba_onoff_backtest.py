#!/usr/bin/env python3
"""Does the with/without-teammate adjustment (edge/nba_onoff.py) beat the
plain season average for a player whose key teammate is out? Held out.

    python3 scripts/nba_onoff_backtest.py                       # 2024-25
    python3 scripts/nba_onoff_backtest.py --season 2025-26 --refresh-days 1

THE TEST, AND WHY IT IS FAIR
Walk the season's dates in order. On each date D, every number used comes
from games strictly BEFORE D: the with/without table (rebuilt every
--refresh-days), and the baseline -- the player's mean DK points for the team
so far, a stand-in for DK's FPPG, which is what the adjustment multiplies on
the live board. For every player-game on D where a key teammate is absent and
the table has a split for the pair, score:

    baseline   = mean DK points before D
    adjusted   = baseline x nba_onoff.ratio(table_before_D, ..., shrink=K)

against what he actually scored. SHRINK_GAMES (K) is chosen on the FIRST half
of the dates and reported on the SECOND -- the slate (date) is the unit, so
the halves never share a night. The uncertainty is a bootstrap over report
dates of the paired MAE difference.

A teammate counts as absent only while he is on the roster: he plays for the
team again later, or his last game was within ROSTER_DAYS and he never turns
up for another team. That is the knowledge DK's own OUT tag carries on the
night -- it is not a peek at the outcome.

Reads data/nba_ground_truth/player_gamelog_<season>.json (desktop only;
scripts/nba_ground_truth_collect.py regenerates it for free).
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
import sys
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import nba, nba_onoff  # noqa: E402
from edge.names import norm  # noqa: E402

GT = ROOT / "data" / "nba_ground_truth"
K_GRID = (1.0, 3.0, 5.0, 10.0, 20.0, 40.0, 1e9)       # 1e9 = no adjustment
ROSTER_DAYS = 45
MIN_BASE_GAMES = 5
WARMUP_DAYS = 14


def _d(s: str) -> date:
    return date.fromisoformat(str(s)[:10])


def run(rows: list, refresh_days: int = 3, k_grid=K_GRID, seed: int = 0) -> dict:
    rows = [r for r in rows if nba_onoff._minutes(r) > 0]
    for r in rows:
        r["_team"] = nba.team(r["TEAM_ABBREVIATION"])
        r["_date"] = str(r["GAME_DATE"])[:10]
        r["_fp"] = nba.actual_points(r)
        r["_pid"] = r.get("PLAYER_ID") or r.get("PLAYER_NAME")
    dates = sorted({r["_date"] for r in rows})
    if not dates:
        return {"error": "no rows"}
    start = (_d(dates[0]) + timedelta(days=WARMUP_DAYS)).isoformat()
    dates = [d for d in dates if d >= start]
    by_game: dict = defaultdict(list)
    by_player_team: dict = defaultdict(list)                # (pid, team) -> [dates]
    teams_of: dict = defaultdict(set)                       # pid -> {(team, date)}
    for r in rows:
        by_game[(r["_date"], r["GAME_ID"], r["_team"])].append(r)
        by_player_team[(r["_pid"], r["_team"])].append(r["_date"])
        teams_of[r["_pid"]].add((r["_team"], r["_date"]))
    name_pid = {}
    for r in rows:
        name_pid[(r["_team"], norm(r["PLAYER_NAME"]))] = r["_pid"]

    def on_roster(pid, team, day):
        ds = by_player_team.get((pid, team), [])
        if any(x >= day for x in ds):
            return True
        before = [x for x in ds if x < day]
        if not before or (_d(day) - _d(max(before))).days > ROSTER_DAYS:
            return False
        last = max(before)
        return not any(t != team and x > last for t, x in teams_of[pid])

    history: dict = defaultdict(list)                       # (pid, team) -> [fp] before D
    table, built_on = {}, None
    obs = []                                                # (date, actual, base, {K: adj})
    for day in dates:
        if built_on is None or (_d(day) - _d(built_on)).days >= refresh_days:
            table, built_on = nba_onoff.build(rows, before=day), day
        games = [k for k in by_game if k[0] == day]
        for key in games:
            _, gid, team = key
            present = {norm(r["PLAYER_NAME"]) for r in by_game[key]}
            absent = [e["name"] for b, e in (table.get(team) or {}).items()
                      if b not in present and on_roster(name_pid.get((team, b)), team, day)]
            if not absent:
                continue
            for r in by_game[key]:
                hist = history[(r["_pid"], team)]
                if len(hist) < MIN_BASE_GAMES:
                    continue
                base = statistics.fmean(hist)
                adj = {}
                for k in k_grid:
                    f, used = nba_onoff.ratio(table, team, r["PLAYER_NAME"], absent, shrink=k)
                    if not used:
                        break
                    adj[k] = base * f
                if adj:
                    obs.append((day, r["_fp"], base, adj))
        for key in games:
            for r in by_game[key]:
                history[(r["_pid"], key[2])].append(r["_fp"])

    if not obs:
        return {"error": "no player-games with a key teammate out and a split"}
    obs_dates = sorted({o[0] for o in obs})
    cut = obs_dates[len(obs_dates) // 2]
    fit = [o for o in obs if o[0] < cut]
    rep = [o for o in obs if o[0] >= cut]

    def mae(sample, k):
        return statistics.fmean(abs(o[1] - (o[3][k] if k in o[3] else o[2])) for o in sample)

    grid = {k: (mae(fit, k), mae(rep, k)) for k in k_grid}
    best = min(k_grid, key=lambda k: grid[k][0])
    # paired bootstrap over report DATES: MAE(best) - MAE(no adjustment)
    by_date = defaultdict(list)
    for o in rep:
        by_date[o[0]].append(abs(o[1] - o[3][best]) - abs(o[1] - o[2]))
    keys = list(by_date)
    rng = random.Random(seed)
    boots = []
    for _ in range(1000):
        pick = [rng.choice(keys) for _ in keys]
        vals = [v for d in pick for v in by_date[d]]
        boots.append(statistics.fmean(vals))
    boots.sort()
    return {"n_fit": len(fit), "n_rep": len(rep),
            "dates_fit": len({o[0] for o in fit}), "dates_rep": len(keys), "cut": cut,
            "grid": grid, "best_k": best,
            "rep_base_mae": mae(rep, 1e9), "rep_best_mae": mae(rep, best),
            "rep_bias_base": statistics.fmean(o[2] - o[1] for o in rep),
            "rep_bias_best": statistics.fmean(o[3][best] - o[1] for o in rep),
            "diff_ci90": (boots[50], boots[949])}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--season", default="2024-25")
    ap.add_argument("--refresh-days", type=int, default=3)
    args = ap.parse_args()
    p = GT / f"player_gamelog_{args.season}.json"
    if not p.exists():
        print(f"{p} missing -- run scripts/nba_ground_truth_collect.py --season {args.season}")
        return 1
    res = run(json.loads(p.read_text()), refresh_days=args.refresh_days)
    if res.get("error"):
        print(res["error"])
        return 1
    print(f"{args.season}: fit {res['n_fit']} player-games / {res['dates_fit']} dates, report "
          f"{res['n_rep']} player-games / {res['dates_rep']} dates (from {res['cut']})")
    print(f"  {'K':>8} {'fit MAE':>8} {'report MAE':>11}")
    for k, (f, r) in res["grid"].items():
        label = "none" if k >= 1e8 else f"{k:g}"
        print(f"  {label:>8} {f:8.3f} {r:11.3f}" + ("   <- chosen on fit" if k == res["best_k"] else ""))
    lo, hi = res["diff_ci90"]
    print(f"\nreport half, K={res['best_k']:g} vs no adjustment: MAE {res['rep_best_mae']:.3f} vs "
          f"{res['rep_base_mae']:.3f}  (diff 90% CI [{lo:+.3f}, {hi:+.3f}], by date)")
    print(f"  bias: adjusted {res['rep_bias_best']:+.2f}, baseline {res['rep_bias_base']:+.2f}")
    verdict = ("helps" if hi < 0 else "hurts" if lo > 0 else "not distinguishable from no adjustment")
    print(f"  -> {verdict}. Set nba_onoff.SHRINK_GAMES = {res['best_k']:g} only if it helps, "
          "with this run's n and date in the comment.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
