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

11 F / 7 D LINEUPS
A team that dresses seven defencemen splits the blue line's minutes seven
ways: each of the six regulars loses 1.5-1.9 minutes. The books' shots and
points lines do not absorb it (our prop-based D missed by -0.88 pts on 10
logged nights), so every defenceman on such a team has his means scaled by
SEVEN_D_SCALE (NHL_STATUS.md 6h). Forwards gain ~1 minute but no measurable
DK points, so they are left alone.
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
#: Per-event multipliers for a defenceman whose team dresses 7 D. Fitted
#: 2026-10-19 on 2025-26 (707 regular-D games in 111 seven-D team-games,
#: scripts/nhl_context_features.py): actual over leave-one-out baseline in
#: 7-D games, divided by the same ratio for the same depth slot in 6-D games.
#: blocks 0.855 [0.83, 0.89], shots 0.933 [0.90, 0.96], goals 0.92 [0.81,
#: 1.03], assists 0.97 [0.91, 1.04]; DK points 0.902 [0.88, 0.93]. Applied to
#: the 7th D too, whose own drop is larger (-3.3 min) but fitted on 110 games.
SEVEN_D_SCALE = {"blocks": 0.855, "shots": 0.933, "goals": 0.92, "assists": 0.97}
#: The DK points line is too flat: actual points rise ~1.3x as fast as the
#: market's points mean (all four lab split/state cells), and $7.5k+ skaters ran
#: 1.3-1.5 pts under projection. A prop skater's points mean m becomes
#: POINTS_REF * (m / POINTS_REF) ** POINTS_STRETCH, goals and assists keeping the
#: market's split. POINTS_REF = geometric mean of the prop points means on the
#: lab's TRAIN nights at lock (0.556). POINTS_STRETCH FITTED 2026-10-22 on TRAIN
#: (11 nights, 2,192 skater-games, 4 seeds) together with the exact assist draw
#: (edge/nhl_sim._assist_matrix): grid 1.0-1.3, RMSE 6.0816 -> 6.0803. Alone, the
#: stretch fit 1.3; with the draw fixed (which also feeds stars) it is 1.1.
#: NHL_CRITIQUE_LOG.md, runner-ups.
POINTS_REF = 0.556
POINTS_STRETCH = 1.1
CURRENT_SEASON = "20262027"
#: THIS SEASON'S PRODUCTION (NHL_CRITIQUE_LOG.md cycle 4). Beyond the ice-time
#: scaling, a mean no market sets is blended with the skater's own per-game rate
#: this season: m = w * m + (1 - w) * rate (x scale for goals, assists, shots;
#: blocks unscaled), w = K / (K + games this season). A rookie with no last
#: season otherwise ran on flat defaults all year, and last season's blocks ran
#: high (2025-26: 0.853 projected vs 0.765 blocked in H2). FITTED 2026-10-22 on
#: scripts/nhl_season_lab.py H1 (2025-26 to 12/31, 81 dates, 22,716 skater-games,
#: 1 seed), grid K points {10..80} x shots {3..40} x blocks {10..40}: H1 RMSE
#: 6.0991 -> 6.0317, flat near the argmin (6.032-6.035). Checked on H2 (86 dates,
#: 24,515): RMSE 6.2622 -> 6.1190, squared error -1.77 [-2.01, -1.53] per skater-
#: game, better on 78/86 dates; on the 2026-27 lab's TEST not worse in either state.
SEASON_K = {"points": 30.0, "shots": 7.0, "blocks": 20.0}
#: THIS SEASON'S ROLE (NHL_CRITIQUE_LOG.md cycle 1). Every mean no market sets
#: -- goals, assists and shots of a skater without props, blocks of every
#: skater -- is last season's rate, so a skater whose ice time has moved keeps
#: last year's output. Each is scaled by r ** ROLE_POWER, where r is his ice
#: time this season (mean of the last ROLE_LAST games, shrunk toward last
#: season with ROLE_K games of prior weight) over last season's. Prop means are
#: left alone: scaling them did not help held out (any power 0-1.5 within
#: +/-0.003 RMSE). FITTED 2026-10-22 on the lab's TRAIN nights (9/29-10/12,
#: 11 nights, 2,187 skater-games at lock, scripts/nhl_skater_lab.py), grid on
#: RMSE over K {3,5,10,20} x power {0.5..2.5} x blocks power {0.5..4} x last
#: {5,10}: train RMSE 6.107 -> 6.093. The surface is flat (most cells
#: 6.093-6.100), so the powers are loosely determined; > 1 says ice time moves
#: with role quality (PP / PK time), not just minutes. TEST (10/13-10/21, 8
#: nights): RMSE 5.991 -> 5.96, both states -- NHL_CRITIQUE_LOG.md cycle 1.
ROLE_K = 5.0
ROLE_LAST = 5
ROLE_POWER = {"goals": 2.0, "assists": 2.0, "shots": 2.0, "blocks": 2.5}


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
    # keyed by salaries key: build_board reads props[k], so a book spelling
    # ("Sebastian Aho (CAR)") lands on the right one of two Ahos (critique cycle 5)
    return dfs_slate.own_game_markets(by_event, kickoffs, lookup.get,
                                      games if games is not None else slate_games(salaries),
                                      key_of=lookup.key_of)


