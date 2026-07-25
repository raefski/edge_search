#!/usr/bin/env python3
"""Download free NBA ground-truth data (stats.nba.com's own backend -- no
API key, no cost, but unofficial/undocumented for third-party use, unlike
MLB's statsapi or nflverse's GitHub releases; needs browser-like headers or
it 403s). One bulk call returns every player's box score for a whole
season. Mirrors data/bt_boxscores/'s role for MLB.

Usage: python3 scripts/nba_ground_truth_collect.py [--season 2024-25]
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "data" / "nba_ground_truth"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://www.nba.com/",
    "Origin": "https://www.nba.com",
    "Accept": "application/json",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2024-25")
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    url = (f"https://stats.nba.com/stats/leaguegamelog?Counter=0&DateFrom=&DateTo="
          f"&LeagueID=00&PlayerOrTeam=P&Season={args.season}&SeasonType=Regular+Season&Sorter=DATE")
    req = urllib.request.Request(url, headers=HEADERS)
    d = json.load(urllib.request.urlopen(req, timeout=60))
    rs = d["resultSets"][0]
    cols = rs["headers"]
    rows = [dict(zip(cols, r)) for r in rs["rowSet"]]
    out = OUT_DIR / f"player_gamelog_{args.season}.json"
    out.write_text(json.dumps(rows))
    print(f"{len(rows)} player-game rows -> {out}", flush=True)

    # team-level too (opponent context, pace, etc -- useful context, small/free)
    url_t = url.replace("PlayerOrTeam=P", "PlayerOrTeam=T")
    req_t = urllib.request.Request(url_t, headers=HEADERS)
    d_t = json.load(urllib.request.urlopen(req_t, timeout=60))
    rs_t = d_t["resultSets"][0]
    rows_t = [dict(zip(rs_t["headers"], r)) for r in rs_t["rowSet"]]
    out_t = OUT_DIR / f"team_gamelog_{args.season}.json"
    out_t.write_text(json.dumps(rows_t))
    print(f"{len(rows_t)} team-game rows -> {out_t}", flush=True)


if __name__ == "__main__":
    main()
