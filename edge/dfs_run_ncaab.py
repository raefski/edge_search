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
    1. PROPS. DraftKings posts college props as one-sided milestone ladders, so a
       ladder is inverted by edge/dfs_ladder.mean_gapped at a fitted overround; a
       two-sided line from another book devigs itself. A stat with no line is derived
       from a combo (Pts+Reb minus Pts, ...), then from his own shape last season, then
       the position profile, scaled by his points. `imputed` names each one.
    2. BLEND: half last season's production carried to this team (CARRYOVER), half the
       salary line (SALARY_LINE). Neither DK number is usable alone on opening night.
       DK's FPPG is LAST season's wherever he played it (Dennis Parker Jr., 31.5 from
       Radford, $3,500 at Kansas); DK's salary misprices transfers the other way.
    3. SALARY: no verifiable history and no prop (a freshman), at a price or a starting
       job that says he plays, on a line from results (SALARY_TIER_LINE). Tyran Stokes
       $10,300, Caleb Holt $10,200.
    4. BENCH: cheap, not a listed starter. Last season's own output, or BENCH_POINTS.
A team's bench is then scaled to what its implied points leave (TEAM_DK_PER_POINT).
Every row's `source` says which.

TEAM NAMES DO NOT MATCH. "DUKE" against "Duke Blue Devils": a book event is
matched by which slate players its props resolve to, then by name similarity
inside one tip-off window (edge/ncaab.match_team). A game no line matches is
simulated at the league average and named on the page; nothing is dropped.

PLAYER NAMES DO NOT MATCH EITHER, see edge/ncaab_names.py: every outside name is
resolved inside a team or game, by rule, and what a source could not resolve is listed
in stats["name_audit"] (shown on the page and by the CLI).

THE FIRST LIVE SLATE IS 2026-11-02 (draft group 155565). Carryover is fitted on real
seasons and held out; the salary line, blend and bench rule are one night against the
prop market, not against outcomes. Fit them on graded slates, then the simulator's
constants and the ownership weights (NBA_STATUS.md, college section).
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
from edge import dfs, dfs_ladder, dfs_ncaab_theory as theory, dfs_opt_ncaab, ncaab, ncaab_sim
from edge import ncaab_rotowire, ncaab_torvik
from edge.ncaab_names import Audit, Rosters
from edge.dfs_slate import parse_time as _parse_time, slate_games

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
#: Fewest milestones that make a ladder: edge/dfs_ladder.mean_gapped needs a slope.
MIN_RUNGS = 3
#: DK points a player earns per point his team scores. MEASURED, Torvik 2025-26, all
#: rows summed per team (365 teams with 25+ games): mean 2.040, sd 0.063, 5th-95th
#: 1.94-2.14. The whole team's output, so what props do not claim is left for the rest.
TEAM_DK_PER_POINT = 2.04
#: The smallest a team's bench may be scaled to fit that budget. PRIOR, 2026-11-02.
BENCH_SCALE_FLOOR = 0.35
#: Last season's points move with tonight's implied team points to this power. Same hold-
#: out: power 0 MAE 2.85 bias -0.65, power .5 2.87 / -0.04, power 1 3.12 / +0.63.
ENV_POWER = 0.5
#: DK points from salary in $K, read off the prop market: least squares on the 33 slate
#: players with DK ladders on 2026-11-02 (4 games), r=0.73. The flat slope is the
#: finding: $3,000 = 15.6 and $10,000 = 30.5, where the earlier prior (4.6 per $1K, a
#: pro pricing rule) said 13.8 and 46. PRIOR until graded: one night, players a book
#: chose to price, against the market rather than results. Refit: scripts/ncaab_prior_fit.py.
SALARY_LINE = (9.21, 2.13)
#: DK points from salary for a player with NO history and NO prop at lock. FITTED on results
#: (box scores, scripts/ncaab_box_grade.py --fit): 41 such players, 3 slates, 17 games,
#: 2026-11-02..04 (gids 155565, 156039, 156123). Held out by slate: MAE 8.56, bias +0.70; as
#: logged 10.67 / +2.77. First fit (11/2 only, 1.86 + 1.45) held out on 11/3 and 11/4: bias
#: -4.61 and -3.82. SALARY_LINE was read off players a book chose to price, and the book not
#: pricing one is news: on 11/2 the 4 no-history players WITH props averaged 33.8, these 11.2.
#: Not enough data below ~5 slates; refit each night.
SALARY_TIER_LINE = (2.06, 1.81)
#: A blend projection is mapped to a + b x itself. FITTED on results, 269 blend rows, 3 slates
#: (same gids, box scores including the ones who sat): blend ran high every night, +2.50,
#: +5.00, +3.34, all of it under $7,000 (actual/proj .72-.80; above $7,000, 1.00). Each slate
#: held out, fitted on the other two: MAE 9.29 -> 8.52, 9.39 -> 8.12, 8.53 -> 7.75; a flat
#: scale (.81) only 8.99 / 8.32 / 7.96. The steep slope is the finding: the salary line's
#: intercept carries cheap rotation players too far. Not enough data below ~5 slates.
BLEND_RESULT = (-11.33, 1.407)
#: Last season's DK points carried to this one, by what changed: (intercept, slope).
#: FITTED on Torvik players with 8+ games in both 2024-25 and 2025-26, joined by Torvik's
#: player id, up/down by conference tier. HELD OUT on 2023-24 -> 2024-25, MAE vs carrying
#: raw FPPG: same school 4.60 vs 4.84 (n=1,554), moved up 6.00 vs 8.89 (315), same tier
#: 5.67 vs 6.41 (511), moved down 6.12 vs 9.81 (221). Bias there +0.2 to +1.2 (the +1.2
#: on same school: both seasons required, so the seasons a player lost his job are gone).
CARRYOVER = {"same": (5.75, 0.884), "up": (7.30, 0.406), "side": (10.40, 0.469),
             "down": (14.80, 0.340)}
