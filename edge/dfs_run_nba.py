"""DK NBA Classic: slate -> board -> simulated games -> lineups, one entry point.

pages/12_🏀_NBA_DFS.py and scripts/dfs_lineups_nba.py both call build_slate,
so the phone and the desktop cannot produce different lineups. College
basketball (edge/dfs_run_ncaab.py) runs the same code under its own Spec --
the scoring is identical, the roster, the prop supply and the team names are
not, and everything that differs is a field on Spec rather than a branch here.

THE SLATE IS DEFINED BY DRAFTKINGS
Each player's team, opponent, game and tip time come from DK's own draftables
(`matchup`, `competitionId`, `start`), never from the odds feed: a scan holds
every event a book has posted, two nights of them at once, and inferring the
slate from that is how the NFL builder once gave 21 of 28 teams the wrong
opponent (DFS_STATUS.md). A book's line is admitted only for a slate game, and
only if its tip is within MATCH_TOLERANCE of DK's.

WHERE EACH PLAYER'S MEANS COME FROM, IN ORDER
    1. PROPS. Points, rebounds, assists, threes, steals, blocks, turnovers --
       every book that posts them, each two-sided line inverted to a mean
       under the simulator's own variance model (nba_sim.mean_from_market)
       and averaged across books. A stat with no line of its own is derived
       from a combo (Pts+Reb minus Pts, ...) and then from the player's
       position profile scaled by his points. `imputed` names each one.
    2. DK FPPG (season to date), when he has no points line at all, scaled
       by tonight's implied team points against the league average and split
       into a box score by his position's profile. Flagged `fppg`.
    3. SALARY, college only: DK's price implies a projection. Flagged
       `salary`. The NBA does not use it -- a priced NBA player with no prop
       and no FPPG is a deep-bench DNP risk, and projecting him off a $3,000
       salary would hand the optimiser a 14-point "value" who will not play.
A player DK lists OUT is dropped; Q / GTD are kept and named on the page.

THE BOARD IS STILL ONLY AS GOOD AS THE INJURY NEWS IN THE PROPS. When a star
is ruled out the books pull his teammates' props and re-post them higher. A
fallback (FPPG) projection does NOT move -- so the page names every OUT and Q
player and how many teammates are on a fallback, and the answer to a late
scratch is Refresh once the books have re-posted.
"""
from __future__ import annotations

import csv
import logging
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from statistics import NormalDist
from typing import Callable
from zoneinfo import ZoneInfo

from edge import dfs, dfs_nba_theory as theory, dfs_opt_nba, nba, nba_onoff, nba_sim
from edge.dfs_run_nfl import _parse_time, slate_games
from edge.names import norm

log = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[1]
ET = ZoneInfo("America/New_York")
MATCH_TOLERANCE = timedelta(hours=6)
#: A college event with no player votes is matched by name only inside this
#: window around DK's tip, where the candidates are a handful of games.
NAME_WINDOW = timedelta(hours=3)

#: Odds market -> simulator stat (singles) and combo keys.
SINGLE = {"player_points": "pts", "player_rebounds": "reb", "player_assists": "ast",
          "player_threes": "fg3", "player_steals": "stl", "player_blocks": "blk",
          "player_turnovers": "tov"}
COMBO = {"player_points_rebounds_assists": "pra", "player_points_rebounds": "pr",
         "player_points_assists": "pa", "player_rebounds_assists": "ra",
         "player_blocks_steals": "sb"}
MARKETS = list(SINGLE) + list(COMBO)

