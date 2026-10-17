"""DK College Basketball Classic: slate -> board -> simulated games -> lineups.

pages/13_🏀_NCAAB_DFS.py and scripts/dfs_lineups_ncaab.py both call build_slate,
so the phone and the desktop cannot produce different lineups.

HOW THIS RELATES TO THE NBA BUILD
The 2026-10-13 web session built one engine for NBA and college (a per-league
Spec). The NBA kept its own model on 2026-10-16 -- minutes x points per minute,
held out at 7.65 DK points mean |error| on 2025-26 (edge/dfs_run_nba.py) -- so
this module is that engine with college as its only league. Same DK scoring
table (edge/nba.actual_points); different roster, prop supply and team names.

THE SLATE IS DEFINED BY DRAFTKINGS
Each player's team, opponent, game and tip time come from DK's own draftables,
never from the odds feed: a scan holds every event a book has posted, two
nights at once, and inferring the slate from it is how the NFL builder once
gave 21 of 28 teams the wrong opponent (DFS_STATUS.md). A book's line is
admitted only for a slate game, and only if its tip is within MATCH_TOLERANCE.

WHERE EACH PLAYER'S MEANS COME FROM, IN ORDER
    1. PROPS. Points, rebounds, assists, threes, steals, blocks, turnovers --
       every book that posts them, each two-sided line inverted to a mean
       under the simulator's own variance model (ncaab_sim.mean_from_market)
       and averaged across books. A stat with no line of its own is derived
       from a combo (Pts+Reb minus Pts, ...) and then from the position
       profile scaled by his points. `imputed` names each one.
    2. DK FPPG, when he has no points line, scaled half-way toward tonight's
       implied team points, split into stats by his position profile.
    3. SALARY (SALARY_VALUE points per $1K) for a rotation player a week into
       the season with no FPPG yet -- DK's price is the only projection there is.
Books post college props only for the top few players of the televised games,
so most of a board is (2). Every row's `source` says which.

TEAM NAMES DO NOT MATCH. "DUKE" against "Duke Blue Devils": a book event is
matched by which slate players its props resolve to, then by name similarity
inside one tip-off window (edge/ncaab.match_team). A game no line matches is
simulated at the league average and named on the page; nothing is dropped.

EVERYTHING HERE IS A PRIOR until the first college slate is graded (season
opens ~11/3): SALARY_VALUE, the profiles, the simulator's constants and the
ownership weights. NBA_STATUS.md (college section) lists what to fit first.
"""
from __future__ import annotations

import csv
import logging
import statistics
from datetime import datetime, timedelta
from pathlib import Path
from statistics import NormalDist
from zoneinfo import ZoneInfo

from edge import dfs_slate
from edge import dfs_forward_log
from edge import dfs, dfs_ncaab_theory as theory, dfs_opt_ncaab, ncaab, ncaab_sim
from edge.dfs_slate import parse_time as _parse_time, slate_games
from edge.names import norm

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
ET = ZoneInfo("America/New_York")
SPORT = "basketball_ncaab"
DK_SPORT = ncaab.DK_SPORT
LEAGUE = ncaab_sim.NCAAB
MATCH_TOLERANCE = timedelta(hours=6)
#: A college event with no player votes is matched by name only inside this
#: window around DK's tip, where the candidates are a handful of games.
NAME_WINDOW = timedelta(hours=3)
#: College margin vs the closing spread (ncaab_sim's eff_sd prior), for a
#: moneyline -> spread conversion when no spread posts.
MARGIN_SD = 10.7
#: A no-prop player is projected from FPPG only at or above this.
MIN_FPPG = 6.0
#: DK points per $1K for the salary fallback: the value a DK college salary
#: implies for a rotation player (DK's own pricing runs ~4.5-5x). PRIOR,
#: 2026-10-13; fit it on the first slate with scripts/nba_calibration.py --sport ncaab.
SALARY_VALUE = 4.6
#: The salary fallback applies only above min salary x this (a min-salary
#: player with no FPPG is a probable DNP and is left off).
SALARY_FLOOR_MULT = 1.15

