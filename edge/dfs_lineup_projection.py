"""Predict an MLB batting order BEFORE it is posted, platoon-aware.

WHY: a staggered postseason slate (today's 2026-10-03 Division Series: 1:00,
4:00, 6:30, 8:30 ET) locks long before most lineups exist. Official orders post
about 2-3 hours before first pitch, so building on confirmed lineups alone means
building on one game's worth of players. Starting pitchers ARE known, and a
manager's order against a left-hander is not his order against a right-hander,
so the platoon is predictable from the team's own recent games.

HOW (no new data source: one statsapi schedule call with the `lineups` and
`probablePitcher` hydrations returns every recent game's batting order and
starter):

  * every recent game is a vote for each player who started it. Games against
    the SAME-HANDED starter count in full; all games count as a prior, shrunk
    together with `m` pseudo-games, so a platoon bat who sat every time a lefty
    started is ruled out of a lefty game and a regular is not, even when the
    sample against that hand is three games.
  * recent games weigh more (a returning injury, a lost starter), and POSTSEASON
    games weigh double -- a playoff order is the manager's real lineup, where
    the final regular-season weekend is often the bench.
  * the nine most likely starters, ordered by their usual slot against that
    hand, are the projected order; each carries `p_start`, so the optimiser can
    discount a 60% bat instead of treating him as a lock.

Validated by scripts/dfs_lineup_projection_backtest.py against the real posted
orders; the numbers are in DFS_STATUS.md.
"""
from __future__ import annotations

import datetime as _dt
from collections import defaultdict

#: statsapi gameType codes that are real games: regular season, wild card,
#: division series, league championship, World Series. (Excludes A all-star,
#: S/E exhibition, and spring training.)
REAL_GAME_TYPES = "R,F,D,L,W"
POSTSEASON = {"F", "D", "L", "W"}

DECAY = 0.94            # per game back, per team
POSTSEASON_WEIGHT = 2.0
PRIOR_GAMES = 3.0       # pseudo-games of "all opponents" folded into the vs-hand rate
HISTORY_DAYS = 45


def fetch_history(end_date: str, days: int = HISTORY_DAYS, get=None) -> list[dict]:
    """Completed real games before `end_date`, oldest first:
    {pk, date, type, side: {team_id: {"order": [(pid, name)], "opp_sp": pid}}}."""
    if get is None:
        from edge import dfs
        get = dfs._get
    end = _dt.date.fromisoformat(end_date) - _dt.timedelta(days=1)
    start = end - _dt.timedelta(days=days)
    s = get("https://statsapi.mlb.com/api/v1/schedule?sportId=1"
            f"&startDate={start.isoformat()}&endDate={end.isoformat()}"
            f"&hydrate=lineups,probablePitcher&gameType={REAL_GAME_TYPES}")
    out = []
    for d in s.get("dates", []):
        for g in d.get("games", []):
            if g.get("status", {}).get("abstractGameState") != "Final":
                continue
            lu = g.get("lineups") or {}
            sides = {}
            for side, key, other in (("home", "homePlayers", "away"), ("away", "awayPlayers", "home")):
                players = lu.get(key) or []
                tid = g["teams"][side]["team"]["id"]
                opp_sp = (g["teams"][other].get("probablePitcher") or {}).get("id")
                if players:
                    sides[tid] = {"order": [(p["id"], p["fullName"]) for p in players[:9]],
                                  "opp_sp": opp_sp}
            if sides:
                out.append({"pk": g["gamePk"], "date": g.get("gameDate", ""),
                            "type": g.get("gameType", "R"), "sides": sides})
    out.sort(key=lambda x: x["date"])
    return out


def team_games(history: list[dict], team_id) -> list[dict]:
    """This team's games, MOST RECENT FIRST: {order, opp_sp, type}."""
    rows = [{"order": g["sides"][team_id]["order"], "opp_sp": g["sides"][team_id]["opp_sp"],
             "type": g["type"]} for g in history if team_id in g["sides"]]
    return rows[::-1]


def predict_lineup(games: list[dict], opp_hand: str | None, hands: dict,
                   n_games: int = 20) -> list[dict]:
    """Projected nine for one team against a starter throwing `opp_hand`
    ("L"/"R"/None). `games` is team_games(); `hands` maps str(pid) -> {"throw"}.
    Returns [{id, name, slot, p_start}] best-first by slot; empty with no history."""
    games = games[:n_games]
    if not games:
        return []
    w_all = defaultdict(float)
    w_hand = defaultdict(float)
    slot_hand: dict = defaultdict(list)
    slot_all: dict = defaultdict(list)
    name = {}
    tot_all = tot_hand = 0.0
    for k, g in enumerate(games):
        w = DECAY ** k * (POSTSEASON_WEIGHT if g["type"] in POSTSEASON else 1.0)
        same = bool(opp_hand) and (hands.get(str(g["opp_sp"])) or {}).get("throw") == opp_hand
        tot_all += w
        if same:
            tot_hand += w
        for i, (pid, nm) in enumerate(g["order"], start=1):
            name[pid] = nm
            w_all[pid] += w
            slot_all[pid].append((w, i))
            if same:
                w_hand[pid] += w
                slot_hand[pid].append((w, i))
    p = {}
    for pid in w_all:
        p_all = w_all[pid] / tot_all
        p[pid] = (w_hand[pid] + PRIOR_GAMES * p_all) / (tot_hand + PRIOR_GAMES) if opp_hand else p_all

    def mean_slot(pid):
        pts = slot_hand.get(pid) or slot_all[pid]
        return sum(w * s for w, s in pts) / sum(w for w, _ in pts)

    nine = sorted(p, key=lambda x: -p[x])[:9]
    nine.sort(key=mean_slot)
    return [{"id": pid, "name": name[pid], "slot": i + 1, "p_start": round(min(p[pid], 1.0), 3)}
            for i, pid in enumerate(nine)]
