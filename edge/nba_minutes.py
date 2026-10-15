"""NBA minutes when the rotation changes: who absorbs a scratched player's minutes.

Adam, 2026-10-15: the NBA DFS edge is a late scratch -- a cheap bench player
promoted to starter. DK's salary is fixed the night before, so his minutes (and
points) jump while his price does not. This module turns tonight's lineup news
(edge/nba_lineups.py: starters, inactives) into a minutes projection for every
player on the team, from each player's recent role:

  1. baseline  b_i = his average minutes over his last WINDOW team games in which
               he was active (0 for a DNP-coach's-decision);
  2. missing   M   = the baselines of tonight's inactive players;
  3. shared    each available player gets b_i^ROLE_POW weight, times (1 + POS_W)
               if he shares a position group with the minutes that went missing,
               times (1 + PROMO_W) if he starts tonight but has not been starting
               (started < half his last WINDOW games); M is split by weight;
  4. promoted  a promoted starter is also lifted toward starter minutes even when
               nobody is out (a coach's switch): + PROMO_LIFT x (starter level - b_i);
  5. scaled    the team is scaled back to 240 minutes (overtime aside).

The weights are fitted on 2024-25 and reported on 2025-26 by
scripts/nba_minutes_backtest.py -- see the constants for the numbers. Minutes only;
points come from each player's DK points per minute (points_per_minute below).
"""
from __future__ import annotations

#: Recent games that define a player's role.
WINDOW = 10
#: A player must average this many minutes to count as a scratched regular.
REGULAR_MIN = 10.0
#: Minutes a team plays in regulation.
TEAM_MIN = 240.0
#: Active players needed before the team is rescaled to TEAM_MIN.
FULL_ROSTER = 8
#: Fitted by scripts/nba_minutes_backtest.py on 2024-25 (2,470 team-games, grid over
#: role power 0-2, position weight 0-2, promotion weight 0-3, lift 0-1.25), scored on
#: 2025-26 (2,466 team-games), 2026-10-15. Mean |error| in minutes, held out:
#:                                   all (29,311)  teammates of a scratch  promoted starters (1,816)
#:   recent minutes, no change          5.89            5.99                 9.58 (bias -8.6)
#:   proportional share                 5.89            5.99                 8.63 (bias -6.8)
#:   these weights, starters confirmed  5.66            5.72                 5.48 (bias +0.1)
#:   these weights, inactives only      5.85            5.93                 8.37 (bias -6.8)
#: A bench player who starts plays ~8.6 more minutes than his recent average; the
#: confirmed starting five (30 min before tip) is what reveals it.
ROLE_POW = 0.5
POS_W = 0.5
PROMO_W = 1.0
PROMO_LIFT = 1.0
#: Typical minutes of a starter (refined per team from history when available).
STARTER_MIN = 31.0


def groups(position: str | None) -> set:
    """'G-F' -> {'G', 'F'}; unknown -> empty."""
    return set((position or "").replace("-", " ").split())


def role(history: list) -> dict:
    """{"base", "start_rate", "games"} from [(minutes, started), ...] newest last,
    restricted to games the player was active for."""
    recent = history[-WINDOW:]
    if not recent:
        return {"base": 0.0, "start_rate": 0.0, "games": 0}
    return {"base": sum(m for m, _ in recent) / len(recent),
            "start_rate": sum(1 for _, s in recent if s) / len(recent), "games": len(recent)}


