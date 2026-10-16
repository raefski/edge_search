#!/usr/bin/env python3
"""Grade an NBA or college basketball build against a DK contest export.

    python3 scripts/nba_calibration.py data/contest-standings-N.csv
    python3 scripts/nba_calibration.py data/contest-standings-N.csv --fit-ownership
    python3 scripts/nba_calibration.py data/contest-standings-N.csv --sport ncaab

THE EXPORT IS MATCHED TO A BUILD BY DRAFT GROUP, never by "the slate whose
players overlap most" (CLAUDE.md -- graders have silently matched the wrong
night that way). The draft group comes from data/contest_meta.json, which
`harness run import-contests` writes when it files an export; an export it has
not tagged is refused rather than guessed.

Reports n, bias, MAE, rank correlation and the tails against the simulated
floor and ceiling; for college, also per projection source (props / fppg /
salary -- the board's three tiers, which says whether the fallbacks earn their
place). The NBA log carries no source column: every NBA row is minutes x rate.
ONE CONTEST IS ONE SLATE: below ~5 slates this is a reading, not a fit.

--fit-ownership grid-searches VALUE_WEIGHT / SALARY_WEIGHT on THIS contest and
prints the best pair; it ships nothing. Cash and GPP contests are fitted
separately (CLAUDE.md: never pooled) -- the type comes from contest_meta.json.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import dfs_nba_theory, dfs_ncaab_theory, dfs_opt_nba  # noqa: E402
from edge.dfs_contest import parse_contest_file  # noqa: E402
from edge.names import norm  # noqa: E402


def _rank(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    for k, i in enumerate(order):
        r[i] = float(k)
    return r


def spearman(a, b) -> float:
    if len(a) < 3:
        return float("nan")
    ra, rb = _rank(a), _rank(b)
    ma, mb = statistics.fmean(ra), statistics.fmean(rb)
    num = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    den = (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** 0.5
    return num / den if den else float("nan")


def contest_meta(path: str) -> dict | None:
    cid = Path(path).stem.rsplit("-", 1)[-1]
    try:
        return json.loads((ROOT / "data" / "contest_meta.json").read_text()).get(cid)
    except (OSError, ValueError):
        return None


def logged_build(sport: str, gid) -> list[dict]:
    log = ROOT / "data" / f"dfs_proj_log_{sport}.csv"
    if not log.exists():
        return []
    with log.open(newline="") as fh:
        return [r for r in csv.DictReader(fh) if str(r["gid"]) == str(gid)]


def line(label: str, grp: list[dict], pcts: tuple = (25.0, 90.0)) -> str:
    lo, hi = pcts
    err = [r["proj"] - r["actual"] for r in grp]
    return (f"  {label:10} n={len(grp):3}  bias {statistics.fmean(err):+6.2f}  "
            f"MAE {statistics.fmean(abs(e) for e in err):5.2f}  "
            f"rho {spearman([r['proj'] for r in grp], [r['actual'] for r in grp]):+.3f}  "
            f"below floor {sum(r['actual'] < r['floor'] for r in grp) / len(grp):4.0%} ({lo:.0f}%)  "
            f"above ceil {sum(r['actual'] > r['ceil'] for r in grp) / len(grp):4.0%} ({100 - hi:.0f}%)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("file")
    ap.add_argument("--sport", choices=("nba", "ncaab"), default=None,
                    help="default: from contest_meta.json")
    ap.add_argument("--gid", type=int, default=None,
                    help="draft group, when contest_meta.json has not tagged this export")
    ap.add_argument("--fit-ownership", action="store_true")
    args = ap.parse_args()

    meta = contest_meta(args.file) or {}
    gid = args.gid or meta.get("draft_group")
    sport = args.sport or {"NBA": "nba", "CBB": "ncaab", "NCAAB": "ncaab"}.get(
        str(meta.get("sport", "")).upper())
    if not gid or not sport:
        print("This export is not tagged with a draft group / sport in data/contest_meta.json. "
              "Run `python3 -m harness run import-contests`, or pass --gid and --sport.")
        return 2
    logged = logged_build(sport, gid)
    if not logged:
        print(f"No {sport} build was logged for draft group {gid} "
              f"(data/dfs_proj_log_{sport}.csv). Nothing to grade.")
        return 1
    contest = parse_contest_file(args.file)
    rows = []
    for r in logged:
        real = contest.get(norm(r["player"]))
        rows.append({**r, "salary": float(r["salary"]), "proj": float(r["proj"]),
                     "floor": float(r["floor"]), "ceil": float(r["ceil"]),
                     "own": float(r["own"] or 0), "group": (r["dk_pos"] or "").split("/")[0],
                     # the NBA theory groups by primary position the way the build does
                     "pos": sorted(dfs_opt_nba.eligible(r["dk_pos"]) or {"UTIL"},
                                   key=["PG", "SG", "SF", "PF", "C", "UTIL"].index)[0],
                     "promoted": r.get("promoted") in ("1", "True", "true"),
                     "actual": real["fpts"] if real else None,
                     "own_actual": real["pct_drafted"] if real else 0.0})
    scored = [r for r in rows if r["actual"] is not None]
    th = dfs_ncaab_theory if sport == "ncaab" else dfs_nba_theory
    pcts = (th.CASH_PCT, th.GPP_PCT)
    kind = meta.get("type") or "unknown type"
    print(f"{Path(args.file).name}: {sport} draft group {gid} ({kind}, field "
          f"{meta.get('field', '?')}) -- {len(scored)} of {len(rows)} logged players drafted "
          f"or scored. ONE slate: a reading, not a fit.")
    if len(scored) >= 5:
        print(line("all", scored, pcts))
    for src in ("props", "fppg", "salary"):
        grp = [r for r in scored if r.get("proj_source") == src]
        if len(grp) >= 5:
            print(line(src, grp, pcts))
    # Promoted starters (NBA: a bench player starting tonight) graded on their own rows.
    promo = [r for r in scored if r.get("promoted")]
    if promo:
        print(line("promoted", promo, pcts) if len(promo) >= 5 else
              f"  promoted   n={len(promo)} -- too few to read")
    if not args.fit_ownership:
        return 0

    if kind == "unknown type":
        print("\nOwnership not fitted: the contest type (cash/gpp) is unknown, and the two are "
              "never pooled.")
        return 0
    # Each league fits its OWN theory: college's groups by G/F, the NBA's by
    # primary position with its promoted-starter bump.
    def refit(trial, vw, sw):
        if sport == "ncaab":
            dfs_ncaab_theory.add_ownership(trial, weights=(vw, sw))
        else:
            dfs_nba_theory.add_ownership(trial, weights=(vw, sw))
    own_err = statistics.fmean(abs(r["own"] - r["own_actual"]) for r in rows)
    print(f"\nOWNERSHIP ({kind})  shipped prior: MAE {own_err:.2f}  rank "
          f"{spearman([r['own'] for r in rows], [r['own_actual'] for r in rows]):+.3f}")
    best = None
    for vw in [x / 10 for x in range(0, 26)]:
        for sw in [x / 10 for x in range(0, 21)]:
            trial = [dict(r) for r in rows]
            refit(trial, vw, sw)
            err = statistics.fmean(abs(t["own"] - r["own_actual"]) for t, r in zip(trial, rows))
            if best is None or err < best[0]:
                best = (err, vw, sw)
    err, vw, sw = best
    print(f"  best fit on this contest: MAE {err:.2f}  VALUE_WEIGHT={vw}  SALARY_WEIGHT={sw}"
          f"  (n=1 contest -- do not ship below ~5 slates of the same type)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
