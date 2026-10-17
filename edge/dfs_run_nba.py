"""DK NBA Classic: slate -> board -> simulated players -> lineups, one entry point.

    from edge.dfs_run_nba import build_slate
    out = build_slate()            # tonight's main slate: pool, cash, gpp, stats

The app (pages/12_🏀_NBA_DFS.py) and the CLI (scripts/dfs_lineups_nba.py) both call
build_slate, so the phone and the desktop cannot produce different lineups.

WHERE EACH PLAYER'S NUMBER COMES FROM
    minutes   edge/nba_minutes.py: his recent role, re-shared for tonight's news --
              NBA.com's starting five (Expected / Confirmed) and inactive list, and
              DraftKings' OUT flags. A bench player starting tonight is "promoted".
    rate      edge/nba_project.py: his DK points per minute over recent games,
              x the opponent's points allowed, x tonight's implied team total
              when the books have one.
    history   data/nba_player_history.json (scripts/nba_history_publish.py): the
              same file on the desktop and the cloud. Rookies use preseason games.
    no data   DK's own price: IMPUTED_PER_K points per $1,000, flagged `imputed`.

Held out on 2025-26: 7.65 DK points mean |error|, 0.765 correlation, against
7.88 / 0.743 for a last-10 average (scripts/nba_projection_backtest.py).
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from edge import dfs_slate
from edge import dfs_forward_log
from edge import dfs, dfs_nba_theory as theory, dfs_opt_nba, nba_lineups, nba_minutes as NM
from edge import nba_project as NP
from edge.dfs_slate import parse_time as _parse_time, slate_games
from edge import nba
from edge.nba import TEAM_NAME_TO_ABBR
from edge.names import norm

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
SPORT = "basketball_nba"
DK_SPORT = "NBA"
CLASSIC = 70
HISTORY = ROOT / "data" / "nba_player_history.json"
#: DK points per $1,000 for a player with no history at all (rookie, no preseason).
IMPUTED_PER_K = 4.5
#: Players a team dresses on a game night (13 active; 2025-26 NBA.com lineups median).
ACTIVE_ROSTER = 13
ET = ZoneInfo("America/New_York")
#: First regular-season night. Before it, DK's slates are PRESEASON games, where
#: starters play 15-25 minutes -- the projections assume regular-season roles.
REGULAR_SEASON_START = "2026-10-20"


#: DK -> NBA.com team codes (GS -> GSW, PHO -> PHX, ...): one table, edge/nba.DK_ALIAS.
team = nba.team


def classic_groups(groups: list[dict] | None = None) -> list[dict]:
    """The lobby's Classic slates, earliest first (edge/dfs_slate.py)."""
    groups = groups if groups is not None else dfs.draft_groups(DK_SPORT)
    return dfs_slate.classic_rows(groups, CLASSIC)


def resolve_slate(draft_group=None, groups: list[dict] | None = None):
    """(gid, meta): the explicit group, else the next Main slate to lock."""
    rows = classic_groups(groups)
    if draft_group:
        return dfs_slate.explicit(rows, draft_group)
    if not rows:
        return None, {"error": "DraftKings is listing no NBA Classic slates."}
    pick = rows[dfs_slate.next_main(rows)]
    return pick["gid"], pick


def load_history() -> dict:
    """data/nba_player_history.json, from GitHub when the disk copy is stale (cloud)."""
    from edge import repo_files
    found = repo_files.read(HISTORY)
    if found is None:
        return {"players": {}, "names": {}, "teams": {}, "league_pm": None}
    return found.json()


def game_lines(client, games: dict) -> dict:
    """{team: implied points} from the books' total and spread, when posted."""
    want = {frozenset((team(g["home"]), team(g["away"]))) for g in games.values()}
    out: dict = {}
    try:
        events = client.get_featured_odds(SPORT, ["spreads", "totals"], "us")
    except Exception as exc:                                # noqa: BLE001
        log.warning("nba: no game lines (%s)", exc)
        return out
    for ev in events:
        home = TEAM_NAME_TO_ABBR.get(ev.get("home_team") or "")
        away = TEAM_NAME_TO_ABBR.get(ev.get("away_team") or "")
        if not home or not away or frozenset((home, away)) not in want:
            continue
        spread = total = None
        for bk in ev.get("bookmakers", []):
            for m in bk.get("markets", []):
                for o in m.get("outcomes", []):
                    if m["key"] == "totals" and o.get("name") == "Over" and total is None:
                        total = o.get("point")
                    if (m["key"] == "spreads" and spread is None
                            and TEAM_NAME_TO_ABBR.get(o.get("name") or "") == home):
                        spread = o.get("point")
        if total and spread is not None:
            out[home] = round(total / 2 - spread / 2, 1)
            out[away] = round(total / 2 + spread / 2, 1)
    return out


def tonight_status(date: str) -> dict:
    """{(norm name, team): {"starter", "confirmed", "inactive", "id"}} from NBA.com."""
    raw = nba_lineups.tonight(date)
    out = {}
    for g in nba_lineups.parse(raw):
        for p in g["players"]:
            out[(norm(p["name"]), p["team"])] = {"starter": p["starter"], "confirmed": p["confirmed"],
                                                 "inactive": p["inactive"], "id": str(p["id"])}
    return out


