#!/usr/bin/env python3
"""Log current DK +EV candidates as open CLV positions (0 credits — reads cache).

Runs the WNBA scan over already-cached odds and snapshots every flagged DK bet
to data/clv_log.csv. Re-running is idempotent (de-duped per bet).
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.wnba_scout import load_env, SPORT, FEATURED, DEFAULT_PROPS, SHARP_BOOKS  # noqa: E402
from edge.client import OddsAPIClient  # noqa: E402
from edge.scanner import scan  # noqa: E402
from edge.clv import log_open_bets, load  # noqa: E402

LOG = ROOT / "data/clv_log.csv"


def main():
    load_env()
    # cache-only: huge TTL so we read the last pull without spending; dry_run
    # guarantees no paid call sneaks through if something isn't cached.
    c = OddsAPIClient(cache_dir=ROOT / "data/cache", ledger_path=ROOT / "data/odds_api_credits.json",
                      dry_run=True, live_ttl=10**9)
    events = c.get_events(SPORT)
    allev = list(c.get_featured_odds(SPORT, FEATURED, "us"))
    for ev in events:
        allev.append(c.get_event_odds(SPORT, ev["id"], DEFAULT_PROPS, "us"))

    flagged = scan(allev, target_books={"draftkings"}, ref_books=set(SHARP_BOOKS))
    added = log_open_bets(flagged, allev, SPORT, LOG)
    print(f"Flagged DK bets: {len(flagged)}  |  newly logged: {len(added)}  |  total in log: {len(load(LOG))}")
    for r in added:
        print(f"  + {r['commence_time']}  {r['event']}  {r['subject']} {r['side']} "
              f"{r['point']} {r['market']} @ {r['taken_american']:+d}  (EV {float(r['ev_at_scan'])*100:.1f}%)")
    print(f"\nLog: {LOG}")


if __name__ == "__main__":
    main()
