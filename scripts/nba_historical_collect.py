#!/usr/bin/env python3
"""Collect a durable historical NBA player-prop SAMPLE while the paid key
still has credit budget (see nfl_historical_collect.py's docstring for the
same one-shot-opportunity rationale). Unlike NFL (262 games/season, fully
affordable), a full NBA season is ~1,230 games at ~130cr/event -- roughly
160k credits, more than this key has left after the NFL pull. This
deliberately SAMPLES rather than exhausts: a well-distributed few hundred
games (spread across months, not clustered) is already large-n for the
props-vs-skill-model test this is collecting for (MLB's own early signal
tests ran on comparably-sized samples, e.g. DFS_METHODOLOGY.md §4's
team_total check used 3,501 hitter-games).

Pulls closing lines for 7 core markets: player_points, player_rebounds,
player_assists, player_threes, player_blocks, player_steals,
player_turnovers -- covers every DK NBA Classic scoring category except
free throws.

Usage: python3 scripts/nba_historical_collect.py [--max-credits 30000] [--sample-every-n-days 3]
"""
import argparse
import datetime
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.wnba_scout import load_env  # noqa: E402
from edge.client import OddsAPIClient  # noqa: E402

SPORT = "basketball_nba"
CORE_MARKETS = ["player_points", "player_rebounds", "player_assists", "player_threes",
                "player_blocks", "player_steals", "player_turnovers"]
OUT_DIR = ROOT / "data" / "nba_historical_props_2025"
EVENTS_CACHE = ROOT / "data" / "nba_historical_events_2025.json"

# 2024-25 NBA regular season: ~2024-10-22 through ~2025-04-13.
SEASON_START = datetime.date(2024, 10, 22)
SEASON_END = datetime.date(2025, 4, 13)


def sample_dates(every_n_days):
    d, out = SEASON_START, []
    while d <= SEASON_END:
        out.append(d.isoformat())
        d += datetime.timedelta(days=every_n_days)
    return out


def collect_events(c, every_n_days):
    if EVENTS_CACHE.exists():
        return json.loads(EVENTS_CACHE.read_text())
    seen = {}
    for day in sample_dates(every_n_days):
        try:
            r = c.get_historical_events(SPORT, f"{day}T23:30:00Z")
        except Exception as e:
            print(f"  events@{day}: {e}", flush=True)
            continue
        for e in r.get("data", []):
            seen[e["id"]] = {"id": e["id"], "commence_time": e["commence_time"],
                             "home_team": e["home_team"], "away_team": e["away_team"]}
        if len(seen) % 40 < 6:  # light progress logging, not every single day
            print(f"  {day}: {len(seen)} unique events so far (rem {c.remaining_credits()})", flush=True)
    events = sorted(seen.values(), key=lambda e: e["commence_time"])
    EVENTS_CACHE.write_text(json.dumps(events, indent=1))
    return events


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-credits", type=int, default=30000)
    ap.add_argument("--sample-every-n-days", type=int, default=3,
                    help="pull the events list every N days across the season -- "
                         "spreads the sample across the whole year instead of "
                         "clustering in one stretch; games/day still adds up fast")
    args = ap.parse_args()

    load_env()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    c = OddsAPIClient(cache_dir=str(ROOT / "data/cache"),
                      ledger_path=str(ROOT / "data/odds_api_credits.json"), dry_run=False)
    print(f"start: {c.remaining_credits()} credits remaining", flush=True)

    print(f"collecting event list every {args.sample_every_n_days}d (cheap, 1cr/snapshot)...", flush=True)
    events = collect_events(c, args.sample_every_n_days)
    print(f"{len(events)} unique real NBA games found in the sample", flush=True)

    spent_this_run = 0
    done = skipped_exist = failed = 0
    for i, ev in enumerate(events):
        out = OUT_DIR / f"{ev['id']}.json"
        if out.exists():
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
        out.write_text(json.dumps({"event": ev, "odds": r.get("data", {})}))
        done += 1
        if (i + 1) % 10 == 0 or i == 0:
            print(f"  [{i+1}/{len(events)}] {ev['away_team']}@{ev['home_team']}: "
                  f"{cost}cr (run total {spent_this_run}, remaining {c.remaining_credits()})", flush=True)
        time.sleep(0.05)

    print(f"\nDONE: {done} new games collected, {skipped_exist} already on disk, "
          f"{failed} failed, {spent_this_run} credits spent this run, "
          f"{c.remaining_credits()} remaining -> {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
