#!/usr/bin/env python3
"""Close the loop on the MMA build: the forward log against what happened.

    python3 scripts/mma_calibration.py --grade              # every logged card UFCStats has
    python3 scripts/mma_calibration.py --grade 2026-09-26
    python3 scripts/mma_calibration.py --fit-ownership      # every MMA contest export in data/
    python3 scripts/mma_calibration.py --fit-ownership data/contest-standings-1234.csv

TWO LOOPS, BECAUSE THE TWO HALVES OF THIS BUILD HAVE DIFFERENT GROUND TRUTH

--grade needs NO contest export. Actual DK points are recomputed from UFCStats
(the same scoring that reproduces DK's own FPPF to the decimal for 15 of 18
fighters), so every logged card grades itself once the mirror has it -- about a
day after the event. It checks the three things the backtest checked on 3,454
historical fighter-fights, now on real forward builds:
  * the MEAN     MAE and bias against actual, next to DK's FPPF as a baseline
  * the SPREAD   share of actual scores below the logged p25 (target 25%) and
                 above the logged p90 (target 10%); a spread too NARROW is the
                 dangerous failure, it makes every lineup look safer than it is
  * the MARKET   Brier score of the logged win probabilities

--fit-ownership needs DK contest-standings exports, and it is the loop that
matters most: the field model in edge/dfs_mma_theory.py (FIELD_TAU_GPP,
FIELD_TAU_CASH, PUBLIC_FPPF_WEIGHT) is a PRIOR. A card is ~24 fighters and a
lineup is six, so one export pins the ownership curve across the whole card.
The fit reruns the SAME lineup-softmax the app uses, over the logged board,
for a grid of (tau, fppf weight), and reports the pair that reproduces the
real ownership best. Cash and GPP are fitted SEPARATELY (data/contest_meta.json
tags each export) -- small-field cash concentrates far harder, and pooling them
was already a mistake once in MLB.
"""
from __future__ import annotations

import argparse
import collections
import csv
import glob
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np                                    # noqa: E402

from edge import dfs_mma_theory as theory, dfs_opt_mma, mma   # noqa: E402
from edge.dfs_contest import parse_contest_file       # noqa: E402
from edge.names import norm                           # noqa: E402

PROJ_LOG = ROOT / "data" / "dfs_proj_log_mma.csv"
META = ROOT / "data" / "contest_meta.json"


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def load_log() -> dict:
    """{(date, gid): [row, ...]}"""
    out: dict = collections.defaultdict(list)
    if PROJ_LOG.exists():
        for r in csv.DictReader(PROJ_LOG.open(newline="")):
            out[(r["date"], r["gid"])].append(r)
    return dict(out)


# ---------------------------------------------------------------------------
# --grade
# ---------------------------------------------------------------------------
def actuals_for(date: str) -> dict:
    """{fighter key: (dk points, won)} for UFC fights on `date` (+1 day, for a
    card that crosses midnight UTC)."""
    import datetime as dt
    d = dt.date.fromisoformat(date)
    out = {}
    for f in mma.fights():
        if f["date"] in (d, d + dt.timedelta(days=1)):
            for me in f["f"]:
                out[me["key"]] = (me["dk"], me["won"], f["nc"] or f["draw"])
                out.setdefault(norm(me["name"]), out[me["key"]])
    return out


def grade(dates: list[str] | None) -> None:
    log = load_log()
    rows = []
    for (date, gid), rs in sorted(log.items()):
        if dates and date not in dates:
            continue
        act = actuals_for(date)
        if not act:
            print(f"{date} gid {gid}: UFCStats has no fights for this date yet "
                  "(the mirror lags the event by about a day) -- run with --refresh")
            continue
        got = 0
        for r in rs:
            a = act.get(r["key"]) or act.get(norm(r["fighter"]))
            if a is None:
                continue
            got += 1
            rows.append({**r, "actual": a[0], "won": a[1], "void": a[2]})
        print(f"{date} gid {gid}: {got}/{len(rs)} logged fighters found in UFCStats")
    if not rows:
        return
    y = np.array([r["actual"] for r in rows])
    p = np.array([_f(r["proj"]) for r in rows])
    print(f"\n  n={len(rows)} fighter-fights")
    print(f"  projection   MAE {np.mean(np.abs(p - y)):6.2f}  bias {np.mean(p - y):+6.2f}  "
          f"corr {np.corrcoef(p, y)[0, 1]:.3f}")
    fp = [(r["actual"], _f(r["dk_fppf"])) for r in rows if _f(r["dk_fppf"]) is not None]
    if fp:
        a, b = np.array([x for x, _ in fp]), np.array([x for _, x in fp])
        print(f"  DK FPPF      MAE {np.mean(np.abs(b - a)):6.2f}  bias {np.mean(b - a):+6.2f}  "
              f"(n={len(fp)}, the number DK prints in its lobby)")
    below = np.mean([r["actual"] < _f(r["floor"]) for r in rows])
    above = np.mean([r["actual"] > _f(r["ceil"]) for r in rows])
    print(f"  spread       {100 * below:.0f}% below p25 (target 25%), "
          f"{100 * above:.0f}% above p90 (target 10%)")
    wp = [(_f(r["p_win"]), 1.0 if r["won"] else 0.0) for r in rows if not r["void"]]
    if wp:
        brier = statistics.fmean((q - o) ** 2 for q, o in wp)
        print(f"  win prob     Brier {brier:.4f} (coin flip 0.25), "
              f"{sum(o for _, o in wp):.0f} wins vs {sum(q for q, _ in wp):.1f} expected")
    print(f"\n  {'fighter':<24}{'proj':>7}{'p25':>6}{'p90':>6}{'actual':>8}{'win%':>7}")
    for r in sorted(rows, key=lambda r: -r["actual"]):
        print(f"  {r['fighter'][:23]:<24}{_f(r['proj']):7.1f}{_f(r['floor']):6.0f}"
              f"{_f(r['ceil']):6.0f}{r['actual']:8.1f}{100 * _f(r['p_win']):6.0f}%"
              f"{'  W' if r['won'] else ''}")
    lp = ROOT / "data"
    for (date, gid), _rs in sorted(log.items()):
        f = lp / f"dfs_lineups_mma_{date}.csv"
        if (dates and date not in dates) or not f.exists():
            continue
        act = actuals_for(date)
        by_mode = collections.defaultdict(list)
        for r in csv.DictReader(f.open()):
            if r.get("gid") and r["gid"] != gid:
                continue
            a = act.get(mma.fighter_key(r["fighter"])) or act.get(norm(r["fighter"]))
            by_mode[r["mode"]].append((r["fighter"], a[0] if a else None))
        for mode, fs in by_mode.items():
            tot = sum(x for _, x in fs if x is not None)
            print(f"\n  {date} {mode.upper()} lineup scored {tot:.1f}: "
                  + ", ".join(f"{n} {x:.0f}" if x is not None else f"{n} ?" for n, x in fs))


