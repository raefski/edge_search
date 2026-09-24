#!/usr/bin/env python3
"""Close the loop on the NASCAR build: predicted vs actual, from a DK export.

    python3 scripts/nascar_calibration.py                    # every export found
    python3 scripts/nascar_calibration.py data/contest-standings-1234.csv
    python3 scripts/nascar_calibration.py --fit-ownership

WHY ONE NASCAR CONTEST IS WORTH MORE THAN ONE FOOTBALL CONTEST
The whole field picks six drivers out of about thirty-seven. A single export
therefore pins down the ownership curve across essentially the entire player
pool, where an NFL export covers a few hundred players thinly. Everything in
edge/dfs_nascar_theory.py's field model is a prior and has never been fitted
against anything, so the first export is the most valuable file this build will
ever receive.

WHAT ELSE IT CHECKS, WHICH IS THE MORE INTERESTING HALF
The contest file carries each driver's REAL DK points, so it grades the
simulator directly and in three separate ways:

  * the MEAN. Is the projection unbiased?
  * the SPREAD. The simulator produces a floor and a ceiling per driver; over
    enough races the realised scores should land inside them at roughly the
    right rate -- about 25% below the floor and about 10% above the ceiling,
    since those are the 25th and 90th percentiles by construction. A simulator
    whose intervals are too NARROW is the dangerous failure, because it makes
    every GPP lineup look safer than it is, and the mean can be perfect while
    that is badly wrong.
  * the ORDER. Spearman correlation between projected and actual, which is
    what a lineup actually depends on.

THE PROVISIONAL ROWS ARE SEPARATED, AND THAT MATTERS MORE HERE THAN ANYWHERE.
A build made before qualifying used ESTIMATED starting positions, so its place
differential -- most of the scoring for most of a lineup -- was a guess.
Pooling those rows with post-qualifying ones would blame the simulator for a
missing input. The forward-test log records which it was, and this splits on it.
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import dfs_nascar_theory as theory   # noqa: E402
from edge.dfs_contest import parse_contest_file  # noqa: E402
from edge.names import norm                    # noqa: E402

PROJ_LOG = ROOT / "data" / "dfs_proj_log_nascar.csv"


def load_proj_log() -> dict:
    if not PROJ_LOG.exists():
        return {}
    out: dict = collections.defaultdict(dict)
    with PROJ_LOG.open(newline="") as fh:
        for row in csv.DictReader(fh):
            out[row["date"]][norm(row["driver"])] = row
    return dict(out)


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def best_date(contest: dict, log: dict) -> tuple[str | None, int]:
    scores = sorted(((len(set(contest) & set(drivers)), date)
                     for date, drivers in log.items()), reverse=True)
    if not scores or scores[0][0] == 0:
        return None, 0
    return scores[0][1], scores[0][0]


def _spearman(pairs) -> float:
    if len(pairs) < 3:
        return float("nan")

    def rank(vals):
        order = sorted(range(len(vals)), key=lambda i: vals[i])
        r = [0.0] * len(vals)
        for pos, i in enumerate(order):
            r[i] = float(pos)
        return r

    a = rank([p for p, _ in pairs])
    b = rank([q for _, q in pairs])
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den else float("nan")


def grade(rows: list[dict], tag: str) -> None:
    if len(rows) < 5:
        print(f"  {tag:<26} too few drivers matched")
        return
    proj = [r["proj"] for r in rows]
    act = [r["actual"] for r in rows]
    errs = [p - a for p, a in zip(proj, act)]
    below = sum(1 for r in rows if r["actual"] < r["floor"]) / len(rows)
    above = sum(1 for r in rows if r["actual"] > r["ceil"]) / len(rows)
    print(f"  {tag:<26}n={len(rows):<4} proj {statistics.fmean(proj):6.1f}  "
          f"actual {statistics.fmean(act):6.1f}  bias {statistics.fmean(errs):+6.1f}  "
          f"MAE {statistics.fmean(abs(e) for e in errs):5.1f}  "
          f"rho {_spearman(list(zip(proj, act))):+.3f}")
    print(f"  {'':<26}below floor {100 * below:5.1f}% (target 25)   "
          f"above ceiling {100 * above:5.1f}% (target 10)")


def fit_ownership(rows: list[dict]) -> None:
    """Sweep the field model against what the field actually did."""
    print("\nOWNERSHIP")
    summed = sum(r["own_actual"] for r in rows)
    print(f"  summed actual ownership {summed:.0f}%  -> "
          f"{summed / 100:.2f} lineups' worth (must be ~6 for a single-entry "
          f"board; more means multi-entry)")

    best = None
    for gamma in [x / 20.0 for x in range(6, 61)]:
        for vw in (0.4, 0.5, 0.6, 0.7, 0.8, 1.0):
            pool = [dict(r, proj=r["proj"], ceil=r["ceil"], salary=r["salary"])
                    for r in rows]
            theory.add_ownership(pool, gamma=gamma, value_weight=vw,
                                 ceiling_weight=1.0 - vw)
            err = statistics.fmean(abs(p["own"] - r["own_actual"])
                                  for p, r in zip(pool, rows))
            if best is None or err < best[0]:
                best = (err, gamma, vw)
    err, gamma, vw = best
    print(f"  best fit: OWNERSHIP_GAMMA={gamma:.2f}  VALUE_WEIGHT={vw:.2f}  "
          f"CEILING_WEIGHT={1 - vw:.2f}   (mean abs error {err:.1f} points)")
    print(f"  currently shipped: {theory.OWNERSHIP_GAMMA:.2f} / "
          f"{theory.VALUE_WEIGHT:.2f} / {theory.CEILING_WEIGHT:.2f}")

    worst = sorted(rows, key=lambda r: -abs((r["own"] or 0) - r["own_actual"]))[:6]
    print("\n  biggest ownership misses (predicted vs actual):")
    for r in worst:
        print(f"    {r['driver'][:24]:<26}P{r['start']:<4}"
              f"pred {(r['own'] or 0):5.1f}%  actual {r['own_actual']:5.1f}%")
    print("\n  A cash board and a GPP board concentrate very differently -- do "
          "not pool them.\n  Re-run per contest type.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*")
    ap.add_argument("--date", default=None)
    ap.add_argument("--fit-ownership", action="store_true")
    args = ap.parse_args()

    log = load_proj_log()
    if not log:
        print(f"No {PROJ_LOG.relative_to(ROOT)} yet. Build a slate first "
              f"(scripts/dfs_lineups_nascar.py or the app).", file=sys.stderr)
        return 1
    print(f"forward-test log: {len(log)} race(s) — {', '.join(sorted(log))}")

    files = args.files or sorted(
        glob.glob(str(ROOT / "data" / "contest-standings-*.csv")))
    pooled: list[dict] = []
    for path in files:
        contest = parse_contest_file(path)
        date, overlap = ((args.date, len(set(contest) & set(log.get(args.date, {}))))
                         if args.date else best_date(contest, log))
        if not date or overlap < 5:
            print(f"\n{Path(path).name}: no NASCAR race matches "
                  f"({overlap} drivers overlap) — skipped")
            continue
        joined = []
        for key, row in log[date].items():
            real = contest.get(key)
            proj = _f(row.get("proj"))
            if not real or proj is None:
                continue
            joined.append({
                "driver": row["driver"], "start": row.get("start", ""),
                "salary": _f(row.get("salary"), 0.0), "proj": proj,
                "floor": _f(row.get("floor"), 0.0), "ceil": _f(row.get("ceil"), 0.0),
                "own": _f(row.get("own")), "own_actual": real["pct_drafted"],
                "actual": real["fpts"],
                "provisional": str(row.get("start_estimated", "")) == "1",
            })
        if not joined:
            continue
        pooled.extend(joined)
        print(f"\n{Path(path).name}  ->  race {date}   "
              f"{len(joined)} drivers joined of {len(contest)} on the board")
        grade(joined, "all drivers")
        prov = [r for r in joined if r["provisional"]]
        real = [r for r in joined if not r["provisional"]]
        if prov and real:
            grade(prov, "PROVISIONAL grid")
            grade(real, "real qualifying grid")
        elif prov:
            print("  (this build used an ESTIMATED grid — its place "
                  "differential was a guess)")

    if args.fit_ownership and pooled:
        fit_ownership([r for r in pooled if r["own"] is not None])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
