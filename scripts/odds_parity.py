#!/usr/bin/env python3
"""Measure the free scraped path against the paid Odds API, on one real slate.

    python3 scripts/odds_parity.py --pickem            # ~2 credits
    python3 scripts/odds_parity.py --dfs --confirm     # ~2 credits per MLB event

WHY THIS RUNS ONCE AND MATTERS FOREVER
The comparison is only possible while the Odds API key still has credits. After
that there is no way to establish what dropping it cost -- so this is evidence
that has to be collected before the migration finishes, not after.

WHAT IT COMPARES
Both clients satisfy the same interface, so the SAME consumer code runs over
both and any difference is attributable to the source rather than to a second
code path. Pick'em is compared in spread/total points; DFS in projected DK
fantasy points, which is the unit the optimiser consumes.

MARKET LIST IS SHARED ON PURPOSE
--dfs asks both sources for the same two markets (`pitcher_outs`,
`pitcher_strikeouts`) -- the two `project_pitcher` requires. Everything else it
imputes from league rates, identically on both sides, so restricting the list
cuts the credit cost by two thirds without biasing the difference.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def load_key() -> str | None:
    """Prefer the environment; fall back to either repo's .env.

    The key lives in ~/arbitrage/.env on this machine, not in edge_search's.
    Both are gitignored (HANDOFF.md section 6) and neither is printed here.
    """
    if os.environ.get("ODDS_API_KEY"):
        return os.environ["ODDS_API_KEY"]
    for env in (ROOT / ".env", Path.home() / "arbitrage" / ".env"):
        if not env.exists():
            continue
        for line in env.read_text().splitlines():
            if line.startswith("ODDS_API_KEY") and "=" in line:
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
                if key:
                    os.environ["ODDS_API_KEY"] = key
                    return key
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pickem", action="store_true", help="NFL lines parity (~2 credits)")
    ap.add_argument("--dfs", action="store_true", help="MLB pitcher projection parity")
    ap.add_argument("--confirm", action="store_true",
                    help="actually spend credits; without it, estimate only")
    ap.add_argument("--db", default=None)
    args = ap.parse_args()
    if not (args.pickem or args.dfs):
        ap.error("pick at least one of --pickem / --dfs")

    from edge.client import OddsAPIClient
    from edge.odds import OddsStore, ScrapedOddsClient
    from edge.odds.parity import compare_dfs_pitchers, compare_pickem, report

    if not load_key():
        print("no ODDS_API_KEY found in env, ./.env or ~/arbitrage/.env",
              file=sys.stderr)
        return 2

    paid = OddsAPIClient(cache_dir=ROOT / "data/cache",
                         ledger_path=ROOT / "data/odds_api_credits.json",
                         dry_run=not args.confirm, live_ttl=600)
    store = OddsStore(args.db) if args.db else OddsStore()

    if args.pickem:
        from edge.pickem_live import MARKETS, SPORT, fetch_week
        free_client = ScrapedOddsClient(store, "pickem_nfl", max_age_seconds=86400)
        print(f"pick'em NFL parity -- estimated cost {len(MARKETS)} credit(s)"
              + ("" if args.confirm else "   [DRY RUN, add --confirm to spend]"))
        free_games = fetch_week(free_client, SPORT)
        try:
            paid_games = fetch_week(paid, SPORT)
        except Exception as exc:
            print(f"  paid path unavailable: {type(exc).__name__}: {exc}")
            paid_games = []
        if paid_games:
            print()
            print(report(compare_pickem(paid_games, free_games),
                         "PICK'EM NFL -- paid Odds API vs free scrape"))
            pb = sorted({b for g in paid_games for b in g.book_lines})
            fb = sorted({b for g in free_games for b in g.book_lines})
            print(f"\n  paid books ({len(pb)}): {', '.join(pb)}")
            print(f"  free books ({len(fb)}): {', '.join(fb)}")

    if args.dfs:
        # Only what project_pitcher REQUIRES -- see module docstring.
        markets = ["pitcher_outs", "pitcher_strikeouts"]
        sport = "baseball_mlb"
        free_client = ScrapedOddsClient(store, "dfs_mlb", max_age_seconds=86400)
        n = len(free_client.get_events(sport))
        print(f"\nDFS MLB parity -- {n} events x {len(markets)} markets = "
              f"~{n * len(markets)} credits"
              + ("" if args.confirm else "   [DRY RUN, add --confirm to spend]"))
        if args.confirm:
            res = compare_dfs_pitchers(paid, free_client, sport, markets)
            print()
            print(report(res, "MLB DFS -- paid Odds API vs free scrape"))

    print(f"\ncredits spent this run: {paid.spent_this_session}  "
          f"remaining: {paid.remaining_credits()}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
