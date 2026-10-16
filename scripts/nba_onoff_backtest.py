#!/usr/bin/env python3
"""Does the teammate-out split (edge/nba_onoff.py) improve the NBA model, held out?

The model it would join is edge/dfs_run_nba.py: minutes (edge/nba_minutes.py,
which already re-shares a scratched teammate's minutes) x points per minute
(edge/nba_project.py) x opponent. So two ways of applying the split are tested
against that model, not against a bare average:

    rate     nba_onoff.rate_ratio  -- only his per-minute change with the
             teammate out (the minutes change is already in the minutes model)
    points   nba_onoff.ratio       -- the whole points change, as the web
             session's FPPG board applied it; on this model it counts the
             minutes twice, and is here to show it

Every team-game is rebuilt leak-free by scripts/nba_projection_backtest.py
(histories, tonight's starters and inactives from NBA.com's daily lineups),
and the split table for a date is built from games BEFORE that date only. In
2025-26 last season's table is merged in at nba_onoff.PRIOR_SEASON_WEIGHT,
which is how the live table is published. Fit on 2024-25 (the shrink K and
the variant), report on 2025-26 -- the split main's 7.65 was reported on.
The DATE is the bootstrap unit: players on one night share one realisation.

    python3 scripts/nba_onoff_backtest.py
"""
from __future__ import annotations

import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from edge import nba_minutes as NM, nba_onoff as OO, nba_project as NP   # noqa: E402
from edge.names import norm                                             # noqa: E402
import nba_projection_backtest as BT                                     # noqa: E402

KS = (2.5, 5.0, 10.0, 20.0, 40.0, 80.0)


def tables(season: str, prior: dict | None) -> dict:
    """{date: split table from games strictly before it} for one season."""
    rows = json.load(open(BT.GT / f"player_gamelog_{season}.json"))
    out = {}
    for d in sorted({r["GAME_DATE"][:10] for r in rows}):
        t = OO.build(rows, before=d)
        out[d] = OO.merge(t, prior) if prior else t
    return out


def predictions(games, by_date, decay=NP.RATE_DECAY, opp_pow=NP.OPP_POW):
    """[(date, actual, base prediction, {(variant, K): adjusted prediction}, affected)]"""
    out = []
    for t in games:
        team = [{"id": r["id"], "position": r["position"], "inactive": r["inactive"],
                 "starter": r["starter"], "history": [(m, s) for m, s, _ in r["history"]]}
                for r in t["rows"]]
        mins = NM.project(team)
        of = NP.opponent_factor(t["opp_pm"], t["league_pm"], opp_pow)
        table = by_date.get(t["date"]) or {}
        keys = table.get(t["team"]) or {}
        outs = [r["name"] for r in t["rows"] if r["inactive"] and norm(r["name"]) in keys]
        for r in t["rows"]:
            if r["inactive"] or len(r["history"]) < 5:
                continue
            base = mins[r["id"]]["minutes"] * NP.rate(r["history"], r["position"], decay) * of
            adj = {}
            affected = False
            for k in KS:
                fr, used = OO.rate_ratio(table, t["team"], r["name"], outs, shrink=k)
                fp, _ = OO.ratio(table, t["team"], r["name"], outs, shrink=k)
                affected = affected or bool(used)
                adj[("rate", k)] = base * fr
                adj[("points", k)] = base * fp
            out.append((t["date"], r["pts"], base, adj, affected))
    return out


def mae(preds, pick):
    return float(np.mean([abs(pick(p) - p[1]) for p in preds]))


def boot_ci(preds, pick, n=1000, seed=0):
    """95% CI of MAE(variant) - MAE(base), resampling DATES."""
    by = defaultdict(list)
    for p in preds:
        by[p[0]].append(abs(pick(p) - p[1]) - abs(p[2] - p[1]))
    dates = list(by)
    rng = random.Random(seed)
    stats = []
    for _ in range(n):
        xs = [x for d in (rng.choice(dates) for _ in dates) for x in by[d]]
        stats.append(sum(xs) / len(xs))
    stats.sort()
    return stats[int(0.025 * n)], stats[int(0.975 * n)]


def main() -> int:
    train_g, test_g = BT.build("2024-25"), BT.build("2025-26")
    t24 = tables("2024-25", None)
    last24 = OO.build(json.load(open(BT.GT / "player_gamelog_2024-25.json")))
    t25 = tables("2025-26", last24)
    train, test = predictions(train_g, t24), predictions(test_g, t25)
    print(f"player-games: train 2024-25 {len(train)} ({sum(p[4] for p in train)} with a key "
          f"teammate out), test 2025-26 {len(test)} ({sum(p[4] for p in test)})")
    print(f"  model alone           train MAE {mae(train, lambda p: p[2]):.3f}")
    best = None
    for variant in ("rate", "points"):
        for k in KS:
            m = mae(train, lambda p: p[3][(variant, k)])
            print(f"  {variant:6} K={k:<5}      train MAE {m:.3f}")
            if best is None or m < best[0]:
                best = (m, variant, k)
    _, variant, k = best
    pick = lambda p: p[3][(variant, k)]                       # noqa: E731
    hit = [p for p in test if p[4]]
    lo, hi = boot_ci(test, pick)
    lo_a, hi_a = boot_ci(hit, pick)
    print(f"\nfitted on 2024-25: {variant} K={k}")
    print(f"2025-26 held out, n={len(test)} player-games over {len({p[0] for p in test})} dates:")
    print(f"  model alone          MAE {mae(test, lambda p: p[2]):.3f}")
    print(f"  model + on/off       MAE {mae(test, pick):.3f}   diff 95% CI [{lo:+.3f}, {hi:+.3f}]")
    print(f"  affected only (n={len(hit)}): model {mae(hit, lambda p: p[2]):.3f} -> "
          f"{mae(hit, pick):.3f}   diff 95% CI [{lo_a:+.3f}, {hi_a:+.3f}]")
    other = "points" if variant == "rate" else "rate"
    print(f"  {other} K={k} instead MAE {mae(test, lambda p: p[3][(other, k)]):.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
