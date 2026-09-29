"""DK NHL Classic scoring + free NHL data: the NHL's own API and DailyFaceoff.

WHAT DRAFTKINGS ACTUALLY SCORES
Read off api.draftkings.com/lineups/v1/gametypes/125/rules (roster, cap, team
and game minimums); the point values are DraftKings' published NHL table and
are VALIDATED against DraftKings' own FPPG -- see scripts/nhl_fit.py --scoring
and NHL_STATUS.md -- rather than taken from an article.

    skater   goal 8.5  assist 5  shot on goal 1.5  blocked shot 1.3
             short-handed point +2  shootout goal +1.5
             bonuses: hat trick +3, 5+ shots +3, 3+ blocks +3, 3+ points +3
    goalie   win 6  save 0.7  goal against -3.5  shutout +4  OT loss +2
             bonus: 35+ saves +3

WHERE THE DATA COMES FROM, ALL FREE, NO KEY
    api-web.nhle.com/v1/schedule/{date}               the slate's games
    api-web.nhle.com/v1/gamecenter/{id}/boxscore      per-player goals, assists,
                                                      shots, blocks, PPG, TOI;
                                                      goalie saves, GA, decision
    api.nhle.com/stats/rest/en/skater/{report}        season aggregates
    dailyfaceoff.com/teams/{slug}/line-combinations   forward lines 1-4, D pairs
                                                      1-3, PP1/PP2 units, goalies,
                                                      injuries, last 5/10 games
    dailyfaceoff.com/starting-goalies/{date}          starters + confirmation

Lines are what make hockey DFS what it is: a goal pays one scorer and up to two
assisters, and those are nearly always linemates or power-play unit-mates.
DailyFaceoff is the only free source of CURRENT lines (the NHL API has none),
so edge/nhl_sim.py reads its assist correlation from here.
"""
from __future__ import annotations

import json
import re
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "nhl_cache"
#: Committed by scripts/nhl_lines_publish.py; read when DailyFaceoff is not
#: reachable live (the cloud app).
LINES_SNAPSHOT = ROOT / "data" / "nhl_lines_snapshot.json"

# ---------------------------------------------------------------------------
# DraftKings contest shape (gametype 125, read live 2026-09-28)
# ---------------------------------------------------------------------------
DK_SPORT = "NHL"
CLASSIC_GAME_TYPE = 125
SHOWDOWN_GAME_TYPE = 127
SLOTS = ["C", "C", "W", "W", "W", "D", "D", "G", "UTIL"]
SALARY_CAP = 50000
MIN_TEAMS = 3
MIN_GAMES = 2
#: DK NHL allows late swap (allowLateSwap=true), unlike NASCAR.
ALLOW_LATE_SWAP = True


def slots_for(dk_position: str) -> set[str]:
    """DK positions C / LW / RW / D / G -> roster slots. Both wings fill W;
    every skater can fill UTIL, a goalie cannot."""
    pos = (dk_position or "").upper()
    parts = {p.strip() for p in pos.replace("/", " ").split()}
    out = set()
    if "C" in parts:
        out.add("C")
    if parts & {"LW", "RW", "W"}:
        out.add("W")
    if "D" in parts:
        out.add("D")
    if "G" in parts:
        return {"G"}
    if out:
        out.add("UTIL")
    return out


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------
GOAL, ASSIST, SHOT, BLOCK = 8.5, 5.0, 1.5, 1.3
SH_POINT, SHOOTOUT_GOAL = 2.0, 1.5
HAT_TRICK, SHOTS_5, BLOCKS_3, POINTS_3 = 3.0, 3.0, 3.0, 3.0
WIN, SAVE, GOAL_AGAINST, SHUTOUT, OT_LOSS, SAVES_35 = 6.0, 0.7, -3.5, 4.0, 2.0, 3.0


def skater_points(goals, assists, shots, blocks, sh_points=0, shootout_goals=0):
    """DK points for a skater's game. Works on scalars or numpy arrays."""
    pts = (GOAL * goals + ASSIST * assists + SHOT * shots + BLOCK * blocks
           + SH_POINT * sh_points + SHOOTOUT_GOAL * shootout_goals)
    return (pts + HAT_TRICK * (goals >= 3) + SHOTS_5 * (shots >= 5)
            + BLOCKS_3 * (blocks >= 3) + POINTS_3 * ((goals + assists) >= 3))


def goalie_points(saves, goals_against, won, ot_loss, shutout):
    """DK points for a goalie's game. `won`/`ot_loss`/`shutout` are 0/1 (or
    boolean arrays). A shutout needs the goalie to have played the whole game,
    which the caller decides."""
    return (SAVE * saves + GOAL_AGAINST * goals_against + WIN * won + OT_LOSS * ot_loss
            + SHUTOUT * shutout + SAVES_35 * (saves >= 35))


