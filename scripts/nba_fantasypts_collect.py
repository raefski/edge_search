#!/usr/bin/env python3
"""Add the player_fantasy_points market for the NBA events already collected by
nba_historical_collect.py -- flagged as a gap in that run's own manifest
(data/HISTORICAL_COLLECTION_README.md): a book-quoted fantasy-points line is
arguably the single most valuable NBA signal (no MLB position had anything
like it), and since the 572 event IDs are already known, adding just this
one market is far cheaper than a full re-pull (1 market vs 7).

Writes to a SEPARATE parallel directory keyed by the same event ids (not
merged into the existing per-event files, to avoid any risk of corrupting
data already collected tonight) -- a downstream ingestion script joins the
two by event id.

Usage: python3 scripts/nba_fantasypts_collect.py [--max-credits 8000]
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.wnba_scout import load_env  # noqa: E402
from edge.client import OddsAPIClient  # noqa: E402

SPORT = "basketball_nba"
EVENTS_CACHE = ROOT / "data" / "nba_historical_events_2025.json"
OUT_DIR = ROOT / "data" / "nba_historical_fantasypts_2025"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-credits", type=int, default=8000)
    args = ap.parse_args()

    load_env()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    events = json.loads(EVENTS_CACHE.read_text())
    c = OddsAPIClient(cache_dir=str(ROOT / "data/cache"),
                      ledger_path=str(ROOT / "data/odds_api_credits.json"), dry_run=False)
    print(f"start: {c.remaining_credits()} credits remaining, {len(events)} known events", flush=True)

    spent = done = skipped = failed = 0
    for i, ev in enumerate(events):
        out = OUT_DIR / f"{ev['id']}.json"
        if out.exists():
            skipped += 1
            continue
        if spent >= args.max_credits:
            print(f"hit --max-credits {args.max_credits}, stopping "
                  f"({len(events) - i} events left for a future run)", flush=True)
            break
        before = c.remaining_credits()
        try:
            r = c.get_historical_event_odds(SPORT, ev["id"], ev["commence_time"],
                                            ["player_fantasy_points"])
        except Exception as e:
            print(f"  [{i+1}/{len(events)}] FAILED {e}", flush=True)
            failed += 1
            continue
        cost = before - c.remaining_credits()
        spent += cost
        out.write_text(json.dumps({"event": ev, "odds": r.get("data", {})}))
        done += 1
        if (i + 1) % 20 == 0 or i == 0:
            print(f"  [{i+1}/{len(events)}] {cost}cr (run total {spent}, "
                  f"remaining {c.remaining_credits()})", flush=True)
        time.sleep(0.05)

    print(f"\nDONE: {done} collected, {skipped} already on disk, {failed} failed, "
          f"{spent} credits spent, {c.remaining_credits()} remaining -> {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
