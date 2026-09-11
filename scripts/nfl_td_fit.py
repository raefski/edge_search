#!/usr/bin/env python3
"""Fit (and re-check) the NFL touchdown-per-yard rates in edge/dfs_sport.py.

    python3 scripts/nfl_ground_truth_collect.py --season 2023
    python3 scripts/nfl_ground_truth_collect.py --season 2024
    python3 scripts/nfl_td_fit.py                    # fit + report
    python3 scripts/nfl_td_fit.py --oos              # fit 2023, test 2024

WHY THIS EXISTS
Rushing and receiving touchdowns have no two-sided prop market -- DraftKings
prices them only as "Anytime TD", a field with no opposing side -- so
edge/dfs_sport.py::_nfl_impute scales them off projected yardage. At 6 points
each that is the largest imputed term in an NFL projection. It shipped as two
round-number priors (1 per 180 rushing yards, 1 per 200 receiving); this is
the script that replaced them with measurements.

THREE THINGS IT GETS RIGHT THAT A NAIVE FIT DOES NOT

1. The regressor is an EXPECTED mean, not a realized game. The rate is
   multiplied by the market's implied mean yardage, so it must be fitted
   against the same quantity. Fitting on realized yards double-counts: a
   5-yard touchdown catch IS 5 receiving yards, so the touchdown inflates its
   own predictor and the bottom bin reads a spurious 1 TD per 66 yards. The
   stand-in used here is a LEAVE-ONE-OUT season mean -- the mean of that
   player's OTHER games that season -- so the week being predicted contributes
   nothing to its own prediction.

2. The population is the DFS pool, not the league. A player the book posts no
   yardage prop for is never projected, so he should not vote on the rate.
   `--floor` restricts to expected yards >= 20. (It happens not to matter --
   the rate is flat in yardage -- but that is a finding, not an assumption.)

3. A split has to earn its place. Every position split is bootstrapped, and
   the ones whose confidence intervals overlap are reported as NOT separated
   and deliberately not used. Wide receivers and tight ends convert receiving
   yards at the same rate (161 vs 167, p~0.60); splitting them anyway would
   look more careful while being less true.
"""
from __future__ import annotations

import argparse
import collections
import json
import random
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DATA = ROOT / "data" / "nfl_ground_truth"
OFFENSE = {"QB", "RB", "WR", "TE", "FB"}
#: LOO means are only meaningful for a player with a real sample of games.
MIN_GAMES = 6

STATS = ("passing_yards", "passing_tds", "passing_interceptions",
         "rushing_yards", "rushing_tds", "receiving_yards", "receiving_tds",
         "receptions")


def _f(row: dict, key: str) -> float:
    return float(row.get(key) or 0)


def load_season(year: int) -> list[dict]:
    path = DATA / f"player_week_{year}.json"
    if not path.exists():
        raise SystemExit(f"{path} missing -- run: python3 "
                         f"scripts/nfl_ground_truth_collect.py --season {year}")
    return [r for r in json.loads(path.read_text()) if r["season_type"] == "REG"]


def loo_table(rows: list[dict], min_games: int = MIN_GAMES) -> list[dict]:
    """One record per player-week: leave-one-out season means + that week.

    The LOO mean stands in for the market's implied mean. It is a WEAKER
    predictor than the market -- it knows nothing about the matchup, the
    injury report or the game script -- so absolute errors here are larger
    than the live system's. What it is fit for is COMPARING imputation rules,
    which it does cleanly because every arm sees exactly the same input.
    """
    by_player = collections.defaultdict(list)
    for r in rows:
        if r["position"] in OFFENSE:
            by_player[r["player_id"]].append(r)
    out = []
    for games in by_player.values():
        n = len(games)
        if n < min_games:
            continue
        totals = {k: sum(_f(r, k) for r in games) for k in STATS}
        for r in games:
            out.append({"pos": r["position"],
                        "exp": {k: (totals[k] - _f(r, k)) / (n - 1) for k in STATS},
                        "act": r})
    return out


def rate(recs, ykey, tkey, positions=None, floor=20.0):
    """Ratio-of-totals: the estimator that makes projected TDs sum to actual."""
    sel = [x for x in recs if x["exp"][ykey] >= floor
           and (positions is None or x["pos"] in positions)]
    yards = sum(x["exp"][ykey] for x in sel)
    tds = sum(_f(x["act"], tkey) for x in sel)
    return (tds / yards if yards else 0.0), len(sel), yards, tds


