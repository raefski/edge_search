#!/usr/bin/env python3
"""Benchmark this build's NFL projections + ownership against Dan's Projections.

    python3 scripts/dfs_compare_dan.py Dan_Projections_Main_DK_2026-09-27.csv
    python3 scripts/dfs_compare_dan.py Dan_Projections_Main_DK_2026-09-27.csv --date 2026-09-27 --gid 153769

WHY THIS EXISTS
Adam's stated goal (2026-09-14) is an in-house NFL projection + ownership
model as good as Dan's Projections -- a paid third-party sheet -- and
eventually independent of it. That is not a graded backtest (the games have
not been played), it is a HEAD-TO-HEAD on the same player pool: where do the
two projections and the two ownership estimates agree, where do they not, and
-- the case that actually costs money -- which players does Dan project that
this build's pool is MISSING entirely, because a book pulled a prop or never
posted one.

THIS FILE IS NEVER COMMITTED. Dan's Projections is a paid product and this
repo is public (see .gitignore); this script reads whatever copy is on disk
and writes nothing back into it.

THE JOIN
Both sides key on edge.dfs.SalaryLookup (exact name first, suffix-free
fallback) -- the same fix that recovered James Cook III et al. from the NFL
pool on 2026-09-27, and it matters here too: Dan's sheet and this build's log
do not always agree on "Jr."/"III" either.
"""
from __future__ import annotations

import argparse
import csv
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.dfs import SalaryLookup, norm  # noqa: E402

PROJ_LOG = ROOT / "data" / "dfs_proj_log_nfl.csv"
#: Dan's file has no DK Classic use for kickers; DST needs no salary match.
SKIP_POS = {"K"}


