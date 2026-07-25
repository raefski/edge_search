#!/usr/bin/env python3
"""Join collected NFL historical props (data/nfl_historical_props_<season>/)
with free ground truth (data/nfl_ground_truth/) into one leak-free row per
skill-position player per game: real DK-posted lines (closing, pre-game --
never anything that happened during or after the game) alongside the real
DK fantasy points that game produced. Mirrors MLB's
scripts/dfs_model_lab_collect.py role: this is the raw material a future
props-vs-skill-model backtest runs against, not the backtest itself.

Only seasons with BOTH props and player-level ground truth produce full
rows (2024, as of this collection -- 2025 props exist but nflverse hasn't
published detailed 2025 player stats yet, see nfl_ground_truth_collect.py's
docstring). Games without a ground-truth match are skipped and counted, not
silently dropped -- coverage is reported so a thin join doesn't masquerade
as a complete one.

Usage: python3 scripts/nfl_ingest.py [--season 2024]
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from edge.nfl import norm, actual_offense_points, TEAM_NAME_TO_ABBR  # noqa: E402

PROP_MARKETS = [
    "player_pass_yds", "player_pass_tds", "player_pass_interceptions",
    "player_rush_yds", "player_rush_tds",
    "player_receptions", "player_reception_yds", "player_reception_tds",
]


def load_ground_truth(season):
    p = ROOT / "data/nfl_ground_truth" / f"player_week_{season}.json"
    if not p.exists():
        return None
    rows = json.load(open(p))
    # index by (week, norm(display_name)) -- nflverse gives one row per
    # player per week per season_type; REG only, to match real DK slates
    idx = {}
    for r in rows:
        if r.get("season_type") != "REG":
            continue
        idx[(r["week"], norm(r["player_display_name"]))] = r
    return idx


def load_games(season):
    """(home_abbr, away_abbr) -> week. games.json's own team fields are
    already DK/nflverse-style abbreviations (e.g. "MIA"), not the full names
    the Odds API uses ("Miami Dolphins") -- callers must convert via
    TEAM_NAME_TO_ABBR first. Deliberately NOT keyed by date: games.json's
    "gameday" is the ET calendar date, but an Odds-API event's commence_time
    is UTC -- a late Thursday/Sunday/Monday night kickoff (e.g. 20:15 ET =
    00:15 UTC the NEXT day) rolls to a different date string in UTC, which
    silently broke a date-string join 100% of the time on the first attempt
    here. An ordered (home, away) pair is confirmed unique within a season
    (checked directly: 0 of 272 real 2024 pairs repeat), so team-pair alone
    is both simpler and correct where the date string wasn't."""
    p = ROOT / "data/nfl_ground_truth/games.json"
    rows = json.load(open(p))
    by_teams = {}
    for r in rows:
        if r["season"] != str(season) or r["game_type"] != "REG":
            continue
        by_teams[(r["home_team"], r["away_team"])] = r["week"]
    return by_teams


def extract_prop_lines(event_odds, market_keys):
    """{norm(player_name): {market_key: point}} from the DK bookmaker if
    present, else the first bookmaker that has any of these markets --
    matches project convention elsewhere of preferring DK specifically."""
    bms = event_odds.get("bookmakers", [])
    dk = next((b for b in bms if b["key"] == "draftkings"), None)
    src = dk or (bms[0] if bms else None)
    if not src:
        return {}
    out = {}
    for m in src.get("markets", []):
        if m["key"] not in market_keys:
            continue
        for o in m.get("outcomes", []):
            if o.get("name") != "Over":  # point is identical on Over/Under, just take one
                continue
            nm = norm(o.get("description", ""))
            if not nm:
                continue
            out.setdefault(nm, {})[m["key"]] = o.get("point")
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--season", type=int, default=2024)
    args = ap.parse_args()
    season = args.season

    prop_dir = ROOT / "data" / f"nfl_historical_props_{season}"
    if not prop_dir.exists():
        sys.exit(f"no collected props for {season} at {prop_dir}")
    gt = load_ground_truth(season)
    games = load_games(season)

    rows = []
    n_events = n_no_week = n_no_gt_file = 0
    n_players_seen = n_players_matched = 0
    for f in sorted(prop_dir.glob("*.json")):
        d = json.loads(f.read_text())
        ev, odds = d["event"], d["odds"]
        n_events += 1
        date = ev["commence_time"][:10]
        home_abbr = TEAM_NAME_TO_ABBR.get(ev["home_team"], ev["home_team"])
        away_abbr = TEAM_NAME_TO_ABBR.get(ev["away_team"], ev["away_team"])
        week = games.get((home_abbr, away_abbr))
        if week is None:
            n_no_week += 1
            continue
        lines = extract_prop_lines(odds, PROP_MARKETS)
        if gt is None:
            n_no_gt_file += 1
            continue
        for nm, market_points in lines.items():
            n_players_seen += 1
            gtr = gt.get((week, nm))
            if gtr is None:
                continue
            n_players_matched += 1
            team = gtr.get("team")
            rows.append({
                "season": season, "week": int(week), "date": date,
                "player": gtr["player_display_name"], "team": team,
                "opp": gtr.get("opponent_team"),
                "home": team == home_abbr,
                "lines": market_points,
                "actual": actual_offense_points(gtr),
            })

    out = ROOT / f"data/nfl_model_rows_{season}.json"
    out.write_text(json.dumps(rows))
    print(f"season {season}: {n_events} events ({n_no_week} unmatched to a real week, "
          f"{n_no_gt_file} with no ground-truth file at all)", flush=True)
    print(f"  players in props: {n_players_seen}, matched to ground truth: {n_players_matched} "
          f"({100*n_players_matched/n_players_seen:.1f}%)" if n_players_seen else "  no players found",
          flush=True)
    print(f"  -> {len(rows)} rows -> {out}", flush=True)


if __name__ == "__main__":
    main()