#: Blend of carryover and salary line, each game's props hidden and predicted against the
#: 33 players the books priced on 2026-11-02 (MAE, scripts/ncaab_prior_fit.py): 4.6 x salary
#: 8.23; raw FPPG 4.18; history alone 3.71; salary line alone 3.34; w=.3 2.88; w=.5 2.87,
#: bias -0.04; w=.7 3.09. Not enough data to say more (one night, 4 games).
BLEND_WEIGHT = 0.5
#: Under this price and not a listed starter, no credit for salary: the salary line was
#: read off players a book chose to price, and a $3,000 walk-on is not one of them.
BENCH_SALARY = 4000
#: DK points for a cheap non-starter with no history. PRIOR, 2026-11-02; most of the
#: ~120 no-history players on the first board are walk-ons and redshirts.
BENCH_POINTS = 3.0
#: Fewer games than this last season is not a season.
MIN_PRIOR_GAMES = 3
#: A no-prop questionable player's chance of playing (a props price already carries it).
#: PRIOR, 2026-11-02: an opening-night tag, nothing to fit it on yet.
Q_PLAY = 0.7

#: Odds market -> simulator stat (singles) and combo keys.
SINGLE = {"player_points": "pts", "player_rebounds": "reb", "player_assists": "ast",
          "player_threes": "fg3", "player_steals": "stl", "player_blocks": "blk",
          "player_turnovers": "tov"}
COMBO = {"player_points_rebounds_assists": "pra", "player_points_rebounds": "pr",
         "player_points_assists": "pa", "player_rebounds_assists": "ra",
         "player_blocks_steals": "sb"}
MARKETS = list(SINGLE) + list(COMBO)

