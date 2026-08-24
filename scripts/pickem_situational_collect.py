#!/usr/bin/env python3
"""Join situational game context onto the pick'em odds history.

The odds history (scripts/pickem_historical_collect.py) carries lines, totals
and scores but nothing about the CIRCUMSTANCES of a game -- rest, weather,
venue, kickoff slot, starting QB. Those are free from nflverse's games.csv and
had never been pulled into this project.

Writes data/pickem_situational.csv, one row per game in the odds history,
keyed the same way so the two files join on (season, week, home, away).

Public schedule data, small, and it must survive a Streamlit rebuild -- so it
is COMMITTED, same category as data/pbp_team_game.csv.

Run: python3 scripts/pickem_situational_collect.py
"""
from __future__ import annotations

import csv
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.pickem_features import canon  # noqa: E402

SRC = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
ODDS = ROOT / "data" / "pickem_odds_history.csv"
OUT = ROOT / "data" / "pickem_situational.csv"

# Everything here is known BEFORE kickoff except temp/wind (measured at
# kickoff) -- which is fine, because the model consumes them at pick time via
# a forecast in live use. For the backtest they stand in for the forecast.
KEEP = ("home_rest", "away_rest", "div_game", "roof", "surface",
        "temp", "wind", "weekday", "gametime", "location",
        "home_qb_name", "away_qb_name", "referee", "stadium_id", "overtime",
        # spread JUICE -- the price on each side of the spread. A direct
        # readout of where the money is, and the closest thing to a
        # historical public-pick distribution that exists (5f).
        "home_spread_odds", "away_spread_odds")


def fetch() -> list[dict]:
    print(f"fetching {SRC}")
    with urllib.request.urlopen(SRC, timeout=120) as r:
        text = r.read().decode("utf-8", "replace")
    return list(csv.DictReader(text.splitlines()))


def main() -> None:
    sched = {}
    for r in fetch():
        if r.get("game_type") != "REG":
            continue
        try:
            s = int(r["season"])
        except (ValueError, TypeError):
            continue
        sched[(s, int(r["week"]), canon(r["home_team"]), canon(r["away_team"]))] = r

    rows, missed = [], 0
    with ODDS.open() as f:
        for g in csv.DictReader(f):
            key = (int(g["season"]), int(g["week"]),
                   canon(g["home_team"]), canon(g["away_team"]))
            src = sched.get(key)
            if src is None:
                missed += 1
                continue
            out = {"season": g["season"], "week": g["week"],
                   "home_team": g["home_team"], "away_team": g["away_team"]}
            for c in KEEP:
                v = src.get(c, "")
                out[c] = "" if v in (None, "NA") else v
            rows.append(out)

    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    print(f"wrote {len(rows)} rows -> {OUT}   ({missed} odds-history games unmatched)")
    for c in KEEP:
        n = sum(1 for r in rows if r[c] != "")
        print(f"  {c:<14} {n:5d}  {n/len(rows):5.1%} populated")
    print("\nNOTE: temp/wind are blank for indoor games (dome/closed/open roof)")
    print("      by construction -- that is missing-because-inapplicable, not a gap.")


if __name__ == "__main__":
    main()
