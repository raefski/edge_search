#!/usr/bin/env python3
"""Publish every NBA player's recent games for the NBA app (desktop -> GitHub -> cloud).

    python3 scripts/nba_history_publish.py            # refresh data, write the snapshot
    python3 scripts/nba_history_publish.py --push     # ...and commit + push it if it changed

The app projects DK points as tonight's minutes x points per minute
(edge/nba_project.py), both from each player's recent games. Those come from
stats.nba.com box scores (data/nba_ground_truth/, gitignored, desktop only) and
NBA.com's daily lineups for who started (data/nba_lineups/). The cloud app has
neither, so this writes the compact part it needs:

    data/nba_player_history.json
      players: {nba_id: {name, team, pos, hist: [[minutes, started, dk_points], ...]}}
               (last KEEP active games, oldest first: the rate reads its
               RATE_WINDOW, a season-opening role the full SEASON_START_WINDOW)
      names:   {norm(name): nba_id}
      teams:   {team: [dk points allowed, opponent minutes] over its last OPP_WINDOW}
      league_pm: DK points per minute, league-wide, same window

A player with fewer than PRESEASON_IF regular-season games (a rookie, a player
back from a long absence) gets this preseason's games instead, minutes scaled by
PRESEASON_MIN_RATIO and marked in `preseason`, so the #1 pick does not project to
zero on opening night.

Run daily (deploy/nba-history-publish.timer): it also pulls the current season's
box scores and caches any new days' lineups first.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import nba_minutes as NM, nba_project as NP     # noqa: E402
from edge.nba import actual_points                        # noqa: E402
from edge.nba_lineups import backfill                     # noqa: E402
from edge.names import norm                               # noqa: E402
from scripts.nba_ground_truth_collect import HEADERS      # noqa: E402

KEEP = max(NP.RATE_WINDOW, NM.SEASON_START_WINDOW)

GT = ROOT / "data" / "nba_ground_truth"
OUT = ROOT / "data" / "nba_player_history.json"
SEASONS = ("2024-25", "2025-26", "2026-27")
CURRENT = "2026-27"
PRESEASON_IF = 5
#: An undrafted player's "pick" for the rookie minutes prior (edge/nba_minutes.ROOKIE_PRIOR).
PICK_UNDRAFTED = 70
#: Preseason minutes overstate a newcomer's regular-season role. 2025-26 players with
#: no prior NBA season (n=112): 14.8 preseason minutes -> 9.7 over their first 10
#: regular-season games = 0.66 (a 20-minute preseason rookie played ~13; a fifth
#: never played). Points are scaled the same, so his per-minute rate is unchanged.
PRESEASON_MIN_RATIO = 0.66


def gamelog(season: str, season_type: str = "Regular Season") -> list[dict]:
    url = ("https://stats.nba.com/stats/leaguegamelog?Counter=0&DateFrom=&DateTo=&LeagueID=00"
           f"&PlayerOrTeam=P&Season={season}&SeasonType={season_type.replace(' ', '+')}&Sorter=DATE")
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=90) as r:
        rs = json.load(r)["resultSets"][0]
    return [dict(zip(rs["headers"], row)) for row in rs["rowSet"]]


def refresh() -> dict:
    """Pull this season's regular-season and preseason box scores; cache new lineups."""
    got = {}
    for kind, path in (("Regular Season", GT / f"player_gamelog_{CURRENT}.json"),
                       ("Pre Season", GT / f"player_gamelog_{CURRENT}_pre.json")):
        try:
            rows = gamelog(CURRENT, kind)
        except Exception as exc:                            # noqa: BLE001
            print(f"{kind}: stats.nba.com unavailable ({exc}); keeping what is on disk")
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows))
        got[kind] = len(rows)
        today = date.today().isoformat()
        new = sorted({r["GAME_DATE"] for r in rows if r["GAME_DATE"] < today})
        if kind == "Regular Season" and new:
            got["lineup days fetched"] = backfill(new, pace=1.0)
            # NBA.com's lineups carry no inactive list in 2026-27: the box scores do
            # (edge/nba_availability.py, critique cycle 3)
            from edge.nba_availability import backfill as availability_backfill
            gids = sorted({r["GAME_ID"] for r in rows if r["GAME_DATE"] < today})
            got["availability games fetched"] = availability_backfill(gids, pace=1.0)
    return got


