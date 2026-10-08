#!/usr/bin/env python3
"""Does YouTube buzz improve the NHL ownership model?

    python3 scripts/buzz_fit_nhl.py data/contest-standings-<id>.csv

Loads the contest, matches against the logged projections, and sweeps buzz_gamma
to find if it improves ownership prediction. Uses the same matching logic as
nhl_calibration.py.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs_nhl_theory as theory
from edge.dfs_contest import parse_contest_file
from edge.names import norm

LOG = ROOT / "data" / "dfs_proj_log_nhl.csv"
BUZZ_GAMMAS = [round(0.1 * i, 1) for i in range(0, 31)]


def _rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    for k, i in enumerate(order):
        r[i] = float(k)
    return r


def spearman(a, b) -> float:
    ra, rb = _rank(a), _rank(b)
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else float("nan")


def best_slate(contest: dict) -> tuple[tuple, list[dict]]:
    by = defaultdict(list)
    with LOG.open(newline="") as fh:
        for r in csv.DictReader(fh):
            by[(r["date"], r["gid"])].append(r)
    key = max(by, key=lambda k: sum(norm(r["player"]) in contest for r in by[k]))
    return key, by[key]


def load_buzz() -> dict:
    """Load buzz data: {date: {player_norm: mentions}}"""
    buzz = defaultdict(dict)
    buzz_path = ROOT / "data/buzz_nhl.csv"
    if buzz_path.exists():
        with buzz_path.open(newline="") as fh:
            for r in csv.DictReader(fh):
                buzz[r["date"]][norm(r["player"])] = float(r["mentions"])
    return buzz


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("file")
    args = ap.parse_args()

    contest = parse_contest_file(args.file)
    (date, gid), logged = best_slate(contest)
    buzz_data = load_buzz().get(date, {})

    rows = []
    for r in logged:
        real = contest.get(norm(r["player"]))
        if real:
            rows.append({
                **r,
                "salary": float(r["salary"]),
                "proj": float(r["proj"]),
                "pos": "G" if r["dk_pos"] == "G" else ("D" if r["dk_pos"] == "D" else "C" if r["dk_pos"] == "C" else "W"),
                "actual_own": real["pct_drafted"],
                "buzz": buzz_data.get(norm(r["player"]), 0.0),
                "pp": "PP1" if r.get("pp") == "PP1" else None,
            })

    print(f"{Path(args.file).name} -> {date} draft group {gid}: {len(rows)} of {len(logged)} "
          f"logged players on the contest board")

    if len(rows) < 5:
        print(f"  WARNING: Only {len(rows)} players matched; buzz test inconclusive")
        return 0

    # Group by position and test buzz_gamma values
    print(f"\n  baseline (no buzz) vs contest ownership:")
    by_pos = defaultdict(list)
    for r in rows:
        by_pos[r["pos"]].append(r)

    # Score baseline (no buzz)
    base_pool = [dict(r) for r in rows]
    theory.add_ownership(base_pool, buzz_gamma=0.0)
    base_mae = statistics.fmean(abs(r["actual_own"] - r["own"]) for r in base_pool)
    base_rho = spearman([r["actual_own"] for r in base_pool], [r["own"] for r in base_pool])
    print(f"    MAE {base_mae:6.2f}  rank corr {base_rho:+.3f}")

    # Test different buzz_gammas
    print(f"\n  {'gamma':>5}  {'MAE':>6}  {'rank corr':>9}  {'improvement':>11}")
    best = (base_mae, 0.0)
    for g in BUZZ_GAMMAS:
        test_pool = [dict(r) for r in rows]
        theory.add_ownership(test_pool, buzz_gamma=g)
        m = statistics.fmean(abs(r["actual_own"] - r["own"]) for r in test_pool)
        rho = spearman([r["actual_own"] for r in test_pool], [r["own"] for r in test_pool])
        improvement = base_mae - m
        print(f"  {g:5.1f}  {m:6.2f}  {rho:+9.3f}  {improvement:+10.2f}")
        if m < best[0]:
            best = (m, g)

    if best[1] > 0.0:
        print(f"\n  ✓ buzz helps: gamma {best[1]}, MAE {best[0]:.2f} (gain {base_mae - best[0]:+.2f})")
    else:
        print(f"\n  ✗ buzz doesn't help on this contest (already at best MAE {base_mae:.2f})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