#: Odds market -> simulator stat (singles) and combo keys.
SINGLE = {"player_points": "pts", "player_rebounds": "reb", "player_assists": "ast",
          "player_threes": "fg3", "player_steals": "stl", "player_blocks": "blk",
          "player_turnovers": "tov"}
COMBO = {"player_points_rebounds_assists": "pra", "player_points_rebounds": "pr",
         "player_points_assists": "pa", "player_rebounds_assists": "ra",
         "player_blocks_steals": "sb"}
MARKETS = list(SINGLE) + list(COMBO)

#: A typical box score per position group, used to split an FPPG (or a points
#: line with nothing else) into stats. Only the SHAPE matters -- each is scaled
#: to the player's own total. PRIORS: shorter game, fewer threes per point
#: than the NBA.
PROFILES = {
    "G": {"pts": 13.0, "reb": 3.6, "ast": 3.2, "fg3": 1.9, "stl": 1.1, "blk": 0.2, "tov": 1.9},
    "F": {"pts": 12.0, "reb": 6.5, "ast": 1.6, "fg3": 0.9, "stl": 0.8, "blk": 0.9, "tov": 1.5},
}
OUT_STATUS = {"OUT", "O", "IR", "SUSP", "NA"}
DOUBT_STATUS = {"Q", "GTD", "D", "DOUBTFUL", "QUESTIONABLE"}


def team(code):
    return (str(code).strip().upper() or None) if code else None


def positions(dk_position: str) -> list:
    """Groups in DK's own order ('G/F' -> ['G', 'F']), so a player's ownership
    GROUP is the position DK lists first."""
    allowed = ncaab.positions(dk_position)
    out = []
    for tok in (dk_position or "").replace(",", "/").split("/"):
        for p in ncaab.positions(tok):
            if p in allowed and p not in out:
                out.append(p)
    return out


# ---------------------------------------------------------------------------
# The slate, as DraftKings describes it
# ---------------------------------------------------------------------------
def classic_groups(groups: list[dict] | None = None) -> list[dict]:
    """College Classic slates. CBB's Classic GameTypeId has never been read
    live, so there is no default: Classic is whatever the lobby's own
    GameTypes list NAMES "Classic". If the lobby names none, the fallback is
    every multi-game group, which is only ever wrong by including a Tiers or
    snake contest -- the page shows each group's label, and the row says
    `unverified_type`."""
    groups = groups if groups is not None else dfs.draft_groups(DK_SPORT)
    ids = dfs.classic_game_types(DK_SPORT)
    if ids:
        out = [dfs_slate.group_row(g) for g in groups if g.get("GameTypeId") in ids]
    else:
        out = [dfs_slate.group_row(g, unverified_type=True) for g in groups if (g.get("GameCount") or 0) >= 2]
    out.sort(key=lambda r: (r["start"] or "", -r["games"]))
    return out


#: Index of the NEXT Main slate to lock -- not the biggest (edge/dfs_slate.py).
default_slate = dfs_slate.next_main


def resolve_slate(draft_group=None, groups: list[dict] | None = None):
    """(gid, meta): the explicit group, else the next Main slate to lock."""
    rows = classic_groups(groups)
    if draft_group:
        return dfs_slate.explicit(rows, draft_group)
    if not rows:
        return None, {"error": "DraftKings is listing no college basketball Classic slates."}
    pick = rows[default_slate(rows)]
    return pick["gid"], pick


# ---------------------------------------------------------------------------
# Props
# ---------------------------------------------------------------------------
def _subject(name: str) -> str:
    """DraftKings writes a college prop subject as "Cooper Flagg (DUKE)"."""
    return name.split(" (")[0].strip() if name.endswith(")") else name