#: A typical box score per position group, used to split a projection into stats when
#: a player has no season of his own. Only the SHAPE matters -- each is scaled to the
#: player's own total. MEASURED, Torvik 2025-26, 15+ mpg and 15+ games: G = Pure PG /
#: Scoring PG / Combo G / Wing G (n=1,618), F = Wing F / Stretch 4 / PF/C / C (n=1,028).
PROFILES = {
    "G": {"pts": 10.18, "reb": 3.13, "ast": 2.22, "fg3": 1.28, "stl": 0.96, "blk": 0.19, "tov": 1.72},
    "F": {"pts": 9.06, "reb": 5.17, "ast": 1.18, "fg3": 0.61, "stl": 0.66, "blk": 0.70, "tov": 1.46},
}
#: Conference tiers for CARRYOVER's up/down.
POWER_CONF = {"ACC", "B10", "B12", "SEC", "BE"}
MID_CONF = {"A10", "MWC", "WCC", "Amer", "MVC"}
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


def _two_sided(outcomes: list, name: str) -> dict | None:
    """The {Over, Under, point} closest to even for one subject, or None if the book
    posted no point with both sides (DK's college props have none, 2026-11-02)."""
    by_point: dict = {}
    for o in outcomes:
        if o.get("description") == name and o.get("point") is not None:
            by_point.setdefault(float(o["point"]), {})[o.get("name")] = o.get("price")
    pairs = [(pt, v) for pt, v in by_point.items() if v.get("Over") and v.get("Under")]
    if not pairs:
        return None
    pt, v = min(pairs, key=lambda kv: abs(1.0 / kv[1]["Over"] - 1.0 / kv[1]["Under"]))
    return {"Over": v["Over"], "Under": v["Under"], "point": pt}


def market_mean(market_key: str, stat: str, outcomes: list, name: str) -> float | None:
    """One subject's one market -> its mean. A two-sided pair devigs itself; a one-sided
    milestone ladder (DK) goes through edge/dfs_ladder with the fitted hoops overround."""
    pair = _two_sided(outcomes, name)
    if pair:
        return ncaab_sim.mean_from_market(stat if stat in ncaab_sim.STATS else "pts", pair, LEAGUE)
    rungs = dfs_ladder.rungs_from_outcomes(outcomes, name)
    if len(rungs) < MIN_RUNGS:
        return None
    return dfs_ladder.mean_gapped(rungs, dfs_ladder.HOOPS_OVERROUND.get(
        market_key, dfs_ladder.DEFAULT_OVERROUND))


def _event_subjects(payload: dict) -> set:
    return {o["description"] for bk in payload.get("bookmakers", [])
            for m in bk.get("markets", []) for o in m.get("outcomes", []) if o.get("description")}


