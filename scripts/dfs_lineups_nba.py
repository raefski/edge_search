#!/usr/bin/env python3
"""DK NBA Classic cash + GPP lineups from the terminal -- the same build_slate the app runs.

    python3 scripts/dfs_lineups_nba.py                     # tonight's main slate
    python3 scripts/dfs_lineups_nba.py --list              # the Classic slates DK lists
    python3 scripts/dfs_lineups_nba.py --draft-group 154575 --board 20

Logs the projections and lineups (data/dfs_proj_log_nba.csv, data/dfs_lineups_nba_<date>.csv)
unless --no-log: the dfs-autolog timer runs this before each lock.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs_run_nba as R  # noqa: E402


def show(title: str, res: dict | None) -> None:
    if not res:
        print(f"\n{title}: no legal lineup")
        return
    print(f"\n{title}: proj {res['proj']}  floor {res['floor']}  90th {res['ceil']}  "
          f"own {res['own']:.0f}%  ${res['salary']:,}")
    for slot, p in res["slots"]:
        print(f"  {slot:4} {p['name']:24} {p['team']:3} ${p['salary']:>6,} {p.get('minutes', 0):4.0f} min "
              f"{p['proj']:5.1f} pts{'  PROMOTED' if p.get('promoted') else ''}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--draft-group", type=int, default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--sims", type=int, default=3000)
    ap.add_argument("--iters", type=int, default=80)
    ap.add_argument("--board", type=int, default=0, help="print the top N of the board")
    ap.add_argument("--no-log", action="store_true")
    args = ap.parse_args()
    if args.list:
        for s in R.classic_groups():
            print(f"{s['gid']}  {s['label']:12} {s['games']} games  {s['start']}")
        return 0
    res = R.build_slate(draft_group=args.draft_group, n_sims=args.sims, iters=args.iters,
                        persist=not args.no_log)
    if "pool" not in res:
        print(res.get("error") or res.get("unpriced_reason") or "slate not priced yet")
        return 1
    info = res["stats"]
    print(f"draft group {res['gid']} ({res['meta'].get('label')}), {info['games']} games, "
          f"{info['pool']} projected of {info['priced']} priced · lineups {info['lineups_source']} · "
          f"salaries {info['draftables_source']}")
    if info.get("implied"):
        print("implied points: " + ", ".join(f"{t} {v:.0f}" for t, v in
                                             sorted(info["implied"].items(), key=lambda kv: -kv[1])))
    promoted = [p for p in res["pool"] if p.get("promoted")]
    if promoted:
        print("PROMOTED: " + ", ".join(f"{p['name']} ({p['team']} ${p['salary']:,} +{p['gain']:.1f})"
                                      for p in promoted))
    if info.get("out"):
        print("OUT: " + ", ".join(info["out"]))
    show("CASH", res["cash"])
    show("GPP", res["gpp"])
    if args.board:
        print(f"\n{'player':24} pos     tm      $  min    pts  val   own")
        for p in sorted(res["pool"], key=lambda p: -p["proj"])[:args.board]:
            print(f"{p['name'][:24]:24} {p['dk_pos']:7} {p['team']:3} {p['salary']:6} {p['minutes']:4.0f} "
                  f"{p['proj']:6.1f} {p['value']:4.1f} {p['own']:5.1f}")
    if res.get("log"):
        print(f"\nlogged: {res['log']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
