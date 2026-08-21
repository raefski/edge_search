"""Live NFL spreads for pick'em -- via The Odds API (edge/client.py), the
same paid, ToS-sanctioned source every other sport in this repo already uses.

History: this originally hit ESPN's public scoreboard endpoint for free, no
key. That got blocked in production -- 403, `Server: AkamaiGHost` -- and it
reproduced on the actual deployed Streamlit Cloud app, not just a sandbox
quirk (confirmed 2026-08-21: fuller browser-style headers didn't help
either, meaning it's an IP-reputation block, not a fixable header issue).
Continuing to probe for a way around a bot-detection wall isn't the right
call regardless of the cause -- The Odds API is the legitimate path this
account already pays for, and edge/client.py already has the caching,
credit-ledger, and dry-run guardrails this needs.

Cost: get_featured_odds('americanfootball_nfl', ['spreads'], 'us') is
markets x regions = 1 credit for the WHOLE week's slate in a single call
(edge/client.py's cost model -- one call covers every game), then free for
`live_ttl` seconds (default 600) via the client's own on-disk cache.
"""
from __future__ import annotations

from dataclasses import dataclass

from edge.client import OddsAPIClient
from edge.nfl import TEAM_NAME_TO_ABBR  # shared team-name->abbr map, not re-derived


@dataclass
class LiveGame:
    away: str
    home: str
    away_abbr: str
    home_abbr: str
    kickoff: str               # ISO 8601 UTC, as the API reports it
    live_line: float | None    # home-team spread, mean across books; None if no market posted


def _parse_events(events: list[dict]) -> list[LiveGame]:
    """Pure parsing, split out from fetch_week so it's testable without a
    live client/key -- same shape as edge.clv's _dk_sides, one JSON-shape
    assumption in one place. home-team spread = mean of every book's point
    for the outcome named after the home team (a simple, low-noise
    consensus; not weighted toward any single "sharp" book -- see
    PICKEM_STATUS.md if that ever needs revisiting)."""
    games = []
    for ev in events:
        home, away = ev.get("home_team", ""), ev.get("away_team", "")
        points = [
            float(o["point"])
            for bm in ev.get("bookmakers", [])
            for mk in bm.get("markets", [])
            if mk.get("key") == "spreads"
            for o in mk.get("outcomes", [])
            if o.get("name") == home and o.get("point") is not None
        ]
        games.append(LiveGame(
            away=away, home=home,
            away_abbr=TEAM_NAME_TO_ABBR.get(away, away[:3].upper()),
            home_abbr=TEAM_NAME_TO_ABBR.get(home, home[:3].upper()),
            kickoff=ev.get("commence_time", ""),
            live_line=(sum(points) / len(points)) if points else None,
        ))
    return games


def fetch_week(client: OddsAPIClient, sport: str = "americanfootball_nfl") -> list[LiveGame]:
    """Raises NoApiKey / DryRunBlocked / CreditFloorError exactly as
    edge.client does -- callers handle those the same way app.py already
    does for MLB (degrade gracefully, don't crash the page)."""
    return _parse_events(client.get_featured_odds(sport, ["spreads"], "us"))
