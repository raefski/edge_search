#!/usr/bin/env python3
"""Close the loop on the NHL build: predicted vs actual, from a DK export.

    python3 scripts/nhl_calibration.py data/contest-standings-<id>.csv
    python3 scripts/nhl_calibration.py data/contest-standings-<id>.csv --fit-ownership

ONE CONTEST AT A TIME: a cash board and a GPP board concentrate differently
(DFS_STATUS.md; every sport here has confirmed it), so never pool them.

Grades the simulator three ways against the real DK points in the export --
the MEAN (bias, MAE), the ORDER (Spearman), and the SPREAD (the share of
players under their simulated floor should be ~25%, over their 95th-percentile
ceiling ~5%) -- and, with --fit-ownership, sweeps the field model's value /
salary / PP1 weights against the export's real ownership.
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
from edge import dfs_nhl_theory as theory  # noqa: E402
from edge.dfs_contest import parse_contest_file  # noqa: E402
from edge.names import norm  # noqa: E402

LOG = ROOT / "data" / "dfs_proj_log_nhl.csv"


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("file")
    ap.add_argument("--fit-ownership", action="store_true")
    args = ap.parse_args()
    contest = parse_contest_file(args.file)
    (date, gid), logged = best_slate(contest)
    rows = []
    for r in logged:
        real = contest.get(norm(r["player"]))
        rows.append({**r, "salary": float(r["salary"]), "proj": float(r["proj"]),
                     "floor": float(r["floor"]), "ceil": float(r["ceil"]),
                     "own": float(r["own"] or 0), "pos": "G" if r["dk_pos"] == "G" else
                     ("D" if r["dk_pos"] == "D" else ("C" if r["dk_pos"] == "C" else "W")),
                     "actual": real["fpts"] if real else None,
                     "own_actual": real["pct_drafted"] if real else 0.0})
    scored = [r for r in rows if r["actual"] is not None]
    print(f"{Path(args.file).name} -> {date} draft group {gid}: {len(scored)} of {len(rows)} "
          f"logged players on the board")
    for label, grp in (("skaters", [r for r in scored if r["pos"] != "G"]),
                       ("goalies", [r for r in scored if r["pos"] == "G"])):
        if len(grp) < 3:
            continue
        err = [r["proj"] - r["actual"] for r in grp]
        print(f"  {label:8} n={len(grp):3}  bias {statistics.fmean(err):+.2f}  "
              f"MAE {statistics.fmean(abs(e) for e in err):.2f}  "
              f"rho {spearman([r['proj'] for r in grp], [r['actual'] for r in grp]):+.3f}  "
              f"below floor {sum(r['actual'] < r['floor'] for r in grp) / len(grp):.0%} (25)  "
              f"above ceiling {sum(r['actual'] > r['ceil'] for r in grp) / len(grp):.0%} (5)")
    imputed = [r for r in scored if r.get("imputed") and r["pos"] != "G"]
    priced = [r for r in scored if not r.get("imputed") and r["pos"] != "G"]
    for label, grp in (("priced by props", priced), ("estimated", imputed)):
        if len(grp) >= 5:
            print(f"  {label:16} n={len(grp):3}  bias "
                  f"{statistics.fmean(r['proj'] - r['actual'] for r in grp):+.2f}")
    if not args.fit_ownership:
        return 0

    own_err = statistics.fmean(abs(r["own"] - r["own_actual"]) for r in rows)
    print(f"\nOWNERSHIP  shipped prior: MAE {own_err:.2f}  rank "
          f"{spearman([r['own'] for r in rows], [r['own_actual'] for r in rows]):+.3f}  "
          f"(summed actual {sum(r['own_actual'] for r in rows):.0f}%)")
    best = None
    for vw in [x / 10 for x in range(0, 21)]:
        for sw in [x / 10 for x in range(0, 21)]:
            for pp in (0.0, 0.2, 0.4, 0.6, 0.8):
                theory.VALUE_WEIGHT, theory.SALARY_WEIGHT, theory.PP1_BONUS = vw, sw, pp
                trial = [dict(r) for r in rows]
                theory.add_ownership(trial)
                err = statistics.fmean(abs(t["own"] - r["own_actual"]) for t, r in zip(trial, rows))
                if best is None or err < best[0]:
                    best = (err, vw, sw, pp)
    err, vw, sw, pp = best
    print(f"  best fit here: MAE {err:.2f}  VALUE_WEIGHT={vw}  SALARY_WEIGHT={sw}  PP1_BONUS={pp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