def player_props(client, lookup: dfs.SalaryLookup, games: dict | None = None,
                 refused: list | None = None) -> tuple[dict, dict, dict]:
    """({salary_key: {stat: [mean per book]}}, {event_id: {salary_keys}},
    {book: players priced}) for every book that posted a line.

    A subject's lines are taken only from the event that is HIS game on this
    slate (edge/dfs_slate.event_games): college basketball has hundreds of
    off-slate games a night, and a name-only join averaged any namesake's
    line into a slate player's mean -- the NCAAF build lost a cash game to
    exactly that on 2026-10-16. Names refused are appended to `refused`."""
    payloads: dict = {}
    kickoffs: dict = {}
    subjects: dict = {}
    for ev in client.get_events(SPORT):
        kickoffs[ev["id"]] = ev.get("commence_time")
        try:
            payload = client.get_event_odds(SPORT, ev["id"], MARKETS, "us")
        except Exception as exc:                            # noqa: BLE001
            log.warning("ncaab: no props for event %s (%s)", ev.get("id"), exc)
            continue
        payloads[ev["id"]] = payload
        subjects[ev["id"]] = {_subject(o["description"]) for bk in payload.get("bookmakers", [])
                              for m in bk.get("markets", []) for o in m.get("outcomes", [])
                              if o.get("description")}
    owner, wrong = dfs_slate.subject_events(subjects, lookup.get, kickoffs, games)
    if refused is not None:
        refused.extend(wrong)

    out: dict = {}
    event_players: dict = {}
    books: dict = {}
    for ev_id, payload in payloads.items():
        for bk in payload.get("bookmakers", []):
            names = {o["description"] for m in bk.get("markets", [])
                     for o in m.get("outcomes", []) if o.get("description")}
            for name in names:
                if owner.get(_subject(name)) != ev_id:
                    continue
                info = lookup.get(_subject(name))
                if info is None:
                    continue
                key = norm(info["name"])
                pm = dfs.player_markets(bk, name)
                row = out.setdefault(key, {})
                hit = False
                for market, stat in {**SINGLE, **COMBO}.items():
                    mu = ncaab_sim.mean_from_market(stat if stat in ncaab_sim.STATS else "pts",
                                                    pm.get(market), LEAGUE)
                    if mu is not None:
                        row.setdefault(stat, []).append(mu)
                        hit = True
                if hit:
                    event_players.setdefault(ev_id, set()).add(key)
                    books[bk.get("key")] = books.get(bk.get("key"), 0) + 1
    return out, event_players, books


def means_from_props(stats: dict, profile: dict) -> tuple[dict | None, list]:
    """{stat: mean} for one player from per-book means, or (None, []) without
    points. Missing stats come from combos, then from the position profile
    scaled by his points; the second value names every stat not priced."""
    m = {k: statistics.fmean(v) for k, v in stats.items() if v}
    for _ in range(2):
        if "pts" not in m:
            for combo, rest in (("pr", ("reb",)), ("pa", ("ast",)), ("pra", ("reb", "ast"))):
                if combo in m and all(r in m for r in rest):
                    m["pts"] = m[combo] - sum(m[r] for r in rest)
                    break
        for stat, combos in (("reb", (("pr", ("pts",)), ("ra", ("ast",)), ("pra", ("pts", "ast")))),
                             ("ast", (("pa", ("pts",)), ("ra", ("reb",)), ("pra", ("pts", "reb"))))):
            if stat in m:
                continue
            for combo, rest in combos:
                if combo in m and all(r in m for r in rest):
                    m[stat] = m[combo] - sum(m[r] for r in rest)
                    break
    if "pts" not in m or m["pts"] <= 0:
        return None, []
    scale = m["pts"] / profile["pts"]
    imputed = []
    if "sb" in m and ("stl" not in m or "blk" not in m):
        both = profile["stl"] + profile["blk"]
        if "stl" not in m and "blk" not in m:
            m["stl"], m["blk"] = m["sb"] * profile["stl"] / both, m["sb"] * profile["blk"] / both
        elif "stl" not in m:
            m["stl"] = max(m["sb"] - m["blk"], 0.0)
        else:
            m["blk"] = max(m["sb"] - m["stl"], 0.0)
    for stat in ncaab_sim.STATS:
        if stat not in m:
            m[stat] = profile[stat] * scale
            imputed.append(stat)
        m[stat] = max(float(m[stat]), 0.0)
    return {s: round(m[s], 3) for s in ncaab_sim.STATS}, imputed


