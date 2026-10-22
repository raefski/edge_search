"""Who was actually available: the inactive list NBA.com's lineups feed stopped carrying.

Critique cycle 3 (NBA_MINUTES_CRITIQUE_LOG.md): from 2026-27 NBA.com's daily-lineups
JSON (edge/nba_lineups.py) marks every listed player "Active" -- 103 of 103 on
2026-10-20, where 35 did not play; 2025-26 files flagged ~4.5 inactives a team-game.
Two consequences, both fixed here:

  history   an injured / G-League / two-way player's missed game was recorded as a
            0-minute coach's decision, so his role decayed toward 0 while he was hurt
            (Taurean Prince 25.8 -> 12.9 after one) and the absence that the return
            ramp keys on vanished. `game_availability` reads stats.nba.com's
            traditional box score (v3), whose comment says "Inactive - Injury/Illness",
            "Inactive - G League - Two-Way" or "DNP - Coach's Decision": inactive
            games are skipped, coach's-decision zeros kept. Cached per finished game
            in data/nba_availability/<game_id>.json.

  tonight   before tip there is no inactive list at all, only DK's sparse OUT flags
            (6 of 35 non-players on 10/20). `tonight_out` reads ESPN's public
            injuries JSON: every player listed "Out" is treated as out. Day-To-Day
            players are left to the news (NBA.com's starters, DK's flags).
            LAST_SOURCE records what was read, for the page; every distinct
            snapshot is logged to ESPN_LOG (critique cycle 6: the Out list's miss rate).
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "nba_availability"
BOX_URL = ("https://stats.nba.com/stats/boxscoretraditionalv3?GameID={gid}&LeagueID=00&endPeriod=0"
           "&startPeriod=0&rangeType=0&startRange=0&endRange=0")
ESPN_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"
#: What tonight_out last read: "espn <fetched UTC>" | "failed: ..." | "none".
LAST_SOURCE = "none"
#: Every distinct ESPN injuries snapshot, all statuses (Out, Day-To-Day, ...), one JSON
#: line per change, per ET date -- critique cycle 6 needs the Out list's real miss rate
#: (role-10+ scratches it did not list Out), measured against the box scores in CACHE.
ESPN_LOG = ROOT / "data" / "nba_espn_injuries"


def _headers() -> dict:
    from scripts.nba_ground_truth_collect import HEADERS
    return HEADERS


def parse_box(raw: dict) -> dict:
    """{player_id: {"team", "status": "played" | "dnp" | "inactive", "comment"}} from a
    boxscoretraditionalv3 response. No comment = he played."""
    bt = raw.get("boxScoreTraditional") or {}
    out = {}
    for side in ("homeTeam", "awayTeam"):
        team = bt.get(side) or {}
        for p in team.get("players") or []:
            c = (p.get("comment") or "").strip()
            status = "inactive" if c.lower().startswith("inactive") else ("dnp" if c else "played")
            out[str(p.get("personId"))] = {"team": team.get("teamTricode"), "status": status, "comment": c}
    return out


def game_availability(game_id: str, fetch: bool = True, pace: float = 1.0) -> dict | None:
    """Availability for one game, cached once the box score lists players. None when it
    is neither cached nor fetchable (fetch=False, or the request failed)."""
    path = CACHE / f"{game_id}.json"
    if path.exists():
        return json.loads(path.read_text())
    if not fetch:
        return None
    try:
        time.sleep(pace)
        with urllib.request.urlopen(urllib.request.Request(BOX_URL.format(gid=game_id), headers=_headers()),
                                    timeout=60) as r:
            av = parse_box(json.load(r))
    except Exception:                                       # noqa: BLE001 -- the caller keeps the lineup flags
        return None
    if av:
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(av))
    return av or None


def backfill(game_ids, pace: float = 1.0) -> int:
    """Cache availability for finished games not cached yet. Returns how many were fetched."""
    n = 0
    for gid in game_ids:
        if not (CACHE / f"{gid}.json").exists() and game_availability(gid, fetch=True, pace=pace):
            n += 1
    return n


def apply(players: list[dict], game_id: str) -> list[dict]:
    """Set `inactive` on NBA.com lineup rows ({"id", "inactive", ...}) from the cached
    box score when there is one; rows are returned unchanged otherwise."""
    av = game_availability(game_id, fetch=False)
    if not av:
        return players
    # A player the box score does not list at all did not dress (a coach's-decision
    # DNP is listed, with its comment): inactive too -- Taurean Prince, 2026-10-20.
    out = []
    for p in players:
        a = av.get(str(p.get("id")))
        out.append({**p, "inactive": a is None or a["status"] == "inactive"})
    return out


def tonight_out(fetch=None) -> list[tuple[str, str]]:
    """[(display name, NBA team code)] that ESPN's injuries feed lists as Out right now."""
    global LAST_SOURCE
    from edge import nba
    try:
        if fetch is None:
            with urllib.request.urlopen(urllib.request.Request(ESPN_URL, headers={"User-Agent": "Mozilla/5.0"}),
                                        timeout=30) as r:
                data = json.load(r)
        else:
            data = fetch()
    except Exception as exc:                                # noqa: BLE001
        LAST_SOURCE = f"failed: {exc}"
        return []
    out, listed = [], []
    for team in data.get("injuries") or []:
        for inj in team.get("injuries") or []:
            ath = inj.get("athlete") or {}
            abbr = nba.team(((ath.get("team") or {}).get("abbreviation")) or (team.get("abbreviation") or ""))
            name = ath.get("displayName") or ""
            if name and abbr:
                listed.append([name, abbr, inj.get("status") or ""])
                if (inj.get("status") or "").lower() == "out":
                    out.append((name, abbr))
    if fetch is None:
        _log_snapshot(sorted(listed))
    LAST_SOURCE = f"espn {datetime.now(timezone.utc).isoformat(timespec='minutes')} ({len(out)} out)"
    return out


def _log_snapshot(listed: list) -> None:
    """Append ESPN's list to today's log when it differs from the last line (the scratch
    monitor reads it every few minutes). Best effort: a read-only disk (the cloud) skips."""
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(timezone.utc)
        path = ESPN_LOG / f"{now.astimezone(ZoneInfo('America/New_York')).date().isoformat()}.jsonl"
        if path.exists():
            lines = path.read_text().splitlines()
            if lines and json.loads(lines[-1]).get("players") == listed:
                return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            f.write(json.dumps({"at": now.isoformat(timespec="seconds"), "players": listed}) + "\n")
    except Exception:                                       # noqa: BLE001
        pass
