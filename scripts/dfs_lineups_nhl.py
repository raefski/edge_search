#!/usr/bin/env python3
"""DK NHL cash + GPP lineups from the command line (same builder as the app).

    python3 scripts/dfs_lineups_nhl.py                    # main slate, both lineups
    python3 scripts/dfs_lineups_nhl.py --list             # slates DraftKings is listing
    python3 scripts/dfs_lineups_nhl.py --gpp 5            # five different GPP lineups
    python3 scripts/dfs_lineups_nhl.py --board 40         # the top of the projected board

Props come from the local odds store (scripts/odds_collect.py --profile dfs_nhl);
lines and starting goalies from DailyFaceoff. Every build is logged to
data/dfs_proj_log_nhl.csv so a contest export can be graded against it
(pass --no-log for a dry run).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs_opt_nhl, dfs_run_nhl as R  # noqa: E402


def show(title: str, res: dict | None) -> None:
    if not res:
        print(f"\n{title}: no legal lineup")
        return
    stacks = ", ".join(f"{t}x{n}" for t, n in res["stacks"].items() if n >= 2)
    print(f"\n{title}: proj {res['proj']}  floor {res['floor']}  95th {res['ceil']}  "
          f"1-in-100 {res['p99']}  own {res['own']:.0f}%  ${res['salary']:,}  stacks {stacks}"
          + (f"  same line {res['line_stacks']}" if res["line_stacks"] else ""))
    for slot, p in res["slots"]:
        print(f"  {slot:4} {p['name']:24} {p['team']:3} {(p.get('line') or ''):3} "
              f"{(p.get('pp') or ''):4} ${p['salary']:>5,}  {p['proj']:5.1f}  own {p.get('own', 0):4.1f}%")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--draft-group", type=int, default=None)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--sims", type=int, default=2000)
    ap.add_argument("--iters", type=int, default=150)
    ap.add_argument("--gpp", type=int, default=1, help="how many GPP lineups")
    ap.add_argument("--board", type=int, default=0, help="print the top N of the board")
    ap.add_argument("--no-log", action="store_true")
    args = ap.parse_args()

    if args.list:
        for s in R.classic_groups():
            print(f"{s['gid']}  {s['label']:12} {s['games']} games  {s['start']}")
        return 0
    res = R.build_slate(draft_group=args.draft_group, n_sims=args.sims, iters=args.iters,
                        persist=not args.no_log)
    if res.get("error") or res.get("unpriced"):
        print(res.get("error") or "slate not priced yet")
        return 1
    info = res["stats"]
    print(f"draft group {res['gid']} ({res['meta'].get('label')}), {info['games']} games, "
          f"{info['pool']} players dressing, {info['imputed']} estimated from last season")
    print("implied goals: " + ", ".join(f"{t} {g:.2f}" for t, g in
                                        sorted(info["implied"].items(), key=lambda kv: -kv[1])))
    unconfirmed = [f"{t} {v.get('goalie')}" for t, v in (info.get("starters") or {}).items()
                   if t in info["implied"] and (v.get("status") or "").lower() != "confirmed"]
    if unconfirmed:
        print("UNCONFIRMED goalies: " + ", ".join(sorted(unconfirmed)))
    show("CASH", res["cash"])
    if res["cash"].get("p_cash") is not None:
        print(f"  clears the simulated field's cash line (~{res['cash']['field_line']}) "
              f"in {res['cash']['p_cash']:.0%} of simulations")
    if args.gpp > 1:
        for k, lu in enumerate(dfs_opt_nhl.portfolio(res["pool"], res["sim"], args.gpp,
                                                      iters=max(40, args.iters // 3)), 1):
            show(f"GPP {k}", lu)
    else:
        show("GPP", res["gpp"])
    if args.board:
        print(f"\n{'player':24} pos tm  line      $     pts  floor  ceil   own")
        for p in sorted(res["pool"], key=lambda p: -p["proj"])[:args.board]:
            print(f"{p['name'][:24]:24} {p['pos']:3} {p['team']:3} {(p.get('line') or ''):3} "
                  f"{(p.get('pp') or ''):4} {p['salary']:>5} {p['proj']:6.2f} {p['floor']:6.1f} "
                  f"{p['ceil']:5.1f} {p.get('own', 0):5.1f}{'  *est' if p.get('imputed') else ''}")
    if res.get("log", {}) and res["log"].get("logged"):
        print(f"\nlogged {res['log']['n']} players for {res['log']['date']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
