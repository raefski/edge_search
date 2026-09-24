#!/usr/bin/env python3
"""Build DK College Football Classic lineups from the free scraped ladders.

    python3 scripts/dfs_lineups_ncaaf.py                     # cash + gpp, main slate
    python3 scripts/dfs_lineups_ncaaf.py --mode gpp -n 3     # a 3-lineup portfolio
    python3 scripts/dfs_lineups_ncaaf.py --list-slates
    python3 scripts/dfs_lineups_ncaaf.py --draft-group 153831
    python3 scripts/dfs_lineups_ncaaf.py --board             # the projected pool

THIS IS A THIN WRAPPER ON PURPOSE
Everything that decides a lineup lives in edge/dfs_run_ncaaf.py, which
pages/3_🏈_NCAAF_DFS.py also calls, so a lineup printed here and a lineup on
the phone are the same lineup by construction rather than by agreement.

WHAT TO KNOW BEFORE READING THE OUTPUT
  * A cash lineup will usually start TWO quarterbacks, from different teams.
    That is not a bug and not a stack: college quarterbacks average 17.2 DK
    points against 8.9 for receivers, and the S-FLEX accepts one. Two from the
    SAME team is forbidden outright (-0.098 correlation).
  * A GPP lineup will usually start a quarterback, THREE of his own receivers,
    and the opposing quarterback. Three, not the NFL's two, because team-mate
    receivers are positively correlated in college (+0.048) and negatively
    correlated in the NFL (-0.029).
  * `band` on the board is how many DK points of a projection rest on the
    UNPRICED head of a ladder -- the part edge/dfs_ladder.py had to model
    rather than read off a posted price. High band, less trustworthy number.

Every constant and its provenance: edge/dfs_ncaaf_theory.py and
edge/dfs_ladder.py.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import dfs, dfs_opt_ncaaf, dfs_run_ncaaf  # noqa: E402
from edge.ncaaf import base_position                # noqa: E402
from edge.odds.cli import add_source_args, client_from_args, describe  # noqa: E402

SPORT_KEY = dfs_run_ncaaf.SPORT


def show(res, idx=None, mode="cash"):
    if res is None:
        print("  no legal lineup")
        return
    head = f"lineup {idx}" if idx else mode.upper()
    edge_stat = (f"floor {res['floor']}" if mode == "cash" else f"ceil {res['ceil']}")
    print(f"\n{head}: {edge_stat}  proj {res['proj']}  sd {res['sd']}  "
          f"${res['salary']:,}/50,000  own {res['own']:.0f}%")
    if res.get("stack"):
        s = res["stack"]
        mates = ", ".join(s["with"]) or "none"
        back = ("  + bring-back " + ", ".join(s["bring_back"])
                if s["bring_back"] else "")
        print(f"  stack: {s['qb']} ({s['team']}) + {mates}{back}")
    if len(res.get("qbs") or []) > 1:
        print(f"  two quarterbacks: {', '.join(res['qbs'])}")
    print(f"  {'slot':<8}{'player':<24}{'team':<7}{'salary':>8}{'proj':>7}"
          f"{'own':>7}{'band':>7}")
    for r in dfs_run_ncaaf.lineup_rows(res):
        print(f"  {r['slot']:<8}{r['player'][:23]:<24}{r['team']:<7}"
              f"{r['salary']:>8,}{r['proj']:>7.1f}{r['own']:>6.0f}%"
              f"{r['band']:>7.1f}")


def show_board(pool, top, pos_filter):
    rows = sorted(pool, key=lambda p: -(p.get("proj") or 0))
    if pos_filter:
        rows = [p for p in rows if base_position(p.get("dk_pos")) == pos_filter.upper()]
    print(f"\n{'pos':<5}{'player':<24}{'team':<7}{'salary':>8}{'proj':>7}"
          f"{'val':>6}{'own':>7}{'lev':>6}{'band':>7}  ladders")
    for p in rows[:top]:
        val = 1000.0 * p["proj"] / p["salary"] if p["salary"] else 0.0
        lad = " ".join(f"{m.split('_')[-1]}:{d['rungs']}"
                       for m, d in sorted(p.get("ladders", {}).items()))
        print(f"{base_position(p.get('dk_pos')):<5}{p['name'][:23]:<24}"
              f"{p.get('team', ''):<7}{p['salary']:>8,}{p['proj']:>7.1f}"
              f"{val:>6.2f}{p.get('own', 0):>6.0f}%{p.get('leverage', 0):>+6.0f}"
              f"{p.get('band_points', 0):>7.1f}  {lad[:44]}")
    print("\n'band' is DK points of this projection that came from the UNPRICED "
          "head of a\nladder rather than from a posted price. 'ladders' is how "
          "many rungs each\nmarket supplied -- more rungs, less assumed.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["cash", "gpp", "both"], default="both")
    ap.add_argument("-n", "--lineups", type=int, default=1,
                    help="more than 1 builds a diversified GPP portfolio")
    ap.add_argument("--max-overlap", type=int, default=5,
                    help="max shared players between two portfolio lineups "
                         "(of 8; default %(default)s)")
    ap.add_argument("--stack-n", type=int, default=None,
                    help="pass-catchers stacked with the GPP quarterback "
                         "(default 3 -- see edge/dfs_ncaaf_theory.py)")
    ap.add_argument("--bring-back", type=int, default=None)
    ap.add_argument("--draft-group", type=int, default=None)
    ap.add_argument("--list-slates", action="store_true")
    ap.add_argument("--board", action="store_true",
                    help="print the projected pool instead of lineups")
    ap.add_argument("--pos", default=None, help="board filter: QB, RB or WR")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--iters", type=int, default=700)
    ap.add_argument("--no-log", action="store_true",
                    help="do not write the forward-test log")
    add_source_args(ap)
    args = ap.parse_args()

    if args.list_slates:
        for s in dfs_run_ncaaf.classic_groups(dfs.draft_groups(
                dfs_run_ncaaf.DK_SPORT)):
            tag = " [featured]" if s["featured"] else ""
            print(f"{s['gid']:>8}  {s['label']:<18}{s['games']:>3} games  "
                  f"{s.get('start_est') or s.get('start')}{tag}")
        return 0

    client = client_from_args(args, SPORT_KEY, consumer="dfs")
    print(describe(client))

    res = dfs_run_ncaaf.build_slate(
        client, draft_group=args.draft_group, iters=args.iters,
        stack_n=args.stack_n, bring_back=args.bring_back,
        persist=not args.no_log)

    if res.get("error"):
        print(res["error"], file=sys.stderr)
        return 1
    if res.get("unpriced"):
        print("DraftKings lists this slate but has not PRICED it yet -- normal "
              "a few days out.", file=sys.stderr)
        return 1

    meta, stats = res["meta"], res["stats"]
    print(f"slate {res['gid']} ({meta['label']}, {meta['games']} games)")
    print(f"pool: {stats['projected']} projected of {stats['slate_players']} "
          f"on the slate ({stats['priced_players']} players carry a ladder "
          f"somewhere, {stats['not_on_slate']} of them in another game)")
    if stats.get("one_rung"):
        print(f"  WARNING: {stats['one_rung']} players had ONE rung per market. "
              f"That is the signature of a client built with main_line_only "
              f"left ON -- see edge/dfs_run_ncaaf.py.", file=sys.stderr)
    if stats.get("missing_games"):
        print(f"  note: no game total for {', '.join(stats['missing_games'])} "
              f"-- display only, no player is affected.", file=sys.stderr)

    if args.board:
        show_board(res["pool"], args.top, args.pos)
        return 0

    if args.lineups > 1:
        if args.mode != "gpp":
            raise SystemExit("a portfolio only makes sense for --mode gpp")
        kw = {}
        if args.stack_n is not None:
            kw["stack_n"] = args.stack_n
        if args.bring_back is not None:
            kw["bring_back"] = args.bring_back
        out = dfs_opt_ncaaf.portfolio(res["pool"], args.lineups,
                                      max_overlap=args.max_overlap, mode="gpp",
                                      iters=args.iters, **kw)
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