def _season_rates() -> dict:
    """{norm(name): per-game rates} from last season's NHL reports."""
    try:
        stats = nhl.season_skater_stats(LAST_SEASON)
    except Exception as exc:                                # noqa: BLE001
        log.warning("nhl: season stats unavailable (%s)", exc)
        return {}
    out = {}
    for r in stats.values():
        row = rate_row(r)
        if row is not None:
            out[norm(r["skaterFullName"])] = row
    return out


def rate_row(r: dict) -> dict | None:
    """One skater's per-game rates from a season_skater_stats row; None under 5
    games. A field one report lacks stays MISSING (the board's default + `imputed`
    flag), never 0.0: a missing realtime row once zeroed 27 skaters' blocks."""
    gp = r.get("gamesPlayed") or 0
    if gp < 5 or not r.get("skaterFullName"):
        return None
    row = {k: (r.get(src) or 0) / gp for k, src in
           (("goals", "goals"), ("assists", "assists"), ("shots", "shots"),
            ("blocks", "blockedShots")) if src in r}
    row.update({"toi": (r.get("timeOnIcePerGame") or 0) / 60.0 or None, "gp": gp})
    return row


def group_toi(rows: list[dict]) -> dict:
    """{(norm(name), 'D' | 'F'): [minutes, oldest game first]} from
    nhl.skater_game_toi rows. Keyed by name, not team, so a traded skater keeps
    his games; the D/F split keeps VAN's two Elias Petterssons apart."""
    out: dict = {}
    for r in sorted(rows, key=lambda r: r["date"]):
        out.setdefault((norm(r["name"]), "D" if r.get("pos") == "D" else "F"), []).append(r["toi"])
    return out


def current_toi(date: str) -> list | None:
    """This season's skater-games before `date` (nhl.skater_game_toi rows), or
    None when the NHL stats API cannot be reached -- the board then runs on last
    season alone and says so (info["role_source"])."""
    try:
        return nhl.skater_game_toi(CURRENT_SEASON, date)
    except Exception as exc:                                # noqa: BLE001
        log.warning("nhl: this season's ice time unavailable (%s)", exc)
        return None


def current_production(date: str) -> dict | None:
    """{(norm(name), 'D' | 'F'): (games, goals, assists, shots, blocks)} this
    season before `date`, or None when the NHL stats API cannot be reached (the
    board then runs without it and says so, info["season_source"])."""
    try:
        rows = nhl.skater_season_to_date(CURRENT_SEASON, date)
    except Exception as exc:                                # noqa: BLE001
        log.warning("nhl: this season's production unavailable (%s)", exc)
        return None
    return {(norm(r["name"]), "D" if r.get("pos") == "D" else "F"):
            (r["gp"], r["goals"], r["assists"], r["shots"], r["blocks"]) for r in rows if r["gp"]}


def blend_season(means: dict, season: tuple | None, scale: float, imputed,
                 k: dict | None = None) -> dict:
    """`means` blended with this season's per-game production (SEASON_K):
    season = (games, goals, assists, shots, blocks). Only what last season set
    ('points' -> goals and assists, 'shots'); blocks always."""
    if not season or season[0] <= 0:
        return means
    k = SEASON_K if k is None else k
    n = season[0]
    g, a, sh, b = (x / n for x in season[1:])
    out = dict(means)
    if "points" in imputed:
        w = k["points"] / (k["points"] + n)
        out["goals"] = w * out["goals"] + (1 - w) * g * scale
        out["assists"] = w * out["assists"] + (1 - w) * a * scale
    if "shots" in imputed:
        w = k["shots"] / (k["shots"] + n)
        out["shots"] = w * out["shots"] + (1 - w) * sh * scale
    w = k["blocks"] / (k["blocks"] + n)
    out["blocks"] = w * out["blocks"] + (1 - w) * b
    out["shots"] = max(out["shots"], out["goals"] + 0.05)
    return out


