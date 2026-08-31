"""Fanatics prices via Oddschecker's CX API.

betfanatics.com does not serve its own odds -- its front end calls Oddschecker
under a white-label arrangement. The api-key below is an app-wide constant
baked into that site's JavaScript, shared by every visitor; no cookies and no
account are involved. One request returns a whole league including alternate
ladders.
"""
from __future__ import annotations

from . import http

BASE = "https://api.oddschecker.com/cx/v1/subevent-group"
API_KEY = "b91c36c9-540c-4809-9328-bbdb276ad018"
HEADERS = {
    "accept": "application/json",
    "api-key": API_KEY,
    "content-type": "application/json",
    "country-code": "US",
    "origin": "https://betfanatics.com",
    "referer": "https://betfanatics.com/",
    "repub": "US",
    "subdivision-code": "CT",     # the endpoint is state-scoped
}


def fetch_fanatics_league(event_id: int, bettype_ids: list[int] | None = None,
                          bet_limit: int = 8, subevent_limit: int = 50) -> dict:
    """One league's whole board.

    `bettype_ids` is now OPTIONAL, and leaving it out is the better call.
    Filtering was believed to be required -- adding a league meant capturing
    its totals bettypeId, which differs per sport (526 points, 1055802107
    runs) -- but the endpoint returns every bet type when the parameter is
    omitted: 17 markets per MLB game against 3, and on NFL that includes
    passing, rushing and receiving yards and anytime touchdown scorer, which
    COVERAGE.md had recorded as app-only.

    That breadth is only safe because `marketmap.is_full_game` now refuses the
    period and team-scoped markets it also returns -- "1st Quarter Point
    Spread" would otherwise share a GroupKey with the full-game spread.
    """
    params = [("eventId", str(event_id)), ("betLimit", str(bet_limit)),
              ("subeventLimit", str(subevent_limit))]
    params += [("bettypeIds", str(b)) for b in (bettype_ids or [])]
    params.append(("overrideBookies", "FNP"))       # FNP = Fanatics
    r = http.get(BASE, params=params, headers=HEADERS, timeout=25)
    r.raise_for_status()
    return r.json() or {}
