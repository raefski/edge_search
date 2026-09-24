#!/usr/bin/env python3
"""Build DK NASCAR Classic lineups from NASCAR's own free timing feeds.

    python3 scripts/dfs_lineups_nascar.py                    # cash + gpp, next Cup race
    python3 scripts/dfs_lineups_nascar.py --board            # the driver board
    python3 scripts/dfs_lineups_nascar.py --mode gpp -n 3    # a portfolio
    python3 scripts/dfs_lineups_nascar.py --list-slates
    python3 scripts/dfs_lineups_nascar.py --exhaustive       # exact, slower

THIS SPORT COSTS NOTHING AND NEEDS NO ODDS CLIENT
Three of the four things DraftKings scores -- place differential, laps led,
fastest laps -- have no betting market at any book, so there is nothing to
devig and no snapshot to be stale. Everything comes from NASCAR's public
timing feeds and DraftKings' own draftables. Unlike every other build here it
works from any IP and has no freshness contract.

READ THE PROVISIONAL FLAG BEFORE READING THE LINEUP
DraftKings prices a NASCAR field days before qualifying. Until the grid is set
there is no starting position, so there is no place differential -- and place
differential is most of the scoring for most of a lineup. Before qualifying
this builds from an ESTIMATED grid (practice rank if practice has run, recent
average finish otherwise) and says so. Rebuild after qualifying.

AND THERE IS NO LATE SWAP. DraftKings does not allow it on NASCAR (gametype
173). Whatever is entered at the green flag is what runs.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import dfs, dfs_opt_nascar, dfs_run_nascar as R  # noqa: E402


def show(res, idx=None, mode="cash"):
    if res is None:
        print("  no legal lineup")
        return
    head = f"lineup {idx}" if idx else mode.upper()
    edge_stat = (f"floor {res['floor']}" if mode == "cash" else f"ceil {res['ceil']}")
    print(f"\n{head}: {edge_stat}  proj {res['proj']}  sd {res['sd']}  "
          f"p99 {res['p99']}  worst {res['worst']}  "
          f"${res['salary']:,}/50,000  own {res['own']:.0f}%")
    print(f"  {'driver':<24}{'start':>6}{'salary':>8}{'proj':>7}{'floor':>7}"
          f"{'ceil':>7}{'own':>7}{'lev':>6}")
    for r in R.lineup_rows(res):
        print(f"  {r['driver'][:23]:<24}{r['start']:>6}{r['salary']:>8,}"
              f"{r['proj']:>7.1f}{r['floor']:>7.1f}{r['ceil']:>7.1f}"
              f"{r['own']:>6.0f}%{r['leverage']:>+6.0f}")


def show_board(pool, top):
    rows = sorted(pool, key=lambda d: -(d.get("proj") or 0))[:top]
    print(f"\n{'driver':<24}{'start':>6}{'salary':>8}{'proj':>7}{'sd':>7}"
          f"{'floor':>7}{'ceil':>7}{'val':>6}{'own':>7}{'lev':>6}{'form':>6}")
    for d in rows:
        val = 1000.0 * d["proj"] / d["salary"] if d.get("salary") else 0.0
        print(f"{d['name'][:23]:<24}{d.get('start', 0):>6}{d.get('salary', 0):>8,}"
              f"{d['proj']:>7.1f}{d['sd']:>7.1f}{d['floor']:>7.1f}{d['ceil']:>7.1f}"
              f"{val:>6.2f}{d.get('own', 0):>6.0f}%{d.get('leverage', 0):>+6.0f}"
              f"{(d.get('form_races') or 0):>6}")
    print("\n'floor' and 'ceil' are the 25th and 90th percentiles of that "
          "driver's OWN\nsimulated distribution, not mean -/+ k*sd -- a NASCAR "
          "driver's score has a wreck\nin the left tail and a dominator in the "
          "right, and neither is symmetric.\n'form' is how many past races fed "
          "his form; 0 means the model is using a\ndeliberately mediocre "
          "default for him.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["cash", "gpp", "both"], default="both")
    ap.add_argument("-n", "--lineups", type=int, default=1,
                    help="more than 1 builds a diversified GPP portfolio")
    ap.add_argument("--max-overlap", type=int, default=3,
                    help="max shared drivers between two portfolio lineups "
                         "(of 6; default %(default)s)")
    ap.add_argument("--draft-group", type=int, default=None)
    ap.add_argument("--list-slates", action="store_true")
    ap.add_argument("--board", action="store_true")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--sims", type=int, default=2000,
                    help="simulated races (default %(default)s)")
    ap.add_argument("--iters", type=int, default=400)
    ap.add_argument("--exhaustive", action="store_true",
                    help="enumerate every lineup of the top 24 drivers")
    ap.add_argument("--own-weight", type=float, default=0.0,
                    help="GPP only: DK points to subtract per 100 points of "
                         "summed lineup ownership")
    ap.add_argument("--no-log", action="store_true")
    args = ap.parse_args()

    if args.list_slates:
        for s in R.classic_groups(dfs.draft_groups(R.DK_SPORT)):
            tag = " [featured]" if s["featured"] else ""
            print(f"{s['gid']:>8}  {s['label']:<10}"
                  f"{s.get('start_est') or s.get('start')}{tag}")
        return 0

    res = R.build_slate(draft_group=args.draft_group, n_sims=args.sims,
                        iters=args.iters, own_weight=args.own_weight,
                        persist=not args.no_log, exhaustive=args.exhaustive)
    if res.get("error"):
        print(res["error"], file=sys.stderr)
        return 1
    if res.get("unpriced"):
        print("DraftKings lists this slate but has not PRICED it yet.",
              file=sys.stderr)
        return 1

    st, meta = res["stats"], res["meta"]
    print(f"slate {res['gid']} ({meta['label']}) — {st.get('race')}")
    print(f"{st.get('track')} · {st.get('track_type')} · {st.get('laps')} laps · "
          f"{st.get('entered')} entered, {st.get('priced')} priced")
    if st.get("not_entered"):
        print(f"  priced by DK but NOT entered with NASCAR, dropped: "
              f"{', '.join(st['not_entered'])}", file=sys.stderr)

    if st.get("provisional"):
        src = "practice rank" if st.get("practice") else "recent average finish"
        print(f"\n  *** PROVISIONAL GRID *** qualifying has not run, so the "
              f"starting positions\n  below are ESTIMATED from {src}. Place "
              f"differential is most of the scoring\n  for most of a lineup, "
              f"so rebuild after qualifying — and note that DraftKings\n  "
              f"does NOT allow late swap on NASCAR.", file=sys.stderr)
    else:
        print("\n  grid is SET — starting positions are the real qualifying "
              "order.")

    if args.board:
        show_board(res["pool"], args.top)
        return 0

    if args.lineups > 1:
        if args.mode != "gpp":
            raise SystemExit("a portfolio only makes sense for --mode gpp")
        out = dfs_opt_nascar.portfolio(res["pool"], res["sim"], args.lineups,
                                       max_overlap=args.max_overlap,
                                       iters=args.iters,
                                       own_weight=args.own_weight)
        for i, r in enumerate(out, 1):
            show(r, i, "gpp")
        if len(out) < args.lineups:
            print(f"\n  only {len(out)} of {args.lineups} lineups were distinct "
                  f"enough (max_overlap={args.max_overlap}).", file=sys.stderr)
        return 0

    for mode in (("cash", "gpp") if args.mode == "both" else (args.mode,)):
        show(res[mode], mode=mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