def _initial_last(team: str, name: str) -> tuple:
    first, _, last = name.partition(" ")
    return (team, norm(first)[:1], norm(last or first))


def name_aliases(rows: list[dict], rates: dict) -> dict:
    """{(team, first initial, norm(last)): the NHL's norm(full name)} from this
    season's games, for a DK spelling the NHL writes differently -- DK's
    "Nicholas Robertson", "Zack Bolduc", "Will Borgen", "Joe Veleno", "Jeffrey
    Viel" are the NHL's Nick / Zachary / William / Joseph / Jeff and had no
    last-season rates (2026-10-22). Only when exactly one player on the team has
    that initial and last name, and he has last-season rates: a debuting rookie
    who shares a teammate's surname (the two Protas) is never matched."""
    ids: dict = {}
    for r in rows:
        ids.setdefault(_initial_last(r["team"], r["name"]), {})[r["id"]] = norm(r["name"])
    return {k: next(iter(v.values())) for k, v in ids.items()
            if len(v) == 1 and next(iter(v.values())) in rates}


def role_ratio(toi_last: float | None, recent: list | None, k: float = ROLE_K,
               last: int = ROLE_LAST) -> float:
    """This season's ice time over last season's, shrunk toward 1 (ROLE_K).
    1.0 without both: a rookie, or a skater yet to play this season."""
    if not toi_last or not recent:
        return 1.0
    tail = recent[-last:]
    cur = sum(tail) / len(tail)
    n = len(recent)
    return (k * toi_last + n * cur) / (k + n) / toi_last


def scale_role(means: dict, r: float, imputed, power: dict | None = None) -> dict:
    """Scale the means last season set (`imputed`: 'points' -> goals and
    assists, 'shots' -> shots; blocks always) by r ** power."""
    power = ROLE_POWER if power is None else power
    if r == 1.0:
        return means
    out = dict(means)
    if "points" in imputed:
        out["goals"] *= r ** power["goals"]
        out["assists"] *= r ** power["assists"]
    if "shots" in imputed:
        out["shots"] *= r ** power["shots"]
    out["blocks"] *= r ** power["blocks"]
    out["shots"] = max(out["shots"], out["goals"] + 0.05)
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


def seven_d_teams(lines: dict) -> set[str]:
    """Teams DailyFaceoff lists in an 11 F / 7 D lineup, from {(team, norm
    name): line row}. A d4 defenceman says so directly; a lines snapshot
    written before D4 was read (2026-10-19) still shows it as exactly 11
    forwards in F1-F4 -- 16 of 19 such teams dressed 7 D in the box score
    (9/30-10/19)."""
    forwards: dict = {}
    out = set()
    for (team, _), row in lines.items():
        ln = row.get("line") or ""
        if ln == "D4":
            out.add(team)
        elif ln in ("F1", "F2", "F3", "F4"):
            forwards[team] = forwards.get(team, 0) + 1
    return out | {t for t, n in forwards.items() if n == 11}


def scale_seven_d(means: dict) -> dict:
    """A defenceman's means on a 7-D team (SEVEN_D_SCALE)."""
    return {k: v * SEVEN_D_SCALE.get(k, 1.0) for k, v in means.items()}


