"""NBA with/without-teammate splits: what a player does in games a key
teammate MISSED, against games that teammate played. Our own CourtIQ.

WHY GAME-LEVEL, NOT ON-COURT
RotoGrinders' CourtIQ (and every on/off tool) splits minutes by who was on the
floor. That answers "what happens when the star sits for a breather", which is
not the DFS question. The DFS question is "the star is OUT TONIGHT": every
minute of the game is played without him, the rotation tightens, the backup
starts. On-court "off" minutes are mostly bench units against the other
team's bench, so they overstate how much a teammate's per-minute rate jumps
and say nothing about the minutes he gains. Games the star missed are that
scenario exactly, and they come from the box scores the repo already pulls
(scripts/nba_ground_truth_collect.py: one stats.nba.com call a season).

WHAT IT CORRECTS -- AND WHAT IT LEAVES ALONE
A PROP already knows. When a star is ruled out the books pull his props and
re-post his teammates' within minutes, and the market does this better than
twenty games of splits can. So edge/dfs_run_nba.py applies this table only:
  * to a player projected from DK's FPPG (no prop), whose projection would
    otherwise not move at all when a teammate is scratched; and
  * to a PROP player while the props are visibly STALE -- the OUT player
    himself still has a line in the scan, i.e. the books have not re-posted
    yet. Once his line is gone, the market's numbers are trusted again.

THE SHRINKAGE IS THE WHOLE GAME
A player's DK points swing by ~10 a night, so ten games without a teammate
measure the shift to about +/-3 -- the same size as most real shifts. Each
split is shrunk toward "no change" by n_without / (n_without + SHRINK_GAMES).
SHRINK_GAMES is fitted out of sample by scripts/nba_onoff_backtest.py (fit on
the first half of a season's dates, reported on the second); the value below
is the PRIOR it starts from until that has run.

THE MULTIPLIER IS RELATIVE TO THE PLAYER'S OWN MIX OF GAMES
DK's FPPG already contains the games a long-injured teammate missed. So the
ratio compares the shrunk "without" level to the player's all-games level over
the same window, not to his "with" level: if the star has missed most of the
season, FPPG is already a without-star number and the ratio is ~1, as it
should be.

RESULT, 2026-10-16 -- NOT USED BY THE NBA BUILD
Tested held out against edge/dfs_run_nba.py's model (scripts/nba_onoff_backtest.py:
fit on 2024-25, report on 2025-26, 29,311 player-games over 155 dates, 19,642 of
them with a key teammate out). That model already re-shares a scratched
teammate's minutes, and on top of it:
    model alone                                   MAE 7.575
    ratio()      (whole points change, K=10)      MAE 7.694   (counts minutes twice)
    rate_ratio() (per-minute change, K=10)        MAE 7.593   CI [+0.007, +0.026]
    per-minute, shrunk to no change (best K=160)  MAE 7.576   CI [-0.001, +0.002]
The fit picks the weakest adjustment and it still adds nothing: once minutes are
known, a teammate's absence does not move a player's per-minute production
enough to measure. Kept as research (the splits are a fair way to answer "what
did he do without X"); the board publisher and its timer were not shipped.
"""
from __future__ import annotations

import json
import statistics
from collections import defaultdict

from edge import nba
from edge.names import norm


#: Pseudo-games of "no change" each split is shrunk with. PRIOR (2026-10-13):
#: chosen so a 10-game split keeps half its raw shift. Replace with
#: scripts/nba_onoff_backtest.py's held-out best K once it has run on a season.
SHRINK_GAMES = 10.0
#: A teammate counts as "key" -- worth a split -- at this many minutes a game
#: over at least KEY_MIN_GAMES games for the team.
KEY_MIN_MINUTES = 20.0
KEY_MIN_GAMES = 8
#: A teammate's split is kept only with at least this many games each side.
MIN_WITH, MIN_WITHOUT = 3, 1
#: Bounds on the combined multiplier: two stars out can double a backup's
#: night, but a ratio past these is a small-sample artefact, not a role.
RATIO_MIN, RATIO_MAX = 0.75, 1.6
#: Weight on last season's games when merging it into this season's table:
#: roles drift over a summer, rosters change, so it counts half.
PRIOR_SEASON_WEIGHT = 0.5