def boot_ci(recs, ykey, tkey, positions, floor=20.0, n=4000, seed=7):
    rng = random.Random(seed)
    sel = [x for x in recs if x["exp"][ykey] >= floor and x["pos"] in positions]
    draws = []
    for _ in range(n):
        s = [sel[rng.randrange(len(sel))] for _ in range(len(sel))]
        y = sum(x["exp"][ykey] for x in s)
        if y:
            draws.append(sum(_f(x["act"], tkey) for x in s) / y)
    draws.sort()
    return draws[int(0.025 * len(draws))], draws[int(0.975 * len(draws))]


def boot_diff(recs, ykey, tkey, a_pos, b_pos, floor=20.0, n=4000, seed=11):
    """Is the gap between two position groups bigger than resampling noise?"""
    rng = random.Random(seed)
    def r(sel):
        y = sum(x["exp"][ykey] for x in sel)
        return sum(_f(x["act"], tkey) for x in sel) / y if y else 0.0
    a = [x for x in recs if x["exp"][ykey] >= floor and x["pos"] in a_pos]
    b = [x for x in recs if x["exp"][ykey] >= floor and x["pos"] in b_pos]
    obs = r(a) - r(b)
    ds = []
    for _ in range(n):
        ds.append(r([a[rng.randrange(len(a))] for _ in range(len(a))])
                  - r([b[rng.randrange(len(b))] for _ in range(len(b))]))
    ds.sort()
    lo, hi = ds[int(0.025 * len(ds))], ds[int(0.975 * len(ds))]
    p = 2 * min(sum(1 for d in ds if d <= 0), sum(1 for d in ds if d >= 0)) / len(ds)
    return obs, lo, hi, p