def means_from_total(fp: float, profile: dict) -> dict:
    """A box score whose DK points (before bonuses) equal `fp`, in the shape of
    the position profile."""
    base = ncaab_sim.fantasy_mean(profile)
    return {s: round(profile[s] * fp / base, 3) for s in ncaab_sim.STATS}


# ---------------------------------------------------------------------------
# Game lines
# ---------------------------------------------------------------------------
def _median(xs):
    return statistics.median(xs) if xs else None


def _event_prices(ev: dict) -> dict:
    """total / home spread / P(home wins) across every book on one event."""
    totals, spreads, p_home = [], [], []
    home, away = ev.get("home_team"), ev.get("away_team")
    for bk in ev.get("bookmakers", []):
        for m in bk.get("markets", []):
            o = {x.get("name"): x for x in m.get("outcomes", [])}
            if m["key"] == "totals" and "Over" in o and o["Over"].get("point") is not None:
                totals.append(float(o["Over"]["point"]))
            elif m["key"] == "spreads" and home in o and o[home].get("point") is not None:
                spreads.append(float(o[home]["point"]))
            elif m["key"] == "h2h" and home in o and away in o:
                p_home.append(ncaab_sim.devig(o[home]["price"], o[away]["price"]))
    return {"total": _median(totals), "spread_home": _median(spreads),
            "p_home": _median(p_home)}


def _match_event(ev: dict, games: dict, event_players: dict,
                 salaries: dict) -> tuple[str | None, bool]:
    """(DK game id, flipped) for one book event, or (None, False). `flipped`
    when the book's home team is DK's away team (neutral sites)."""
    when = _parse_time(ev.get("commence_time"))

    def near(g, tol):
        return not (when and g["start"]) or abs(when - g["start"]) <= tol

    votes: dict = {}
    for key in event_players.get(ev.get("id"), ()):
        info = salaries.get(key)
        if info and info.get("game"):
            votes[info["game"]] = votes.get(info["game"], 0) + 1
    scored = []
    for gid, g in games.items():
        if not near(g, MATCH_TOLERANCE if gid in votes else NAME_WINDOW):
            continue
        straight = (ncaab.match_team(g["home"], ev.get("home_team"))
                    + ncaab.match_team(g["away"], ev.get("away_team")))
        flip = (ncaab.match_team(g["home"], ev.get("away_team"))
                + ncaab.match_team(g["away"], ev.get("home_team")))
        score = max(straight, flip) + 2.0 * votes.get(gid, 0)
        scored.append((score, gid, flip > straight))
    if not scored:
        return None, False
    score, gid, flipped = max(scored)
    return (gid, flipped) if score >= 1.2 else (None, False)