def skater_means(base: dict, pos: str, scale: float, shots: float | None = None,
                 points: float | None = None, assists: float | None = None,
                 toi_recent: list | None = None, seven_d: bool = False,
                 season: tuple | None = None) -> tuple[dict, list, float]:
    """(means, imputed, role ratio) for one skater: the DK market where it posts a
    line (shots / points + assists, Poisson means), last season's per-game rates
    (`base`, x `scale` = tonight's implied goals over the league's) where it does
    not, blocks always from last season; then this season's ice time (role_ratio
    on `toi_recent`), this season's production (blend_season on `season`) and an
    11 F / 7 D lineup (SEVEN_D_SCALE). The one recipe
    build_board and scripts/nhl_season_lab.py (2025-26 replay) both run."""
    imputed = []
    if shots is None:
        shots = base.get("shots", 1.0 if pos != "D" else 1.2) * scale
        imputed.append("shots")
    if points is None or assists is None:
        g_ = base.get("goals", 0.08 if pos != "D" else 0.04) * scale
        a_ = base.get("assists", 0.12 if pos != "D" else 0.12) * scale
        imputed.append("points")
    else:
        a_ = assists
        g_ = max(points - assists, 0.02)
        k = POINTS_REF * (points / POINTS_REF) ** POINTS_STRETCH / (g_ + a_)
        g_, a_ = g_ * k, a_ * k
    means = {"shots": max(shots, g_ + 0.05), "goals": g_, "assists": a_,
             "blocks": base.get("blocks", DEFAULT_BLOCKS[pos])}
    if "blocks" not in base:
        imputed.append("blocks")
    r = role_ratio(base.get("toi"), toi_recent)
    if r != 1.0:
        means = scale_role(means, r, imputed)
    means = blend_season(means, season, scale, imputed)
    if seven_d:
        means = scale_seven_d(means)
    return means, imputed, r


def build_board(gid: int, meta: dict, client, n_sims: int = 2000, seed: int = 0):
    """(pool, sim, info) for one DK NHL Classic slate."""
    salaries = dfs.fetch_draftables(gid)
    games = slate_games(salaries)
    teams = {nhl.team(g[s]) for g in games.values() for s in ("home", "away")}
    implied = game_lines(client, games)
    props, wrong_game = player_props(client, ["player_shots_on_goal", "player_points",
                                              "player_assists", "player_total_saves"],
                                     salaries=salaries, games=games)
    props = {k if k in salaries else norm(k): v for k, v in props.items()}
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
    toi_rows = current_toi(date)
    toi_now = group_toi(toi_rows) if toi_rows is not None else None
    aliases = name_aliases(toi_rows or [], rates)
    # An alias may only serve a DK name whose (team, initial, last) is unique on
    # DK's board too: NBA 2026-10-26 lost Jaylin Williams to his teammate Jalen's
    # Out flag through the same kind of key; here a debuting DK skater would
    # inherit his teammate's rates.
    dk_keys: dict = {}
    for v in salaries.values():
        if v.get("salary"):
            k = _initial_last(nhl.team(v.get("team")), v["name"])
            dk_keys[k] = dk_keys.get(k, 0) + 1
    aliases = {k: v for k, v in aliases.items() if dk_keys.get(k, 0) <= 1}
    produced = current_production(date)
    seven_d = seven_d_teams(lines)

    info = {"games": len(games), "priced": len(salaries), "implied": implied,
            "seven_d": sorted(seven_d & teams),
            "missing_lines": sorted(teams - set(implied)), "date": date,
            "starters": starters, "no_lineup": [], "imputed": 0,
            "wrong_game": wrong_game,
            "role_source": "this season's ice time" if toi_now is not None else "unavailable",
            "season_source": "this season's production" if produced is not None else "unavailable"}
    pool = []
    for skey, sal in salaries.items():
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
            saves = nhl_sim.market_mean((props.get(skey) or {}).get("player_total_saves"))
            row["means"] = {"saves": saves} if saves else {}
            pool.append(row)
            continue
        pm = props.get(skey) or {}
        if not ln.get("line") and not pm:
            info["no_lineup"].append(sal["name"])
            continue
        if (ln.get("injury") or "").lower() in ("ir", "out", "ltir"):
            continue
        scale = implied[t] / LEAGUE_TEAM_GOALS
        rkey = key if key in rates else aliases.get(_initial_last(t, sal["name"]), key)
        base = rates.get(rkey) or {}
        means, imputed, r = skater_means(
            base, pos, scale, nhl_sim.market_mean(pm.get("player_shots_on_goal")),
            nhl_sim.market_mean(pm.get("player_points")), nhl_sim.market_mean(pm.get("player_assists")),
            (toi_now or {}).get((rkey, "D" if pos == "D" else "F")), pos == "D" and t in seven_d,
            (produced or {}).get((rkey, "D" if pos == "D" else "F")))
        row["imputed"] += imputed
        if imputed:
            info["imputed"] += 1
        if r != 1.0:
            row["role"] = round(r, 3)
        if pos == "D" and t in seven_d:
            row["seven_d"] = True
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
