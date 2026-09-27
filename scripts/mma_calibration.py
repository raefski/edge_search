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


def actuals_from_exports(rows: list[dict]) -> dict:
    """{fighter: (DK points, None, False)} from any export played on this board.

    DraftKings' own scoring, available the night of the card -- a day before
    UFCStats has it. It does not say who WON, so the win-probability check
    waits for UFCStats."""
    names = {norm(r["fighter"]) for r in rows}
    out: dict = {}
    for path in sorted(glob.glob(str(ROOT / "data" / "contest-standings-*.csv"))):
        board = parse_contest_file(path)
        # A contest lists only fighters someone drafted, so one export can miss
        # a fighter another has: take the union of every export on this board.
        # Every fighter the export lists must be on this board. A fighter's
        # points are the same on every slate that night, so a late-slate
        # export legitimately grades those fights on the main-card board too.
        if board and set(board) <= names:
            for k, v in board.items():
                out.setdefault(k, (v["fpts"], None, False))
    return out


def grade(dates: list[str] | None) -> None:
    log = load_log()
    rows = []
    by_slate: dict = {}
    for (date, gid), rs in sorted(log.items()):
        if dates and date not in dates:
            continue
        act = actuals_for(date)
        source = "UFCStats"
        by_slate[(date, gid)] = act
        if not act:
            act = actuals_from_exports(rs)
            source = "DK contest export"
            by_slate[(date, gid)] = act
        if not act:
            print(f"{date} gid {gid}: no results yet -- UFCStats lags the event by "
                  "about a day (run with --refresh) and no contest export matches")
            continue
        got = 0
        for r in rs:
            a = act.get(r["key"]) or act.get(norm(r["fighter"]))
            if a is None:
                continue
            got += 1
            rows.append({**r, "actual": a[0], "won": a[1], "void": a[2]})
        print(f"{date} gid {gid}: {got}/{len(rs)} logged fighters graded from {source}")
    if not rows:
        return
    # One fight is one data point: a fighter on both the main card and the
    # late Captain slate is logged twice that night. Keep the first.
    seen, uniq = set(), []
    for r in rows:
        k = (r["date"], norm(r["fighter"]))
        if k not in seen:
            seen.add(k)
            uniq.append(r)
    rows = uniq
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
    # An export gives points, not results: the winner is unknown there.
    wp = [(_f(r["p_win"]), 1.0 if r["won"] else 0.0) for r in rows
          if not r["void"] and r["won"] is not None]
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
        act = by_slate.get((date, gid)) or {}
        by_mode = collections.defaultdict(list)
        for r in csv.DictReader(f.open()):
            if r.get("gid") and r["gid"] != gid:
                continue
            a = act.get(mma.fighter_key(r["fighter"])) or act.get(norm(r["fighter"]))
            mult = dfs_opt_mma.CPT_MULT if str(r.get("cpt")) == "1" else 1.0
            label = ("CPT " if mult > 1 else "") + r["fighter"]
            by_mode[r["mode"]].append((label, mult * a[0] if a else None))
        for mode, fs in by_mode.items():
            tot = sum(x for _, x in fs if x is not None)
            miss = sum(1 for _, x in fs if x is None)
            print(f"\n  {date} gid {gid} {mode.upper()} (last logged build) scored "
                  f"{tot:.1f}{f' ({miss} not graded)' if miss else ''}: "
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


def _match_board(contest: dict, boards: dict):
    """(key, board) of the logged board this export was played on, or (None, {})."""
    # Jaccard, not raw overlap: every late-slate (Captain) fighter is also on
    # the main card, so raw overlap tied the two boards and picked the wrong
    # one. A board fighter missing from the export has to count against it.
    def score(kv):
        b = set(kv[1])
        return len(set(contest) & b) / max(1, len(set(contest) | b))
    best = max(boards.items(), key=score, default=(None, {}))
    k, board = best
    if k is None or len(set(contest) & set(board)) < 0.6 * len(board):
        return None, {}
    return k, board


def fit_ownership(paths: list[str]) -> None:
    log = load_log()
    boards = {k: {norm(r["fighter"]): r for r in rs} for k, rs in log.items()}
    fits = collections.defaultdict(list)
    for path in paths:
        contest = parse_contest_file(path)
        k, board = _match_board(contest, boards)
        if k is None:
            continue                                   # not an MMA export we logged
        ctype = _contest_type(path)
        captain = any(v.get("cpt_pct") for v in contest.values())
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
        act_cpt = np.array([contest.get(norm(d["name"]), {}).get("cpt_pct", 0.0)
                            for d in pool])
        sal = np.array([d["salary"] for d in pool])
        if captain:
            # DK's CPT salary is exactly 1.5x (8,000 -> 12,000 on 2026-09-26).
            lineups = dfs_opt_mma.legal_captain_lineups(
                sal, np.round(dfs_opt_mma.CPT_MULT * sal).astype(int))
        else:
            lineups = dfs_opt_mma.legal_lineups(sal)
        shipped_tau = theory.FIELD_TAU_CASH if ctype == "cash" else theory.FIELD_TAU_GPP
        shipped_alpha = theory.PUBLIC_FPPF_WEIGHT
        n_entries = _entries(path)
        print(f"\n{Path(path).name}: {ctype}, {n_entries} entries, "
              f"{'Captain' if captain else 'Classic'}, logged board {k[0]} gid {k[1]}, "
              f"field ownership sums to {actual.sum():.0f}%")
        grid = []
        for alpha in (0.0, 0.15, 0.3, 0.5, 0.7, 1.0):
            theory.PUBLIC_FPPF_WEIGHT = alpha
            for tau in (3, 5, 8, 12, 16, 20, 25, 30, 40, 60):
                own, fl, w = theory.field_model(pool, lineups, opp, tau, captain)
                cpt_mae = (float(np.mean(np.abs(theory.captain_ownership(len(pool), fl, w)
                                                - act_cpt))) if captain else float("nan"))
                grid.append((float(np.mean(np.abs(own - actual))), alpha, tau, cpt_mae))
        theory.PUBLIC_FPPF_WEIGHT = shipped_alpha
        prior = min(grid, key=lambda g: (abs(g[1] - shipped_alpha), abs(g[2] - shipped_tau)))
        grid.sort()
        mae, alpha, tau, cpt_mae = grid[0]
        fits[ctype].append({"mae": mae, "alpha": alpha, "tau": tau, "n": n_entries,
                            "file": Path(path).name})
        print(f"  shipped: fppf weight {shipped_alpha}, tau {shipped_tau} -> ownership "
              f"MAE {prior[0]:.1f} pts" + (f", CPT MAE {prior[3]:.1f}" if captain else ""))
        print(f"  best:    fppf weight {alpha}, tau {tau} -> ownership MAE {mae:.1f} pts"
              + (f", CPT MAE {cpt_mae:.1f}" if captain else ""))
        for g in grid[1:4]:
            print(f"           fppf weight {g[1]}, tau {g[2]} -> {g[0]:.1f}")
        theory.PUBLIC_FPPF_WEIGHT = alpha
        own, _fl, _w = theory.field_model(pool, lineups, opp, tau, captain)
        theory.PUBLIC_FPPF_WEIGHT = shipped_alpha
        print(f"    {'fighter':<22}{'field':>7}{'fitted':>8}")
        for i in np.argsort(-actual):
            print(f"    {pool[i]['name'][:21]:<22}{actual[i]:6.1f}%{own[i]:7.1f}%")
    for ctype, fs in fits.items():
        print(f"\n{ctype}: {len(fs)} export(s)")
        for f in fs:
            print(f"   {f['file']}: {f['n']} entries -> fppf weight {f['alpha']}, "
                  f"tau {f['tau']} (MAE {f['mae']:.1f})")
    if not fits:
        print("No MMA contest export matched a logged board. Export contest standings "
              "from DraftKings into data/ (contest-standings-<id>.csv) and tag each "
              "one cash/gpp in data/contest_meta.json.")


def _entries(path: str) -> int:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return sum(1 for r in csv.DictReader(fh) if (r.get("EntryId") or "").strip())


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
