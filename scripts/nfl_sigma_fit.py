#!/usr/bin/env python3
"""Fit (and re-check) the NFL sigmas in edge/dfs_sport.py, and measure how
much they actually matter.

    python3 scripts/nfl_sigma_fit.py
    python3 scripts/nfl_sigma_fit.py --odds-db data/odds.db   # inertness check

WHAT A SIGMA IS HERE
edge/dfs_project.py turns one posted line plus its price into a mean:

    implied_mean = line + sigma * inv_cdf(devigged P(over))

so sigma is the CONDITIONAL spread of the outcome around the market's own
expectation -- not the spread of the stat across players, which is much
larger and is the number a prior tends to reach for by mistake.

THE ESTIMATE, AND THE TWO CORRECTIONS IT NEEDS
The market's expectation is not in this dataset (props cannot be backfilled --
see ODDS_LAYER.md section 6), so the stand-in is a leave-one-out season mean,
and sigma is the SD of the outcome around it. That needs correcting twice, in
opposite directions:

  1. The LOO mean is itself noisy, which INFLATES the residual. Var(actual -
     loo) = Var(actual|mu) + Var(loo), and Var(loo) ~ s^2/(n-1), so the raw SD
     is deflated by sqrt(1 + 1/(n-1)).

  2. A season average knows less than the market does -- no matchup, no injury
     report, no game script -- so what is left is still an UPPER BOUND on the
     true conditional sigma. `--opponent-adjust` bounds that gap by measuring
     how much a crude opponent-strength adjustment explains: 1-4%, depending
     on the stat. So the bound is tight, and the numbers below are shipped as
     the low end of it.

AND THEN THE PART WORTH READING FIRST
Sigma only does work when the price is away from even money, and it very
rarely is. Run with --odds-db to measure |z| across the real posted prices in
the store: it comes back at a mean of 0.014-0.128, because books post yardage
props at -110/-110 and move the LINE instead of the price. A 19% sigma error
is worth about 0.03 DK points. Fit them because it is cheap and they should
be traceable to data, not because it will change a lineup.
"""
from __future__ import annotations

import argparse
import collections
import json
import sqlite3
import statistics
import sys
from pathlib import Path
from statistics import NormalDist

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.dfs_project import exceed_prob  # noqa: E402
from edge.oddsmath import devig  # noqa: E402
from scripts.nfl_td_fit import OFFENSE, load_season, loo_table, _f  # noqa: E402

#: stat -> (gate stat for the pool floor, floor, QB-only, shipped sigma)
SPEC = {
    "passing_yards":         ("passing_yards", 150.0, True, 75.0),
    "passing_tds":           ("passing_yards", 150.0, True, 1.1),
    "passing_interceptions": ("passing_yards", 150.0, True, 0.85),
    "rushing_yards":         ("rushing_yards", 20.0, False, 29.0),
    "rushing_tds":           ("rushing_yards", 20.0, False, 0.58),
    "receiving_yards":       ("receiving_yards", 20.0, False, 30.0),
    "receptions":            ("receiving_yards", 20.0, False, 2.0),
    "receiving_tds":         ("receiving_yards", 20.0, False, 0.5),
}

BONUSES = (("rush 100+", "rushing_yards", 100.0),
           ("rec 100+", "receiving_yards", 100.0),
           ("pass 300+", "passing_yards", 300.0))


def pool(recs, stat):
    gate, floor, qb, _ = SPEC[stat]
    return [x for x in recs if x["exp"][gate] >= floor and (x["pos"] == "QB" or not qb)]


def deflate(sd: float, games: int = 14) -> float:
    """Remove the LOO mean's own sampling noise from the residual."""
    return sd / (1 + 1 / (games - 1)) ** 0.5


def opponent_strength(rows, stat):
    opp = collections.defaultdict(list)
    for r in rows:
        if r["position"] in OFFENSE:
            opp[(r["opponent_team"], r["season"])].append(float(r.get(stat) or 0))
    league = statistics.mean(v for vs in opp.values() for v in vs)
    return {k: (sum(vs) / len(vs)) / league for k, vs in opp.items() if len(vs) > 50}


