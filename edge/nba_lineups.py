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

The per-player "timestamp" is NBA.com's regeneration time in Eastern time, bumped
every 5 minutes whether or not anything changed -- not when a lineup was posted.

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


# ── the cloud copy ─────────────────────────────────────────────────────────────
# Streamlit Cloud may be refused by stats.nba.com (it refuses many datacenter IPs),
# so the desktop's scratch timer (harness/nba_scratch.py) publishes tonight's raw
# file whenever it changes, and the app falls back to it -- saying so, because a
# snapshot shown as live is how a day-long outage once went unnoticed.
SNAPSHOT = ROOT / "data" / "nba_lineups_snapshot.json"
#: "live", "snapshot <time>", or "none" -- what tonight() last served.
LAST_SOURCE = "none"


def _signature(games: list) -> list:
    """What a lineup change is: who starts, who is confirmed, who is inactive. NOT the
    per-player timestamps, which NBA.com bumps every 5 minutes on each regeneration
    (15:36:32 -> 15:41:32 with nothing else different, 2026-10-15) and which made the
    snapshot commit three times in six minutes."""
    return sorted((p["game_id"], p["team"], str(p["id"]), p["starter"], p["position"],
                   p["confirmed"], p["inactive"])
                  for g in parse({"games": games}) for p in [{**q, "game_id": g["game_id"]}
                                                               for q in g["players"]])


def write_snapshot(d, raw: dict, at: str) -> bool:
    """Write tonight's file for the cloud; True when a lineup actually changed."""
    body = {"date": _day(d), "fetched_at": at, "games": raw.get("games", [])}
    old = json.loads(SNAPSHOT.read_text()) if SNAPSHOT.exists() else {}
    if old.get("date") == body["date"] and _signature(old.get("games", [])) == _signature(body["games"]):
        return False
    SNAPSHOT.write_text(json.dumps(body, separators=(",", ":")))
    return True


def tonight(d) -> dict:
    """Tonight's raw lineups: live from NBA.com, else the published snapshot for
    the same date. Sets LAST_SOURCE."""
    global LAST_SOURCE
    try:
        raw = fetch(d, use_cache=False)
        LAST_SOURCE = "live"
        return raw
    except Exception:                                       # noqa: BLE001
        pass
    from edge import repo_files
    found = repo_files.read(SNAPSHOT)
    if found is not None:
        snap = found.json()
        if snap.get("date") == _day(d):
            LAST_SOURCE = f"snapshot {snap.get('fetched_at', '?')}"
            return {"games": snap.get("games", [])}
    LAST_SOURCE = "none"
    return {"games": []}