def build(before: str | None = None, current: str = CURRENT, seasons=SEASONS) -> dict:
    """The snapshot. `before` = 'YYYY-MM-DD' rebuilds it as it stood that morning
    (scripts/nba_ownership_fit.py rebuilds past contest nights): only games before
    it count, and `current` names that night's season for preseason / season_games."""
    meta: dict = {}
    hist = NM.load_history(seasons, before=before, meta=meta)
    pos = json.loads((GT / "positions.json").read_text()) if (GT / "positions.json").exists() else {}
    latest: dict = {}
    allowed: dict = defaultdict(list)
    rows_by_game: dict = defaultdict(list)
    for s in seasons:
        path = GT / f"player_gamelog_{s}.json"
        if not path.exists():
            continue
        for r in json.loads(path.read_text()):
            if before and r["GAME_DATE"] >= before:
                continue
            latest[str(r["PLAYER_ID"])] = (r["GAME_DATE"], r["PLAYER_NAME"], r["TEAM_ABBREVIATION"])
            rows_by_game[(r["GAME_ID"], r["GAME_DATE"])].append(r)
    team_last: dict = {}                        # each team's latest regular-season game (any season)
    for (gid, d), rows in sorted(rows_by_game.items(), key=lambda kv: kv[0][1]):
        teams = {r["TEAM_ABBREVIATION"] for r in rows}
        for t in teams:
            team_last[t] = max(team_last.get(t, ""), d[:10])
        for t in teams:
            opp_rows = [r for r in rows if r["TEAM_ABBREVIATION"] != t]
            allowed[t].append((sum(actual_points(r) for r in opp_rows),
                               sum(float(r["MIN"] or 0) for r in opp_rows)))
    pre: dict = defaultdict(list)
    pre_path = GT / f"player_gamelog_{current}_pre.json"
    if pre_path.exists():
        for r in sorted(json.loads(pre_path.read_text()), key=lambda r: r["GAME_DATE"]):
            if before and r["GAME_DATE"] >= before:
                continue
            pid = str(r["PLAYER_ID"])
            pre[pid].append((float(r["MIN"] or 0) * PRESEASON_MIN_RATIO, False,
                             actual_points(r) * PRESEASON_MIN_RATIO))
            if pid not in latest or latest[pid][0] < r["GAME_DATE"]:
                latest[pid] = (r["GAME_DATE"], r["PLAYER_NAME"], r["TEAM_ABBREVIATION"])
    season_games: dict = defaultdict(int)
    sched: dict = defaultdict(set)              # this season's game dates per team
    cur = GT / f"player_gamelog_{current}.json"
    if cur.exists():
        for r in json.loads(cur.read_text()):
            if before and r["GAME_DATE"] >= before:
                continue
            season_games[str(r["PLAYER_ID"])] += 1
            sched[r["TEAM_ABBREVIATION"]].add(r["GAME_DATE"])
    game_no = {t: {d: i + 1 for i, d in enumerate(sorted(ds))} for t, ds in sched.items()}
    draft = {}
    dpath = GT / "draft_history.json"
    if dpath.exists():
        for r in json.loads(dpath.read_text()):
            draft[str(r["PERSON_ID"])] = int(r["OVERALL_PICK"] or PICK_UNDRAFTED)
    first_day = min((d for ds in sched.values() for d in ds), default=None)
    players, names = {}, {}
    for pid, (_, name, team) in latest.items():
        h = hist.get(pid, [])
        used_pre = sum(1 for m, _, _ in h if m > 0) < PRESEASON_IF and bool(pre.get(pid))
        if used_pre:
            h = h + pre[pid]
        if not h:
            continue
        # this season's ACTIVE games (DNPs included, unlike season_games' box rows):
        # the newest cur_n real entries are this season's -- the minutes role weighs
        # them apart from last season's (edge/nba_minutes.role)
        m_ = meta.get(pid, [])
        this = [(t, game_no.get(t, {}).get(d)) for d, t in m_ if first_day and d >= first_day]
        this = [(t, no) for t, no in this if no]
        players[pid] = {"name": name, "team": team, "pos": pos.get(pid),
                        "season_games": season_games.get(pid, 0),
                        "hist": [[round(m, 1), bool(s), round(p, 2)] for m, s, p in h[-KEEP:]],
                        "preseason": used_pre, "n_pre": len(pre[pid]) if used_pre else 0,
                        "cur_n": len(this), "pick": draft.get(pid, PICK_UNDRAFTED),
                        "since_return": NM.games_since_return(
                            this, team, len(sched.get(team, ())) + 1, len(this), m_[-1][1] if m_ else None),
                        # critique cycle 6: did not dress for his team's latest game (his last
                        # active game was with this team, before it) -- he may be out again.
                        # Only a game of THIS season: before a team's opener the "latest game"
                        # is last season's finale, a rest night -- players who sat it dressed
                        # 77% (2025-26 openers, n 95) / 91% (2026-27, n 92) vs 16-22% in season.
                        # Sam Merrill / Thomas Bryant, CLE's opener 2026-10-22, flagged 50% / 20%.
                        "missed_last": bool(m_ and m_[-1][1] == team and m_[-1][0] < team_last.get(team, "")
                                            and first_day is not None and team_last.get(team, "") >= first_day)}
        names[norm(name)] = pid
    teams = {}
    for t, v in allowed.items():
        last = v[-NP.OPP_WINDOW:]
        teams[t] = [round(sum(a for a, _ in last), 1), round(sum(m for _, m in last), 1)]
    tot_pts = sum(v[0] for v in teams.values())
    tot_min = sum(v[1] for v in teams.values())
    return {"generated_at": before or date.today().isoformat(), "players": players, "names": names,
            "teams": teams, "league_pm": round(tot_pts / tot_min, 4) if tot_min else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--no-refresh", action="store_true", help="build from what is on disk")
    a = ap.parse_args()
    if not a.no_refresh:
        print("refreshed:", refresh())
    snap = build()
    text = json.dumps(snap, separators=(",", ":"), sort_keys=True)
    OUT.write_text(text)
    print(f"{OUT.relative_to(ROOT)}: {len(snap['players'])} players, {len(text) // 1024} KB, "
          f"{sum(p['preseason'] for p in snap['players'].values())} on preseason games")
    if a.push:
        from edge.data_branch import publish_one
        return 0 if publish_one(OUT, "nba player history") else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