#: A typical box score per position group, used to split an FPPG (or a points
#: line with nothing else) into stats. PRIORS from 2024-25 league per-position
#: shares (~27-30 DK points each); scripts/nba_fit.py --profiles re-derives
#: them from data/nba_ground_truth/. Only the SHAPE matters -- each is scaled
#: to the player's own total.
NBA_PROFILES = {
    "PG": {"pts": 14.0, "reb": 3.5, "ast": 5.5, "fg3": 1.8, "stl": 1.0, "blk": 0.3, "tov": 2.0},
    "SG": {"pts": 15.0, "reb": 3.8, "ast": 3.0, "fg3": 2.0, "stl": 0.9, "blk": 0.3, "tov": 1.4},
    "SF": {"pts": 14.0, "reb": 5.0, "ast": 2.6, "fg3": 1.6, "stl": 0.9, "blk": 0.5, "tov": 1.3},
    "PF": {"pts": 13.0, "reb": 6.5, "ast": 2.3, "fg3": 1.1, "stl": 0.7, "blk": 0.7, "tov": 1.3},
    "C": {"pts": 12.0, "reb": 8.5, "ast": 2.2, "fg3": 0.5, "stl": 0.7, "blk": 1.3, "tov": 1.5},
}
#: College: shorter game, fewer threes per point than the NBA. PRIORS.
NCAAB_PROFILES = {
    "G": {"pts": 13.0, "reb": 3.6, "ast": 3.2, "fg3": 1.9, "stl": 1.1, "blk": 0.2, "tov": 1.9},
    "F": {"pts": 12.0, "reb": 6.5, "ast": 1.6, "fg3": 0.9, "stl": 0.8, "blk": 0.9, "tov": 1.5},
}


@dataclass(frozen=True)
class Spec:
    """Everything that differs between DK NBA and DK College Basketball."""
    name: str                       # "nba" / "ncaab": log file names, labels
    sport: str                      # odds key
    dk_sport: str                   # DK lobby code
    classic_default: int | None     # Classic GameTypeId when the lobby names none
    league: nba_sim.League
    roster: dfs_opt_nba.Roster
    group_slots: dict
    profiles: dict
    positions: Callable
    slots_for: Callable
    team: Callable
    #: book full name -> DK code; None = match college names fuzzily
    team_from_name: Callable | None
    #: margin sd, for a moneyline -> spread conversion when no spread posts
    margin_sd: float
    #: a no-prop player is projected from FPPG only at or above this
    min_fppg: float
    #: DK points per $1K salary for the salary fallback; None disables it
    salary_value: float | None = None
    #: the salary fallback only for players priced above min salary x this
    salary_floor_mult: float = 1.15
    #: adjust for OUT teammates from edge/nba_onoff.py's with/without table
    onoff: bool = False
    extra: dict = field(default_factory=dict)


def _nba_spec() -> Spec:
    return Spec(name="nba", sport="basketball_nba", dk_sport=nba.DK_SPORT,
                classic_default=nba.CLASSIC_GAME_TYPE, league=nba_sim.NBA,
                roster=dfs_opt_nba.NBA, group_slots=theory.NBA_GROUP_SLOTS,
                profiles=NBA_PROFILES, positions=lambda s: _ordered(s, nba.positions),
                slots_for=nba.slots_for, team=nba.team, team_from_name=nba.team_from_name,
                # 13.5: NBA margin vs the closing spread (nba_sim's eff_sd prior).
                margin_sd=13.5, min_fppg=8.0, salary_value=None, onoff=True)


def _ordered(dk_position: str, fn) -> list:
    """Positions in DK's own order ('SF/PF' -> ['SF', 'PF']), so a player's
    ownership GROUP is the position DK lists first."""
    allowed = fn(dk_position)
    out = []
    for tok in (dk_position or "").replace(",", "/").split("/"):
        for p in fn(tok):
            if p in allowed and p not in out:
                out.append(p)
    return out


SPEC = _nba_spec()
SPORT = SPEC.sport


# ---------------------------------------------------------------------------
# The slate, as DraftKings describes it
# ---------------------------------------------------------------------------
def classic_groups_for(spec: Spec, groups: list[dict] | None = None) -> list[dict]:
    groups = groups if groups is not None else dfs.draft_groups(spec.dk_sport)
    ids = dfs.classic_game_types(spec.dk_sport, spec.classic_default)
    out = []
    for g in groups:
        if g.get("GameTypeId") not in ids:
            continue
        suffix = (g.get("ContestStartTimeSuffix") or "").strip()
        out.append({"gid": g.get("DraftGroupId"), "games": g.get("GameCount") or 0,
                    "label": suffix.strip("()") or "Main", "start": g.get("StartDate"),
                    "featured": g.get("DraftGroupTag") == "Featured"})
    out.sort(key=lambda r: (r["start"] or "", -r["games"]))
    return out


