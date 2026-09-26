#!/usr/bin/env python3
"""Build DK MMA Classic lineups: a CASH lineup and a GPP lineup.

    python3 scripts/dfs_lineups_mma.py                    # cash + gpp, next main card
    python3 scripts/dfs_lineups_mma.py --capture          # refresh DK Sportsbook first
    python3 scripts/dfs_lineups_mma.py --board            # the fighter board
    python3 scripts/dfs_lineups_mma.py --mode gpp -n 3    # a GPP portfolio
    python3 scripts/dfs_lineups_mma.py --lock "Ailin Perez" --ban "Raoni Barcelos"
    python3 scripts/dfs_lineups_mma.py --list-slates

WHERE THE NUMBERS COME FROM
  win / method / round   DraftKings Sportsbook (data/odds_snapshot_dfs_mma.json,
                         written by scripts/mma_odds_capture.py), de-biased --
                         the method market underprices decisions by 3-4 points
                         in every era since 2013 (edge/mma_market.py)
  stats                  UFCStats: each fighter's style index, relative to what
                         his results alone would predict (edge/mma_sim.py)
  objectives             P(beat the double-up line) and P(top 1%), against a
                         simulated field on the same simulated fights
                         (edge/dfs_mma_theory.py)

NO LATE SWAP. Lock is the first bell of the first fight on the slate, which is
three-plus hours before the main event. Capture odds and build before that.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import dfs, dfs_opt_mma, dfs_run_mma as R  # noqa: E402


def _pct(x: float) -> str:
    return f"{100 * x:.1f}%"


def show(res: dict, mode: str, title: str | None = None) -> None:
    if not res or "lineup" not in res:
        print(f"\n{mode.upper()}: {res.get('error', 'no legal lineup') if res else 'none'}")
        return
    head = (f"P(cash) {_pct(res['p_cash'])}" if mode == "cash"
            else f"P(top 1%) {_pct(res['p_top1'])}")
    print(f"\n{title or mode.upper()}: {head}   proj {res['proj']}  "
          f"floor(p25) {res['floor']}  ceil(p90) {res['ceil']}   "
          f"${res['salary']:,}/50,000   exp. wins {res['exp_wins']}   "
          f"field own {res['own']:.0f}%")
    if res.get("same_fight"):
        print("  ! holds BOTH corners of a fight -- a guaranteed winner and a "
              "guaranteed loser, chosen by the objective, not by accident")
    print(f"  {'fighter':<23}{'vs':<18}{'$':>7}{'win':>7}{'fin':>6}{'proj':>7}"
          f"{'p25':>6}{'p90':>6}{'own':>6}  src")
    for r in R.lineup_rows(res):
        tag = "CPT " if r.get("cpt") else ""
        print(f"  {(tag + r['fighter'])[:22]:<23}{r['opponent'][:17]:<18}{r['salary']:>7,}"
              f"{_pct(r['p_win']):>7}{100 * r['p_finish']:>5.0f}%{r['proj']:>7.1f}"
              f"{r['floor']:>6.0f}{r['ceil']:>6.0f}{r['own']:>5.0f}%  "
              f"{r['source']}{' 5R' if r['sched'] == 5 else ''}")


def show_board(pool: list, top: int) -> None:
    rows = sorted(pool, key=lambda d: -d["proj"])[:top]
    print(f"\n{'fighter':<23}{'vs':<18}{'$':>7}{'win':>7}{'fin':>6}{'R1':>6}"
          f"{'proj':>7}{'p25':>6}{'p90':>6}{'val':>6}{'own':>6}{'FPPF':>7}{'style':>7}  src")
    for d in rows:
        fp = f"{d['dk_fppf']:.1f}" if d.get("dk_fppf") is not None else "  --"
        print(f"{d['name'][:22]:<23}{d['opponent'][:17]:<18}{d['salary']:>7,}"
              f"{_pct(d['p_win']):>7}{100 * d['p_finish']:>5.0f}%{100 * d['p_r1']:>5.0f}%"
              f"{d['proj']:>7.1f}{d['floor']:>6.0f}{d['ceil']:>6.0f}{d['value']:>6.2f}"
              f"{d['own']:>5.0f}%{fp:>7}{d['off']:>7.2f}  "
              f"{d['source']}{' 5R' if d['sched'] == 5 else ''}"
              f"{'' if d.get('key') else ' DEBUT'}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--draft-group", type=int)
    ap.add_argument("--list-slates", action="store_true")
    ap.add_argument("--capture", action="store_true",
                    help="refresh the DK Sportsbook snapshot first (8 requests)")
    ap.add_argument("--board", action="store_true")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--mode", choices=("both", "cash", "gpp"), default="both")
    ap.add_argument("-n", type=int, default=1, help="lineups per theory")
    ap.add_argument("--sims", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--lock", action="append", default=[], metavar="FIGHTER")
    ap.add_argument("--ban", action="append", default=[], metavar="FIGHTER")
    ap.add_argument("--no-log", action="store_true",
                    help="do not write data/dfs_proj_log_mma.csv")
    args = ap.parse_args()

    groups = dfs.draft_groups(R.DK_SPORT)
    if args.list_slates:
        for s in R.classic_groups(groups):
            print(f"{s['gid']}  {s['label']:<16} {s['fights']:>2} fights  {s['start']}")
        return 0
    if args.capture:
        subprocess.run([sys.executable, str(ROOT / "scripts/mma_odds_capture.py")],
                       check=False)

    res = R.build_slate(args.draft_group, n_sims=args.sims, seed=args.seed,
                        groups=groups, persist=not args.no_log,
                        locked=args.lock, banned=args.ban)
    if res.get("unpriced"):
        print("DraftKings lists this slate but has not priced it yet.")
        return 1
    if res.get("error"):
        print(res["error"])
        return 1
    st, book = res["stats"], res["stats"].get("book", {})
    print(f"draft group {res['gid']} ({res['meta'].get('label')}): {st['fights']} fights, "
          f"salaries {st['draftables_source']}; odds captured {book.get('generated_at')}"
          + (f"  ** {book['age_hours']:.1f}h OLD **" if book.get("stale") and book.get("age_hours") else ""))
    for label, key in (("scratched", "scratched"), ("REPLACEMENT OPPONENT -- book prices "
                       "a different fight, using DK salaries instead", "replaced"),
                       ("no sportsbook market", "no_market"), ("UFC debut", "debuts"),
                       ("has a DK FPPF but no UFCStats match", "unmatched")):
        if st.get(key):
            print(f"  {label}: {', '.join(st[key])}")
    print(f"  field lines (median over sims): cash {res['lines']['cash']['median']}, "
          f"top-1% {res['lines']['gpp']['median']}")

    if args.board:
        show_board(res["pool"], args.top)
    if args.n > 1:
        # A portfolio needs the per-sim lines, which build_slate keeps.
        pool, pts, lines = res["pool"], res["points"], res["_lines"]
        import numpy as np
        sal = np.array([d["salary"] for d in pool])
        cap = res.get("captain", False)
        lus = None
        if cap:
            cpt = np.array([d.get("cpt_salary") or round(1.5 * d["salary"]) for d in pool])
            lus = dfs_opt_mma.legal_captain_lineups(sal, cpt)
        for mode in (("cash", "gpp") if args.mode == "both" else (args.mode,)):
            for k, lu in enumerate(dfs_opt_mma.portfolio(pts, sal, lines, args.n, mode,
                                                          lineups=lus, captain=cap), 1):
                show(R._lineup_result(pool, pts, lu["idx"], lines, cap), mode,
                     f"{mode.upper()} #{k}")
    else:
        for mode in (("cash", "gpp") if args.mode == "both" else (args.mode,)):
            show(res[mode], mode)
    if res.get("log", {}) and res["log"].get("logged"):
        print(f"\nlogged {res['log']['n']} fighters -> data/dfs_proj_log_mma.csv; "
              f"lineups -> {res['log'].get('lineup_file')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
