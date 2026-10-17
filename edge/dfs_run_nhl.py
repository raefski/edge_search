"""DK NHL Classic: slate -> board -> simulated games -> lineups, one entry point.

THE BOARD IS WHO IS DRESSING, NOT WHO IS PRICED
DraftKings prices every rostered player -- 308 on the 2026-09-29 four-game
slate, against about 170 who will play. The board here is the DailyFaceoff
projected lineup (forward lines F1-F4, defence pairs D1-D3) plus anyone a book
priced a prop for, and only the projected STARTING goalie per team. A priced
healthy scratch is worth zero points, and an optimiser that can see him will
eventually play him because he is cheap.

WHERE EACH SKATER'S MEANS COME FROM
    shots    DraftKings Shots on Goal O/U  -> Poisson mean      else last season/GP
    points   DraftKings Points O/U         -> Poisson mean      else last season/GP
    assists  DraftKings Assists O/U        -> Poisson mean      else last season/GP
    goals    points minus assists                               else last season/GP
    blocks   no market at any book         -> last season/GP (by position if new)
Fallback rates are scaled by the team's market-implied goals relative to the
league average, so a depth player on a favourite is not priced like one on an
underdog. Every fallback is flagged `imputed` in the app.

Goalies: the starter's saves come from his opponents' simulated shots, rescaled
to his DraftKings Saves O/U when one is posted.
"""
from __future__ import annotations

import csv
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from edge import dfs_slate
from edge import dfs_forward_log
from edge import dfs, dfs_nhl_field, dfs_nhl_theory as theory, dfs_opt_nhl, nhl, nhl_sim
from edge.dfs_slate import parse_time as _parse_time, slate_games
from edge.names import norm

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
SPORT = "icehockey_nhl"
#: 2025-26 regular season goals per team-game, measured over 1,311 games
#: (scripts/nhl_fit.py --sim). Scales last-season fallback rates to tonight.
LEAGUE_TEAM_GOALS = 3.128
LAST_SEASON = "20252026"
DEFAULT_BLOCKS = {"D": 1.25, "C": 0.45, "W": 0.4}


def classic_groups(groups: list[dict] | None = None) -> list[dict]:
    """The lobby's Classic slates, earliest first (edge/dfs_slate.py)."""
    groups = groups if groups is not None else dfs.draft_groups(nhl.DK_SPORT)
    return dfs_slate.classic_rows(groups, nhl.CLASSIC_GAME_TYPE)


#: Index of the NEXT Main slate to lock. DK lists tomorrow's Main next to
#: tonight's, so "the Main with the most games" picked tomorrow's 10-game
#: slate over tonight's 3-game one (2026-10-07).
default_slate = dfs_slate.next_main


def resolve_slate(draft_group=None, groups: list[dict] | None = None):
    """(gid, meta). Defaults to DK's main slate: the Classic group with no
    suffix, as for the NFL (see dfs_run_nfl.resolve_slate)."""
    rows = classic_groups(groups)
    if draft_group:
        return dfs_slate.explicit(rows, draft_group)
    if not rows:
        return None, {"error": "DraftKings is listing no NHL Classic slates."}
    pick = rows[default_slate(rows)]
    return pick["gid"], pick


def _team_by_name() -> dict:
    """Book team names ("Carolina Hurricanes", "St Louis Blues") -> NHL abbr."""
    out = {}
    for abbr, slug in nhl.DFO_SLUGS.items():
        out[norm(slug.replace("-", " "))] = abbr
    out[norm("st louis blues")] = "STL"
    out[norm("utah hockey club")] = "UTA"
    return out


def game_lines(client, games: dict) -> dict:
    """{team: implied goals} for the slate, from moneyline + total."""
    names = _team_by_name()
    want = {frozenset((nhl.team(g["home"]), nhl.team(g["away"]))) for g in games.values()}
    out: dict = {}
    for ev in client.get_featured_odds(SPORT, ["h2h", "totals"], "us"):
        home, away = names.get(norm(ev.get("home_team") or "")), names.get(norm(ev.get("away_team") or ""))
        if not home or not away or frozenset((home, away)) not in want:
            continue
        ml, tot = {}, None
        for bk in ev.get("bookmakers", []):
            for m in bk.get("markets", []):
                if m["key"] == "h2h" and not ml:
                    ml = {names.get(norm(o["name"])): o["price"] for o in m["outcomes"]}
                if m["key"] == "totals" and tot is None:
                    o = {x["name"]: x for x in m["outcomes"]}
                    if "Over" in o and "Under" in o:
                        tot = (o["Over"]["point"], nhl_sim.devig(o["Over"]["price"], o["Under"]["price"]))
        if ml.get(home) and ml.get(away) and tot:
            p_home = nhl_sim.devig(ml[home], ml[away])
            lh, la = nhl_sim.implied_goals(tot[0], tot[1], p_home)
            out[home], out[away] = round(lh, 3), round(la, 3)
    return out