def project(team: list, params: dict | None = None) -> dict:
    """Minutes for tonight. `team` = [{"id", "position", "inactive": bool,
    "starter": bool | None (None = not announced), "history": [(min, started), ...]}].
    Returns {id: {"minutes", "base", "promoted", "share"}} for active players."""
    p = {"ROLE_POW": ROLE_POW, "POS_W": POS_W, "PROMO_W": PROMO_W, "PROMO_LIFT": PROMO_LIFT,
         "STARTER_MIN": STARTER_MIN, **(params or {})}
    roles = {pl["id"]: role(pl["history"]) for pl in team}
    out_pl = [pl for pl in team if pl["inactive"] and roles[pl["id"]]["base"] >= REGULAR_MIN]
    missing = sum(roles[pl["id"]]["base"] for pl in out_pl)
    lost_groups: dict = {}
    for pl in out_pl:
        for g in groups(pl.get("position")):
            lost_groups[g] = lost_groups.get(g, 0.0) + roles[pl["id"]]["base"]
    active = [pl for pl in team if not pl["inactive"]]
    weights, promoted = {}, {}
    for pl in active:
        r = roles[pl["id"]]
        promo = bool(pl.get("starter")) and r["start_rate"] < 0.5
        promoted[pl["id"]] = promo
        pos_share = 0.0
        if lost_groups:
            mine = groups(pl.get("position"))
            pos_share = sum(v for g, v in lost_groups.items() if g in mine) / max(missing, 1e-9)
        w = (max(r["base"], 1.0) ** p["ROLE_POW"]) * (1 + p["POS_W"] * min(1.0, pos_share)) \
            * (1 + p["PROMO_W"] * promo)
        weights[pl["id"]] = w
    total_w = sum(weights.values()) or 1.0
    mins = {}
    for pl in active:
        r = roles[pl["id"]]
        m = r["base"] + missing * weights[pl["id"]] / total_w
        if promoted[pl["id"]]:
            m += p["PROMO_LIFT"] * max(0.0, p["STARTER_MIN"] - m)
        mins[pl["id"]] = min(48.0, max(0.0, m))
    s = sum(mins.values())
    # Only a full roster can be held to 240: preseason files (and a team whose bench is
    # not listed yet) carry the starting five alone, which would each be pushed to 48.
    if s > 0 and len(active) >= FULL_ROSTER:
        k = TEAM_MIN / s
        mins = {i: min(48.0, m * k) for i, m in mins.items()}
    return {pl["id"]: {"minutes": round(mins[pl["id"]], 1), "base": round(roles[pl["id"]]["base"], 1),
                       "promoted": promoted[pl["id"]],
                       "share": round(weights[pl["id"]] / total_w, 3)} for pl in active}


def load_history(seasons=("2024-25", "2025-26", "2026-27"), before: str | None = None) -> dict:
    """{player_id: [(minutes, started, dk_points), ...]} oldest first, the same
    definition the backtest fits on: every game he was ACTIVE for (from the cached
    NBA.com lineups, data/nba_lineups/), with 0 for a DNP-coach's-decision. A day
    with no cached lineup falls back to the box score alone. `before` = 'YYYY-MM-DD'."""
    import json
    from collections import defaultdict
    from pathlib import Path

    from edge.nba import actual_points
    from edge.nba_lineups import CACHE, parse

    gt = Path(__file__).resolve().parents[1] / "data" / "nba_ground_truth"
    by_day: dict = defaultdict(list)
    for s in seasons:
        path = gt / f"player_gamelog_{s}.json"
        if path.exists():
            for r in json.loads(path.read_text()):
                by_day[r["GAME_DATE"]].append(r)
    hist: dict = defaultdict(list)
    for d in sorted(by_day):
        if before and d >= before:
            break
        box = {(str(r["PLAYER_ID"]), r["GAME_ID"]): r for r in by_day[d]}
        f = CACHE / f"{d.replace('-', '')}.json"
        if f.exists():
            for g in parse(json.loads(f.read_text())):
                for p in g["players"]:
                    if p["inactive"]:
                        continue
                    pid = str(p["id"])
                    r = box.get((pid, g["game_id"]))
                    hist[pid].append((float(r["MIN"] or 0), p["starter"], actual_points(r))
                                     if r else (0.0, p["starter"], 0.0))
        else:
            for (pid, _), r in box.items():
                hist[pid].append((float(r["MIN"] or 0), False, actual_points(r)))
    return dict(hist)


def points_per_minute(history: list, window: int = 30) -> float:
    """DK points per minute over his recent games (the rate a minutes change scales)."""
    recent = [(m, p) for m, _, p in history[-window:] if m > 0]
    mins = sum(m for m, _ in recent)
    return sum(p for _, p in recent) / mins if mins >= 30 else 0.0
