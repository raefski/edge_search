#!/usr/bin/env python3
"""Collect a durable historical NFL player-prop database while the paid key
still has credit budget (91k+ credits, expiring 2026-07-24 -- this is a
one-shot opportunity, not a repeatable free pull like MLB's box-score
backtest). Mirrors the MLB project's own pattern: build the raw dataset now,
test props-vs-skill-model later against free ground truth (nflverse), same
discipline that killed MLB batter props and validated MLB pitcher props --
don't assume which way NFL breaks.

Pulls CLOSING lines (date=commence_time returns the closest snapshot <=
that timestamp, i.e. the last posted line before kickoff) for every real
2025 regular-season game, across 8 core skill-position markets:
  player_pass_yds, player_pass_tds, player_pass_interceptions,
  player_rush_yds, player_rush_tds,
  player_receptions, player_reception_yds, player_reception_tds

Resumable: writes one file per event, skips ones already on disk, so an
interrupted run (or a deliberate --max-credits cutoff) can continue later
during this same key's remaining window.

Usage: python3 scripts/nfl_historical_collect.py [--max-credits 25000] [--weeks-back 20]
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

SPORT = "americanfootball_nfl"
CORE_MARKETS = [
    "player_pass_yds", "player_pass_tds", "player_pass_interceptions",
    "player_rush_yds", "player_rush_tds",
    "player_receptions", "player_reception_yds", "player_reception_tds",
]
SEASON_WEEK_SNAPSHOTS = {
    # 2025 regular season: ~2025-09-04 (Thu opener) through ~2026-01-05 (Wk 18).
    2025: ["2025-09-09", "2025-09-16", "2025-09-23", "2025-09-30",
          "2025-10-07", "2025-10-14", "2025-10-21", "2025-10-28",
          "2025-11-04", "2025-11-11", "2025-11-18", "2025-11-25",
          "2025-12-02", "2025-12-09", "2025-12-16", "2025-12-23",
          "2025-12-30", "2026-01-06"],
    # 2024 regular season: ~2024-09-05 (Thu opener) through ~2025-01-06 (Wk 18) --
    # added 2026-07-24 for a season-to-season robustness check, the same
    # discipline the salary-regression split-sample check used (DFS_METHODOLOGY.md).
    2024: ["2024-09-10", "2024-09-17", "2024-09-24", "2024-10-01",
          "2024-10-08", "2024-10-15", "2024-10-22", "2024-10-29",
          "2024-11-05", "2024-11-12", "2024-11-19", "2024-11-26",
          "2024-12-03", "2024-12-10", "2024-12-17", "2024-12-24",
          "2024-12-31", "2025-01-07"],
}


def out_dir(season):
    return ROOT / "data" / f"nfl_historical_props_{season}"


def events_cache(season):
    return ROOT / "data" / f"nfl_historical_events_{season}.json"


def collect_events(c, season):
    cache = events_cache(season)
    if cache.exists():
        return json.loads(cache.read_text())
    seen = {}
    for wk in SEASON_WEEK_SNAPSHOTS[season]:
        try:
            r = c.get_historical_events(SPORT, f"{wk}T12:00:00Z")
        except Exception as e:
            print(f"  events@{wk}: {e}", flush=True)
            continue
        for e in r.get("data", []):
            seen[e["id"]] = {"id": e["id"], "commence_time": e["commence_time"],
                             "home_team": e["home_team"], "away_team": e["away_team"]}
        print(f"  {wk}: {len(r.get('data', []))} events seen, {len(seen)} unique so far "
              f"(rem {c.remaining_credits()})", flush=True)
    events = sorted(seen.values(), key=lambda e: e["commence_time"])
    cache.write_text(json.dumps(events, indent=1))
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2025, choices=sorted(SEASON_WEEK_SNAPSHOTS))
    ap.add_argument("--max-credits", type=int, default=30000,
                    help="stop once this many credits have been spent THIS RUN")
    args = ap.parse_args()
    out = out_dir(args.season)

    load_env()
    out.mkdir(parents=True, exist_ok=True)
    c = OddsAPIClient(cache_dir=str(ROOT / "data/cache"),
                      ledger_path=str(ROOT / "data/odds_api_credits.json"), dry_run=False)
    start_remaining = c.remaining_credits()
    print(f"start: {start_remaining} credits remaining (season {args.season})", flush=True)

    print("collecting event list (cheap, 1cr/snapshot)...", flush=True)
    events = collect_events(c, args.season)
    print(f"{len(events)} unique real {args.season} NFL games found", flush=True)

    spent_this_run = 0
    done = skipped_exist = failed = 0
    for i, ev in enumerate(events):
        outf = out / f"{ev['id']}.json"
        if outf.exists():
            skipped_exist += 1
            continue
        if spent_this_run >= args.max_credits:
            print(f"hit --max-credits {args.max_credits}, stopping "
                  f"({len(events) - i} games left for a future run)", flush=True)
            break
        before = c.remaining_credits()
        try:
            r = c.get_historical_event_odds(SPORT, ev["id"], ev["commence_time"], CORE_MARKETS)
        except Exception as e:
            print(f"  [{i+1}/{len(events)}] {ev['away_team']}@{ev['home_team']}: FAILED {e}", flush=True)
            failed += 1
            continue
        cost = before - c.remaining_credits()
        spent_this_run += cost
        outf.write_text(json.dumps({"event": ev, "odds": r.get("data", {})}))
        done += 1
        if (i + 1) % 10 == 0 or i == 0:
            print(f"  [{i+1}/{len(events)}] {ev['away_team']}@{ev['home_team']}: "
                  f"{cost}cr (run total {spent_this_run}, remaining {c.remaining_credits()})", flush=True)
        time.sleep(0.05)  # light pacing, not required but courteous

    print(f"\nDONE: {done} new games collected, {skipped_exist} already on disk, "
          f"{failed} failed, {spent_this_run} credits spent this run, "
          f"{c.remaining_credits()} remaining -> {out}", flush=True)


if __name__ == "__main__":
    main()