# ---------------------------------------------------------------------------
# Teams: DraftKings, the NHL and DailyFaceoff abbreviate a few differently
# ---------------------------------------------------------------------------
#: NHL abbreviation -> DailyFaceoff team slug.
DFO_SLUGS = {
    "ANA": "anaheim-ducks", "BOS": "boston-bruins", "BUF": "buffalo-sabres",
    "CAR": "carolina-hurricanes", "CBJ": "columbus-blue-jackets", "CGY": "calgary-flames",
    "CHI": "chicago-blackhawks", "COL": "colorado-avalanche", "DAL": "dallas-stars",
    "DET": "detroit-red-wings", "EDM": "edmonton-oilers", "FLA": "florida-panthers",
    "LAK": "los-angeles-kings", "MIN": "minnesota-wild", "MTL": "montreal-canadiens",
    "NJD": "new-jersey-devils", "NSH": "nashville-predators", "NYI": "new-york-islanders",
    "NYR": "new-york-rangers", "OTT": "ottawa-senators", "PHI": "philadelphia-flyers",
    "PIT": "pittsburgh-penguins", "SEA": "seattle-kraken", "SJS": "san-jose-sharks",
    "STL": "st-louis-blues", "TBL": "tampa-bay-lightning", "TOR": "toronto-maple-leafs",
    "UTA": "utah-mammoth", "VAN": "vancouver-canucks", "VGK": "vegas-golden-knights",
    "WPG": "winnipeg-jets", "WSH": "washington-capitals",
}
#: Spellings other sources use for the same team -> the NHL's.
TEAM_ALIASES = {"TB": "TBL", "LA": "LAK", "NJ": "NJD", "SJ": "SJS", "VEG": "VGK",
                "WAS": "WSH", "UTAH": "UTA", "ARI": "UTA", "MON": "MTL", "CLS": "CBJ"}


def team(abbr: str | None) -> str | None:
    """Any source's abbreviation -> the NHL's."""
    if not abbr:
        return None
    a = abbr.strip().upper()
    return TEAM_ALIASES.get(a, a)


# ---------------------------------------------------------------------------
# HTTP + cache
# ---------------------------------------------------------------------------
UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"),
      "Accept-Language": "en-US,en;q=0.9"}
_last = [0.0]


def _get(url: str, pace: float = 0.25, timeout: float = 30) -> bytes:
    wait = pace - (time.monotonic() - _last[0])
    if wait > 0:
        time.sleep(wait)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                    timeout=timeout) as r:
            return r.read()
    finally:
        _last[0] = time.monotonic()


def _cached_json(url: str, path: Path | None, refresh: bool = False):
    if path is not None and path.exists() and not refresh:
        return json.loads(path.read_text())
    data = json.loads(_get(url))
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    return data


# ---------------------------------------------------------------------------
# The NHL's API
# ---------------------------------------------------------------------------
def schedule(date: str) -> list[dict]:
    """[{id, away, home, start, type}] for one date (type 1 pre, 2 regular, 3 playoff)."""
    data = _cached_json(f"https://api-web.nhle.com/v1/schedule/{date}", None)
    for day in data.get("gameWeek", []):
        if day.get("date") == date:
            return [{"id": g["id"], "away": g["awayTeam"]["abbrev"],
                     "home": g["homeTeam"]["abbrev"], "start": g.get("startTimeUTC"),
                     "type": g.get("gameType")} for g in day.get("games", [])]
    return []


def boxscore(game_id: int, refresh: bool = False) -> dict:
    """A finished game's per-player lines, compacted and cached forever.

    {id, date, away, home, away_goals, home_goals, last_period ('REG'/'OT'/'SO'),
     skaters: [{id, name, team, pos, goals, assists, shots, blocks, ppg, toi}],
     goalies: [{id, name, team, saves, shots_against, goals_against, decision,
                starter, toi}]}
    """
    path = CACHE_DIR / "box" / f"{game_id}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    raw = json.loads(_get(f"https://api-web.nhle.com/v1/gamecenter/{game_id}/boxscore"))
    out = {"id": game_id, "date": raw.get("gameDate"),
           "away": raw["awayTeam"]["abbrev"], "home": raw["homeTeam"]["abbrev"],
           "away_goals": raw["awayTeam"].get("score"), "home_goals": raw["homeTeam"].get("score"),
           "last_period": (raw.get("gameOutcome") or {}).get("lastPeriodType"),
           "state": raw.get("gameState"), "skaters": [], "goalies": []}
    for side in ("awayTeam", "homeTeam"):
        abbr = raw[side]["abbrev"]
        box = (raw.get("playerByGameStats") or {}).get(side, {})
        for group in ("forwards", "defense"):
            for p in box.get(group, []):
                out["skaters"].append({
                    "id": p["playerId"], "name": p["name"]["default"], "team": abbr,
                    "pos": p.get("position"), "goals": p.get("goals", 0),
                    "assists": p.get("assists", 0), "shots": p.get("sog", 0),
                    "blocks": p.get("blockedShots", 0), "ppg": p.get("powerPlayGoals", 0),
                    "toi": p.get("toi")})
        for p in box.get("goalies", []):
            out["goalies"].append({
                "id": p["playerId"], "name": p["name"]["default"], "team": abbr,
                "saves": p.get("saves", 0), "shots_against": p.get("shotsAgainst", 0),
                "goals_against": p.get("goalsAgainst", 0), "decision": p.get("decision"),
                "starter": p.get("starter"), "toi": p.get("toi")})
    if out["state"] in ("OFF", "FINAL"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out))
    return out