def default_slate(rows: list[dict]) -> int:
    """Index of the NEXT Main slate to lock -- not the biggest. DK lists
    tomorrow's Main beside tonight's (the NHL's 2026-10-07 lesson), and the
    biggest group can be a multi-day tournament. `rows` is start-sorted."""
    return next((i for i, r in enumerate(rows) if r["label"] == "Main"), 0)


def resolve_slate_for(spec: Spec, draft_group=None, groups: list[dict] | None = None):
    rows = classic_groups_for(spec, groups)
    if draft_group:
        match = next((r for r in rows if r["gid"] == int(draft_group)), None)
        return int(draft_group), (match or {"gid": int(draft_group), "label": "(explicit)",
                                            "games": 0})
    if not rows:
        return None, {"error": f"DraftKings is listing no {spec.name.upper()} Classic slates."}
    pick = rows[default_slate(rows)]
    return pick["gid"], pick


# ---------------------------------------------------------------------------
# Props
# ---------------------------------------------------------------------------
def player_props(spec: Spec, client, lookup: dfs.SalaryLookup) -> tuple[dict, dict, dict]:
    """({salary_key: {stat: [mean per book]}}, {event_id: {salary_keys}},
    {book: players priced}) for every book that posted a line."""
    out: dict = {}
    event_players: dict = {}
    books: dict = {}
    for ev in client.get_events(spec.sport):
        try:
            payload = client.get_event_odds(spec.sport, ev["id"], MARKETS, "us")
        except Exception as exc:                            # noqa: BLE001
            log.warning("%s: no props for event %s (%s)", spec.name, ev.get("id"), exc)
            continue
        for bk in payload.get("bookmakers", []):
            names = {o["description"] for m in bk.get("markets", [])
                     for o in m.get("outcomes", []) if o.get("description")}
            for name in names:
                info = lookup.get(_subject(name))
                if info is None:
                    continue
                key = norm(info["name"])
                pm = dfs.player_markets(bk, name)
                row = out.setdefault(key, {})
                hit = False
                for market, stat in {**SINGLE, **COMBO}.items():
                    mu = nba_sim.mean_from_market(stat if stat in nba_sim.STATS else "pts",
                                                  pm.get(market), spec.league)
                    if mu is not None:
                        row.setdefault(stat, []).append(mu)
                        hit = True
                if hit:
                    event_players.setdefault(ev["id"], set()).add(key)
                    books[bk.get("key")] = books.get(bk.get("key"), 0) + 1
    return out, event_players, books


def _subject(name: str) -> str:
    """DraftKings writes a college prop subject as "Cooper Flagg (DUKE)"."""
    return name.split(" (")[0].strip() if name.endswith(")") else name


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
    for stat in nba_sim.STATS:
        if stat not in m:
            m[stat] = profile[stat] * scale
            imputed.append(stat)
        m[stat] = max(float(m[stat]), 0.0)
    return {s: round(m[s], 3) for s in nba_sim.STATS}, imputed


def means_from_total(fp: float, profile: dict) -> dict:
    """A box score whose DK points (before bonuses) equal `fp`, in the shape of
    the position profile."""
    base = nba_sim.fantasy_mean(profile)
    return {s: round(profile[s] * fp / base, 3) for s in nba_sim.STATS}


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
                p_home.append(nba_sim.devig(o[home]["price"], o[away]["price"]))
    return {"total": _median(totals), "spread_home": _median(spreads),
            "p_home": _median(p_home)}


def _match_event(spec: Spec, ev: dict, games: dict, event_players: dict,
                 salaries: dict) -> tuple[str | None, bool]:
    """(DK game id, flipped) for one book event, or (None, False). `flipped`
    when the book's home team is DK's away team (college neutral sites)."""
    when = _parse_time(ev.get("commence_time"))

    def near(g, tol):
        return not (when and g["start"]) or abs(when - g["start"]) <= tol

    if spec.team_from_name is not None:
        h, a = spec.team_from_name(ev.get("home_team")), spec.team_from_name(ev.get("away_team"))
        for gid, g in games.items():
            pair = (spec.team(g["home"]), spec.team(g["away"]))
            if {h, a} == set(pair) and near(g, MATCH_TOLERANCE):
                return gid, h != pair[0]
        return None, False
    from edge import ncaab
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


