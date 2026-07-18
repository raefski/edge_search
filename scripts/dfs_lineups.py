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
    ap.add_argument("--exclude-teams", default=None,
                    help="comma-separated team abbreviations to drop entirely (e.g. BAL,CHC) -- "
                         "for when DK voids/doesn't count specific games. Not detectable "
                         "automatically (DK's contest-scoring rules aren't exposed via any free "
                         "API), so this is a manual override; --list-teams shows what's in the "
                         "slate plus a game-status flag for anything that looks postponed/suspended.")
    ap.add_argument("--list-teams", action="store_true",
                    help="print every team in the slate (with a game-status warning if applicable) and exit")
    args = ap.parse_args()
    load_env()
    c = OddsAPIClient(cache_dir=ROOT / "data/cache", ledger_path=ROOT / "data/odds_api_credits.json",
                      dry_run=args.from_cache, live_ttl=10**9 if args.from_cache else 600)

    if args.draft_group is not None:
        gid, is_main, meta = dfs_run.resolve_slate(args.draft_group, date=args.date)
        if gid is None:
            sys.exit(f"slate {args.draft_group!r} not found. Available now: {meta.get('available')}")
        print(f"slate {meta['label']}  start {meta.get('start','')}Z  games {meta.get('games')}")

    exclude_teams = {t.strip().upper() for t in args.exclude_teams.split(",")} if args.exclude_teams else None
    res = dfs_run.build_slate(c, args.date, draft_group=args.draft_group, iters=args.iters,
                              exclude_teams=exclude_teams)

    if res.get("error"):
        sys.exit(f"{res['error']}. Available now: {res.get('available')}")
    if res.get("unpriced"):
        print("\nslate not priced yet (no salaries) — likely too far out. Upcoming priced-style slates:")
        for n, i, s, gc in res["upcoming"]:
            print(f"   {n:10} {s}Z  {gc} games  (--draft-group {n})")
        return

    if args.list_teams:
        print("\nteams in this slate:")
        for t in res["all_teams"]:
            flag = res["team_status"].get(t, "")
            excluded = " [EXCLUDED]" if t in res.get("excluded_teams", []) else ""
            warn = f"  ⚠ {flag}" if flag else ""
            print(f"  {t}{warn}{excluded}")
        return
    if res.get("excluded_teams"):
        print(f"excluded: {', '.join(res['excluded_teams'])}")
    flagged = {t: s for t, s in res["team_status"].items() if s and t not in (res.get("excluded_teams") or [])}
    if flagged:
        print(f"⚠ teams with a non-normal game status (consider --exclude-teams): "
              f"{', '.join(f'{t} ({s})' for t, s in flagged.items())}")

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
    cash, gpp, stack_team = res["cash"], res["gpp"], res["stack_team"]

    logged = dfs_run.log_forward_test(ROOT, date, is_main, gid, pool, cash, gpp, games=res.get("games"))
    if logged["logged_projections"]:
        dest = "data/dfs_proj_log.csv" if is_main else f"data/dfs_proj_log_{date}_g{gid}.csv"
        print(f"logged {logged['n']} projections -> {dest}")

    if res.get("pitcher_fetch_error"):
        print(f"⚠ pitcher props pull FAILED: {res['pitcher_fetch_error']}  "
              "(not the normal \"DK hasn't posted yet\" case -- check your API key/credits)")

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
        # Actual composition, not construction target -- see app.py's caption
        # for why (the secondary stack can fall short of n=3 on position
        # conflicts with the primary stack; found live 2026-07-11).
        import collections
        teams = collections.Counter(p["team"] for p, _ in gpp["lineup"] if "P" not in p["pos"])
        parts = [f"{n}-man {t}" for t, n in sorted(teams.items(), key=lambda kv: -kv[1]) if n > 1]
        label = f"GPP ({' + '.join(parts) if parts else 'no multi-team stack'}, leverage-picked)"
        show(label, gpp)
    print(f"\nlineups -> {logged['lineup_file']}")


if __name__ == "__main__":
    main()