def season_game_ids(season: int, game_type: int = 2, count: int = 1312) -> list[int]:
    """Regular-season game ids for a season (2025 -> 2025020001 ...). The NHL
    numbers them sequentially; 32 teams x 82 games / 2 = 1312."""
    return [int(f"{season}{game_type:02d}{n:04d}") for n in range(1, count + 1)]


def season_skater_stats(season_id: str, refresh: bool = False) -> dict:
    """{player_id: {...}} season aggregates, merging the NHL's summary and
    realtime reports (the latter has blocked shots, the former short-handed
    points and shots)."""
    out: dict = {}
    for report in ("summary", "realtime"):
        path = CACHE_DIR / f"skater_{report}_{season_id}.json"
        rows, start = [], 0
        if path.exists() and not refresh:
            rows = json.loads(path.read_text())
        else:
            while True:
                url = (f"https://api.nhle.com/stats/rest/en/skater/{report}?limit=100&start={start}"
                       f"&cayenneExp=seasonId={season_id}%20and%20gameTypeId=2")
                page = json.loads(_get(url))
                rows += page.get("data", [])
                start += 100
                if start >= page.get("total", 0):
                    break
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rows))
        for r in rows:
            out.setdefault(r["playerId"], {}).update(r)
    return out


# ---------------------------------------------------------------------------
# DailyFaceoff
# ---------------------------------------------------------------------------
def _next_data(url: str) -> dict:
    html = _get(url, pace=1.0).decode("utf-8", "replace")
    m = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                  html, re.S)
    if not m:
        raise ValueError(f"no page data at {url}")
    return json.loads(m.group(1))["props"]["pageProps"]


#: DailyFaceoff group ids -> what the simulator calls them.
LINE_GROUPS = {"f1": "F1", "f2": "F2", "f3": "F3", "f4": "F4",
               "d1": "D1", "d2": "D2", "d3": "D3", "pp1": "PP1", "pp2": "PP2",
               "pk1": "PK1", "pk2": "PK2", "g": "G"}


def _snapshot() -> dict:
    from edge import repo_files
    found = repo_files.read(LINES_SNAPSHOT)
    return found.json() if found else {}


def team_lines(abbr: str, live_only: bool = False) -> dict:
    """{player name: {line, pp, pk, pos, injury, gtd, last10, last5}} for one
    team, from DailyFaceoff's current line combinations. `line` is F1-F4 or
    D1-D3 (or G1/G2 for goalies), `pp` PP1/PP2/None."""
    slug = DFO_SLUGS.get(team(abbr))
    if not slug:
        return {}
    try:
        combos = _next_data(f"https://www.dailyfaceoff.com/teams/{slug}/line-combinations")
    except Exception:
        if live_only:
            raise
        return (_snapshot().get("lines") or {}).get(team(abbr), {})
    out: dict = {}
    for p in (combos.get("combinations") or {}).get("players", []):
        name = p.get("name")
        group = LINE_GROUPS.get(p.get("groupIdentifier") or "")
        if not name or not group:
            continue
        row = out.setdefault(name, {"line": None, "pp": None, "pk": None, "pos": None,
                                    "injury": p.get("injuryStatus"),
                                    "gtd": bool(p.get("gameTimeDecision")),
                                    "last10": p.get("last10") or {}, "last5": p.get("last5") or {}})
        if group.startswith(("F", "D")):
            row["line"], row["pos"] = group, p.get("positionIdentifier")
        elif group.startswith("PP"):
            row["pp"] = group
        elif group.startswith("PK"):
            row["pk"] = group
        elif group == "G":
            row["line"] = "G1" if p.get("positionIdentifier") == "g1" else "G2"
            row["pos"] = "g"
    return out


def starting_goalies(date: str, live_only: bool = False) -> dict:
    """{NHL team abbr: {goalie, status}} for a date. `status` is DailyFaceoff's
    news strength ("Confirmed", "Likely", ...) or None when unannounced."""
    try:
        rows = _next_data(f"https://www.dailyfaceoff.com/starting-goalies/{date}").get("data") or []
    except Exception:
        if live_only:
            raise
        return (_snapshot().get("goalies") or {}).get(date, {})
    out = {}
    by_name = {v: k for k, v in DFO_SLUGS.items()}
    for g in rows:
        for side in ("home", "away"):
            abbr = by_name.get(g.get(f"{side}TeamSlug") or "")
            if abbr:
                out[abbr] = {"goalie": g.get(f"{side}GoalieName"),
                             "status": g.get(f"{side}NewsStrengthName")}
    return out