def build_board(gid: int, meta: dict, client=None) -> tuple[list, dict]:
    """(pool, info) for one DK NBA Classic slate: every playable player projected."""
    salaries = dfs.fetch_draftables(gid)
    info = {"draftables_source": dfs.LAST_DRAFTABLES_SOURCE}
    games = slate_games(salaries)
    start = _parse_time(meta.get("start") or next(iter(games.values()), {}).get("start"))
    date = (start.astimezone(ET).date() if start else datetime.now(ET).date()).isoformat()
    hist = load_history()
    status = tonight_status(date)
    info["lineups_source"] = nba_lineups.LAST_SOURCE
    implied = game_lines(client, games) if client is not None else {}
    league_pm = hist.get("league_pm")
    info["preseason"] = date < REGULAR_SEASON_START
    info.update({"date": date, "games": len(games), "priced": len(salaries), "implied": implied,
                 "imputed": [], "out": [], "history_date": hist.get("generated_at")})

    # Everyone DK priced, matched to his NBA history and tonight's NBA.com status.
    # History is several seasons of NBA.com ids, so a name can belong to more
    # than one player (Maxwell Lewis: LAL, and a London preseason opponent):
    # `names` kept whichever came last. A shared name now resolves only to the
    # one on this DK team, and to nobody when neither is -- never a guess.
    # The NCAAF build lost a cash game to a name-only join on 2026-10-16.
    by_name: dict = {}
    for hp_id, hp in hist["players"].items():
        by_name.setdefault(norm(hp.get("name") or ""), []).append((hp_id, hp.get("team")))

    def history_id(key, t):
        cands = by_name.get(key, [])
        if len(cands) == 1:
            return cands[0][0]
        mine = [i for i, tm in cands if tm == t]
        return mine[0] if len(mine) == 1 else None

    rows = []
    for sal in salaries.values():
        if not sal.get("salary"):
            continue
        t = team(sal.get("team"))
        key = norm(sal["name"])
        st = status.get((key, t), {})
        pid = st.get("id") or history_id(key, t)
        h = hist["players"].get(pid, {}) if pid else {}
        opp = next((team(g["away"]) if team(g["home"]) == t else team(g["home"])
                    for g in games.values() if t in (team(g["home"]), team(g["away"]))), None)
        out = bool(st.get("inactive")) or sal.get("dk_status") in ("OUT", "O")
        rows.append({"name": sal["name"], "team": t, "opp": opp, "game": sal.get("game"),
                     "salary": sal["salary"], "dk_pos": sal.get("position"),
                     "elig": dfs_opt_nba.eligible(sal.get("position")),
                     "pid": pid, "nba_pos": h.get("pos"), "history": [tuple(x) for x in h.get("hist", [])],
                     "preseason": bool(h.get("preseason")), "out": out,
                     "dk_status": sal.get("dk_status"), "starter": st.get("starter"),
                     "confirmed": st.get("confirmed"), "inactive_listed": bool(st.get("inactive")),
                     "window": (NM.SEASON_START_WINDOW if h.get("season_games", 0) < 5 else None)})

    # Minutes per team, from the whole team's news at once.
    by_team: dict = {}
    for r in rows:
        by_team.setdefault(r["team"], []).append(r)
    for t, ps in by_team.items():
        announced = sum(1 for r in ps if r["starter"]) == 5
        listed = any(r["inactive_listed"] for r in ps)
        if not listed:
            # Until NBA.com posts the inactive list (~30 min before tip), DK's ~20-man
            # list includes players who will not dress. A game-night roster is 13
            # active and its roles add to ~241 minutes (2025-26 median; 230 opening
            # week), so keep the 13 most-used and treat the rest as not dressing.
            # Without this the team was held to 240 over 21 roles summing to 300-360,
            # and every starter lost a quarter of his minutes.
            healthy = [r for r in ps if not r["out"]]
            ranked = sorted(healthy, key=lambda r: -NM.role([(m, s) for m, s, _ in r["history"]],
                                                            r["window"])["base"])
            for r in ranked[ACTIVE_ROSTER:]:
                r["not_dressing"] = True
        team_in = [{"id": r["name"], "position": r["nba_pos"],
                    "inactive": r["out"] or r.get("not_dressing", False),
                    "starter": r["starter"] if announced else None, "window": r["window"],
                    "history": [(m, s) for m, s, _ in r["history"]]} for r in ps]
        mins = NM.project(team_in)
        # "Usual" = the same team with no news: everyone healthy who dresses, no
        # starting five announced. Δ = proj - usual is then what tonight's scratches
        # and lineup changes did, not the 240-minute sharing every team goes through.
        calm = NM.project([{**x, "inactive": bool(next(r for r in ps if r["name"] == x["id"]).get("not_dressing")),
                            "starter": None} for x in team_in])
        allowed = hist["teams"].get(ps[0]["opp"]) if ps else None
        of = NP.opponent_factor(allowed[0] / allowed[1] if allowed and allowed[1] else None, league_pm)
        ef = NP.env_factor(implied.get(t))
        for r in ps:
            r["lineup"] = ("Confirmed" if r["confirmed"] else "Expected") if announced else ""
            if r["out"]:
                info["out"].append(r["name"])
                continue
            if r.get("not_dressing"):
                r["proj"] = 0.0
                continue
            m = mins.get(r["name"], {})
            r["minutes"] = m.get("minutes", 0.0)
            r["base_min"] = calm.get(r["name"], {}).get("minutes", m.get("base", 0.0))
            r["promoted"] = bool(m.get("promoted"))
            r["rate"] = round(NP.rate(r["history"], r["nba_pos"]), 3)
            r["imputed"] = []
            if r["history"]:
                r["proj"] = round(r["minutes"] * r["rate"] * of * ef, 2)
                r["usual"] = round(r["base_min"] * r["rate"] * of * ef, 2)
            else:
                r["proj"] = r["usual"] = round(r["salary"] / 1000.0 * IMPUTED_PER_K, 2)
                r["imputed"].append("no NBA history")
                info["imputed"].append(r["name"])
    pool = [r for r in rows if not r["out"] and r.get("proj", 0) > 0 and r["elig"]]
    for r in pool:
        r["pos"] = sorted(r["elig"], key=["PG", "SG", "SF", "PF", "C"].index)[0]
        r["value"] = round(r["proj"] / (r["salary"] / 1000.0), 2)
        r["gain"] = round(r["proj"] - r["usual"], 1)
    info["pool"] = len(pool)
    return pool, info