def game_lines(spec: Spec, client, games: dict, event_players: dict | None = None,
               salaries: dict | None = None) -> tuple[dict, dict]:
    """({team: implied points}, {game_id: {total, spread_home, source}}) for the
    slate's games only. A game with no line is absent from both -- it is
    still simulated, at the league average, and named on the page."""
    implied: dict = {}
    lines: dict = {}
    try:
        events = client.get_featured_odds(spec.sport, ["h2h", "spreads", "totals"], "us")
    except Exception as exc:                                # noqa: BLE001
        log.warning("%s: no game lines (%s)", spec.name, exc)
        return implied, lines
    for ev in events:
        gid, flipped = _match_event(spec, ev, games, event_players or {}, salaries or {})
        if gid is None or gid in lines:
            continue
        pr = _event_prices(ev)
        spread, total, src = pr["spread_home"], pr["total"], []
        if spread is None and pr["p_home"] is not None:
            p = min(max(pr["p_home"], 0.01), 0.99)
            spread = -spec.margin_sd * NormalDist().inv_cdf(p)
            src.append("spread from moneyline")
        if total is None and spread is None:
            continue
        if total is None:
            total = 2.0 * spec.league.team_points
            src.append("no total: league average")
        spread = spread or 0.0
        if flipped:
            spread = -spread
        g = games[gid]
        ph, pa = nba_sim.implied_points(total, spread)
        implied[spec.team(g["home"])], implied[spec.team(g["away"])] = round(ph, 2), round(pa, 2)
        lines[gid] = {"total": round(total, 1), "spread_home": round(spread, 1),
                      "source": ", ".join(src) or "market"}
    return implied, lines


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------
OUT_STATUS = {"OUT", "O", "IR", "SUSP", "NA"}
DOUBT_STATUS = {"Q", "GTD", "D", "DOUBTFUL", "QUESTIONABLE"}


