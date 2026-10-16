#!/usr/bin/env python3
"""DK College Basketball cash + GPP lineups from the command line -- the same
build_slate (edge/dfs_run_ncaab.py) the phone page calls.

    python3 scripts/dfs_lineups_ncaab.py                    # main slate, both lineups
    python3 scripts/dfs_lineups_ncaab.py --list             # slates DraftKings is listing
    python3 scripts/dfs_lineups_ncaab.py --gpp 5            # five different GPP lineups
    python3 scripts/dfs_lineups_ncaab.py --board 40         # the top of the projected board
    python3 scripts/dfs_lineups_ncaab.py --lock "Cooper Flagg"   # late swap: keep him

Props come from the local odds store (scripts/odds_collect.py --profile
dfs_ncaab). Every build is logged to data/dfs_proj_log_ncaab.csv, keyed by
draft group, so a contest export can be graded against it
(scripts/nba_calibration.py --sport ncaab); pass --no-log for a dry run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs_opt_ncaab, dfs_run_ncaab as R  # noqa: E402


def show(title: str, res: dict | None) -> None:
    if not res:
        print(f"\n{title}: no legal lineup")
        return
    games = ", ".join(f"{g}x{n}" for g, n in res["game_stacks"].items())
    print(f"\n{title}: proj {res['proj']}  floor {res['floor']}  90th {res['ceil']}  "
          f"1-in-100 {res['p99']}  own {res['own']:.0f}%  ${res['salary']:,}"
          + (f"  same game {games}" if games else ""))
    for slot, p in res["slots"]:
        flag = f" [{p['status']}]" if p.get("status") else ""
        print(f"  {slot:4} {p['name'][:24]:24} {p['team']:4} ${p['salary']:>6,}  "
              f"{p['proj']:5.1f}  own {p.get('own', 0):4.1f}%  {p.get('source')}{flag}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--draft-group", type=int, default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--sims", type=int, default=2000)
    ap.add_argument("--iters", type=int, default=150)
    ap.add_argument("--gpp", type=int, default=1, help="how many GPP lineups")
    ap.add_argument("--board", type=int, default=0, help="print the top N of the board")
    ap.add_argument("--lock", action="append", default=[],
                    help="a player who must stay (late swap: his game has started)")
    ap.add_argument("--no-log", action="store_true")
    args = ap.parse_args()
    if args.list:
        for s in R.classic_groups():
            print(f"{s['gid']}  {s['label']:14} {s['games']} games  {s['start']}"
                  + ("  (type unverified)" if s.get("unverified_type") else ""))
        return 0
    res = R.build_slate(draft_group=args.draft_group, n_sims=args.sims, iters=args.iters,
                        persist=not args.no_log, locked=set(args.lock) or None)
    if res.get("error") or res.get("unpriced"):
        print(res.get("error") or "slate not priced yet")
        return 1
    info = res["stats"]
    src = info["sources"]
    print(f"draft group {res['gid']} ({res['meta'].get('label')}), {info['games']} games, "
          f"{info['pool']} projected: {src['props']} props / {src['fppg']} FPPG / "
          f"{src['salary']} salary;  props from {info['books'] or 'no book'}")
    print("implied points: " + ", ".join(f"{t} {g:.1f}" for t, g in
                                         sorted(info["implied"].items(), key=lambda kv: -kv[1])))
    if info["missing_lines"]:
        print("NO LINE (league average): " + ", ".join(info["missing_lines"]))
    if info["out"]:
        print("OUT: " + ", ".join(info["out"]))
    if info["doubtful"]:
        print("QUESTIONABLE (kept): " + ", ".join(info["doubtful"]))
    show("CASH", res["cash"])
    if args.gpp > 1:
        for k, lu in enumerate(dfs_opt_ncaab.portfolio(res["pool"], res["sim"], args.gpp,
                                                      iters=max(30, args.iters // 3)), 1):
            show(f"GPP {k}", lu)
    else:
        show("GPP", res["gpp"])
    if args.board:
        print(f"\n{'player':24} {'pos':6} tm        $    pts  floor  ceil   own  source")
        for p in sorted(res["pool"], key=lambda p: -p["proj"])[:args.board]:
            print(f"{p['name'][:24]:24} {p['dk_pos']:6} {p['team']:4} {p['salary']:>6,} "
                  f"{p['proj']:6.1f} {p['floor']:6.1f} {p['ceil']:5.1f} {p.get('own', 0):5.1f}  "
                  f"{p['source']}")
    if res.get("log"):
        print(f"\nlogged: {res['log']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
