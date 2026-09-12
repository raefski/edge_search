#!/usr/bin/env python3
"""Build DK NFL Classic lineups from the free scraped props.

    python3 scripts/dfs_lineups_nfl.py                      # cash + gpp, main slate
    python3 scripts/dfs_lineups_nfl.py --mode gpp -n 3      # a 3-lineup portfolio
    python3 scripts/dfs_lineups_nfl.py --list-slates
    python3 scripts/dfs_lineups_nfl.py --draft-group 153069 # the Sun-Mon slate

THIS IS A THIN WRAPPER ON PURPOSE
Everything that decides a lineup -- slate resolution, the pool, the two game
theories -- lives in edge/dfs_run_nfl.py, which pages/2_🏈_NFL_DFS.py also
calls. It used to live here, which meant the app could only match the CLI by
copying it, and two copies of a shared core drifting is a failure this repo has
already had (ODDS_LAYER.md). A lineup printed here and a lineup on the phone
are now the same lineup by construction rather than by agreement.

THE TWO MODES ARE TWO THEORIES, NOT ONE WITH A FLAG
  cash  maximises `mean - 0.75*sd` of the lineup total
  gpp   maximises `mean + 1.25*sd`, with a QB stack forced
through a correlation matrix measured on 544 nflverse games. Cash spreads
across games and refuses a stack because correlation raises the spread; GPP
concentrates for the same reason with the sign flipped. See
edge/dfs_nfl_theory.py for every constant and its provenance, and
scripts/nfl_lineup_backtest.py for the head-to-head that tests the claim.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import dfs, dfs_opt_nfl, dfs_run_nfl  # noqa: E402
from edge.odds.cli import add_source_args, client_from_args, describe  # noqa: E402

SPORT_KEY = dfs_run_nfl.SPORT


def show(res, idx=None, mode="cash"):
    if res is None:
        print("  no legal lineup"); return
    head = f"lineup {idx}" if idx else mode.upper()
    edge_stat = (f"floor {res['floor']}" if mode == "cash" else f"ceil {res['ceil']}")
    print(f"\n{head}: {edge_stat}  proj {res['proj']}  sd {res['sd']}  "
          f"salary ${res['salary']:,}  own {res['own']:.0f}%")
    if res["stack"]:
        s = res["stack"]
        print(f"  stack: {s['qb']} ({s['team']}) + {', '.join(s['with']) or 'none'}"
              + (f"  bring-back: {', '.join(s['bring_back'])}" if s["bring_back"] else ""))
    for r in dfs_run_nfl.lineup_rows(res):
        print(f"    {r['slot']:5s} {r['player'][:24]:26s} {r['team']:4s} vs "
              f"{r['opp'] or '?':4s} ${r['salary']:>6,} {r['proj']:6.1f}  "
              f"own {r['own']:5.1f}%  lev {r['leverage']:+6.1f}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", default="both", choices=("cash", "gpp", "both"))
    ap.add_argument("-n", "--lineups", type=int, default=1,
                    help="more than 1 builds a PORTFOLIO (requires --mode gpp)")
    ap.add_argument("--stack-n", type=int, default=2,
                    help="own pass-catchers with the QB (default: %(default)s -- "
                         "teammates are slightly ANTI-correlated, see dfs_opt_nfl)")
    ap.add_argument("--bring-back", type=int, default=1)
    ap.add_argument("--book", default="draftkings")
    ap.add_argument("--draft-group", type=int, default=None,
                    help="default: DK's MAIN Classic slate")
    ap.add_argument("--list-slates", action="store_true")
    ap.add_argument("--iters", type=int, default=700)
    ap.add_argument("--max-overlap", type=int, default=6,
                    help="most players two lineups in a portfolio may share")
    add_source_args(ap)
    args = ap.parse_args()

    if args.list_slates:
        for s in dfs_run_nfl.classic_groups():
            print(f"  {s['gid']:>7}  {s['games']:>2} games  {s['label']:<16} "
                  f"{s.get('start_est') or ''}")
        return 0

    client = client_from_args(args, SPORT_KEY, consumer="dfs")
    print(describe(client))

    res = dfs_run_nfl.build_slate(
        client, draft_group=args.draft_group, iters=args.iters, book=args.book,
        stack_n=args.stack_n, bring_back=args.bring_back)
    if res.get("error"):
        raise SystemExit(res["error"])
    if res.get("unpriced"):
        raise SystemExit("DraftKings lists this slate but has not PRICED it "
                         "yet -- normal a few days out, not an error.")

    meta, stats = res["meta"], res["stats"]
    print(f"slate {res['gid']} ({meta['label']}, {meta['games']} games)")
    print(f"pool: {stats['offense']} offensive players + {stats['dst']} defences "
          f"({stats['no_proj']} unprojectable, {stats['not_on_slate']} propped but "
          f"not on this slate)")
    if stats.get("missing_games"):
        print(f"  WARNING: no game total for {', '.join(stats['missing_games'])} "
              f"-- those defences are absent. Offensive players are unaffected.",
              file=sys.stderr)
    if stats.get("conflicts"):
        print(f"  WARNING: two events matched one game: "
              f"{', '.join(stats['conflicts'])}", file=sys.stderr)
    if stats["dst"] == 0:
        print("  WARNING: no defences priced -- every lineup needs one, so this "
              "will return nothing", file=sys.stderr)

    if args.lineups > 1:
        if args.mode != "gpp":
            raise SystemExit("a portfolio only makes sense for --mode gpp")
        # More than one lineup means a PORTFOLIO, not the same lineup n times --
        # varying only the seed converges to the same optimum.
        out = dfs_opt_nfl.portfolio(res["pool"], args.lineups,
                                    max_overlap=args.max_overlap, mode="gpp",
                                    stack_n=args.stack_n,
                                    bring_back=args.bring_back, iters=args.iters)
        for i, r in enumerate(out, 1):
            show(r, i, "gpp")
        if len(out) < args.lineups:
            print(f"\n  only {len(out)} of {args.lineups} lineups were distinct "
                  f"enough (max_overlap={args.max_overlap}); the pool cannot "
                  f"support more.", file=sys.stderr)
        return 0

    for mode in (("cash", "gpp") if args.mode == "both" else (args.mode,)):
        show(res[mode], mode=mode)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