def build_board(spec: Spec, gid: int, meta: dict, client, n_sims: int = 2000, seed: int = 0):
    """(pool, sim, info) for one DK basketball Classic slate."""
    salaries = dfs.fetch_draftables(gid)
    info = {"draftables_source": dfs.LAST_DRAFTABLES_SOURCE,
            "fppg_id": dfs.LAST_DETECTED_FPPG_ID}
    games = slate_games(salaries)
    lookup = dfs.SalaryLookup(salaries)
    try:
        props, event_players, books = player_props(spec, client, lookup)
    except Exception as exc:                                # noqa: BLE001
        log.warning("%s: props unavailable (%s)", spec.name, exc)
        props, event_players, books = {}, {}, {}
    implied, lines = game_lines(spec, client, games, event_players, salaries)
    start = _parse_time(meta.get("start") or next(iter(games.values()), {}).get("start"))
    date = (start.astimezone(ET).date() if start else datetime.now(ET).date()).isoformat()
    teams = {spec.team(g[s]) for g in games.values() for s in ("home", "away")}
    min_salary = min((s["salary"] for s in salaries.values() if s.get("salary")), default=0)
    info.update({"games": len(games), "priced": len(salaries), "implied": implied,
                 "lines": {games[k]["matchup"]: v for k, v in lines.items()},
                 "missing_lines": sorted(g["matchup"] for k, g in games.items() if k not in lines),
                 "date": date, "books": books, "out": [], "doubtful": [],
                 "dropped_no_projection": [], "sources": {"props": 0, "fppg": 0, "salary": 0},
                 "imputed": 0, "onoff": {}})
    # Teammates ruled OUT, per team, and which teams' props are STALE: the
    # books pull an OUT player's own lines when they re-price his team, so a
    # scratched player who still has a points line means the re-post has not
    # happened yet (edge/nba_onoff.py, "what it corrects").
    out_by_team: dict = {}
    for sal in salaries.values():
        if (sal.get("dk_status") or "").upper() in OUT_STATUS and sal.get("salary"):
            out_by_team.setdefault(spec.team(sal.get("team")), []).append(sal["name"])
    stale = {t for t, names in out_by_team.items()
             if any((props.get(norm(n)) or {}).get("pts") for n in names)}
    table = nba_onoff.load() if spec.onoff and out_by_team else {}
    info["onoff_source"] = nba_onoff.LAST_SOURCE if spec.onoff and out_by_team else None
    info["stale_props_teams"] = sorted(stale)
    opp_of = {}
    for g in games.values():
        opp_of[spec.team(g["home"])] = spec.team(g["away"])
        opp_of[spec.team(g["away"])] = spec.team(g["home"])
    pool = []
    for sal in salaries.values():
        if not sal.get("salary"):
            continue
        t = spec.team(sal.get("team"))
        slots = spec.slots_for(sal.get("position"))
        pos = spec.positions(sal.get("position"))
        if not slots or not pos or t not in teams:
            continue
        status = (sal.get("dk_status") or "").upper()
        if status in OUT_STATUS:
            info["out"].append(f"{sal['name']} ({t})")
            continue
        group = pos[0]
        profile = spec.profiles[group]
        key = norm(sal["name"])
        means, imputed = means_from_props(props.get(key) or {}, profile)
        source = "props"
        if means is None:
            imputed = ["all"]
            fp = sal.get("dk_fppg")
            if fp is not None and fp >= spec.min_fppg:
                source = "fppg"
            elif spec.salary_value and sal["salary"] >= min_salary * spec.salary_floor_mult:
                fp, source = spec.salary_value * sal["salary"] / 1000.0, "salary"
            else:
                info["dropped_no_projection"].append(sal["name"])
                continue
            if t in implied:
                # DK's FPPG already carries his team's average night; scale
                # half-way toward tonight's environment (a PRIOR exponent).
                fp *= (implied[t] / spec.league.team_points) ** 0.5
            means = means_from_total(fp, profile)
        onoff = None
        if table and out_by_team.get(t) and (source != "props" or t in stale):
            r, used = nba_onoff.ratio(table, t, sal["name"], out_by_team[t])
            if used and abs(r - 1.0) > 1e-3:
                means = {k: round(v * r, 3) for k, v in means.items()}
                onoff = {"ratio": round(r, 3), "used": used}
                imputed = list(imputed) + ["onoff"]
                info["onoff"][sal["name"]] = onoff
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
                     "status": status or None, "dk_fppg": sal.get("dk_fppg"),
                     "onoff": onoff})

    sim_games = {}
    for k, g in games.items():
        h, a = spec.team(g["home"]), spec.team(g["away"])
        avg = spec.league.team_points
        sim_games[k] = {"home": h, "away": a, "pts_home": implied.get(h, avg),
                        "pts_away": implied.get(a, avg)}
    sim = nba_sim.simulate(pool, sim_games, n_sims=n_sims, seed=seed, league=spec.league)
    pool = nba_sim.summarise(sim, pool, theory.CASH_PCT, theory.GPP_PCT)
    theory.add_ownership(pool, group_slots=spec.group_slots)
    info["pool"] = len(pool)
    return pool, sim, info


# ---------------------------------------------------------------------------
# Forward-test log
# ---------------------------------------------------------------------------
PROJ_LOG_COLS = ("date", "gid", "player", "team", "opp", "dk_pos", "salary", "proj", "sd",
                 "floor", "ceil", "own", "proj_source", "imputed", "status", "source")


