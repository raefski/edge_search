#!/usr/bin/env python3
"""Join collected NBA historical props (data/nba_historical_props_2025/,
named for the season-END year per the collector's own convention -- this is
the 2024-25 season) with free ground truth (data/nba_ground_truth/) into one
leak-free row per player per game. Mirrors nfl_ingest.py's role/shape.

Game matching is NOT a simple team-pair lookup the way NFL's was: NBA teams
play each other up to 4x/season, so (home,away) alone doesn't disambiguate
-- needs the real date too. And an Odds-API event's UTC commence_time can
roll to the next calendar day versus the game's real (ET-ish) date for any
evening tip (confirmed as a REAL bug in nfl_ingest.py, not hypothetical) --
so this matches on (home_abbr, away_abbr, date OR date-1).

Usage: python3 scripts/nba_ingest.py [--season 2024-25]
"""
import argparse
import datetime
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from edge.nba import norm, actual_points, TEAM_NAME_TO_ABBR  # noqa: E402

PROP_MARKETS = ["player_points", "player_rebounds", "player_assists", "player_threes",
                "player_blocks", "player_steals", "player_turnovers"]


def load_game_index(season):
    """(home_abbr, away_abbr, date) -> GAME_ID, from the team gamelog's own
    MATCHUP field ("XXX vs. YYY" = XXX home, "XXX @ YYY" = XXX away)."""
    rows = json.load(open(ROOT / "data/nba_ground_truth" / f"team_gamelog_{season}.json"))
    by_game = {}
    for r in rows:
        by_game.setdefault(r["GAME_ID"], {})[r["TEAM_ABBREVIATION"]] = r
    idx = {}
    for gid, teams in by_game.items():
        home = next((ab for ab, r in teams.items() if "vs." in r["MATCHUP"]), None)
        away = next((ab for ab, r in teams.items() if "@" in r["MATCHUP"]), None)
        if home and away:
            date = teams[home]["GAME_DATE"]
            idx[(home, away, date)] = gid
    return idx


def load_ground_truth(season):
    rows = json.load(open(ROOT / "data/nba_ground_truth" / f"player_gamelog_{season}.json"))
    idx = {}
    for r in rows:
        idx[(r["GAME_ID"], norm(r["PLAYER_NAME"]))] = r
    return idx


def match_game(game_idx, home_abbr, away_abbr, utc_date):
    d = datetime.date.fromisoformat(utc_date)
    for delta in (0, -1):  # same UTC day, or the day before (late tip rolled to UTC+1)
        cand = (d + datetime.timedelta(days=delta)).isoformat()
        gid = game_idx.get((home_abbr, away_abbr, cand))
        if gid:
            return gid
    return None


def extract_prop_lines(event_odds, market_keys):
    bms = event_odds.get("bookmakers", [])
    dk = next((b for b in bms if b["key"] == "draftkings"), None)
    src = dk or (bms[0] if bms else None)
    if not src:
        return {}, bool(dk)
    out = {}
    for m in src.get("markets", []):
        if m["key"] not in market_keys:
            continue
        for o in m.get("outcomes", []):
            if o.get("name") != "Over":
                continue
            nm = norm(o.get("description", ""))
            if nm:
                out.setdefault(nm, {})[m["key"]] = o.get("point")
    return out, bool(dk)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", default="2024-25")
    ap.add_argument("--props-dir", default=None,
                    help="defaults to data/nba_historical_props_2025 (the collector's own naming)")
    args = ap.parse_args()

    prop_dir = Path(args.props_dir) if args.props_dir else ROOT / "data/nba_historical_props_2025"
    if not prop_dir.exists():
        sys.exit(f"no collected props at {prop_dir}")
    game_idx = load_game_index(args.season)
    gt = load_ground_truth(args.season)

    rows = []
    n_events = n_no_game = n_dk = 0
    n_players_seen = n_players_matched = 0
    for f in sorted(prop_dir.glob("*.json")):
        d = json.loads(f.read_text())
        ev, odds = d["event"], d["odds"]
        n_events += 1
        home_abbr = TEAM_NAME_TO_ABBR.get(ev["home_team"], ev["home_team"])
        away_abbr = TEAM_NAME_TO_ABBR.get(ev["away_team"], ev["away_team"])
        gid = match_game(game_idx, home_abbr, away_abbr, ev["commence_time"][:10])
        if gid is None:
            n_no_game += 1
            continue
        lines, used_dk = extract_prop_lines(odds, PROP_MARKETS)
        n_dk += used_dk
        for nm, market_points in lines.items():
            n_players_seen += 1
            gtr = gt.get((gid, nm))
            if gtr is None:
                continue
            n_players_matched += 1
            rows.append({
                "date": ev["commence_time"][:10], "game_id": gid,
                "player": gtr["PLAYER_NAME"], "team": gtr["TEAM_ABBREVIATION"],
                "home": gtr["TEAM_ABBREVIATION"] == home_abbr,
                "lines": market_points,
                "actual": actual_points(gtr),
            })

    out = ROOT / f"data/nba_model_rows_{args.season}.json"
    out.write_text(json.dumps(rows))
    print(f"season {args.season}: {n_events} events ({n_no_game} unmatched to a real game, "
          f"{n_dk} used DraftKings' own line vs a fallback book)", flush=True)
    print(f"  players in props: {n_players_seen}, matched to ground truth: {n_players_matched} "
          f"({100*n_players_matched/n_players_seen:.1f}%)" if n_players_seen else "  no players found",
          flush=True)
    print(f"  -> {len(rows)} rows -> {out}", flush=True)


if __name__ == "__main__":
    main()