def player_props(client, rosters: Rosters, games: dict, salaries: dict,
                 audit: Audit | None = None) -> tuple[dict, dict, dict]:
    """({salary key: {stat: [mean per book]}}, {event id: {salary keys}}, {book: players
    priced}) for every book that posted a line.

    Keyed by the salary-list key, not the name: board 155565 has two Trent Perrys. An
    event is first assigned to ONE slate game (by its team names and tip, and by whose
    players its subjects resolve to, edge/ncaab.match_team / _match_event); each subject
    is then resolved only among that game's two teams. College basketball has hundreds
    of off-slate games a night, and a name-only join averaged any namesake's line into
    a slate player's mean -- the NCAAF build lost a cash game to exactly that, 2026-10-16.
    """
    out: dict = {}
    event_players: dict = {}
    books: dict = {}
    for ev in client.get_events(SPORT):
        try:
            payload = client.get_event_odds(SPORT, ev["id"], MARKETS, "us")
        except Exception as exc:                            # noqa: BLE001
            log.warning("ncaab: no props for event %s (%s)", ev.get("id"), exc)
            continue
        names = _event_subjects(payload)
        if not names:
            continue
        # Who could this event be? Each slate game votes with the subjects it can resolve.
        votes: dict = {}
        for gid, g in games.items():
            keys = set()
            for name in names:
                info, _ = rosters.resolve(name, (team(g["home"]), team(g["away"])))
                if info is not None:
                    keys.add(rosters.key(info))
            # Half the subjects, as dfs_slate.event_games: one namesake is not a game.
            if keys and 2 * len(keys) >= len(names):
                votes[gid] = keys
        ev_votes = {ev["id"]: set().union(*votes.values())} if votes else {}
        gid, _ = _match_event(ev, games, ev_votes, salaries)
        if gid is None:
            continue
        pair = (team(games[gid]["home"]), team(games[gid]["away"]))
        for bk in payload.get("bookmakers", []):
            markets = {m["key"]: m.get("outcomes", []) for m in bk.get("markets", [])}
            seen = {o["description"] for ocs in markets.values() for o in ocs if o.get("description")}
            for name in seen:
                info, how = rosters.resolve(name, pair)
                if audit is not None:
                    audit.add(name, info, how, "/".join(pair))
                if info is None:
                    continue
                key = rosters.key(info)
                row = out.setdefault(key, {})
                hit = False
                for market, stat in {**SINGLE, **COMBO}.items():
                    mu = market_mean(market, stat, markets.get(market) or [], name)
                    if mu is not None:
                        row.setdefault(stat, []).append(mu)
                        hit = True
                if hit:
                    event_players.setdefault(ev["id"], set()).add(key)
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


def _main_line(outcomes: list, side_a: str, side_b: str) -> float | None:
    """The point closest to even money, read as side_a's. A full-ladder payload carries
    every alternate total and spread as outcomes on one market, so "the" line has to be
    chosen: DK posted 2,302 alternates per market on the 2026-11-02 slate."""
    pairs: dict = {}
    for x in outcomes:
        if x.get("point") is None or not x.get("price"):
            continue
        point = abs(float(x["point"])) if side_a in ("Over", "Under") else float(x["point"])
        if x.get("name") == side_a:
            pairs.setdefault(point, {})["a"] = x["price"]
        elif x.get("name") == side_b:
            # a spread's other side is written at the opposite sign
            other = point if side_a in ("Over", "Under") else -point
            pairs.setdefault(other, {})["b"] = x["price"]
    both = [(pt, v) for pt, v in pairs.items() if "a" in v and "b" in v]
    if both:
        return min(both, key=lambda kv: abs(1.0 / kv[1]["a"] - 1.0 / kv[1]["b"]))[0]
    return next(iter(pairs)) if pairs else None


def _event_prices(ev: dict) -> dict:
    """total / home spread / P(home wins) across every book on one event."""
    totals, spreads, p_home = [], [], []
    home, away = ev.get("home_team"), ev.get("away_team")
    for bk in ev.get("bookmakers", []):
        for m in bk.get("markets", []):
            outs = m.get("outcomes", [])
            if m["key"] == "totals":
                pt = _main_line(outs, "Over", "Under")
                if pt is not None:
                    totals.append(pt)
            elif m["key"] == "spreads":
                pt = _main_line(outs, home, away)
                if pt is not None:
                    spreads.append(pt)
            elif m["key"] == "h2h":
                o = {x.get("name"): x for x in outs}
                if home in o and away in o:
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
# Who starts, who is hurt, what they did last year
# ---------------------------------------------------------------------------
def lineup_news(salaries: dict, rosters: Rosters, teams: set, html: str | None = None):
    """({key: starter row}, {key: tag}, audit, source) from RotoWire, every name resolved
    inside its own team. A failed fetch is an empty result, visibly: `source` says so."""
    audit = Audit("rotowire")
    starters: dict = {}
    tags: dict = {}
    try:
        rows = ncaab_rotowire.parse(html if html is not None else ncaab_rotowire.fetch())
    except Exception as exc:                                # noqa: BLE001
        log.warning("ncaab: RotoWire unavailable (%s)", exc)
        return starters, tags, audit, f"unavailable ({type(exc).__name__})"
    for row in rows:
        code = [t for t in teams if ncaab.match_team(t, row["team"]) >= 0.8]
        if len(code) != 1:
            continue
        for bucket, is_starter in ((row["starters"], True), (row["injuries"], False)):
            for r in bucket:
                info, how = rosters.resolve(r["name"], code)
                audit.add(r["name"], info, how, code[0])
                if info is None:
                    continue
                key = rosters.key(info)
                if is_starter:
                    starters[key] = r
                if r.get("tag"):
                    tags[key] = r["tag"]
    return starters, tags, audit, "rotowire"


