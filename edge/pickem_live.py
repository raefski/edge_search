"""Free, keyless live NFL spreads for pick'em -- ESPN's public scoreboard API.

Deliberately NOT the Odds API: pick'em doesn't need multi-book consensus or
prop markets, just one current game spread per matchup, and ESPN's endpoint
gives that for free with no key and no credit cost -- so the deployed
Streamlit page can refresh live market lines on every page load at zero
cost, the same "free public data refreshes live" shape as app.py's DK
salaries/confirmed-lineups pull (see edge/client.py's module docstring for
why that split matters: free vs. paid data get very different refresh
philosophies here).

Sign convention matches edge.pickem: home-team spread, negative = home
favored. ESPN reports `odds.details` as e.g. "SEA -3.5" or "PICK" -- parsed
against the event's own home/away abbreviations so the sign is never guessed
from string order.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"


@dataclass
class LiveGame:
    away: str          # full team name, e.g. "New England Patriots"
    home: str
    away_abbr: str      # e.g. "NE" -- for matching against a CBS-line CSV
    home_abbr: str
    kickoff: str        # ISO 8601 UTC, as ESPN reports it
    live_line: float | None   # home-team spread; None if no market posted yet


def _parse_spread(details: str, home_abbr: str, away_abbr: str) -> float | None:
    """'SEA -3.5' -> +3.5 if SEA is away, -3.5 if SEA is home. 'PICK'/'' -> 0.0."""
    if not details:
        return None
    details = details.strip()
    if details.upper() in ("PICK", "PK", "EVEN"):
        return 0.0
    parts = details.rsplit(" ", 1)
    if len(parts) != 2:
        return None
    fav_abbr, num = parts
    try:
        pts = abs(float(num))
    except ValueError:
        return None
    if fav_abbr == home_abbr:
        return -pts
    if fav_abbr == away_abbr:
        return pts
    return None  # ESPN abbreviation didn't match either side -- don't guess


def fetch_week(week: int, season_type: int = 2, timeout: int = 15) -> list[LiveGame]:
    """season_type: 1=preseason, 2=regular, 3=postseason (ESPN's own enum)."""
    url = f"{SCOREBOARD_URL}?seasontype={season_type}&week={week}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        payload = json.loads(r.read())

    games = []
    for event in payload.get("events", []):
        comp = event["competitions"][0]
        teams = {c["homeAway"]: c["team"] for c in comp["competitors"]}
        if "home" not in teams or "away" not in teams:
            continue
        odds = comp.get("odds", [{}])
        details = odds[0].get("details", "") if odds else ""
        home_abbr, away_abbr = teams["home"]["abbreviation"], teams["away"]["abbreviation"]
        games.append(LiveGame(
            away=teams["away"]["displayName"], home=teams["home"]["displayName"],
            away_abbr=away_abbr, home_abbr=home_abbr,
            kickoff=event.get("date", ""),
            live_line=_parse_spread(details, home_abbr, away_abbr),
        ))
    return games


if __name__ == "__main__":
    import sys
    wk = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    for g in fetch_week(wk):
        line = f"{g.live_line:+.1f}" if g.live_line is not None else "no line yet"
        print(f"{g.away_abbr:>3} @ {g.home_abbr:<3}  {line:>10}   {g.kickoff}")
