#!/usr/bin/env python3
"""Close the loop on the college build: predicted vs actual, projections AND
ownership, from a DraftKings contest-standings export.

    python3 scripts/ncaaf_calibration.py                       # every export found
    python3 scripts/ncaaf_calibration.py data/contest-standings-1234.csv
    python3 scripts/ncaaf_calibration.py --fit-ownership       # also sweep the prior

WHY THIS MATTERS MORE HERE THAN IN ANY OTHER SPORT IN THIS REPO
Three of the constants the college build ships are unvalidated in a way MLB's
are not, and all three are checkable from one contest export:

  1. THE LADDER OVERROUNDS (edge/dfs_ladder.py). Fitted against FanDuel's
     two-sided lines, which is a cross-book anchor -- an assumption about a
     second book, not about reality. If they are too high every projection is
     biased LOW and this script will show it as a negative bias on `proj`.
  2. THE OWNERSHIP PRIOR (edge/dfs_ncaaf_theory.py). Completely unvalidated.
     MLB's equivalent gammas were tuned against exactly this kind of file;
     college has never had one.
  3. THE SPREADS IN SD_FIT. Regressed against a leave-one-out season mean
     because no projection log existed. Now one does
     (data/dfs_proj_log_ncaaf.csv), so a season from now they can be re-fitted
     against real projections the way scripts/nfl_variance_fit.py did.

WHAT THE EXPORT GIVES, AND THE TRAP IN READING IT
Each DK contest-standings CSV interleaves two unrelated tables in one file:
per-entry leaderboard rows, and in the same rows' trailing columns a field
ownership board (Player, Roster Position, %Drafted, FPTS). Only the second is
wanted, and edge/dfs_contest.py reads it -- the one piece of this loop that
carries no sport in it.

DraftKings lists a player once PER ROSTER SLOT he was used in, so ownership is
the SUM of his rows. That matters more in college than anywhere else: a
receiver can appear as WR, FLEX and S-FLEX, and a quarterback as QB and
S-FLEX. Keeping only the last row dropped two thirds of an NFL GPP board's
ownership when it happened there.

DATE MATCHING IS EASY HERE AND WAS HARD FOR MLB. scripts/dfs_calibration.py
carries real machinery to infer which date a contest file belongs to, because
MLB plays daily and rosters overlap heavily. College plays once a week, so the
overlap between a contest board and one Saturday's projection log is decisive.
The date is still stated in the output rather than assumed silently.
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

from edge import dfs_ncaaf_theory as theory      # noqa: E402
from edge.ncaaf import base_position, norm       # noqa: E402
from edge.dfs_contest import parse_contest_file        # noqa: E402

PROJ_LOG = ROOT / "data" / "dfs_proj_log_ncaaf.csv"


META = ROOT / "data" / "contest_meta.json"


def load_proj_log() -> dict:
    """{(date, gid): {norm_name: row}} from the forward-test log.

    Keyed by SLATE, not date: a college Saturday has several slates, and a
    date-keyed log blurred them into one board."""
    if not PROJ_LOG.exists():
        return {}
    out: dict = collections.defaultdict(dict)
    with PROJ_LOG.open(newline="") as fh:
        for row in csv.DictReader(fh):
            out[(row["date"], row.get("gid", ""))][norm(row["player"])] = row
    return dict(out)


def best_slate(contest: dict, log: dict) -> tuple[tuple | None, int]:
    """(date, gid) of the logged slate this contest was played on, and the
    overlap. Jaccard rather than raw overlap, so a big main-slate board
    cannot outscore the smaller slate the contest actually ran on."""
    def score(item):
        players = set(item[1])
        return len(set(contest) & players) / max(1, len(set(contest) | players))
    if not log:
        return None, 0
    key, players = max(log.items(), key=score)
    overlap = len(set(contest) & set(players))
    return (key, overlap) if overlap else (None, 0)


def contest_type(path: str) -> str:
    import json
    cid = Path(path).stem.split("-")[-1]
    try:
        return json.load(META.open()).get(cid, {}).get("type", "unknown")
    except (OSError, ValueError):
        return "unknown"


def _f(x, default=None):
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def grade(contest: dict, rows: dict) -> dict:
    """Predicted vs actual, for both projections and ownership."""
    joined = []
    for key, row in rows.items():
        real = contest.get(key)
        if not real:
            continue
        proj, own = _f(row.get("proj")), _f(row.get("own"))
        if proj is None:
            continue
        joined.append({
            "player": row["player"], "pos": base_position(row.get("dk_pos")),
            "salary": _f(row.get("salary"), 0.0),
            "proj": proj, "actual": real["fpts"],
            "own": own, "own_actual": real["pct_drafted"],
            "band": _f(row.get("band_points"), 0.0),
        })
    return {"rows": joined}


def _report(name: str, pairs: list[tuple[float, float]]) -> dict | None:
    if len(pairs) < 5:
        return None
    pred = [p for p, _ in pairs]
    act = [a for _, a in pairs]
    errs = [p - a for p, a in pairs]
    mp, ma = statistics.fmean(pred), statistics.fmean(act)
    num = sum((p - mp) * (a - ma) for p, a in pairs)
    den = math.sqrt(sum((p - mp) ** 2 for p in pred) * sum((a - ma) ** 2 for a in act))
    return {"n": len(pairs), "pred": mp, "actual": ma,
            "bias": statistics.fmean(errs),
            "mae": statistics.fmean(abs(e) for e in errs),
            "r": (num / den) if den else float("nan")}


def show(tag: str, pairs: list[tuple[float, float]]) -> None:
    res = _report(tag, pairs)
    if res is None:
        print(f"  {tag:<28} too few matched players")
        return
    print(f"  {tag:<28}n={res['n']:<5} pred {res['pred']:7.2f}  actual "
          f"{res['actual']:7.2f}  bias {res['bias']:+7.2f}  MAE {res['mae']:6.2f}"
          f"  r {res['r']:.3f}")


def fit_ownership(board: dict, contest: dict, ctype: str) -> None:
    """Sweep the prior against ONE contest: gamma, and the slot split.

    Predicted ownership is computed over the whole logged board -- the pool
    the model actually built -- and compared on every logged player, 0% for
    one nobody drafted. Ownership the field put on players the model never
    projected is reported as the modelled share (MODELED_SHARE). One contest
    at a time, never pooled: a cash field concentrates far harder than a GPP.

    Prints a suggestion; never edits the module. A fitted constant should be
    reviewed next to its n, and one contest is not a season.
    """
    import copy
    rows = list(board.values())
    pool = [{"name": r["player"], "dk_pos": r.get("dk_pos"),
             "proj": _f(r.get("proj"), 0.0), "salary": _f(r.get("salary"), 0.0)}
            for r in rows]
    actual = [contest.get(norm(p["name"]), {}).get("pct_drafted", 0.0) for p in pool]
    field_total = sum(v["pct_drafted"] for v in contest.values())
    share = sum(actual) / field_total if field_total else float("nan")
    print(f"\nOWNERSHIP ({ctype}): the field put {share:.0%} of its ownership on "
          f"the {len(pool)} players the model projected (shipped MODELED_SHARE "
          f"{theory.MODELED_SHARE})")
    outside = sorted(((v["pct_drafted"], v["name"]) for k, v in contest.items()
                      if k not in board), reverse=True)[:6]
    if outside:
        print("  most-owned players the model did not project: "
              + ", ".join(f"{n} {o:.1f}%" for o, n in outside))

    # Slots by position over the WHOLE board. The export gives each row's
    # roster slot, so a player's position is read off a QB/RB/WR row.
    slot_pos = {}
    for k, r in board.items():
        slot_pos[k] = base_position(r.get("dk_pos"))
    by_pos: dict = collections.defaultdict(float)
    for k, v in contest.items():
        pos = slot_pos.get(k) or next(
            (sl for sl in v.get("slots", ()) if sl in ("QB", "RB", "WR")), "?")
        by_pos[pos] += v["pct_drafted"] / 100.0
    print("  slots per lineup by position: "
          + ", ".join(f"{p} {by_pos.get(p, 0):.2f}" for p in ("QB", "RB", "WR"))
          + (f", unknown {by_pos['?']:.2f}" if by_pos.get("?") else "")
          + f"   (shipped {theory.SLOTS_BY_POSITION})")

    def mae(gamma):
        pp = copy.deepcopy(pool)
        theory.add_ownership(pp, gamma=gamma)
        return statistics.fmean(abs(p["own"] - a) for p, a in zip(pp, actual))

    shipped = mae(theory.OWNERSHIP_GAMMA)
    best = min((mae(g / 20.0), g / 20.0) for g in range(4, 81))
    print(f"  shipped: gamma {theory.OWNERSHIP_GAMMA:.2f} -> mean abs ownership "
          f"error {shipped:.2f} points")
    print(f"  best   : gamma {best[1]:.2f} -> {best[0]:.2f} points"
          + ("   (at the edge of the sweep)" if best[1] >= 4.0 else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("files", nargs="*",
                    help="contest-standings CSVs (default: every one in data/)")
    ap.add_argument("--date", default=None,
                    help="force the slate date instead of inferring it")
    ap.add_argument("--fit-ownership", action="store_true")
    args = ap.parse_args()

    log = load_proj_log()
    if not log:
        print(f"No {PROJ_LOG.relative_to(ROOT)} yet. Build a slate first "
              f"(scripts/dfs_lineups_ncaaf.py or the app) -- the forward-test "
              f"log is written on every build.", file=sys.stderr)
        return 1
    print(f"forward-test log: {len(log)} slate(s) — "
          + ", ".join(f"{d} gid {g}" for d, g in sorted(log)))

    files = args.files or sorted(glob.glob(str(ROOT / "data" / "contest-standings-*.csv")))
    if not files:
        print("No contest-standings-*.csv in data/.", file=sys.stderr)
        return 1

    for path in files:
        contest = parse_contest_file(path)
        if args.date:
            cands = {k: v for k, v in log.items() if k[0] == args.date}
            key, overlap = best_slate(contest, cands)
        else:
            key, overlap = best_slate(contest, log)
        if not key or overlap < 5:
            # Silently skipping would make an MLB export look like a failed
            # college join. Say which file and why.
            print(f"\n{Path(path).name}: no college slate matches "
                  f"({overlap} players overlap) — skipped")
            continue
        rows = log[key]
        ctype = contest_type(path)
        joined = grade(contest, rows)["rows"]
        print(f"\n{Path(path).name} ({ctype})  ->  slate {key[0]} gid {key[1]}   "
              f"{len(joined)} players joined of {len(contest)} on the board")

        show("projection, all", [(r["proj"], r["actual"]) for r in joined])
        for pos in ("QB", "RB", "WR"):
            sel = [r for r in joined if r["pos"] == pos]
            show(f"projection, {pos}", [(r["proj"], r["actual"]) for r in sel])
        show("ownership, all",
             [(r["own"], r["own_actual"]) for r in joined if r["own"] is not None])

        # Does a high `band` -- a projection resting on the unpriced head of a
        # ladder -- actually predict worse? This is the only direct test of
        # edge/dfs_ladder.py's own risk measure.
        with_band = [r for r in joined if r["band"] is not None]
        if len(with_band) >= 20:
            with_band.sort(key=lambda r: r["band"])
            half = len(with_band) // 2
            lo = _report("lo", [(r["proj"], r["actual"]) for r in with_band[:half]])
            hi = _report("hi", [(r["proj"], r["actual"]) for r in with_band[half:]])
            if lo and hi:
                print(f"  {'band: low half':<28}MAE {lo['mae']:6.2f}   "
                      f"(mean band {statistics.fmean(r['band'] for r in with_band[:half]):.2f})")
                print(f"  {'band: high half':<28}MAE {hi['mae']:6.2f}   "
                      f"(mean band {statistics.fmean(r['band'] for r in with_band[half:]):.2f})")

        if args.fit_ownership:
            fit_ownership(rows, contest, ctype)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
