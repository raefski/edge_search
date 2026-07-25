#!/usr/bin/env python3
"""Download free NFL ground-truth data (nflverse, no API key, no cost) for a
season: per-player weekly box scores, per-team weekly box scores (for DST
scoring), and final game scores (for DST points-allowed). Mirrors the role
data/bt_boxscores/ plays for MLB -- free, cached, reusable across many
backtest/ingestion runs.

nflverse publishes via GitHub release assets (CSV). As of this collection
(2026-07-24), the `player_stats` release has 2024 season files but NOT yet
a 2025 one (the more detailed per-player pipeline lags the simpler
schedules/scores pipeline, which IS current through 2025) -- this script
handles that gap by fetching whatever's available and reporting clearly
what's missing, rather than failing silently or pretending data exists.

Usage: python3 scripts/nfl_ground_truth_collect.py [--season 2024]
"""
import argparse
import csv
import io
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "nfl_ground_truth"
UA = {"User-Agent": "edge-search (research use)"}
BASE = "https://github.com/nflverse/nflverse-data/releases/download"


def fetch_csv(url):
    req = urllib.request.Request(url, headers=UA)
    raw = urllib.request.urlopen(req, timeout=90).read().decode()
    return list(csv.DictReader(io.StringIO(raw)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2024)
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    yr = args.season

    print(f"season {yr}:", flush=True)

    # per-player weekly box scores (passing/rushing/receiving/fumbles/etc)
    try:
        rows = fetch_csv(f"{BASE}/player_stats/stats_player_week_{yr}.csv")
        (OUT_DIR / f"player_week_{yr}.json").write_text(json.dumps(rows))
        print(f"  player_week_{yr}: {len(rows)} rows", flush=True)
    except Exception as e:
        print(f"  player_week_{yr}: NOT AVAILABLE ({e}) -- nflverse's detailed "
              f"pipeline may not have caught up to this season yet", flush=True)

    # per-team weekly box scores (sacks, INTs, fumble recoveries, def TDs -- DST inputs)
    try:
        rows = fetch_csv(f"{BASE}/player_stats/stats_team_week_{yr}.csv")
        (OUT_DIR / f"team_week_{yr}.json").write_text(json.dumps(rows))
        print(f"  team_week_{yr}: {len(rows)} rows", flush=True)
    except Exception as e:
        print(f"  team_week_{yr}: NOT AVAILABLE ({e})", flush=True)

    # full schedule + final scores (all seasons in one file; DST points-allowed)
    try:
        rows = fetch_csv(f"{BASE}/schedules/games.csv")
        season_rows = [r for r in rows if r["season"] == str(yr) and r["game_type"] == "REG"]
        (OUT_DIR / "games.json").write_text(json.dumps(rows))  # keep all seasons, it's small
        print(f"  games.csv: {len(rows)} total rows, {len(season_rows)} REG games in {yr} "
              f"(scored: {sum(1 for r in season_rows if r.get('home_score'))})", flush=True)
    except Exception as e:
        print(f"  games.csv: FAILED ({e})", flush=True)

    print(f"-> {OUT_DIR}", flush=True)


if __name__ == "__main__":
    main()