PROJ_LOG_COLS = ("date", "gid", "player", "team", "opp", "dk_pos", "salary", "minutes", "proj",
                 "sd", "floor", "ceil", "own", "promoted", "lineup", "imputed", "source")


def log_forward_test(pool, cash, gpp, gid, info, root: Path | None = None) -> dict:
    """Persist a build so a contest export has something to be joined to --
    edge/dfs_forward_log.py's rules (past dates never, this draft group only)."""
    date = info.get("date")
    rows = [{"date": date, "gid": gid, "player": p["name"], "team": p["team"], "opp": p.get("opp"),
             "dk_pos": p.get("dk_pos"), "salary": p["salary"], "minutes": p.get("minutes"),
             "proj": p["proj"], "sd": p.get("sd"), "floor": p.get("floor"), "ceil": p.get("ceil"),
             "own": p.get("own", ""), "promoted": int(bool(p.get("promoted"))),
             "lineup": p.get("lineup", ""), "imputed": "|".join(p.get("imputed", [])),
             "source": "live"} for p in pool]
    lineups = [{"mode": mode, "slot": slot, "player": p["name"], "team": p["team"],
                "salary": p["salary"], "proj": p["proj"], "own": p.get("own")}
               for mode, res in (("cash", cash), ("gpp", gpp))
               for slot, p in (res or {}).get("slots") or []]
    return dfs_forward_log.write("nba", date, gid, PROJ_LOG_COLS, rows,
                                 ("mode", "slot", "player", "team", "salary", "proj", "own"),
                                 lineups, root=root)


def build_slate(draft_group=None, client=None, n_sims: int = 3000, iters: int = 200, seed: int = 0,
                own_weight: float = 0.0, groups: list[dict] | None = None,
                persist: bool = True) -> dict:
    """Board + a CASH lineup + a GPP lineup for one DK NBA Classic slate."""
    if client is None:
        try:
            from edge.odds.cli import scraped_client
            client = scraped_client(SPORT, "dfs")
        except Exception as exc:                            # noqa: BLE001
            log.warning("nba: no odds client (%s); projecting without game lines", exc)
            client = None
    all_groups = groups if groups is not None else dfs.draft_groups(DK_SPORT)
    gid, meta = resolve_slate(draft_group, all_groups)
    slates = classic_groups(all_groups)
    if gid is None:
        return {"error": meta.get("error"), "slates": slates}
    try:
        pool, info = build_board(gid, meta, client)
    except dfs.DraftablesUnavailable as exc:
        return {"unpriced": True, "unpriced_reason": str(exc), "gid": gid, "meta": meta,
                "slates": slates}
    if not pool:
        return {"error": "No NBA players could be projected for this slate.", "gid": gid,
                "meta": meta, "slates": slates, "stats": info}
    sim = theory.simulate(pool, n_sims=n_sims, seed=seed)
    theory.summarise(sim, pool)
    theory.add_ownership(pool)
    cash = dfs_opt_nba.optimize(pool, sim, mode="cash", iters=iters, seed=seed)
    gpp = dfs_opt_nba.optimize(pool, sim, mode="gpp", iters=iters, seed=seed, own_weight=own_weight)
    log_result = None
    if persist:
        try:
            log_result = log_forward_test(pool, cash, gpp, gid, info)
        except Exception as exc:                            # noqa: BLE001
            log.warning("dfs_run_nba: forward-test logging failed: %s", exc)
    return {"gid": gid, "meta": meta, "slates": slates, "pool": pool, "sim": sim,
            "stats": info, "cash": cash, "gpp": gpp, "log": log_result}
