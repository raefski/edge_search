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


def load_proj_log() -> dict:
    """{date: {norm_name: row}} from the forward-test log."""
    if not PROJ_LOG.exists():
        return {}
    out: dict = collections.defaultdict(dict)
    with PROJ_LOG.open(newline="") as fh:
        for row in csv.DictReader(fh):
            out[row["date"]][norm(row["player"])] = row
    return dict(out)


def best_date(contest: dict, log: dict) -> tuple[str | None, int]:
    """The logged slate this contest board overlaps most. College is weekly, so
    the winner is decisive rather than marginal -- the runner-up is reported so
    that a close call is visible instead of silently resolved."""
    scores = sorted(((len(set(contest) & set(players)), date)
                     for date, players in log.items()), reverse=True)
    if not scores or scores[0][0] == 0:
        return None, 0
    return scores[0][1], scores[0][0]


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


def show(tag: str, res: dict | None) -> None:
    if res is None:
        print(f"  {tag:<28} too few matched players")
        return
    print(f"  {tag:<28}n={res['n']:<5} pred {res['pred']:7.2f}  actual "
          f"{res['actual']:7.2f}  bias {res['bias']:+7.2f}  MAE {res['mae']:6.2f}"
          f"  r {res['r']:.3f}")


def fit_ownership(joined: list[dict]) -> None:
    """Sweep the prior's two knobs against what the field actually did.

    Only the SHAPE is swept here -- gamma (how sharply the field chases value)
    and the per-position slot totals (how the FLEX and S-FLEX split). Those are
    the two things edge/dfs_ncaaf_theory.py guesses, and the S-FLEX split is
    the one most worth replacing: it encodes how often a real college field
    puts a second quarterback there, and finding 1 in that module says a sharp
    field would do it almost always while a real one plainly does not.

    Prints a suggestion; never edits the module. A fitted constant should be
    reviewed next to its n, and one contest is not a season.
    """
    by_pos: dict = collections.defaultdict(list)
    for r in joined:
        by_pos[r["pos"]].append(r)

    print("\nWHAT THE FIELD ACTUALLY DID, BY POSITION")
    print(f"  {'pos':<5}{'n':>5}{'summed own':>13}{'slots implied':>16}"
          f"{'prior':>8}")
    total_slots = 0.0
    implied = {}
    for pos in ("QB", "RB", "WR"):
        rows = by_pos.get(pos) or []
        if not rows:
            continue
        summed = sum(r["own_actual"] for r in rows)
        slots = summed / 100.0
        implied[pos] = slots
        total_slots += slots
        print(f"  {pos:<5}{len(rows):>5}{summed:>12.1f}%{slots:>16.2f}"
              f"{theory.SLOTS_BY_POSITION.get(pos, 0):>8.2f}")
    print(f"  {'':<5}{'':>5}{'':>13}{total_slots:>16.2f}{8.0:>8.2f}"
          "   <- must come to 8")

    if abs(total_slots - 8.0) > 0.6:
        print("\n  The implied slots do not come to 8. That usually means the "
              "export is a\n  MULTI-ENTRY contest board (ownership sums to "
              "more than one lineup's worth\n  per entry) or that the join "
              "missed players. Treat the split below as\n  indicative only.")

    print("\n  suggested SLOTS_BY_POSITION = {" + ", ".join(
        f'"{p}": {8.0 * v / total_slots:.2f}' for p, v in implied.items()) + "}")

    best = None
    for gamma in [x / 20.0 for x in range(6, 51)]:
        pool = [dict(r, dk_pos=r["pos"], proj=r["proj"], salary=r["salary"])
                for r in joined]
        theory.add_ownership(pool, gamma=gamma)
        err = statistics.fmean(abs(p["own"] - r["own_actual"])
                              for p, r in zip(pool, joined))
        if best is None or err < best[0]:
            best = (err, gamma)
    print(f"\n  best OWNERSHIP_GAMMA = {best[1]:.2f}  (mean abs ownership error "
          f"{best[0]:.2f} points)")
    print(f"  currently shipped     = {theory.OWNERSHIP_GAMMA:.2f}")
    print("\n  One contest is not a season, and a cash board and a GPP board "
          "concentrate\n  very differently -- do not pool them. Re-run per "
          "contest type.")


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
    print(f"forward-test log: {len(log)} slate(s) — {', '.join(sorted(log))}")

    files = args.files or sorted(glob.glob(str(ROOT / "data" / "contest-standings-*.csv")))
    if not files:
        print("No contest-standings-*.csv in data/.", file=sys.stderr)
        return 1

    pooled: list[dict] = []
    for path in files:
        contest = parse_contest_file(path)
        date, overlap = (args.date, len(set(contest) & set(log.get(args.date, {})))) \
            if args.date else best_date(contest, log)
        if not date or overlap < 5:
            # Silently skipping would make an MLB export look like a failed
            # college join. Say which file and why.
            print(f"\n{Path(path).name}: no college slate matches "
                  f"({overlap} players overlap) — skipped")
            continue
        rows = log[date]
        res = grade(contest, rows)
        joined = res["rows"]
        pooled.extend(joined)
        print(f"\n{Path(path).name}  ->  slate {date}   "
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
                print("  If the high-band half is NOT worse, the unpriced head "
                      "is costing less than\n  edge/dfs_ladder.py assumes and "
                      "the constants can be loosened.")

    if args.fit_ownership and pooled:
        fit_ownership([r for r in pooled if r["own"] is not None])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