def player_props(client, markets: list[str], book: str = "draftkings",
                 salaries: dict | None = None, games: dict | None = None) -> tuple[dict, list]:
    """({player: {market: {Over, Under, point}}}, [names refused]) for one book.

    Each player's lines come only from the event that is HIS game on this
    slate (edge/dfs_slate.event_games). The flat walk this replaces merged a
    player's lines across every event with .update(), and NHL scans hold two
    nights at once -- a team on a back-to-back could carry tomorrow's shots
    line into tonight's projection, and any namesake's line joined by name.
    Without `salaries` there is no slate to check against, so nothing is
    refused (the shape a caller with no board still gets)."""
    by_event, kickoffs = dfs_slate.player_markets_by_event(client, SPORT, markets, book)
    if salaries is None:
        out: dict = {}
        for players in by_event.values():
            for name, pm in players.items():
                if pm:
                    out.setdefault(name, {}).update(pm)
        return out, []
    lookup = dfs.SalaryLookup(salaries)
    return dfs_slate.own_game_markets(by_event, kickoffs, lookup.get,
                                      games if games is not None else slate_games(salaries))


def _season_rates() -> dict:
    """{norm(name): per-game rates} from last season's NHL reports."""
    try:
        stats = nhl.season_skater_stats(LAST_SEASON)
    except Exception as exc:                                # noqa: BLE001
        log.warning("nhl: season stats unavailable (%s)", exc)
        return {}
    out = {}
    for r in stats.values():
        gp = r.get("gamesPlayed") or 0
        if gp < 5 or not r.get("skaterFullName"):
            continue
        out[norm(r["skaterFullName"])] = {
            "goals": (r.get("goals") or 0) / gp, "assists": (r.get("assists") or 0) / gp,
            "shots": (r.get("shots") or 0) / gp, "blocks": (r.get("blockedShots") or 0) / gp,
            "gp": gp}
    return out


BUZZ_FILE = ROOT / "data" / "buzz_nhl.csv"


def attach_buzz(pool: list, date: str, path: Path = BUZZ_FILE) -> int:
    """YouTube mentions for tonight (scripts/buzz_nhl.py), for DISPLAY: no NHL
    contest export exists yet to fit what they are worth to ownership, which
    is how the NFL's weight was earned. Returns how many rows the night has."""
    import io

    from edge import repo_files
    found = repo_files.read(path)
    if found is None:
        return 0
    rows = [r for r in csv.DictReader(io.StringIO(found.data)) if r.get("date") == date]
    mentions = {norm(r["player"]): int(float(r.get("mentions") or 0)) for r in rows}
    for p in pool:
        p["buzz"] = mentions.get(norm(p["name"]), 0) if rows else None
    return len(rows)