def team_tiers(teams: set, rows: list) -> dict:
    """{DK team: conference tier}: 2 power, 1 multi-bid, 0 the rest, by last season's
    conference (a school that has since moved is a season behind)."""
    votes: dict = {}
    for r in rows:
        for t in teams:
            if ncaab.match_team(t, r["team"]) >= 0.8:
                votes.setdefault(t, {}).setdefault(r["conf"], 0)
                votes[t][r["conf"]] += 1
    return {t: tier(max(v, key=v.get)) for t, v in votes.items()}


def tier(conf: str) -> int:
    return 2 if conf in POWER_CONF else 1 if conf in MID_CONF else 0


def carried(tv: dict | None, fppg: float | None, team_tier: int | None) -> float | None:
    """Last season's production, carried across whatever changed; None without one."""
    if tv and tv["gp"] >= MIN_PRIOR_GAMES:
        fp = ncaab_torvik.dk_points(tv)
        if tv.get("same_school"):
            kind = "same"
        else:
            was, now = tier(tv["conf"]), team_tier if team_tier is not None else 0
            kind = "up" if now > was else "down" if now < was else "side"
    elif fppg:
        # DK has a number, Torvik's row did not verify (2 of 308 on the first board):
        # no school to judge a move by, and treated as a sideways move.
        fp, kind = fppg, "side"
    else:
        return None
    a, b = CARRYOVER[kind]
    return a + b * fp


def no_prop_points(salary: int, tv: dict | None, fppg: float | None, starter: bool,
                   team_tier: int | None, implied: float | None = None,
                   env_power: float | None = None, weight: float | None = None,
                   team_listed: bool = True) -> tuple[float, str]:
    """(DK points before the team budget, source) for a player with no props. Last
    season's output is a season's average game, so it is moved to tonight's by how many
    points his team is implied to score; the salary line was read off tonight's market
    and already knows. `team_listed`: RotoWire named starters for his team. On a team
    it did not (Memphis, 2026-11-02, whose $3,500-3,700 players price at 20+), "not a
    listed starter" says nothing, so a cheap player with a real history is a player."""
    env = ENV_POWER if env_power is None else env_power
    w = BLEND_WEIGHT if weight is None else weight
    factor = ((implied or LEAGUE.team_points) / LEAGUE.team_points) ** env
    line = SALARY_LINE[0] + SALARY_LINE[1] * salary / 1000.0
    prior = carried(tv, fppg, team_tier)
    prior = prior * factor if prior is not None else None
    cheap = salary < BENCH_SALARY and not starter
    if cheap and (team_listed or prior is None or (tv and tv.get("same_school")
                                                   and ncaab_torvik.dk_points(tv) < 10)):
        # His own numbers if he has any. Below 10 DK points a same-school bump to the
        # carryover's intercept (a bigger role) is what never came for a walk-on.
        if tv and tv.get("same_school") and ncaab_torvik.dk_points(tv) < 10:
            return max(ncaab_torvik.dk_points(tv), 1.0), "bench"
        if prior is not None:
            return w * prior + (1 - w) * BENCH_POINTS, "bench"
        return BENCH_POINTS, "bench"
    if prior is None:
        return SALARY_TIER_LINE[0] + SALARY_TIER_LINE[1] * salary / 1000.0, "salary"
    return max(BLEND_RESULT[0] + BLEND_RESULT[1] * (w * prior + (1 - w) * line), 1.0), "blend"