def measure_z(db_path: Path):
    """|z| across the real two-sided NFL prop quotes already in the store."""
    con = sqlite3.connect(db_path)
    rows = con.execute(
        """SELECT scan_id, group_key, market, book, side, decimal FROM quote
           WHERE sport_key='americanfootball_nfl'
             AND market IN ('player_pass_yds','player_rush_yds',
                            'player_reception_yds','player_receptions')""").fetchall()
    con.close()
    groups = collections.defaultdict(dict)
    for scan, gk, market, book, side, dec in rows:
        groups[(scan, gk, book, market)][side] = dec
    N = NormalDist()
    out = collections.defaultdict(list)
    for (_, _, _, market), sides in groups.items():
        if "over" in sides and "under" in sides:
            p = devig([sides["over"], sides["under"]])[0]
            out[market].append(N.inv_cdf(min(max(p, 1e-3), 1 - 1e-3)))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", type=int, nargs="+", default=[2023, 2024])
    ap.add_argument("--odds-db", type=Path, default=ROOT / "data" / "odds.db")
    args = ap.parse_args()

    raw = [r for y in args.seasons for r in load_season(y)]
    recs = [x for y in args.seasons for x in loo_table(load_season(y))]
    print(f"seasons {args.seasons}: {len(recs)} offensive player-weeks")

    print("\n=== RESIDUAL SD AROUND THE MARKET'S EXPECTATION ===")
    print(f"  {'stat':24s}{'n':>6}{'raw SD':>9}{'deflated':>10}"
          f"{'opp-adj':>9}{'shipped':>9}")
    for stat, (gate, floor, qb, shipped) in SPEC.items():
        sel = pool(recs, stat)
        d = [_f(x["act"], stat) - x["exp"][stat] for x in sel]
        strength = opponent_strength(raw, stat)
        adj = [_f(x["act"], stat)
               - x["exp"][stat] * strength.get(
                   (x["act"]["opponent_team"], x["act"]["season"]), 1.0)
               for x in sel]
        print(f"  {stat:24s}{len(sel):6d}{statistics.pstdev(d):9.2f}"
              f"{deflate(statistics.pstdev(d)):10.2f}"
              f"{deflate(statistics.pstdev(adj)):9.2f}{shipped:9.2f}")
    print("\n  opp-adj is the LOWER end of the bound: a season average knows "
          "less than\n  the market, and this is how much of that gap a crude "
          "matchup term closes.")

    print("\n=== DOES SIGMA SCALE WITH THE LEVEL? ===")
    for stat, bands in (("rushing_yards", [20, 40, 60, 80, 10 ** 4]),
                        ("receiving_yards", [20, 40, 60, 80, 10 ** 4]),
                        ("receptions", [20, 40, 60, 80, 10 ** 4]),
                        ("passing_yards", [150, 200, 230, 260, 10 ** 4])):
        gate, _, qb, _ = SPEC[stat]
        print(f"  {stat}")
        for lo, hi in zip(bands[:-1], bands[1:]):
            sel = [x for x in recs if lo <= x["exp"][gate] < hi
                   and (x["pos"] == "QB" or not qb)]
            if len(sel) < 60:
                continue
            d = [_f(x["act"], stat) - x["exp"][stat] for x in sel]
            print(f"    gate {lo:4d}-{hi:<6d} n={len(sel):5d} "
                  f"meanExp={statistics.mean(x['exp'][stat] for x in sel):7.1f} "
                  f"SD={deflate(statistics.pstdev(d)):6.2f}")
    print("\n  It does, for the rushing/receiving stats -- SD roughly doubles "
          "across the\n  range. One scalar per stat is therefore wrong at both "
          "ends. It is left as\n  a scalar anyway: see the inertness measurement "
          "below for why that costs\n  under 0.05 DK points, which is not worth "
          "making sigma a function of the\n  mean (it would also make the "
          "inversion implicit, since the mean is what\n  you are solving for).")

    print("\n=== THRESHOLD BONUS CALIBRATION (where sigma does real work) ===")
    print(f"  {'bonus':12s}{'n':>6}{'actual':>9}{'modelled':>10}{'DK bias':>9}")
    for label, stat, thr in BONUSES:
        sel = pool(recs, stat)
        sigma = SPEC[stat][3]
        act = sum(1 for x in sel if _f(x["act"], stat) >= thr) / len(sel)
        mod = statistics.mean(exceed_prob(x["exp"][stat], sigma, thr) for x in sel)
        print(f"  {label:12s}{len(sel):6d}{act:9.4f}{mod:10.4f}{3 * (mod - act):+9.3f}")
    print("\n  The 100+ bonuses stay UNDER-predicted at the fitted sigma: "
          "yardage is\n  right-skewed and a normal's right tail is too thin. "
          "Worth ~0.05 DK points.")

    if args.odds_db.exists():
        print(f"\n=== INERTNESS: how far do real prices move the mean? ===")
        z = measure_z(args.odds_db)
        total = sum(len(v) for v in z.values())
        print(f"  {total} two-sided NFL prop quotes in {args.odds_db}")
        print(f"  {'market':26s}{'n':>6}{'mean|z|':>9}{'p90|z|':>8}")
        for market, vals in sorted(z.items()):
            a = sorted(abs(v) for v in vals)
            print(f"  {market:26s}{len(a):6d}{sum(a) / len(a):9.3f}"
                  f"{a[int(0.9 * len(a))]:8.3f}")
        print("\n  implied_mean = line + sigma*z, so THIS is the multiplier on "
              "any sigma\n  error. Books post yardage props at -110/-110 and "
              "move the line instead.")
    else:
        print(f"\n  (no {args.odds_db} -- skipping the inertness check)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
