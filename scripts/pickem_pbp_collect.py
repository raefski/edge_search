#!/usr/bin/env python3
"""Download nflverse play-by-play and aggregate to one row per (game, team).

Output: data/pbp_team_game.csv -- ~2 rows per game, tiny (a few hundred KB),
committed. The raw pbp (19MB/season gzipped) is NOT kept: everything the
efficiency model needs is in the aggregate, and re-running this script
regenerates it for free from nflverse's public releases.

Aggregates each team's OFFENSIVE plays in each game (the defense's numbers
are just the opponent's offensive row, looked up from the other side), so
there is exactly one source of truth per play.

Garbage time: plays are tagged by win probability at snap. `_gt` columns
exclude plays where wp is outside [0.05, 0.95] -- real DVOA does something
similar. Both variants are stored so the model can test which is better
rather than assuming (see PICKEM_MODEL.md).

Stdlib only, matching this repo's zero-dep core convention.
Run: python3 scripts/pickem_pbp_collect.py [first_season] [last_season]
"""
from __future__ import annotations

import csv
import gzip
import io
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "pbp_team_game.csv"
URL = "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{}.csv.gz"

FIELDS = ["season", "week", "game_id", "team", "opp", "is_home",
          "off_plays", "off_epa", "off_pass_plays", "off_pass_epa",
          "off_rush_plays", "off_rush_epa", "off_success",
          "off_plays_gt", "off_epa_gt"]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def collect_season(season: int) -> list[dict]:
    raw = urllib.request.urlopen(URL.format(season), timeout=180).read()
    acc: dict[tuple, dict] = {}
    with gzip.open(io.BytesIO(raw), "rt") as fh:
        for p in csv.DictReader(fh):
            if p.get("season_type") != "REG":
                continue
            if p.get("play_type") not in ("pass", "run"):
                continue
            epa = _f(p.get("epa"))
            off, dfn = p.get("posteam"), p.get("defteam")
            if epa is None or not off or not dfn:
                continue
            key = (p["game_id"], off)
            a = acc.get(key)
            if a is None:
                a = acc[key] = {
                    "season": season, "week": int(p["week"]), "game_id": p["game_id"],
                    "team": off, "opp": dfn, "is_home": int(p.get("home_team") == off),
                    "off_plays": 0, "off_epa": 0.0, "off_pass_plays": 0, "off_pass_epa": 0.0,
                    "off_rush_plays": 0, "off_rush_epa": 0.0, "off_success": 0,
                    "off_plays_gt": 0, "off_epa_gt": 0.0,
                }
            a["off_plays"] += 1
            a["off_epa"] += epa
            if p.get("play_type") == "pass":
                a["off_pass_plays"] += 1
                a["off_pass_epa"] += epa
            else:
                a["off_rush_plays"] += 1
                a["off_rush_epa"] += epa
            if (_f(p.get("success")) or 0) > 0:
                a["off_success"] += 1
            wp = _f(p.get("wp"))
            if wp is not None and 0.05 <= wp <= 0.95:
                a["off_plays_gt"] += 1
                a["off_epa_gt"] += epa
    return list(acc.values())


def main() -> None:
    first = int(sys.argv[1]) if len(sys.argv) > 1 else 2013
    last = int(sys.argv[2]) if len(sys.argv) > 2 else 2024
    rows = []
    for s in range(first, last + 1):
        got = collect_season(s)
        rows.extend(got)
        print(f"  {s}: {len(got)} team-games")
    rows.sort(key=lambda r: (r["season"], r["week"], r["game_id"], r["team"]))
    for r in rows:
        for k in ("off_epa", "off_pass_epa", "off_rush_epa", "off_epa_gt"):
            r[k] = round(r[k], 4)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {len(rows)} team-game rows -> {OUT}")


if __name__ == "__main__":
    main()