STAT_KEYS = {"pts": "PTS", "reb": "REB", "ast": "AST", "fg3": "FG3M", "stl": "STL",
             "blk": "BLK", "tov": "TOV"}


def _minutes(row: dict) -> float:
    m = row.get("MIN")
    if isinstance(m, str) and ":" in m:
        a, b = m.split(":", 1)
        return float(a) + float(b) / 60.0
    try:
        return float(m or 0.0)
    except (TypeError, ValueError):
        return 0.0


def build(rows: list[dict], before: str | None = None) -> dict:
    """{team: {key_norm: {"name", "games", "out", "mates": {mate_norm: split}}}}
    from one season of stats.nba.com leaguegamelog player rows.

    `before` (YYYY-MM-DD) uses only games strictly before that date -- the
    backtest's leak guard. A game a player has no row for is a game he did
    not play; a key player's absences count only between his first and last
    game for the team (a trade or a season-ending injury is not a "game he
    missed" for anyone else's split).
    """
    team_games: dict = defaultdict(dict)                    # team -> {gid: date}
    played: dict = defaultdict(dict)                        # (team, pid) -> {gid: row}
    names: dict = {}
    for r in rows:
        if _minutes(r) <= 0:
            continue
        date = str(r.get("GAME_DATE") or "")[:10]
        if before and date >= before:
            continue
        team = nba.team(r["TEAM_ABBREVIATION"])
        pid = r.get("PLAYER_ID") or r.get("PLAYER_NAME")
        team_games[team][r["GAME_ID"]] = date
        played[(team, pid)][r["GAME_ID"]] = r
        names[pid] = r.get("PLAYER_NAME") or str(pid)

    by_team: dict = defaultdict(list)
    for (team, pid), games in played.items():
        by_team[team].append(pid)

    out: dict = {}
    for team, pids in by_team.items():
        order = sorted(team_games[team], key=lambda g: team_games[team][g])
        for b in pids:
            bg = played[(team, b)]
            if len(bg) < KEY_MIN_GAMES or \
                    statistics.fmean(_minutes(r) for r in bg.values()) < KEY_MIN_MINUTES:
                continue
            dates = sorted(team_games[team][g] for g in bg)
            window = [g for g in order if dates[0] <= team_games[team][g] <= dates[-1]]
            missed = {g for g in window if g not in bg}
            entry = {"name": names[b], "games": len(bg), "out": len(missed), "mates": {}}
            if missed:
                for a in pids:
                    if a == b:
                        continue
                    ag = played[(team, a)]
                    w = [ag[g] for g in window if g in ag and g in bg]
                    wo = [ag[g] for g in window if g in ag and g in missed]
                    if len(w) < MIN_WITH or len(wo) < MIN_WITHOUT:
                        continue
                    entry["mates"][norm(names[a])] = _split(names[a], w, wo)
            out.setdefault(team, {})[norm(names[b])] = entry
    return out


def _split(name: str, w: list, wo: list) -> dict:
    fp_w = [nba.actual_points(r) for r in w]
    fp_wo = [nba.actual_points(r) for r in wo]
    return {"name": name, "n_with": len(w), "n_wo": len(wo),
            "fp_with": round(statistics.fmean(fp_w), 2), "fp_wo": round(statistics.fmean(fp_wo), 2),
            "min_with": round(statistics.fmean(_minutes(r) for r in w), 1),
            "min_wo": round(statistics.fmean(_minutes(r) for r in wo), 1)}