def load_dan(path: str) -> dict:
    out = {}
    with open(path, newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            pos = (row.get("Pos") or "").strip()
            if pos in SKIP_POS:
                continue
            name = (row.get("Player") or "").strip()
            if not name:
                continue

            def f(col):
                try:
                    return float(row.get(col) or "")
                except ValueError:
                    return None

            out[name] = {
                "name": name, "team": row.get("Team"), "pos": pos,
                "salary": f("DK Salary"), "proj": f("DK Proj"),
                "own": f("Est Own%"), "leverage": f("Leverage"),
                "opt_rate": f("Opt Rate"), "floor": f("DK Floor"),
                "p50": f("DK P50"), "ceil": f("DK P95"),
                "boom_prob": f("Boom Prob"),
            }
    return out


def load_our_log(date: str | None, gid: str | None) -> tuple[str, str, dict]:
    """(date, gid, {name: row}) for the slate to compare -- the most recent
    logged one unless pinned, since Dan's file names a single date but this
    build's log holds every slate ever persisted."""
    if not PROJ_LOG.exists():
        print(f"No {PROJ_LOG.relative_to(ROOT)} yet -- build the slate first "
              "(scripts/dfs_lineups_nfl.py).", file=sys.stderr)
        raise SystemExit(1)
    rows = list(csv.DictReader(PROJ_LOG.open(newline="")))
    if date is None or gid is None:
        key = max({(r["date"], r["gid"]) for r in rows})
        date, gid = date or key[0], gid or key[1]
    sel = [r for r in rows if r["date"] == date and r["gid"] == gid]
    if not sel:
        print(f"No logged rows for {date} gid {gid}. Logged slates: "
              + ", ".join(sorted({f'{r["date"]}/{r["gid"]}' for r in rows})),
              file=sys.stderr)
        raise SystemExit(1)
    return date, gid, {r["player"]: r for r in sel}


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _stats(pairs: list[tuple[float, float]]) -> dict | None:
    if len(pairs) < 3:
        return None
    ours = [a for a, _ in pairs]
    dan = [b for _, b in pairs]
    errs = [o - d for o, d in pairs]
    mo, md = statistics.fmean(ours), statistics.fmean(dan)
    num = sum((o - mo) * (d - md) for o, d in pairs)
    den = (sum((o - mo) ** 2 for o in ours) * sum((d - md) ** 2 for d in dan)) ** 0.5
    return {"n": len(pairs), "ours": mo, "dan": md, "bias": statistics.fmean(errs),
            "mae": statistics.fmean(abs(e) for e in errs),
            "r": (num / den) if den else float("nan")}


def show(tag: str, pairs: list[tuple[float, float]]) -> None:
    res = _stats(pairs)
    if res is None:
        print(f"  {tag:<20} too few paired players")
        return
    print(f"  {tag:<20} n={res['n']:<4} ours {res['ours']:6.2f}  dan {res['dan']:6.2f}"
          f"  bias {res['bias']:+6.2f}  MAE {res['mae']:5.2f}  r {res['r']:.3f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dan_file")
    ap.add_argument("--date", default=None, help="force our slate's date")
    ap.add_argument("--gid", default=None, help="force our slate's draft group")
    ap.add_argument("--top", type=int, default=12,
                    help="how many biggest disagreements / gaps to print")
    args = ap.parse_args()

    dan = load_dan(args.dan_file)
    date, gid, ours = load_our_log(args.date, args.gid)
    print(f"comparing {len(ours)} of our players (slate {date} gid {gid}) "
          f"against {len(dan)} of Dan's")

    lookup = SalaryLookup({norm(n): v for n, v in dan.items()})
    joined = []
    for name, row in ours.items():
        d = lookup.get(name)
        if d is None:
            continue
        joined.append({"name": name, "pos": row["dk_pos"], "team": row["team"],
                       "our_proj": _f(row["proj"]), "dan_proj": d["proj"],
                       "our_own": _f(row["own"]), "dan_own": d["own"],
                       "our_lev": _f(row["leverage"]), "dan_lev": d["leverage"],
                       "our_sal": _f(row["salary"]), "dan_sal": d["salary"]})
    matched_dan = {j["name"] for j in joined}
    only_ours = [r for n, r in ours.items() if n not in matched_dan]
    dan_names_matched = set()
    for j in joined:
        d = lookup.get(j["name"])
        if d:
            dan_names_matched.add(d["name"])
    only_dan = [d for d in dan.values() if d["name"] not in dan_names_matched]

    print(f"\njoined {len(joined)} players; {len(only_ours)} of ours not in Dan's "
          f"sheet; {len(only_dan)} of Dan's not in our pool")

    print("\nPROJECTION (DK points)")
    show("all", [(j["our_proj"], j["dan_proj"]) for j in joined
                 if j["our_proj"] is not None and j["dan_proj"] is not None])
    for pos in ("QB", "RB", "WR", "TE"):
        sel = [j for j in joined if j["pos"] == pos and j["our_proj"] is not None
              and j["dan_proj"] is not None]
        show(pos, [(j["our_proj"], j["dan_proj"]) for j in sel])

    print("\nOWNERSHIP (percent)")
    show("all", [(j["our_own"], j["dan_own"]) for j in joined
                 if j["our_own"] is not None and j["dan_own"] is not None])
    for pos in ("QB", "RB", "WR", "TE"):
        sel = [j for j in joined if j["pos"] == pos and j["our_own"] is not None
              and j["dan_own"] is not None]
        show(pos, [(j["our_own"], j["dan_own"]) for j in sel])

    # Salary is common ground truth -- a large disagreement there means a
    # STALE salary on one side, not a modelling difference.
    sal_mismatch = [j for j in joined if j["our_sal"] and j["dan_sal"]
                    and abs(j["our_sal"] - j["dan_sal"]) >= 100]
    if sal_mismatch:
        print(f"\n  ! {len(sal_mismatch)} players have a DIFFERENT DK salary on "
              "each side (stale data on one of them):")
        for j in sal_mismatch[:6]:
            print(f"      {j['name']:<24} ours ${j['our_sal']:.0f}  "
                  f"dan ${j['dan_sal']:.0f}")

    print(f"\nBIGGEST PROJECTION DISAGREEMENTS (top {args.top} by |diff|)")
    diffs = sorted((j for j in joined if j["our_proj"] is not None
                    and j["dan_proj"] is not None),
                   key=lambda j: -abs(j["our_proj"] - j["dan_proj"]))
    print(f"  {'player':<24}{'pos':<4}{'ours':>7}{'dan':>7}{'diff':>7}")
    for j in diffs[:args.top]:
        d = j["our_proj"] - j["dan_proj"]
        print(f"  {j['name'][:23]:<24}{j['pos']:<4}{j['our_proj']:7.1f}"
              f"{j['dan_proj']:7.1f}{d:+7.1f}")

    print(f"\nBIGGEST OWNERSHIP DISAGREEMENTS (top {args.top} by |diff|)")
    odiffs = sorted((j for j in joined if j["our_own"] is not None
                     and j["dan_own"] is not None),
                    key=lambda j: -abs(j["our_own"] - j["dan_own"]))
    print(f"  {'player':<24}{'pos':<4}{'ours%':>7}{'dan%':>7}{'diff':>7}")
    for j in odiffs[:args.top]:
        d = j["our_own"] - j["dan_own"]
        print(f"  {j['name'][:23]:<24}{j['pos']:<4}{j['our_own']:7.1f}"
              f"{j['dan_own']:7.1f}{d:+7.1f}")

    # The expensive gap: a player Dan projects highly whom our pool never
    # heard of -- almost always a prop the scraped books never posted.
    missing = sorted((d for d in only_dan if (d["proj"] or 0) >= 8.0),
                     key=lambda d: -(d["proj"] or 0))
    if missing:
        print(f"\nDAN PROJECTS {len(missing)} PLAYERS (proj >= 8) THIS POOL HAS NO "
              "PRICE FOR AT ALL -- no scraped prop, so no projection, no lineup "
              "eligibility:")
        print(f"  {'player':<24}{'pos':<4}{'team':<5}{'dan proj':>9}{'dan own%':>9}")
        for d in missing[:args.top]:
            own = f"{d['own']:.1f}" if d["own"] is not None else "?"
            print(f"  {d['name'][:23]:<24}{d['pos']:<4}{(d['team'] or ''):<5}"
                  f"{d['proj']:9.1f}{own:>9}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