def flatness(recs, ykey, tkey, label):
    """Is the rate constant in yardage? If it is, proportional-through-origin
    is the right SHAPE and only the constant was ever wrong."""
    print(f"\n  {label}: yards per TD by expected-yardage floor")
    line = "    "
    for floor in (0, 10, 20, 30, 40):
        r, n, _, _ = rate(recs, ykey, tkey, None, floor)
        line += f"  >={floor:<3d} {1 / r:5.0f} (n={n})"
    print(line)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", type=int, nargs="+", default=[2023, 2024])
    ap.add_argument("--floor", type=float, default=20.0,
                    help="expected-yards floor: the DFS pool, not the league")
    ap.add_argument("--oos", action="store_true",
                    help="fit on all but the last season, test on the last")
    args = ap.parse_args()

    per_season = {y: loo_table(load_season(y)) for y in args.seasons}
    allr = [x for y in args.seasons for x in per_season[y]]
    print(f"seasons {args.seasons}: {len(allr)} offensive player-weeks "
          f"({MIN_GAMES}+ games), floor {args.floor:g} expected yards")

    print("\n=== SHAPE: is the rate flat in yardage? ===")
    flatness(allr, "rushing_yards", "rushing_tds", "rushing")
    flatness(allr, "receiving_yards", "receiving_tds", "receiving")
    print("\n  Flat => proportional through the origin is the right form. A "
          "fitted\n  intercept is not: the RB rushing one comes out NEGATIVE.")

    print("\n=== RATES ===")
    print(f"  {'group':16s}{'n':>6}{'yards':>9}{'TDs':>7}{'1 TD per':>10}{'95% CI':>16}")
    groups = (("rush QB", "rushing_yards", "rushing_tds", ("QB",)),
              ("rush RB/FB", "rushing_yards", "rushing_tds", ("RB", "FB")),
              ("rush WR/TE", "rushing_yards", "rushing_tds", ("WR", "TE")),
              ("rush ANY", "rushing_yards", "rushing_tds", tuple(OFFENSE)),
              ("rec WR", "receiving_yards", "receiving_tds", ("WR",)),
              ("rec TE", "receiving_yards", "receiving_tds", ("TE",)),
              ("rec WR+TE", "receiving_yards", "receiving_tds", ("WR", "TE")),
              ("rec RB/FB", "receiving_yards", "receiving_tds", ("RB", "FB")),
              ("rec ANY", "receiving_yards", "receiving_tds", tuple(OFFENSE)))
    for label, ykey, tkey, pos in groups:
        r, n, y, t = rate(allr, ykey, tkey, pos, args.floor)
        if not r:
            continue
        lo, hi = boot_ci(allr, ykey, tkey, pos, args.floor)
        print(f"  {label:16s}{n:6d}{y:9.0f}{t:7.0f}{1 / r:10.0f}"
              f"{f'({1 / hi:.0f}-{1 / lo:.0f})':>16}")

    print("\n=== WHICH SPLITS ARE REAL? (bootstrapped difference) ===")
    for label, ykey, tkey, a, b in (
            ("rush  QB vs RB/FB", "rushing_yards", "rushing_tds", ("QB",), ("RB", "FB")),
            ("rec   WR vs TE", "receiving_yards", "receiving_tds", ("WR",), ("TE",)),
            ("rec   WR+TE vs RB/FB", "receiving_yards", "receiving_tds",
             ("WR", "TE"), ("RB", "FB"))):
        obs, lo, hi, p = boot_diff(allr, ykey, tkey, a, b, args.floor)
        verdict = "SEPARATED -- split" if lo * hi > 0 else "not separated -- DO NOT split"
        print(f"  {label:22s} diff={obs:+.6f} 95%CI [{lo:+.6f},{hi:+.6f}] "
              f"p~{p:.3f}  {verdict}")

    if args.oos:
        test_year = args.seasons[-1]
        fit_years = args.seasons[:-1]
        if not fit_years:
            print("\n--oos needs at least two seasons", file=sys.stderr)
            return 1
        fit = [x for y in fit_years for x in per_season[y]]
        test = per_season[test_year]
        print(f"\n=== OUT OF SAMPLE: fit {fit_years}, test {test_year} ===")
        rq = rate(fit, "rushing_yards", "rushing_tds", ("QB",), args.floor)[0]
        ro = rate(fit, "rushing_yards", "rushing_tds", ("RB", "FB", "WR", "TE"), args.floor)[0]
        ra = rate(fit, "rushing_yards", "rushing_tds", tuple(OFFENSE), args.floor)[0]
        cw = rate(fit, "receiving_yards", "receiving_tds", ("WR", "TE"), args.floor)[0]
        cb = rate(fit, "receiving_yards", "receiving_tds", ("RB", "FB"), args.floor)[0]
        ca = rate(fit, "receiving_yards", "receiving_tds", tuple(OFFENSE), args.floor)[0]
        print(f"  fitted: rush QB 1/{1/rq:.0f} other 1/{1/ro:.0f} any 1/{1/ra:.0f} | "
              f"rec WR/TE 1/{1/cw:.0f} RB 1/{1/cb:.0f} any 1/{1/ca:.0f}")

        rng = random.Random(3)
        for route, ykey, tkey, shipped, glob, split in (
                ("RUSH", "rushing_yards", "rushing_tds", 1 / 180.0, ra,
                 lambda x: rq if x["pos"] == "QB" else ro),
                ("REC", "receiving_yards", "receiving_tds", 1 / 200.0, ca,
                 lambda x: cb if x["pos"] in ("RB", "FB") else cw)):
            sel = [x for x in test if x["exp"][ykey] >= args.floor]
            arms = {"old prior": lambda x, r=shipped: x["exp"][ykey] * r,
                    "refit global": lambda x, r=glob: x["exp"][ykey] * r,
                    "refit+position": lambda x, f=split: x["exp"][ykey] * f(x)}
            print(f"\n  {route} touchdown term, n={len(sel)}, in DK POINTS (6/TD)")
            print(f"    {'arm':16s}{'bias':>9}{'MAE':>9}{'RMSE':>9}")
            errs = {}
            for label, f in arms.items():
                e = [6.0 * (f(x) - _f(x["act"], tkey)) for x in sel]
                errs[label] = e
                print(f"    {label:16s}{statistics.mean(e):9.3f}"
                      f"{statistics.mean(map(abs, e)):9.3f}"
                      f"{(sum(v * v for v in e) / len(e)) ** 0.5:9.3f}")
            base = errs["old prior"]
            for label in ("refit global", "refit+position"):
                d = [a * a - b * b for a, b in zip(base, errs[label])]
                bs = sorted(sum(rng.choices(d, k=len(d))) / len(d) for _ in range(3000))
                lo, hi = bs[75], bs[2924]
                print(f"    MSE gain over the old prior, {label:14s} "
                      f"{statistics.mean(d):+.4f} 95%CI [{lo:+.4f},{hi:+.4f}] "
                      f"{'REAL' if lo > 0 else 'not significant'}")
        print("\n  READ THIS BEFORE QUOTING AN IMPROVEMENT: the gain is in BIAS "
              "and in\n  squared error, NOT in MAE -- MAE gets slightly WORSE. A "
              "touchdown is a\n  0/1/2 count whose median is 0, so MAE is minimised "
              "by a rate biased\n  toward zero. RMSE is the metric that matches what "
              "this number is (a\n  conditional mean), and it is the one that improves.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
