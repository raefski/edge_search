"""DraftKings league feed — main lines and props for a whole league per call.

    /api/sportscontent/dkus{state}/v1/leagues/{leagueId}
    /api/sportscontent/dkus{state}/v1/leagues/{leagueId}/categories/{categoryId}

Response is `{events: [...], markets: [...], selections: [...]}` — the same
markets/selections join as the per-event endpoint, with the event list
included, so one request covers the slate.

This host refuses datacenter IPs (403). It answers normally from a residential
connection in-state, so this runs on your machine and not from a server.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from . import http as requests

from .models import Board, EventMeta
from .draftkings_nash import ingest_sportscontent
from .matching import match_event

_FRAC_RE = re.compile(r"\.(\d{1,6})\d*")

log = logging.getLogger("arb.dk_league")

HOST = "https://sportsbook-nash.draftkings.com/api/sportscontent/dkus{state}/v1"
LEAGUE_IDS = {
    "baseball_mlb": 84240, "basketball_nba": 42648,
    "americanfootball_nfl": 88808, "icehockey_nhl": 42133,
    "americanfootball_ncaaf": 87637, "basketball_ncaab": 92483,
}
FULL_GAME_CATEGORY = 493       # "Game Lines"
# Prop categories worth pulling. The league payload lists all 27 categories and
# 241 subcategories for free, so these are filtered from it rather than guessed.
PROP_CATEGORIES = {
    743: "Batter Props", 1031: "Pitcher Props",
    1342: "Passing Props", 1343: "Rushing Props", 1344: "Receiving Props",
    1215: "Player Points", 1216: "Player Rebounds", 1217: "Player Assists",
}
HEADERS = {
    "Accept": "application/json",
    "Referer": "https://sportsbook.draftkings.com/",
    "Origin": "https://sportsbook.draftkings.com",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36"),
    "x-client-name": "web", "x-client-page": "event", "x-pe-ep": "SB",
}


def _ts(value) -> datetime:
    """Parse an ISO timestamp, tolerating .NET-style sub-second precision.

    DraftKings sends "2026-08-29T16:00:00.0000000Z" -- seven fractional
    digits. Python's fromisoformat accepts only 3 or 6, so this silently
    fell back to "now" and every event failed to match on start time.
    """
    if value is None:
        return datetime.now(timezone.utc)
    text = str(value).strip().replace("Z", "+00:00")
    text = _FRAC_RE.sub(lambda m: "." + m.group(1), text)
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)

class DraftKingsLeague:
    def __init__(self, state: str = "ct", session: requests.Session | None = None):
        self.base = HOST.format(state=state.lower())
        self.session = session or requests.Session()
        self.headers = dict(HEADERS, **{"x-pe-loc": f"US-{state.upper()}"})

    def fetch(self, sport_key: str, category_id: int | None = None) -> dict:
        league = LEAGUE_IDS.get(sport_key)
        if league is None:
            return {}
        url = f"{self.base}/leagues/{league}"
        if category_id:
            url += f"/categories/{category_id}"
        r = self.session.get(url, headers=self.headers, timeout=25)
        if r.status_code == 403:
            raise PermissionError(
                "DraftKings returned 403. This host blocks datacenter IPs; run "
                "from a residential connection in Connecticut.")
        r.raise_for_status()
        return r.json() or {}

    def prop_subcategories(self, payload: dict,
                           categories: set[int] | None = None) -> list[tuple[int, int, str]]:
        """(categoryId, subcategoryId, name) for every prop tab in the league.

        Discovered from the league payload, which already carries `categories`
        and `subcategories` -- no per-event capture and no guessing at ids.
        """
        wanted = categories if categories is not None else set(PROP_CATEGORIES)
        out = []
        for sc in payload.get("subcategories") or []:
            cid = sc.get("categoryId")
            if cid in wanted and sc.get("id"):
                out.append((int(cid), int(sc["id"]), sc.get("name") or ""))
        return out

    def fetch_subcategory(self, sport_key: str, category_id: int,
                          subcategory_id: int) -> dict:
        league = LEAGUE_IDS.get(sport_key)
        if league is None:
            return {}
        url = (f"{self.base}/leagues/{league}/categories/{category_id}"
               f"/subcategories/{subcategory_id}")
        r = self.session.get(url, headers=self.headers, timeout=25)
        if r.status_code == 403:
            raise PermissionError("DraftKings 403 on the subcategory path")
        r.raise_for_status()
        return r.json() or {}

    def ingest(self, board: Board, payload: dict, sport_key: str,
               strict_match: bool = True) -> dict:
        """Resolve every event in the payload, then reuse the markets/selections
        join once per event."""
        stats = {"events": 0, "matched": 0, "unmatched": 0, "quotes": 0,
                 "markets_unmapped": set()}
        events = payload.get("events") or []
        markets = payload.get("markets") or []
        selections = payload.get("selections") or []
        if not events:
            return stats

        by_event: dict[str, EventMeta] = {}
        for ev in events:
            stats["events"] += 1
            name = ev.get("name") or ""
            if " @ " not in name:
                continue
            away, home = [p.strip() for p in name.split(" @ ", 1)]
            when = _ts(ev.get("startEventDate") or ev.get("startDate"))
            target = match_event(board, home, away, when, sport_key)
            if target is None:
                stats["unmatched"] += 1
                if strict_match:
                    continue
                target = EventMeta(f"dk:{ev.get('id')}", sport_key, sport_key,
                                   when, home, away)
            else:
                stats["matched"] += 1
            by_event[str(ev.get("id"))] = target

        # split the flat market/selection lists per event, then reuse the parser
        for eid, target in by_event.items():
            ev_markets = [m for m in markets if str(m.get("eventId")) == eid]
            if not ev_markets:
                continue
            ids = {str(m.get("id")) for m in ev_markets}
            ev_sels = [s for s in selections if str(s.get("marketId")) in ids]
            st = ingest_sportscontent(
                board, {"markets": ev_markets, "selections": ev_sels},
                book="draftkings", sport_key=sport_key,
                strict_match=False, event=target)
            stats["quotes"] += st["quotes"]
            stats["markets_unmapped"] |= st["markets_unmapped"]
        return stats