def merge(current: dict, prior: dict, weight: float = PRIOR_SEASON_WEIGHT) -> dict:
    """This season's table with last season's folded in at `weight`. Counts
    are weighted, so the shrinkage sees last season as half the evidence.

    A UNION, not an intersection: in week one most (team, star, teammate)
    trios have no games together yet, and that is exactly when last season is
    the only evidence. It cannot leak a split onto the wrong team, because
    ratio() is only ever asked about tonight's team, with the OUT star and the
    teammate both read off tonight's DK draftables -- a traded player's old
    split sits under his old team and is never looked up."""
    out = json.loads(json.dumps(current))
    for team, keys in prior.items():
        for b, pe in keys.items():
            ce = out.setdefault(team, {}).setdefault(
                b, {"name": pe["name"], "games": 0, "out": 0, "mates": {}})
            for a, ps in pe["mates"].items():
                cs = ce["mates"].get(a)
                if cs is None:
                    cs = ce["mates"][a] = {"name": ps["name"], "n_with": 0, "n_wo": 0,
                                           "fp_with": 0.0, "fp_wo": 0.0,
                                           "min_with": 0.0, "min_wo": 0.0}
                for side in ("with", "wo"):
                    n1, n2 = cs[f"n_{side}"], weight * ps[f"n_{side}"]
                    if n1 + n2 <= 0:
                        continue
                    for k, nd in (("fp", 2), ("min", 1)):
                        cs[f"{k}_{side}"] = round((cs[f"{k}_{side}"] * n1
                                                   + ps[f"{k}_{side}"] * n2) / (n1 + n2), nd)
                    cs[f"n_{side}"] = round(n1 + n2, 1)
            ce["out"] = round(ce["out"] + weight * pe["out"], 1)
    return out


def ratio(table: dict, team: str, player: str, out_players: list[str],
          shrink: float = SHRINK_GAMES) -> tuple[float, list[dict]]:
    """(multiplier on the player's all-games DK points, [the splits used]).

    One factor per OUT key teammate the table has a split for, multiplied
    together and bounded. 1.0 with no evidence."""
    keys = table.get(nba.team(team) or team) or {}
    me = norm(player)
    r, used = 1.0, []
    for b in out_players:
        entry = keys.get(norm(b))
        split = (entry or {}).get("mates", {}).get(me)
        if not split:
            continue
        n_w, n_wo = split["n_with"], split["n_wo"]
        base = (split["fp_with"] * n_w + split["fp_wo"] * n_wo) / (n_w + n_wo)
        if base <= 0:
            continue
        s = n_wo / (n_wo + shrink)
        level = split["fp_with"] + s * (split["fp_wo"] - split["fp_with"])
        f = level / base
        r *= f
        used.append({"without": entry["name"], "factor": round(f, 3), "n_wo": n_wo,
                     "min_with": split.get("min_with"), "min_wo": split.get("min_wo")})
    return min(max(r, RATIO_MIN), RATIO_MAX), used


def rate_ratio(table: dict, team: str, player: str, out_players: list[str],
               shrink: float = SHRINK_GAMES) -> tuple[float, list[dict]]:
    """(multiplier on the player's DK points PER MINUTE, [the splits used]).

    The form edge/dfs_run_nba.py needs. Its minutes model already re-shares a
    scratched teammate's minutes (edge/nba_minutes.py), so a points-level
    ratio() on top would count the minutes twice; this keeps only the change
    in his RATE with the teammate out. Same shrinkage, bounds and per-teammate
    product as ratio(); the base is his all-games rate over the same window."""
    keys = table.get(nba.team(team) or team) or {}
    me = norm(player)
    r, used = 1.0, []
    for b in out_players:
        entry = keys.get(norm(b))
        split = (entry or {}).get("mates", {}).get(me)
        if not split or not split.get("min_with") or not split.get("min_wo"):
            continue
        n_w, n_wo = split["n_with"], split["n_wo"]
        rate_w = split["fp_with"] / split["min_with"]
        rate_wo = split["fp_wo"] / split["min_wo"]
        base = ((split["fp_with"] * n_w + split["fp_wo"] * n_wo)
                / (split["min_with"] * n_w + split["min_wo"] * n_wo))
        if base <= 0:
            continue
        s = n_wo / (n_wo + shrink)
        f = (rate_w + s * (rate_wo - rate_w)) / base
        r *= f
        used.append({"without": entry["name"], "factor": round(f, 3), "n_wo": n_wo})
    return min(max(r, RATIO_MIN), RATIO_MAX), used