def game_lines(client, games: dict, event_players: dict | None = None,
               salaries: dict | None = None) -> tuple[dict, dict]:
    """({team: implied points}, {game_id: {total, spread_home, source}}) for the
    slate's games only. A game with no line is absent from both -- it is
    still simulated, at the league average, and named on the page."""
    implied: dict = {}
    lines: dict = {}
    try:
        events = client.get_featured_odds(SPORT, ["h2h", "spreads", "totals"], "us")
    except Exception as exc:                                # noqa: BLE001
        log.warning("ncaab: no game lines (%s)", exc)
        return implied, lines
    for ev in events:
        gid, flipped = _match_event(ev, games, event_players or {}, salaries or {})
        if gid is None or gid in lines:
            continue
        pr = _event_prices(ev)
        spread, total, src = pr["spread_home"], pr["total"], []
        if spread is None and pr["p_home"] is not None:
            p = min(max(pr["p_home"], 0.01), 0.99)
            spread = -MARGIN_SD * NormalDist().inv_cdf(p)
            src.append("spread from moneyline")
        if total is None and spread is None:
            continue
        if total is None:
            total = 2.0 * LEAGUE.team_points
            src.append("no total: league average")
        spread = spread or 0.0
        if flipped:
            spread = -spread
        g = games[gid]
        ph, pa = ncaab_sim.implied_points(total, spread)
        implied[team(g["home"])], implied[team(g["away"])] = round(ph, 2), round(pa, 2)
        lines[gid] = {"total": round(total, 1), "spread_home": round(spread, 1),
                      "source": ", ".join(src) or "market"}
    return implied, lines


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------
def build_board(gid: int, meta: dict, client, n_sims: int = 2000, seed: int = 0):
    """(pool, sim, info) for one DK college basketball Classic slate."""
    salaries = dfs.fetch_draftables(gid)
    info = {"draftables_source": dfs.LAST_DRAFTABLES_SOURCE,
            "fppg_id": dfs.LAST_DETECTED_FPPG_ID}
    games = slate_games(salaries)
    lookup = dfs.SalaryLookup(salaries)
    wrong_game: list = []
    try:
        props, event_players, books = player_props(client, lookup, games, wrong_game)
    except Exception as exc:                                # noqa: BLE001
        log.warning("ncaab: props unavailable (%s)", exc)
        props, event_players, books = {}, {}, {}
    implied, lines = game_lines(client, games, event_players, salaries)
    start = _parse_time(meta.get("start") or next(iter(games.values()), {}).get("start"))
    date = (start.astimezone(ET).date() if start else datetime.now(ET).date()).isoformat()
    teams = {team(g[s]) for g in games.values() for s in ("home", "away")}
    min_salary = min((s["salary"] for s in salaries.values() if s.get("salary")), default=0)
    info.update({"games": len(games), "priced": len(salaries), "implied": implied,
                 "lines": {games[k]["matchup"]: v for k, v in lines.items()},
                 "missing_lines": sorted(g["matchup"] for k, g in games.items() if k not in lines),
                 "date": date, "books": books, "out": [], "doubtful": [],
                 "dropped_no_projection": [], "sources": {"props": 0, "fppg": 0, "salary": 0},
                 "imputed": 0, "wrong_game": wrong_game})
    opp_of = {}
    for g in games.values():
        opp_of[team(g["home"])] = team(g["away"])
        opp_of[team(g["away"])] = team(g["home"])
    pool = []
    for sal in salaries.values():
        if not sal.get("salary"):
            continue
        t = team(sal.get("team"))
        slots = ncaab.slots_for(sal.get("position"))
        pos = positions(sal.get("position"))
        if not slots or not pos or t not in teams:
            continue
        status = (sal.get("dk_status") or "").upper()
        if status in OUT_STATUS:
            info["out"].append(f"{sal['name']} ({t})")
            continue
        group = pos[0]
        profile = PROFILES[group]
        means, imputed = means_from_props(props.get(norm(sal["name"])) or {}, profile)
        source = "props"
        if means is None:
            imputed = ["all"]
            fp = sal.get("dk_fppg")
            if fp is not None and fp >= MIN_FPPG:
                source = "fppg"
            elif sal["salary"] >= min_salary * SALARY_FLOOR_MULT:
                fp, source = SALARY_VALUE * sal["salary"] / 1000.0, "salary"
            else:
                info["dropped_no_projection"].append(sal["name"])
                continue
            if t in implied:
                # DK's FPPG already carries his team's average night; scale
                # half-way toward tonight's environment (a PRIOR exponent).
                fp *= (implied[t] / LEAGUE.team_points) ** 0.5
            means = means_from_total(fp, profile)
        info["sources"][source] += 1
        if imputed:
            info["imputed"] += 1
        if status in DOUBT_STATUS:
            info["doubtful"].append(f"{sal['name']} ({t}, {status})")
        pool.append({"name": sal["name"], "team": t, "opp": opp_of.get(t),
                     "game": sal.get("game"), "matchup": sal.get("matchup"),
                     "start": sal.get("start"), "salary": sal["salary"],
                     "dk_pos": sal.get("position"), "group": group, "slots": slots,
                     "means": means, "imputed": imputed, "source": source,
                     "status": status or None, "dk_fppg": sal.get("dk_fppg")})

    sim_games = {}
    for k, g in games.items():
        h, a = team(g["home"]), team(g["away"])
        sim_games[k] = {"home": h, "away": a, "pts_home": implied.get(h, LEAGUE.team_points),
                        "pts_away": implied.get(a, LEAGUE.team_points)}
    sim = ncaab_sim.simulate(pool, sim_games, n_sims=n_sims, seed=seed, league=LEAGUE)
    pool = ncaab_sim.summarise(sim, pool, theory.CASH_PCT, theory.GPP_PCT)
    theory.add_ownership(pool)
    info["pool"] = len(pool)
    return pool, sim, info


