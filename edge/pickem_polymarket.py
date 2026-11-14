"""Polymarket's NFL spread ladder as one more reading of "the market" for pick'em.

WHY (Adam, 2026-11-14): Polymarket is an exchange, not a book, and its whole
price history is public. If its line ever moves before DK/FD/Fanatics, it
would be a better live line for edge.pickem.make_pick. Backtest 2026-11-14 on
weeks 1-10 (137 games with a CBS line, a lock reading and a result; this
module's own `report`), the SHIPPED make_pick fed each source:
    book consensus at our lock reading ...... 70-67 (51.1%)
    Polymarket at that same instant ......... 74-63 (54.0%)
    Polymarket 1 minute before the deadline . 73-64 (53.3%)
Polymarket sat 0.25 pt from our consensus on average (16/137 games >= 0.5)
and changed 16 picks, which went 10-6: noise at n=16. So it is LOGGED, not
used -- scripts/pickem_polymarket.py report re-grades as weeks arrive.
Decide after ~8 more weeks (30+ changed picks).

HOW A LINE IS READ. Polymarket lists a ladder per game: "Spread: Broncos
(-4.5)" is a Yes/No market that pays if the Broncos win by more than 4.5.
Each rung becomes a point (x, P(home margin > x)); the fair home spread is
minus the x where that probability crosses 0.5, by linear interpolation
between the two rungs that straddle it. Rungs priced below 3c or above 97c
are dropped (stale far-off rungs that nobody quotes).

TWO API QUIRKS (found 2026-11-14):
  * /events pages with limit > 50 are served gzip only ("gzip_required").
  * prices-history?interval=max returns EMPTY for markets older than about a
    month; an explicit startTs/endTs window (<= ~5 days) with fidelity works.
    So history is fetched in two 5-day windows ending ~1 day after kickoff.
Plain urllib without a browser Accept header gets 406; headers below work.
"""
from __future__ import annotations

import bisect
import gzip
import json
import time
import urllib.request
from pathlib import Path

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
NFL_SERIES = "12185"          # gamma /sports, sport "nfl", 2026-11-14
MIN_RUNG_VOLUME = 500.0       # dollars traded; thinner rungs never quote a real price
CACHE = Path(__file__).resolve().parent.parent / "data" / "pickem" / "polymarket_hist"

# Polymarket names teams by nickname; codes are the pick'em log's (CBS) codes.
NICK_TO_CODE = {
    "Cardinals": "ARI", "Falcons": "ATL", "Ravens": "BAL", "Bills": "BUF", "Panthers": "CAR",
    "Bears": "CHI", "Bengals": "CIN", "Browns": "CLE", "Cowboys": "DAL", "Broncos": "DEN",
    "Lions": "DET", "Packers": "GB", "Texans": "HOU", "Colts": "IND", "Jaguars": "JAX",
    "Chiefs": "KC", "Raiders": "LV", "Chargers": "LAC", "Rams": "LAR", "Dolphins": "MIA",
    "Vikings": "MIN", "Patriots": "NE", "Saints": "NO", "Giants": "NYG", "Jets": "NYJ",
    "Eagles": "PHI", "Steelers": "PIT", "49ers": "SF", "Seahawks": "SEA", "Buccaneers": "TB",
    "Titans": "TEN", "Commanders": "WSH",
}


def _get(url: str):
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0", "Accept": "application/json",
        "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=60) as r:
        body = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            body = gzip.decompress(body)
    return json.loads(body)


def parse_ts(s: str) -> float:
    """Gamma mixes '2026-11-01T21:25:00Z' and '2026-11-01 21:25:00+00'."""
    import datetime as dt
    s = s.strip().replace(" ", "T").replace("Z", "+00:00")
    if s.endswith("+00"):
        s += ":00"
    return dt.datetime.fromisoformat(s).timestamp()


def list_games(since_iso: str, closed: bool | None = None) -> list[dict]:
    """Every NFL game event with a spread ladder starting after `since_iso`."""
    out, off = [], 0
    flag = "" if closed is None else f"&closed={'true' if closed else 'false'}"
    while True:
        page = _get(f"{GAMMA}/events?series_id={NFL_SERIES}&limit=50&offset={off}"
                    f"&start_date_min={since_iso}{flag}")
        if not page:
            break
        out += page
        off += 50
        time.sleep(0.2)
    return [e for e in out if any(m.get("sportsMarketType") == "spreads"
                                  for m in e.get("markets", []))]


def game_rungs(event: dict) -> tuple[set[str], float | None, list[dict]]:
    """({team codes}, kickoff ts, [{code, k, token, flip}]) -- `k` = the
    margin the named team must exceed; `flip` = outcome 0 is NOT that team,
    so the history (always outcome 0's price) must be read as 1 - p."""
    teams = {NICK_TO_CODE.get(t.strip()) for t in event.get("title", "").split(" vs. ")}
    rungs, kick = [], None
    for m in event.get("markets", []):
        if m.get("sportsMarketType") != "spreads" or "clobTokenIds" not in m:
            continue
        if float(m.get("volume") or 0) < MIN_RUNG_VOLUME:
            continue
        kick = kick or m.get("gameStartTime")
        q = m.get("question", "")
        try:
            team = q.split("Spread: ", 1)[1].rsplit(" (", 1)[0]
            k = -float(q.rsplit("(", 1)[1].rstrip(")"))
        except (IndexError, ValueError):
            continue
        code = NICK_TO_CODE.get(team)
        if code is None:
            continue
        outs = json.loads(m["outcomes"])
        rungs.append({"code": code, "k": k, "token": json.loads(m["clobTokenIds"])[0],
                      "flip": outs[0] != team})
    kick = kick or event.get("startDate")
    return teams, (parse_ts(kick) if kick else None), rungs


def history(token: str, kickoff: float, closed: bool) -> list[tuple[float, float]]:
    """[(t, p of outcome 0)], cached on disk once the game is over."""
    path = CACHE / f"{token}.json"
    if path.exists():
        return [tuple(x) for x in json.loads(path.read_text())]
    pts: dict[int, float] = {}
    end = min(time.time(), kickoff + 86400)
    for start in (end - 9 * 86400, end - 4.5 * 86400):
        try:
            h = _get(f"{CLOB}/prices-history?market={token}&startTs={int(start)}"
                     f"&endTs={int(min(start + 4.5 * 86400, end))}&fidelity=10")
        except Exception:
            continue
        for x in h.get("history", []):
            pts[int(x["t"])] = float(x["p"])
        time.sleep(0.1)
    out = sorted(pts.items())
    if closed and out:
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out))
    return out


def price_at(hist: list[tuple[float, float]], t: float) -> float | None:
    i = bisect.bisect_right([x[0] for x in hist], t) - 1
    return None if i < 0 else hist[i][1]


def fair_home_line(home: str, rungs: list[dict], t: float) -> tuple[float | None, int]:
    """(home spread, rungs used) at instant t. Each rung needs a "hist" key."""
    pts = []
    for r in rungs:
        p = price_at(r["hist"], t)
        if p is None:
            continue
        if r["flip"]:
            p = 1 - p
        if p < 0.03 or p > 0.97:
            continue
        # P(home margin > x)
        pts.append((r["k"], p) if r["code"] == home else (-r["k"], 1 - p))
    pts.sort()
    for (x1, p1), (x2, p2) in zip(pts, pts[1:]):
        if p1 >= 0.5 >= p2 and p1 != p2:
            return round(-(x1 + (p1 - 0.5) / (p1 - p2) * (x2 - x1)), 3), len(pts)
    return None, len(pts)