def shape_of(tv: dict | None, profile: dict) -> dict:
    """His own box score as a split when he has one worth reading."""
    if tv and tv["pts"] >= 3 and tv["gp"] >= MIN_PRIOR_GAMES:
        return {s: tv[s] for s in ncaab_sim.STATS}
    return profile


def apply_team_budget(rows: list, implied: dict, floor: float | None = None) -> dict:
    """Fit each team's BENCH rows (in place) to what its implied points leave after the
    rotation, and return {team: bench scale} for the page.

    ONLY THE BENCH MOVES. In the market hold-out (scripts/ncaab_prior_fit.py, 33 priced
    players, 2026-11-02) leaving rotation players alone scored MAE 2.87, bias about zero;
    scaling them to the budget proportionally 4.92, bias -4.5, and cutting them second,
    after the bench, 3.37. The books' top six or seven use nearly the whole team.
    Raising them to fill a shortfall put Davidson's Scovens (21.8 FPPG last year) at 31:
    a shortfall means someone on the roster we cannot see, not that a known player does
    more, so it is left unfilled. A bench is never scaled to zero: most of them will
    log minutes in a 40-point game, and a zero would hide that from the page.
    """
    low = BENCH_SCALE_FLOOR if floor is None else floor
    scales: dict = {}
    by_team: dict = {}
    for r in rows:
        by_team.setdefault(r["team"], []).append(r)
    for t, group in by_team.items():
        bench = [r for r in group if r["source"] == "bench"]
        bench_fp = sum(r["fp"] for r in bench)
        if bench_fp <= 0:
            continue
        budget = TEAM_DK_PER_POINT * implied.get(t, LEAGUE.team_points)
        room = budget - sum(r["fp"] for r in group if r["source"] != "bench")
        scale = min(max(room / bench_fp, low), 1.0)
        for r in bench:
            r["fp"] *= scale
        scales[t] = round(scale, 2)
    return scales


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------
def build_board(gid: int, meta: dict, client, n_sims: int = 2000, seed: int = 0):
    """(pool, sim, info) for one DK college basketball Classic slate."""
    salaries = dfs.fetch_draftables(gid)
    info = {"draftables_source": dfs.LAST_DRAFTABLES_SOURCE,
            "fppg_id": dfs.LAST_DETECTED_FPPG_ID}
    games = slate_games(salaries)
    rosters = Rosters(salaries)
    prop_audit = Audit("props")
    try:
        props, event_players, books = player_props(client, rosters, games, salaries, prop_audit)
    except Exception as exc:                                # noqa: BLE001
        log.warning("ncaab: props unavailable (%s)", exc)
        props, event_players, books = {}, {}, {}
    implied, lines = game_lines(client, games, event_players, salaries)
    start = _parse_time(meta.get("start") or next(iter(games.values()), {}).get("start"))
    date = (start.astimezone(ET).date() if start else datetime.now(ET).date()).isoformat()
    teams = {team(g[s]) for g in games.values() for s in ("home", "away")}
    starters, tags, news_audit, news_source = lineup_news(salaries, rosters, teams)
    torvik_rows = ncaab_torvik.load()
    history, torvik_audit = ncaab_torvik.join(salaries, torvik_rows)
    tiers = team_tiers(teams, torvik_rows)
    listed_teams = {team(salaries[k].get("team")) for k in starters}
    info.update({"games": len(games), "priced": len(salaries), "implied": implied,
                 "lines": {games[k]["matchup"]: v for k, v in lines.items()},
                 "missing_lines": sorted(g["matchup"] for k, g in games.items() if k not in lines),
                 "date": date, "books": books, "out": [], "doubtful": [],
                 "sources": {"props": 0, "blend": 0, "salary": 0, "bench": 0},
                 "imputed": 0, "starters_source": news_source, "starters": len(starters),
                 "history_rows": len(torvik_rows),
                 "name_audit": [prop_audit.row(), news_audit.row(),
                                {"source": "torvik", "joined": len(history), "loose": [],
                                 "refused": [f"{n}: stats do not match DK's" for n in
                                             torvik_audit["refused_mismatch"]]}]})
    opp_of = {}
    for g in games.values():
        opp_of[team(g["home"])] = team(g["away"])
        opp_of[team(g["away"])] = team(g["home"])
    pool = []
    for sal_key, sal in salaries.items():
        if not sal.get("salary"):
            continue
        t = team(sal.get("team"))
        slots = ncaab.slots_for(sal.get("position"))
        pos = positions(sal.get("position"))
        if not slots or not pos or t not in teams:
            continue
        status = (sal.get("dk_status") or "").upper()
        if status == "NONE":
            status = ""
        tag = tags.get(sal_key)
        if status in OUT_STATUS or tag in ncaab_rotowire.OUT_TAGS:
            info["out"].append(f"{sal['name']} ({t})")
            continue
        group = pos[0]
        tv = history.get(sal_key)
        shape = shape_of(tv, PROFILES[group])
        means, imputed = means_from_props(props.get(sal_key) or {}, shape)
        source = "props"
        fp = None
        if means is None:
            imputed = ["all"]
            fp, source = no_prop_points(sal["salary"], tv, sal.get("dk_fppg"), sal_key in starters,
                                        tiers.get(t), implied.get(t),
                                        team_listed=t in listed_teams)
            if status in DOUBT_STATUS or tag:
                fp *= Q_PLAY
        else:
            fp = ncaab_sim.fantasy_mean(means)
        info["sources"][source] += 1
        if imputed:
            info["imputed"] += 1
        if status in DOUBT_STATUS or tag:
            info["doubtful"].append(f"{sal['name']} ({t}, {status or tag})")
        pool.append({"name": sal["name"], "team": t, "opp": opp_of.get(t),
                     "game": sal.get("game"), "matchup": sal.get("matchup"),
                     "start": sal.get("start"), "salary": sal["salary"],
                     "dk_pos": sal.get("position"), "group": group, "slots": slots,
                     "means": means, "imputed": imputed, "source": source, "fp": fp, "shape": shape,
                     "status": status or tag or None, "dk_fppg": sal.get("dk_fppg"),
                     "starter": sal_key in starters,
                     "last_team": tv["team"] if tv else None,
                     "last_mpg": tv["mpg"] if tv else None})
    info["team_scale"] = apply_team_budget(pool, implied)
    for p in pool:
        if p["means"] is None:
            p["means"] = means_from_total(p["fp"], p["shape"])
        del p["shape"]

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
                 "floor", "ceil", "own", "proj_source", "imputed", "status", "starter",
                 "last_team", "last_mpg", "dk_fppg", "source")


def log_forward_test(pool, cash, gpp, gid, info, root: Path | None = None) -> dict:
    """Persist a build so a contest export has something to be joined to --
    edge/dfs_forward_log.py's rules (past dates never, this draft group only)."""
    date = info.get("date")
    rows = [{"date": date, "gid": gid, "player": p["name"], "team": p["team"],
             "opp": p.get("opp"), "dk_pos": p.get("dk_pos"), "salary": p["salary"],
             "proj": p["proj"], "sd": p["sd"], "floor": p["floor"], "ceil": p["ceil"],
             "own": p.get("own", ""), "proj_source": p.get("source"),
             "imputed": "|".join(p.get("imputed", [])), "status": p.get("status") or "",
             "starter": int(bool(p.get("starter"))), "last_team": p.get("last_team") or "",
             "last_mpg": p.get("last_mpg") if p.get("last_mpg") is not None else "",
             "dk_fppg": p.get("dk_fppg") if p.get("dk_fppg") is not None else "",
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