def log_forward_test(spec: Spec, pool, cash, gpp, gid, info, root: Path | None = None) -> dict:
    """Persist a build so a contest export has something to be joined to --
    by DRAFT GROUP (CLAUDE.md: never "the nearest slate"). A past date never
    overwrites the log, the same contract as every other sport."""
    root = root or ROOT
    date = info.get("date")
    today = datetime.now(ET).date().isoformat()
    if not date or date < today:
        return {"logged": False, "date": date}
    plog = root / "data" / f"dfs_proj_log_{spec.name}.csv"
    prior = list(csv.DictReader(plog.open())) if plog.exists() else []
    rows = [r for r in prior if not (r.get("date") == date and str(r.get("gid")) == str(gid))]
    rows += [{"date": date, "gid": gid, "player": p["name"], "team": p["team"],
              "opp": p.get("opp"), "dk_pos": p.get("dk_pos"), "salary": p["salary"],
              "proj": p["proj"], "sd": p["sd"], "floor": p["floor"], "ceil": p["ceil"],
              "own": p.get("own", ""), "proj_source": p.get("source"),
              "imputed": "|".join(p.get("imputed", [])), "status": p.get("status") or "",
              "source": "live"} for p in pool]
    plog.parent.mkdir(parents=True, exist_ok=True)
    with plog.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=PROJ_LOG_COLS)
        w.writeheader()
        w.writerows(rows)
    lpath = root / "data" / f"dfs_lineups_{spec.name}_{date}_g{gid}.csv"
    with lpath.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["mode", "slot", "player", "team", "salary", "proj", "own"])
        for mode, res in (("cash", cash), ("gpp", gpp)):
            for slot, p in (res or {}).get("slots", []):
                w.writerow([mode, slot, p["name"], p["team"], p["salary"], p["proj"], p.get("own")])
    return {"logged": True, "date": date, "n": len(pool)}


# ---------------------------------------------------------------------------
# The whole slate
# ---------------------------------------------------------------------------
def build_slate_for(spec: Spec, draft_group=None, client=None, n_sims: int = 2000,
                    iters: int = 150, seed: int = 0, own_weight: float = 0.0,
                    groups: list[dict] | None = None, persist: bool = True,
                    locked: set | None = None) -> dict:
    """Board + a CASH lineup + a GPP lineup for one DK basketball Classic slate.
    `locked` keeps named players in both lineups (late swap)."""
    if client is None:
        # The local store on the desktop, the published snapshot on the cloud.
        from edge.odds.cli import scraped_client
        client = scraped_client(spec.sport, "dfs")
    all_groups = groups if groups is not None else dfs.draft_groups(spec.dk_sport)
    gid, meta = resolve_slate_for(spec, draft_group, all_groups)
    slates = classic_groups_for(spec, all_groups)
    if gid is None:
        return {"error": meta.get("error"), "slates": slates}
    try:
        pool, sim, info = build_board(spec, gid, meta, client, n_sims=n_sims, seed=seed)
    except dfs.DraftablesUnavailable as exc:
        return {"unpriced": True, "unpriced_reason": str(exc), "gid": gid, "meta": meta,
                "slates": slates}
    info["odds_source"] = type(client).__name__
    info["odds_age_min"] = (round(client.age_seconds / 60.0)
                            if getattr(client, "age_seconds", None) is not None else None)
    if not pool:
        return {"error": f"No {spec.name.upper()} players could be projected for this slate.",
                "gid": gid, "meta": meta, "slates": slates, "stats": info}
    cash = dfs_opt_nba.optimize(pool, sim, spec.roster, mode="cash", iters=iters, seed=seed,
                                locked=locked)
    gpp = dfs_opt_nba.optimize(pool, sim, spec.roster, mode="gpp", iters=iters, seed=seed,
                               own_weight=own_weight, locked=locked)
    log_result = None
    if persist:
        try:
            log_result = log_forward_test(spec, pool, cash, gpp, gid, info)
        except Exception as exc:                            # noqa: BLE001
            log.warning("dfs_run_%s: forward-test logging failed: %s", spec.name, exc)
    return {"gid": gid, "meta": meta, "slates": slates, "pool": pool, "sim": sim,
            "stats": info, "cash": cash, "gpp": gpp, "log": log_result, "spec": spec.name}


# --- the NBA's own entry points (the shape every other sport's module has) ---
def classic_groups(groups: list[dict] | None = None) -> list[dict]:
    return classic_groups_for(SPEC, groups)


def resolve_slate(draft_group=None, groups: list[dict] | None = None):
    return resolve_slate_for(SPEC, draft_group, groups)


def build_slate(draft_group=None, client=None, **kw) -> dict:
    return build_slate_for(SPEC, draft_group, client, **kw)