def build_board(gid: int, meta: dict, client, n_sims: int = 2000, seed: int = 0):
    """(pool, sim, info) for one DK NHL Classic slate."""
    salaries = dfs.fetch_draftables(gid)
    games = slate_games(salaries)
    teams = {nhl.team(g[s]) for g in games.values() for s in ("home", "away")}
    implied = game_lines(client, games)
    props, wrong_game = player_props(client, ["player_shots_on_goal", "player_points",
                                              "player_assists", "player_total_saves"],
                                     salaries=salaries, games=games)
    props = {norm(k): v for k, v in props.items()}
    start = _parse_time(meta.get("start") or next(iter(games.values()), {}).get("start"))
    date = (start.astimezone(ZoneInfo("America/New_York")).date().isoformat()
            if start else datetime.now(ZoneInfo("America/New_York")).date().isoformat())
    lines: dict = {}
    for t in teams:
        try:
            for name, row in nhl.team_lines(t).items():
                lines[(t, norm(name))] = row
        except Exception as exc:                            # noqa: BLE001
            log.warning("nhl: no DailyFaceoff lines for %s (%s)", t, exc)
    try:
        starters = nhl.starting_goalies(date)
    except Exception as exc:                                # noqa: BLE001
        log.warning("nhl: no starting goalies (%s)", exc)
        starters = {}
    rates = _season_rates()

    info = {"games": len(games), "priced": len(salaries), "implied": implied,
            "missing_lines": sorted(teams - set(implied)), "date": date,
            "starters": starters, "no_lineup": [], "imputed": 0,
            "wrong_game": wrong_game}
    pool = []
    for sal in salaries.values():
        if not sal.get("salary"):
            continue
        t = nhl.team(sal.get("team"))
        slots = nhl.slots_for(sal.get("position"))
        if not slots or t not in implied or sal.get("dk_status") in ("OUT", "IR", "O"):
            continue
        key = norm(sal["name"])
        ln = lines.get((t, key), {})
        opp = next((nhl.team(g["away"] if nhl.team(g["home"]) == t else g["home"])
                    for g in games.values() if t in (nhl.team(g["home"]), nhl.team(g["away"]))), None)
        pos = "G" if "G" in slots else ("D" if "D" in slots else ("C" if "C" in slots else "W"))
        row = {"name": sal["name"], "team": t, "opp": opp, "game": sal.get("game"),
               "salary": sal["salary"], "dk_pos": sal.get("position"), "pos": pos,
               "slots": slots, "line": ln.get("line"), "pp": ln.get("pp"),
               "injury": ln.get("injury"), "dk_fppg": sal.get("dk_fppg"), "imputed": []}
        if pos == "G":
            st = starters.get(t) or {}
            if norm(st.get("goalie") or "") != key:
                continue
            row["starter_status"] = st.get("status") or "Projected"
            saves = nhl_sim.market_mean((props.get(key) or {}).get("player_total_saves"))
            row["means"] = {"saves": saves} if saves else {}
            pool.append(row)
            continue
        pm = props.get(key) or {}
        if not ln.get("line") and not pm:
            info["no_lineup"].append(sal["name"])
            continue
        if (ln.get("injury") or "").lower() in ("ir", "out", "ltir"):
            continue
        scale = implied[t] / LEAGUE_TEAM_GOALS
        base = rates.get(key) or {}
        shots = nhl_sim.market_mean(pm.get("player_shots_on_goal"))
        points = nhl_sim.market_mean(pm.get("player_points"))
        assists = nhl_sim.market_mean(pm.get("player_assists"))
        means = {}
        if shots is None:
            shots = base.get("shots", 1.0 if pos != "D" else 1.2) * scale
            row["imputed"].append("shots")
        if points is None or assists is None:
            g_ = base.get("goals", 0.08 if pos != "D" else 0.04) * scale
            a_ = base.get("assists", 0.12 if pos != "D" else 0.12) * scale
            row["imputed"].append("points")
        else:
            a_ = assists
            g_ = max(points - assists, 0.02)
        means.update({"shots": max(shots, g_ + 0.05), "goals": g_, "assists": a_,
                      "blocks": base.get("blocks", DEFAULT_BLOCKS[pos])})
        if "blocks" not in base:
            row["imputed"].append("blocks")
        if row["imputed"]:
            info["imputed"] += 1
        row["means"] = means
        pool.append(row)

    sim_games = {}
    for g in games.values():
        h, a = nhl.team(g["home"]), nhl.team(g["away"])
        if h in implied and a in implied:
            sim_games[g.get("matchup") or f"{a}@{h}"] = {"home": h, "away": a,
                                                         "lam_home": implied[h], "lam_away": implied[a]}
    info["buzz_rows"] = attach_buzz(pool, date)
    sim = nhl_sim.simulate(pool, sim_games, n_sims=n_sims, seed=seed)
    pool = nhl_sim.summarise(sim, pool)
    theory.add_ownership(pool)
    info["pool"] = len(pool)
    return pool, sim, info


