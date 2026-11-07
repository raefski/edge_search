"""ESPN men's college basketball box scores: the grader's truth and, before lock, each
team's LAST GAME, the one thing a college board knows about this season that DK's FPPG
(last season, old school) and Torvik's 2026 file do not.

Why the last game (NCAAB_CRITIQUE_LOG.md, cycle 1, 2026-11-07): a $3,000 player who
played 22 minutes on Monday was still a 1-point "bench" row on Wednesday (Darius Bivins,
St. Mary's: 24.0 on 11/2, projected 1.08 on 11/4, scored 26.75), and a blend player who
did not dress on Monday was still 16 points on Wednesday. The box was final ~20 hours
before lock. Fetched once per team per slate date; final games and past days' scoreboards
never change, so both are cached under data/cache/ncaab_espn/ (gitignored).
"""
from __future__ import annotations

import json
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path

from edge.ncaab import actual_points, match_team

ESPN = "https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball"
CACHE = Path(__file__).resolve().parents[1] / "data" / "cache" / "ncaab_espn"
#: How far back a team's last game is looked for. A college team plays every 2-4 days in
#: November; past a week the game says little about tonight's rotation.
LOOKBACK_DAYS = 7
#: A side of a box counts as the team's only at this score (exact, alias or prefix),
#: never the letters-in-order test: across a whole day's scoreboard that test is not
#: discriminating (match_team's docstring). The roster check in the caller is the second guard.
MIN_MATCH = 0.9
PAUSE_S = 0.5


def get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as fh:
        return json.load(fh)


def _num(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def side_rows(side: dict, code: str) -> list[dict]:
    """One row per athlete on one side of an ESPN summary's boxscore, tagged `code`."""
    out = []
    for table in side.get("statistics", [])[:1]:
        for a in table.get("athletes", []):
            st = dict(zip(table.get("keys", []), a.get("stats", [])))
            threes = str(st.get("threePointFieldGoalsMade-threePointFieldGoalsAttempted", "0"))
            out.append({
                "team": code, "name": a["athlete"]["displayName"], "min": _num(st.get("minutes")),
                "started": bool(a.get("starter")),
                "fp": actual_points({"PTS": _num(st.get("points")), "REB": _num(st.get("rebounds")),
                                     "AST": _num(st.get("assists")), "STL": _num(st.get("steals")),
                                     "BLK": _num(st.get("blocks")), "TOV": _num(st.get("turnovers")),
                                     "FG3M": _num(threes.split("-")[0])})})
    return out


def box_rows(summary: dict, codes: tuple) -> list[dict]:
    """One row per athlete in an ESPN game summary, tagged with his DK team code. The two
    codes are given to the two sides as a pair, never each side's best on its own: alone,
    "Arkansas-Pine Bluff" read as ARK and the whole UAPB box went to Arkansas (11/3)."""
    out = []
    sides = summary.get("boxscore", {}).get("players", [])
    schools = [side["team"]["displayName"] for side in sides]
    order = list(codes)
    if len(sides) == 2 and len(codes) == 2:
        flip = (match_team(codes[1], schools[0]) + match_team(codes[0], schools[1])
                > match_team(codes[0], schools[0]) + match_team(codes[1], schools[1]))
        order = [codes[1], codes[0]] if flip else [codes[0], codes[1]]
    for i, side in enumerate(sides):
        code = order[i] if len(order) == len(sides) else max(codes, key=lambda c: match_team(c, schools[i]))
        out += side_rows(side, code)
    return out


def _cached(name: str, url: str, keep: bool) -> dict:
    path = CACHE / name
    if path.exists():
        return json.loads(path.read_text())
    data = get(url)
    time.sleep(PAUSE_S)
    if keep:
        CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
    return data


def scoreboard(day: date) -> list[dict]:
    """A day's Division I games. Cached only for days before today (still changing today)."""
    data = _cached(f"sb_{day:%Y%m%d}.json",
                   f"{ESPN}/scoreboard?dates={day:%Y%m%d}&groups=50&limit=400",
                   keep=day < date.today())
    return data.get("events", [])


def summary(event_id: str) -> dict:
    return _cached(f"sum_{event_id}.json", f"{ESPN}/summary?event={event_id}", keep=True)


def last_games(codes: set, before: date, lookback: int = LOOKBACK_DAYS,
               validate=None) -> dict:
    """{DK code: {"date", "event", "school", "rows"}} for each team's most recent FINAL game
    on a day strictly before `before` (the slate's date, so tonight's game can never leak
    in). A team with none in the window is absent. Rows are that team's side only.
    `validate(code, rows)` rejects a side whose names are not the DK roster's: SDSU is two
    schools, and a wrong school's box must keep the search going, not zero a rotation."""
    found: dict = {}
    for back in range(1, lookback + 1):
        todo = codes - set(found)
        if not todo:
            break
        day = before - timedelta(days=back)
        for ev in scoreboard(day):
            comp = ev["competitions"][0]
            if not ev.get("status", {}).get("type", {}).get("completed"):
                continue
            names = [c["team"].get("displayName", "") for c in comp["competitors"]]
            for code in sorted(todo - set(found)):
                score = max((match_team(code, n) for n in names), default=0.0)
                if score < MIN_MATCH:
                    continue
                summ = summary(ev["id"])
                sides = summ.get("boxscore", {}).get("players", [])
                if len(sides) != 2:
                    continue
                pick = max(range(2), key=lambda i: match_team(code, sides[i]["team"]["displayName"]))
                rows = side_rows(sides[pick], code)
                if validate is not None and not validate(code, rows):
                    continue
                found[code] = {"date": day.isoformat(), "event": ev["id"],
                               "school": sides[pick]["team"]["displayName"],
                               "rows": rows}
    return found