# ---------------------------------------------------------------------------
# Forward-test log
# ---------------------------------------------------------------------------
PROJ_LOG_COLS = ("date", "gid", "player", "team", "opp", "dk_pos", "salary", "proj", "sd",
                 "floor", "ceil", "own", "proj_source", "imputed", "status", "source")


def log_forward_test(pool, cash, gpp, gid, info, root: Path | None = None) -> dict:
    """Persist a build so a contest export has something to be joined to --
    edge/dfs_forward_log.py's rules (past dates never, this draft group only)."""
    date = info.get("date")
    rows = [{"date": date, "gid": gid, "player": p["name"], "team": p["team"],
             "opp": p.get("opp"), "dk_pos": p.get("dk_pos"), "salary": p["salary"],
             "proj": p["proj"], "sd": p["sd"], "floor": p["floor"], "ceil": p["ceil"],
             "own": p.get("own", ""), "proj_source": p.get("source"),
             "imputed": "|".join(p.get("imputed", [])), "status": p.get("status") or "",
             "source": "live"} for p in pool]
    lineups = [{"mode": mode, "slot": slot, "player": p["name"], "team": p["team"],
                "salary": p["salary"], "proj": p["proj"], "own": p.get("own")}
               for mode, res in (("cash", cash), ("gpp", gpp))
               for slot, p in (res or {}).get("slots") or []]
    return dfs_forward_log.write("ncaab", date, gid, PROJ_LOG_COLS, rows,
                                 ("mode", "slot", "player", "team", "salary", "proj", "own"),
                                 lineups, root=root)


# ---------------------------------------------------------------------------
# The whole slate
# ---------------------------------------------------------------------------
def build_slate(draft_group=None, client=None, n_sims: int = 2000, iters: int = 150,
                seed: int = 0, own_weight: float = 0.0, groups: list[dict] | None = None,
                persist: bool = True, locked: set | None = None) -> dict:
    """Board + a CASH lineup + a GPP lineup for one DK college Classic slate.
    `locked` keeps named players in both lineups (late swap)."""
    if client is None:
        # The local store on the desktop, the published snapshot on the cloud.
        from edge.odds.cli import scraped_client
        client = scraped_client(SPORT, "dfs")
    all_groups = groups if groups is not None else dfs.draft_groups(DK_SPORT)
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
        return {"error": "No college players could be projected for this slate.",
                "gid": gid, "meta": meta, "slates": slates, "stats": info}
    cash = dfs_opt_ncaab.optimize(pool, sim, mode="cash", iters=iters, seed=seed, locked=locked)
    gpp = dfs_opt_ncaab.optimize(pool, sim, mode="gpp", iters=iters, seed=seed,
                                 own_weight=own_weight, locked=locked)
    log_result = None
    if persist:
        try:
            log_result = log_forward_test(pool, cash, gpp, gid, info)
        except Exception as exc:                            # noqa: BLE001
            log.warning("dfs_run_ncaab: forward-test logging failed: %s", exc)
    return {"gid": gid, "meta": meta, "slates": slates, "pool": pool, "sim": sim,
            "stats": info, "cash": cash, "gpp": gpp, "log": log_result, "spec": "ncaab"}