# ---------------------------------------------------------------------------
# The whole slate
# ---------------------------------------------------------------------------
#: source: "live" for a build made before lock, "rebuild" for one reconstructed
#: afterwards from pinned pre-lock inputs (scripts/nhl_rebuild.py).
PROJ_LOG_COLS = ("date", "gid", "player", "team", "opp", "dk_pos", "line", "pp", "salary",
                 "proj", "sd", "floor", "ceil", "own", "imputed", "source")


def log_forward_test(pool, cash, gpp, gid, info, root: Path | None = None,
                     cash_floor: dict | None = None) -> dict:
    """Persist a build so a contest export has something to be joined to --
    edge/dfs_forward_log.py's rules (past dates never, this draft group only)."""
    rows = [{"date": info.get("date"), "gid": gid, "player": p["name"], "team": p["team"],
             "opp": p.get("opp"), "dk_pos": p.get("dk_pos"), "line": p.get("line") or "",
             "pp": p.get("pp") or "", "salary": p["salary"], "proj": p["proj"], "sd": p["sd"],
             "floor": p["floor"], "ceil": p["ceil"], "own": p.get("own", ""),
             "imputed": "|".join(p.get("imputed", [])), "source": "live"} for p in pool]
    lineups = [{"mode": mode, "slot": slot, "player": p["name"], "team": p["team"],
                "line": p.get("line"), "pp": p.get("pp"), "salary": p["salary"],
                "proj": p["proj"], "own": p.get("own")}
               for mode, res in (("cash", cash), ("gpp", gpp), ("cash_floor", cash_floor))
               for slot, p in (res or {}).get("slots", [])]
    return dfs_forward_log.write("nhl", info.get("date"), gid, PROJ_LOG_COLS, rows,
                                 ("mode", "slot", "player", "team", "line", "pp", "salary",
                                  "proj", "own"), lineups, root=root)


def build_slate(draft_group=None, client=None, n_sims: int = 2000, iters: int = 250,
                seed: int = 0, own_weight: float = 0.0, groups: list[dict] | None = None,
                persist: bool = True) -> dict:
    """Board + a CASH lineup + a GPP lineup for one DK NHL Classic slate."""
    if client is None:
        # The local store on the desktop, the published snapshot on the cloud
        # (which has no store) -- the same resolution the NFL page uses.
        from edge.odds.cli import scraped_client
        client = scraped_client(SPORT, "dfs")
    all_groups = groups if groups is not None else dfs.draft_groups(nhl.DK_SPORT)
    gid, meta = resolve_slate(draft_group, all_groups)
    slates = classic_groups(all_groups)
    if gid is None:
        return {"error": meta.get("error"), "slates": slates}
    try:
        pool, sim, info = build_board(gid, meta, client, n_sims=n_sims, seed=seed)
    except dfs.DraftablesUnavailable as exc:
        return {"unpriced": True, "unpriced_reason": str(exc), "gid": gid, "meta": meta,
                "slates": slates}
    info["odds_source"] = type(client).__name__
    info["odds_age_min"] = (round(client.age_seconds / 60.0)
                            if getattr(client, "age_seconds", None) is not None else None)
    if not pool:
        return {"error": "No NHL players could be projected for this slate.", "gid": gid,
                "meta": meta, "slates": slates, "stats": info}
    # Cash is field-aware (NHL_STATUS.md 6c): the lineup most likely to clear
    # the simulated double-up field. The old floor lineup is kept and logged
    # beside it so the switch keeps being graded.
    cash_floor = dfs_opt_nhl.optimize(pool, sim, mode="cash", iters=iters, seed=seed)
    try:
        cash = dfs_nhl_field.optimize_cash(pool, sim, iters=iters, seed=seed)
    except ValueError as exc:
        log.warning("dfs_run_nhl: field-aware cash failed (%s); using the floor lineup", exc)
        cash = cash_floor
    gpp = dfs_opt_nhl.optimize(pool, sim, mode="gpp", iters=iters, seed=seed,
                               own_weight=own_weight)
    log_result = None
    if persist:
        try:
            log_result = log_forward_test(pool, cash, gpp, gid, info, cash_floor=cash_floor)
        except Exception as exc:                            # noqa: BLE001
            log.warning("dfs_run_nhl: forward-test logging failed: %s", exc)
    return {"gid": gid, "meta": meta, "slates": slates, "pool": pool, "sim": sim,
            "stats": info, "cash": cash, "cash_floor": cash_floor, "gpp": gpp,
            "log": log_result}
