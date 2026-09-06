#!/usr/bin/env python3
"""Scrape one collection profile into the market data store.

    python3 scripts/odds_collect.py --profile dfs_mlb
    python3 scripts/odds_collect.py --profile pickem_nfl --dry-run
    python3 scripts/odds_collect.py --status

Runs on the desktop, not on Streamlit Cloud: DraftKings 403s datacenter IPs
(HANDOFF.md section 2), which is the same reason the arbitrage agent exists.
Schedule it beside that agent.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.odds import PROFILES, OddsStore, collect, get_profile  # noqa: E402
from edge.odds.store import DEFAULT_PATH  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--profile", choices=sorted(PROFILES),
                    help="which collection profile to run")
    ap.add_argument("--db", default=str(DEFAULT_PATH))
    ap.add_argument("--status", action="store_true",
                    help="print what the store holds and exit")
    ap.add_argument("--dry-run", action="store_true",
                    help="scrape and report, write nothing")
    ap.add_argument("--no-strict", action="store_true",
                    help="commit even if a required market is missing")
    ap.add_argument("--publish", action="store_true",
                    help="also write data/odds_snapshot_<profile>.json, which is "
                         "what Streamlit Cloud reads (it cannot see data/odds.db)")
    ap.add_argument("--prune-days", type=int,
                    help="drop scans older than this many days, then exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")

    with OddsStore(args.db) as store:
        if args.status:
            print(json.dumps(store.summary(), indent=2))
            rows = store.conn.execute(
                "SELECT profile, COUNT(*) n, MAX(finished_at) last,"
                " SUM(quote_count) quotes FROM scan WHERE ok=1"
                " GROUP BY profile ORDER BY profile").fetchall()
            print(f"\n{'profile':<14} {'scans':>6} {'quotes':>10}  last")
            for r in rows:
                print(f"{r['profile']:<14} {r['n']:>6} {r['quotes'] or 0:>10}  {r['last']}")
            return 0

        if args.prune_days is not None:
            print(f"pruned {store.prune(args.prune_days)} scan(s)")
            return 0

        if not args.profile:
            ap.error("--profile is required unless --status or --prune-days")

        prof = get_profile(args.profile)
        print(f"profile {prof.name}: {list(prof.sports) or 'all catalogued leagues'} "
              f"(props={prof.props}, prop_events={prof.prop_events}, "
              f"~{prof.est_seconds}s)")
        if prof.notes:
            print(f"  note: {prof.notes}")

        started = time.time()

        def progress(label, done, total):
            print(f"  [{done + 1}/{total}] {label} ...", flush=True)

        if args.dry_run:
            # Scrape through the same path but roll the scan back, so a dry run
            # exercises the real collectors rather than a second code path that
            # could pass while the real one fails.
            from edge.arb.run import scan
            from edge.odds.ingest import board_to_rows
            _opps, stats, board = scan(prof.config(), progress=progress,
                                       return_board=True)
            events, quotes = board_to_rows(board)
            found = sorted({q.market for q in quotes})
            missing = [m for m in prof.required_markets if m not in found]
            print(f"\nDRY RUN: {len(events)} events, {len(quotes)} quotes, "
                  f"{len(found)} distinct markets in {time.time() - started:.0f}s")
            print(f"  conflicts: {stats.get('price_conflicts', 0)} (must be 0)")
            print(f"  missing required: {missing or 'none'}")
            print(f"  markets: {', '.join(found[:25])}"
                  + (" ..." if len(found) > 25 else ""))
            return 1 if missing else 0

        try:
            res = collect(prof, store, progress=progress,
                          strict=not args.no_strict)
        except Exception as exc:
            print(f"\nFAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2

        stats = res["stats"]
        print(f"\nscan {res['scan_id']}: {res['events']} events, "
              f"{res['quotes']} quotes in {time.time() - started:.0f}s")
        print(f"  price_conflicts: {stats.get('price_conflicts', 0)}  (must be 0 "
              f"in a one-shot scan -- see HANDOFF.md section 8)")
        skipped = stats.get("skipped_events") or {}
        if skipped:
            print(f"  skipped: {skipped}")

        if args.publish:
            from edge.odds.publish import export
            info = export(store, prof.name,
                          list(prof.publish_markets) or None)
            print(f"  published {info['events']} events -> {info['path']} "
                  f"({info['bytes'] / 1024:.0f} KB)")
            print(f"  commit it for the cloud app: git add -f {info['path']}")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