# ---------------------------------------------------------------------------
# --fit-ownership
# ---------------------------------------------------------------------------
def _contest_type(path: str) -> str:
    cid = Path(path).stem.split("-")[-1]
    try:
        return json.load(META.open()).get(cid, {}).get("type", "unknown")
    except (OSError, ValueError):
        return "unknown"


def fit_ownership(paths: list[str]) -> None:
    log = load_log()
    boards = {k: {norm(r["fighter"]): r for r in rs} for k, rs in log.items()}
    fits = collections.defaultdict(list)
    for path in paths:
        contest = parse_contest_file(path)
        best = max(boards.items(), key=lambda kv: len(set(contest) & set(kv[1])),
                   default=(None, {}))
        k, board = best
        overlap = len(set(contest) & set(board))
        if k is None or overlap < 0.6 * len(board):
            continue                                   # not an MMA export we logged
        ctype = _contest_type(path)
        rows = list(board.values())
        pool = [{"name": r["fighter"], "salary": int(_f(r["salary"], 0)),
                 "proj": _f(r["proj"], 0.0), "dk_fppf": _f(r["dk_fppf"]),
                 "bout": r["bout"]} for r in rows]
        opp = np.full(len(pool), -1)
        by_bout = collections.defaultdict(list)
        for i, d in enumerate(pool):
            by_bout[d["bout"]].append(i)
        for ids in by_bout.values():
            if len(ids) == 2:
                opp[ids[0]], opp[ids[1]] = ids[1], ids[0]
        actual = np.array([contest.get(norm(d["name"]), {}).get("pct_drafted", 0.0)
                           for d in pool])
        sal = np.array([d["salary"] for d in pool])
        lineups = dfs_opt_mma.legal_lineups(sal)
        print(f"\n{Path(path).name}: {ctype}, logged board {k[0]} gid {k[1]}, "
              f"{overlap}/{len(board)} fighters, field ownership sums to "
              f"{actual.sum():.0f}%")
        grid = []
        for alpha in (0.0, 0.1, 0.15, 0.2, 0.3, 0.45):
            theory.PUBLIC_FPPF_WEIGHT = alpha
            for tau in (3, 4, 5, 6, 8, 10, 12, 15, 20):
                own, _fl, _w = theory.field_model(pool, lineups, opp, tau)
                grid.append((float(np.mean(np.abs(own - actual))), alpha, tau))
        grid.sort()
        mae, alpha, tau = grid[0]
        fits[ctype].append((mae, alpha, tau))
        print(f"  best: fppf weight {alpha}, tau {tau} -> ownership MAE {mae:.2f} pts "
              f"(shipped {theory.FIELD_TAU_GPP if ctype != 'cash' else theory.FIELD_TAU_CASH})")
        theory.PUBLIC_FPPF_WEIGHT = alpha
        own, _fl, _w = theory.field_model(pool, lineups, opp, tau)
        for i in np.argsort(-actual)[:10]:
            print(f"    {pool[i]['name']:<24} actual {actual[i]:5.1f}%  fitted {own[i]:5.1f}%")
    for ctype, fs in fits.items():
        print(f"\n{ctype}: {len(fs)} exports; per-export best (MAE, fppf weight, tau): {fs}")
    if not fits:
        print("No MMA contest export matched a logged board. Export contest standings "
              "from DraftKings into data/ (contest-standings-<id>.csv) and tag each "
              "one cash/gpp in data/contest_meta.json.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grade", nargs="*", metavar="DATE")
    ap.add_argument("--fit-ownership", nargs="*", metavar="EXPORT")
    ap.add_argument("--refresh", action="store_true",
                    help="re-download the UFCStats mirror first")
    args = ap.parse_args()
    if args.refresh:
        print("mirror:", mma.refresh_mirror(force=True))
        mma.fights(refresh=True)
    if args.grade is not None:
        grade(args.grade or None)
    if args.fit_ownership is not None:
        fit_ownership(args.fit_ownership
                      or sorted(glob.glob(str(ROOT / "data" / "contest-standings-*.csv"))))
    if args.grade is None and args.fit_ownership is None:
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
