"""NBA.com's daily lineups: who starts, who is in the rotation, who is inactive.

    from edge.nba_lineups import fetch, parse
    games = parse(fetch("2026-10-20"))

The "Today's Lineups" page on nba.com reads one public JSON file per day
(stats.nba.com/js/data/leaders/00_daily_lineups_YYYYMMDD.json). For every game it
lists BOTH full rosters, each player marked:

  * position set ("PG".."C")  -> in the starting five;
  * lineupStatus "Expected"   -> the team has not submitted it yet (NBA.com's guess),
                 "Confirmed"  -> submitted (teams must, 30 min before tip, since 2023);
  * rosterStatus "Inactive"   -> will not play (the inactive list, ~30 min before tip).

Checked 2026-10-15 on 2026-04-10: 15 games, 150 starters (10 per game), 251 active
bench, 133 inactive, every row timestamped. That is the official source of
"replacement starter" news -- the X accounts (beat reporters, Underdog NBA) relay the
same team announcements, sometimes minutes earlier, but not as data.

Past days are cached in data/nba_lineups/ (gitignored) since they never change;
today's is always refetched. No key; it needs nba.com's Referer like the stats API.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "nba_lineups"
URL = "https://stats.nba.com/js/data/leaders/00_daily_lineups_{d}.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Referer": "https://www.nba.com/",
    "Accept": "application/json",
}


def _day(d) -> str:
    if isinstance(d, (date, datetime)):
        return d.strftime("%Y%m%d")
    return str(d).replace("-", "")[:8]


def fetch(d, use_cache: bool = True, today: date | None = None) -> dict:
    """The raw daily-lineups JSON for one date ({"games": []} when none / not posted)."""
    key = _day(d)
    is_past = key < (today or date.today()).strftime("%Y%m%d")
    path = CACHE / f"{key}.json"
    if use_cache and is_past and path.exists():
        return json.loads(path.read_text())
    req = urllib.request.Request(URL.format(d=key), headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = r.read()
    except urllib.error.HTTPError as exc:
        if exc.code in (302, 403, 404):          # nothing posted for that day yet
            return {"games": []}
        raise
    data = json.loads(body) if body.strip() else {"games": []}
    if is_past and data.get("games"):
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    return data


def parse(raw: dict) -> list[dict]:
    """[{game_id, status, home, away, players: [{id, name, team, starter, position,
    confirmed, inactive, at}]}] -- one row per player on either roster."""
    out = []
    for g in raw.get("games", []):
        players = []
        for side in ("homeTeam", "awayTeam"):
            t = g.get(side) or {}
            for p in t.get("players") or []:
                players.append({
                    "id": p.get("personId"), "name": p.get("playerName"),
                    "team": t.get("teamAbbreviation"),
                    "starter": bool(p.get("position")), "position": p.get("position") or "",
                    "confirmed": p.get("lineupStatus") == "Confirmed",
                    "inactive": p.get("rosterStatus") == "Inactive",
                    "at": p.get("timestamp"),
                })
        out.append({"game_id": g.get("gameId"), "status": g.get("gameStatusText"),
                    "home": (g.get("homeTeam") or {}).get("teamAbbreviation"),
                    "away": (g.get("awayTeam") or {}).get("teamAbbreviation"),
                    "players": players})
    return out


def backfill(dates, pace: float = 1.0) -> int:
    """Cache past days' lineups (one request per uncached day, `pace` s apart)."""
    n = 0
    for d in dates:
        if (CACHE / f"{_day(d)}.json").exists():
            continue
        fetch(d)
        n += 1
        time.sleep(pace)
    return n
