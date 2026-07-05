#!/usr/bin/env python3
"""Build DK MLB DFS cash + GPP lineups.

Pitchers: Vegas-implied projection from sportsbook props (Odds API).
Hitters:  skill x opportunity x park x matchup model (backtested corr ~0.16 on
          19k 2025 hitter-games vs 0.02 for the old prop-only model). Needs
          confirmed lineups (statsapi) for batting-order/opportunity, so the
          hitter pool is empty until lineups post (~3-4h pregame).
Then a dependency-free optimizer builds CASH (mean) and GPP (stack + ceiling
faded by modeled ownership) lineups, and logs everything for forward grading.

The pipeline itself lives in edge/dfs_run.build_slate so the Streamlit app
(app.py) runs the exact same code. This script keeps the CLI printing + the
forward-test CSV logs.
"""
import argparse
import csv
import datetime
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.wnba_scout import load_env  # noqa: E402
from edge.client import OddsAPIClient  # noqa: E402
from edge import dfs, dfs_opt, dfs_run  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from-cache", action="store_true")
    ap.add_argument("--iters", type=int, default=800)
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    ap.add_argument("--draft-group", default=None,
                    help="slate NAME (Main/Early/Turbo/Night/Afternoon) or a numeric id; default = main slate")
    args = ap.parse_args()
    load_env()
    c = OddsAPIClient(cache_dir=ROOT / "data/cache", ledger_path=ROOT / "data/odds_api_credits.json",
                      dry_run=args.from_cache, live_ttl=10**9 if args.from_cache else 600)

    if args.draft_group is not None:
        gid, is_main, meta = dfs_run.resolve_slate(args.draft_group)
        if gid is None:
            sys.exit(f"slate {args.draft_group!r} not found. Available now: {meta.get('available')}")
        print(f"slate {meta['label']}  start {meta.get('start','')}Z  games {meta.get('games')}")

    res = dfs_run.build_slate(c, args.date, draft_group=args.draft_group, iters=args.iters)

    if res.get("error"):
        sys.exit(f"{res['error']}. Available now: {res.get('available')}")
    if res.get("unpriced"):
        print("\nslate not priced yet (no salaries) — likely too far out. Upcoming priced-style slates:")
        for n, i, s, gc in res["upcoming"]:
            print(f"   {n:10} {s}Z  {gc} games  (--draft-group {n})")
        return

    print(f"{res['salaries_n']} salaries | {res['skill_n']} skill | {res['lineup_hitters_n']} lineup hitters | rem {c.remaining_credits()}")
    ph, hh = res["pitchers"], res["hitters"]
    nproj = sum(1 for h in hh if not h.get("confirmed", True))
    tag = f" ({len(hh) - nproj} confirmed, {nproj} PROJECTED*)" if nproj else " (all confirmed)"
    print(f"pool: {len(ph)} pitchers, {len(hh)} hitters{tag} | spent {c.spent_this_session} cr")
    _log_and_optimize(args, res)
    print(f"remaining {c.remaining_credits()} cr")


def _log_and_optimize(args, res):
    date = args.date
    pool, is_main, gid = res["pool"], res["is_main"], res["gid"]
    # only the main slate writes the forward-test log; a sub-slate (--draft-group)
    # must not clobber it with its smaller player set.
    if is_main:
        plog = ROOT / "data/dfs_proj_log.csv"
        prior = [r for r in csv.DictReader(open(plog))] if plog.exists() else []
        with plog.open("w", newline="") as fh:
            w = csv.writer(fh); w.writerow(["date", "player", "team", "pos", "salary", "proj", "ceiling", "own", "conf"])
            for r in prior:
                if r["date"] != date:
                    w.writerow([r[k] for k in ("date", "player", "team", "pos", "salary", "proj", "ceiling", "own", "conf")])
            for p in pool:
                w.writerow([date, p["name"], p["team"], "/".join(sorted(p["pos"])), p["salary"],
                            p["proj"], p.get("ceiling"), p.get("own", ""), p["conf"]])
        print(f"logged {len(pool)} projections -> data/dfs_proj_log.csv")
    else:
        print(f"(sub-slate draft group {gid}: not overwriting the main forward-test log)")

    cash, gpp, stack_team = res["cash"], res["gpp"], res["stack_team"]
    if cash is None and gpp is None:
        print("pool too thin to build lineups (lineups not posted yet?) — pitchers logged; re-run near lock.")
        return

    def show(title, r):
        own_tot = sum(p.get("own", 0) for p, _ in r["lineup"])
        print(f"\n=== {title} ===  proj {r['proj']}  ceil {r['ceil']}  totOwn {own_tot:.0f}%  salary ${r['salary']:,}")
        for p, slot in sorted(r["lineup"], key=lambda x: dfs_opt.SLOTS.index(x[1])):
            print(f"  {slot:3} {p['name']:22} {p['team']:3} ${p['salary']:>5} {p['proj']:>5} "
                  f"ceil {p['ceiling']:>5} own {p.get('own', 0):>4.1f}%  {p['conf']}")

    if cash:
        show("CASH (mean / floor)", cash)
    if gpp:
        show(f"GPP (4-man {stack_team} stack + ceiling)", gpp)
    fname = f"data/dfs_lineups_{date}.csv" if is_main else f"data/dfs_lineups_{date}_g{gid}.csv"
    with (ROOT / fname).open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["mode", "slot", "player", "team", "salary", "proj", "ceiling", "own", "pos", "game", "conf"])
        for mode, r in (("cash", cash), ("gpp", gpp)):
            for p, slot in sorted(r["lineup"], key=lambda x: dfs_opt.SLOTS.index(x[1])) if r else []:
                w.writerow([mode, slot, p["name"], p["team"], p["salary"], p["proj"], p["ceiling"],
                            p.get("own"), "/".join(sorted(p["pos"])), p.get("game", ""), p.get("conf", "")])
    print(f"\nlineups -> {fname}")


if __name__ == "__main__":
    main()
