"""Build one DK NFL Classic slate end to end: pool, cash lineup, GPP lineup.

The NFL analogue of edge/dfs_run.py, and the single entry point both
scripts/dfs_lineups_nfl.py and the Streamlit page call. Before this existed the
CLI carried the pool-building logic inline, which meant the app could only get
the same lineups by copying it -- the exact shape that let the two copies of
the shared core drift the first time (ODDS_LAYER.md).

THE SLATE IS DEFINED BY DRAFTKINGS, NOT BY THE ODDS FEED
This is the correction that motivated the extraction, and it was a live bug
rather than a tidiness argument.

A scraped `dfs_nfl` scan holds every NFL event the books have posted, and by
Saturday of week N that is week N *and* week N+1 -- 27 events on 2026-09-12,
14 of them next week's. The old `game_lines()` walked all of them into a
`{team: line}` dict, so for any team playing in both weeks the later event
silently overwrote the earlier one. Measured on that scan: **21 of 28 slate
teams carried the wrong opponent and the wrong game total**. The Chargers read
as facing Las Vegas (a week-3 game) rather than Arizona; Miami read as a 12.5
underdog off a week-3 line against San Francisco.

Two things broke, both quietly:

  1. Every DST was projected from the WRONG game's total and spread, since
     project_dst's only input is the opponent's implied team total.
  2. The one hard correlation rule -- a defence never faces its own lineup --
     compares `team` against `opp_team`, so a wrong opponent DISABLES the check
     rather than tripping it. A lineup could roster a DST against the very
     stack it was built around, which is the -0.351 pair the optimizer exists
     to forbid.

The fix is to stop asking the odds feed a question it cannot answer. DK's own
draftables carry `matchup` ("ARI @ LAC"), `game` (competitionId) and `start`
for every player, and that IS the slate -- authoritative, unambiguous, and
already fetched. So:

  * opponent and game id come from DraftKings,
  * the odds feed supplies only `total` and `spread`, and a line is accepted
    only when its team PAIR matches a slate matchup *and* its kickoff is within
    MATCH_TOLERANCE of DK's own start time for that game.

Seven days separate two meetings of the same pair, so a six-hour tolerance
separates the weeks with room to spare while absorbing the minute-level
disagreement between DK's start time and a book's (17:00:00Z vs 17:01:00Z on
this slate). A second event matching the same game is reported, never silently
preferred.

A CONSEQUENCE WORTH STATING: A MISSING LINE NO LONGER DROPS A PLAYER
The old builder dropped any player whose team had no game line, because that
was where the opponent came from. It is not any more. An offensive player is
projected purely from his own props and now needs no game line at all, so a
book that has not posted a total costs exactly the DSTs in that game and
nothing else. That matters because the failure it replaces was invisible: on
the live 2026-09-06 slate an unaliased team code removed Puka Nacua, the
highest-projected receiver on the board, and a thin pool looks like a thin
slate.
"""
from __future__ import annotations

import csv
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from edge import dfs, dfs_opt_nfl, dfs_project, dfs_sport
from edge.nfl import TEAM_NAME_TO_ABBR

ROOT = Path(__file__).resolve().parents[1]

log = logging.getLogger("edge.dfs_run_nfl")

SPORT = "americanfootball_nfl"

#: DK's own game-type id for Classic salary-cap contests. Every other id on a
#: Sunday lobby is a different game entirely -- 96 is Showdown (one game,
#: captain slot), 189/51/353 are Best Ball / Tiers / Touchdowns, 145 is a
#: season-long tournament. Filtering on the id rather than on the suffix text
#: is what keeps a "(NFL Tiers)" group with 12 games out of a Classic picker.
CLASSIC_GAME_TYPE = 1

#: How far a book's kickoff may sit from DraftKings' for the same game.
#: Sized to separate consecutive weeks (7 days) with enormous margin while
#: absorbing the minute-level disagreement seen on every real slate.
MATCH_TOLERANCE = timedelta(hours=6)

#: nflverse spelling -> DraftKings spelling, where they disagree. Exactly one
#: team, and it earns the dictionary rather than a special case: edge/nfl.py's
#: TEAM_NAME_TO_ABBR must stay on nflverse's spelling because the ground-truth
#: joins key on it, and DraftKings writes the Rams LAR where nflverse writes
#: LA. Unaliased, every Rams player found no game and was dropped.
DK_ALIAS = {"LA": "LAR"}


def _parse_time(value) -> datetime | None:
    """DK's '...0000000Z' and a book's '+00:00' into one comparable instant.

    DraftKings serialises SEVEN fractional digits; fromisoformat accepts at
    most six, so the tail is trimmed rather than the whole parse abandoned. A
    naive timestamp is read as UTC, which is what both sources actually emit.
    """
    if not value:
        return None
    s = str(value).strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def abbr(name: str) -> str | None:
    """Full team name -> the code DraftKings uses on the slate."""
    if not name:
        return None
    code = TEAM_NAME_TO_ABBR.get(name)
    if code is None:
        tail = name.rsplit(" ", 1)[-1]
        for full, c in TEAM_NAME_TO_ABBR.items():
            if full.rsplit(" ", 1)[-1] == tail:
                code = c
                break
    return DK_ALIAS.get(code, code)


# ---------------------------------------------------------------------------
# The slate, as DraftKings describes it
# ---------------------------------------------------------------------------
def classic_groups(groups: list[dict] | None = None) -> list[dict]:
    """Every Classic draft group DK is currently listing, soonest first.

    Returned as display rows rather than raw DK dicts so a picker (CLI or app)
    does not have to know DK's field names. `label` is what a human recognises
    the slate by: "Main", "Sun-Mon", "Early Only".
    """
    groups = groups if groups is not None else dfs.draft_groups("NFL")
    out = []
    for g in groups:
        if g.get("GameTypeId") != CLASSIC_GAME_TYPE:
            continue
        suffix = (g.get("ContestStartTimeSuffix") or "").strip()
        out.append({
            "gid": g.get("DraftGroupId"),
            "games": g.get("GameCount") or 0,
            "label": suffix.strip("()") or "Main",
            "start": g.get("StartDate"),
            "start_est": g.get("StartDateEst"),
            "featured": g.get("DraftGroupTag") == "Featured",
        })
    out.sort(key=lambda r: (r["start"] or "", -r["games"]))
    return out


def resolve_slate(draft_group=None, groups: list[dict] | None = None):
    """(gid, meta) for the slate to build. Defaults to DK's MAIN slate.

    "Main" is a property, not a name. Among Classic groups, DK leaves the main
    slate's ContestStartTimeSuffix EMPTY and suffixes every variant --
    " (Early Only)", " (Sun-Mon)", " (Afternoon Only)". Verified on the live
    2026-09-13 lobby: of five Classic groups that Sunday, exactly one (151307,
    12 games) had no suffix, and it is the slate the large-field tournaments
    and the deepest cash games run on.

    Deliberately NOT dfs.pick_priced_group: that rule is "the biggest priced
    group", which on an NFL lobby is the 16-game season-long tournament, and
    before that the 14-game Sun-Mon slate. Biggest is the right rule for MLB
    and the wrong one here.
    """
    rows = classic_groups(groups)
    if not rows:
        return None, {"error": "DraftKings is listing no NFL Classic slates."}
    if draft_group:
        match = next((r for r in rows if r["gid"] == int(draft_group)), None)
        return int(draft_group), (match or {"gid": int(draft_group),
                                            "label": "(explicit)", "games": 0})
    main = [r for r in rows if r["label"] == "Main"]
    pick = max(main or rows, key=lambda r: (r["games"], r["featured"]))
    return pick["gid"], pick


def slate_games(salaries: dict) -> dict:
    """{game_id: {"home", "away", "start", "matchup"}} from the draftables.

    DK writes `matchup` as "AWAY @ HOME" and gives every player the
    competitionId of the game he is in, so the slate's games and each team's
    opponent are both read straight off the salaries rather than inferred.
    """
    games: dict = {}
    for info in salaries.values():
        gid, matchup = info.get("game"), info.get("matchup")
        if not gid or not matchup or "@" in str(gid):
            continue
        if gid in games:
            continue
        parts = [p.strip().upper() for p in str(matchup).split("@")]
        if len(parts) != 2:
            continue
        away, home = parts
        games[gid] = {"home": home, "away": away, "matchup": matchup,
                      "start": _parse_time(info.get("start"))}
    return games


def team_opponents(games: dict) -> dict:
    """{team: (opponent, game_id)} for every team on the slate."""
    out = {}
    for gid, g in games.items():
        out[g["home"]] = (g["away"], gid)
        out[g["away"]] = (g["home"], gid)
    return out


def game_lines(client, games: dict) -> tuple[dict, dict]:
    """{game_id: {"total", "spread_home"}} for the slate's games only.

    An event is admitted only when its team pair IS one of the slate's games
    and its kickoff is within MATCH_TOLERANCE of DK's start time for that
    game. Everything the books have posted for other weeks is discarded here
    rather than allowed to overwrite -- see this module's docstring for the
    21-of-28 measurement that made this necessary.

    Returns (lines, report). `report` carries `unmatched_events`,
    `conflicts` (a second event matching a game already filled) and
    `missing_games` (slate games no book priced), because each of those is a
    different problem and a bare count cannot tell them apart.
    """
    want = {frozenset((g["home"], g["away"])): gid for gid, g in games.items()}
    lines: dict = {}
    report = {"unmatched_events": 0, "conflicts": [], "missing_games": []}

    for ev in client.get_featured_odds(SPORT, ["totals", "spreads"], "us"):
        home, away = abbr(ev.get("home_team")), abbr(ev.get("away_team"))
        gid = want.get(frozenset((home, away))) if home and away else None
        if gid is None:
            report["unmatched_events"] += 1
            continue
        kickoff = _parse_time(ev.get("commence_time"))
        scheduled = games[gid]["start"]
        if kickoff and scheduled and abs(kickoff - scheduled) > MATCH_TOLERANCE:
            # The same pair, a different week. This is the case that used to
            # win by being iterated last.
            report["unmatched_events"] += 1
            continue

        total = spread_home = None
        for bk in ev.get("bookmakers", []):
            for m in bk.get("markets", []):
                for o in m.get("outcomes", []):
                    if m["key"] == "totals" and total is None and o.get("point") is not None:
                        total = float(o["point"])
                    if (m["key"] == "spreads" and spread_home is None
                            and o.get("point") is not None
                            and abbr(o.get("name")) == home):
                        spread_home = float(o["point"])
        if total is None or spread_home is None:
            continue
        if gid in lines:
            report["conflicts"].append(games[gid]["matchup"])
            continue
        lines[gid] = {"total": total, "spread_home": spread_home, "home": home}

    report["missing_games"] = sorted(g["matchup"] for gid, g in games.items()
                                     if gid not in lines)
    return lines, report


# ---------------------------------------------------------------------------
# The pool
# ---------------------------------------------------------------------------
def build_pool(client, salaries: dict, book: str = "draftkings") -> tuple[list, dict]:
    """The projected player pool for one slate.

    Offensive players come from props; defences have no prop market at all and
    come from the game line (dfs_project.project_dst). A player carries his
    opponent because the optimizer's DST rule is only enforceable if he does.
    """
    from scripts.dfs_board import collect_player_markets

    sport = dfs_sport.get(SPORT)
    games = slate_games(salaries)
    opponents = team_opponents(games)
    lines, line_report = game_lines(client, games)

    props = collect_player_markets(client, SPORT, sport.market_keys()).get(book, {})

    pool: list[dict] = []
    stats = {"offense": 0, "dst": 0, "no_proj": 0, "no_salary": 0,
             "not_on_slate": 0, **line_report}

    for name, markets in props.items():
        info = salaries.get(dfs.norm(name))
        if not info:
            stats["not_on_slate"] += 1
            continue
        if not info.get("salary"):
            stats["no_salary"] += 1
            continue
        team = info.get("team")
        opp, gid = opponents.get(team, (None, info.get("game")))
        res = dfs_project.project(markets, sport, position=info.get("position"))
        if res["proj"] is None:
            stats["no_proj"] += 1
            continue
        pool.append({
            "name": info["name"], "pos": dfs_opt_nfl.eligible_slots(info.get("position")),
            "salary": info["salary"], "proj": res["proj"], "team": team,
            "opp_team": opp, "game": gid, "dk_pos": info.get("position") or "",
            "components": res.get("components", {}), "means": res.get("means", {}),
            "dk_fppg": info.get("dk_fppg"),
        })
        stats["offense"] += 1

    for info in salaries.values():
        if "DST" not in (info.get("position") or "").upper():
            continue
        team = info.get("team")
        opp, gid = opponents.get(team, (None, info.get("game")))
        line = lines.get(gid)
        if not line or not info.get("salary"):
            continue
        # The team's OWN spread, from the home side's number.
        spread = line["spread_home"] if line["home"] == team else -line["spread_home"]
        opp_pts = dfs_project.implied_team_points(line["total"], -spread)
        res = dfs_project.project_dst(opp_pts)
        pool.append({
            "name": info["name"], "pos": {"DST"}, "salary": info["salary"],
            "proj": res["proj"], "team": team, "opp_team": opp, "game": gid,
            "dk_pos": "DST", "components": res.get("components", {}), "means": {},
            "dk_fppg": info.get("dk_fppg"), "implied_opp_points": res["implied_opp_points"],
        })
        stats["dst"] += 1

    return pool, stats


# ---------------------------------------------------------------------------
# The whole slate, in one call
# ---------------------------------------------------------------------------
#: Proj-log columns. Mirrors edge/dfs_run.py::log_forward_test's MLB shape
#: closely enough that an NFL scripts/dfs_calibration_nfl.py can be written by
#: adapting that script rather than starting over -- same join, same idea,
#: different sport's columns (dk_pos, opp_team, leverage in place of MLB's
#: pitcher-specific outs_mean/k_mean).
PROJ_LOG_COLS = ("date", "gid", "player", "team", "opp_team", "dk_pos", "salary",
                 "proj", "sd", "own", "leverage", "game", "games")


def _slate_date(meta: dict) -> str | None:
    """The slate's own calendar date in ET, for keying the forward-test log.

    NFL's cadence is weekly rather than MLB's near-daily, so unlike
    dfs_calibration.py's ground-truth date-inference machinery (built because
    MLB genuinely has multiple candidate dates a contest file could be from),
    a single ISO date is enough here: there is at most one Sunday main slate a
    week, and the date goes straight into the log so a later contest-standings
    export can be matched by eye rather than inferred.
    """
    dt = _parse_time(meta.get("start"))
    if dt is None:
        return None
    try:
        return dt.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:                                       # noqa: BLE001
        return dt.date().isoformat()


def log_forward_test(pool: list, cash: dict | None, gpp: dict | None,
                     gid, meta: dict, root: Path | None = None) -> dict:
    """Persist a build to disk, so a real contest result has something to be
    JOINED against later -- the exact gap that made an NFL ownership fit
    impossible before this existed. See edge/dfs_run.py::log_forward_test,
    which this mirrors: MLB's OWNERSHIP_GAMMA was tuned against real DK
    contest exports (data/contest-standings-*.csv) joined against a logged
    history of that day's predicted pool (data/dfs_proj_log.csv). NFL's
    edge/dfs_nfl_theory.py ownership model has never had that -- it is a
    prior with a plausible shape and zero validation, stated as such in its
    own docstring -- because nothing was ever logged for it to be checked
    against. This starts that clock; it does nothing for a week already gone.

    data/dfs_proj_log_nfl.csv: every player in the pool, one row per player,
    for the slate's date. Re-running for the same date overwrites that date's
    rows in place (the freshest pre-lock build is what a real decision was
    actually made from), other dates untouched -- same rule as MLB.

    data/dfs_lineups_nfl_<date>.csv: the built CASH/GPP lineups, if any --
    separate from the pool log because it answers a different question (what
    did the app actually recommend) from the one the pool log answers (what
    was every player's predicted own/proj, for joining against a contest's
    real ownership board regardless of who was rostered).

    A build for a PAST date never overwrites the log -- the identical failure
    mode MLB's own version guards against (a --date review rebuild clobbering
    real forward-test data, restored from git once already). Returns
    {"logged": bool, "n": int, "date": str|None}.
    """
    root = root or ROOT
    result = {"logged": False, "n": 0, "date": None}
    date = _slate_date(meta)
    if date is None:
        return result
    result["date"] = date

    try:
        today_et = datetime.now(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:                                       # noqa: BLE001
        today_et = datetime.now().date().isoformat()
    if date < today_et:
        result["skipped_past_date"] = True
        return result

    (root / "data").mkdir(parents=True, exist_ok=True)
    games = meta.get("games")

    def _rows():
        for p in pool:
            yield {"date": date, "gid": gid, "player": p["name"], "team": p.get("team", ""),
                  "opp_team": p.get("opp_team", ""), "dk_pos": p.get("dk_pos", ""),
                  "salary": p.get("salary", ""), "proj": p.get("proj", ""),
                  "sd": p.get("_sd", ""), "own": p.get("own", ""),
                  "leverage": p.get("leverage", ""), "game": p.get("game", ""),
                  "games": games if games is not None else ""}

    plog = root / "data/dfs_proj_log_nfl.csv"
    prior = list(csv.DictReader(open(plog))) if plog.exists() else []
    with plog.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(list(PROJ_LOG_COLS))
        for r in [r for r in prior if r.get("date") != date] + list(_rows()):
            w.writerow([r.get(c, "") for c in PROJ_LOG_COLS])
    result["logged"] = True
    result["n"] = len(pool)

    if cash or gpp:
        lpath = root / f"data/dfs_lineups_nfl_{date}.csv"
        with lpath.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["mode", "slot", "player", "team", "opp_team", "salary",
                       "proj", "own"])
            for mode, r in (("cash", cash), ("gpp", gpp)):
                for row in (lineup_rows(r) if r else []):
                    w.writerow([mode, row["slot"], row["player"], row["team"],
                               row["opp"], row["salary"], row["proj"], row["own"]])
        result["lineup_file"] = str(lpath.relative_to(root))
    return result


def build_slate(client, draft_group=None, iters: int = 700, book: str = "draftkings",
                stack_n: int = 2, bring_back: int = 1, own_gamma: float | None = None,
                own_weight: float = 0.0, groups: list[dict] | None = None,
                persist: bool = True) -> dict:
    """Pool + a CASH lineup + a GPP lineup for one DK NFL Classic slate.

    The single entry point for both the CLI and the Streamlit page, so the two
    cannot produce different lineups from the same slate.

    `persist=True` (the default) writes the pool and lineups to disk via
    log_forward_test -- see that function for why. Every caller gets this
    for free; a caller that wants a dry run (rebuilding a past slate for
    review, say) passes persist=False rather than relying on the forward-
    test log's own past-date guard to protect itself silently.

    Returns {gid, meta, slates, pool, stats, cash, gpp, teams, games} or, when
    the slate is not priced yet, {unpriced: True, ...}. A `cash` or `gpp` of
    None means no legal lineup exists under the cap -- a real answer on a thin
    board, and deliberately distinguishable from a bad one.
    """
    from edge import dfs_nfl_theory as theory

    all_groups = groups if groups is not None else dfs.draft_groups("NFL")
    gid, meta = resolve_slate(draft_group, all_groups)
    slates = classic_groups(all_groups)
    if gid is None:
        return {"error": meta.get("error"), "slates": slates}

    salaries = dfs.fetch_draftables(gid)
    priced = {k: v for k, v in salaries.items() if v.get("salary")}
    if not priced:
        # DK lists a slate before it prices it; that is normal a few days out
        # and is not the same thing as an empty board.
        return {"unpriced": True, "gid": gid, "meta": meta, "slates": slates}

    pool, stats = build_pool(client, salaries, book=book)
    games = slate_games(salaries)
    theory.add_ownership(pool, **({"gamma": own_gamma} if own_gamma else {}))

    cash = dfs_opt_nfl.optimize(pool, mode="cash", iters=iters, seed=0)
    gpp = dfs_opt_nfl.optimize(pool, mode="gpp", iters=iters, seed=0,
                               stack_n=stack_n, bring_back=bring_back,
                               own_weight=own_weight)
    log_result = None
    if persist:
        try:
            log_result = log_forward_test(pool, cash, gpp, gid, meta)
        except Exception as exc:                            # noqa: BLE001
            # A logging failure must never take the lineups down with it --
            # the whole point is a record for LATER, not a dependency of NOW.
            log.warning("dfs_run_nfl: forward-test logging failed: %s", exc)
    return {
        "gid": gid, "meta": meta, "slates": slates, "pool": pool,
        "stats": stats, "cash": cash, "gpp": gpp, "games": games,
        "teams": sorted({p["team"] for p in pool if p.get("team")}),
        "log": log_result,
    }


def lineup_rows(result: dict) -> list[dict]:
    """One lineup as flat display rows, in DK roster order."""
    if not result:
        return []
    order = {s: i for i, s in enumerate(dfs_opt_nfl.SLOTS)}
    rows = []
    for p, slot in sorted(result["lineup"], key=lambda t: order[t[1]]):
        rows.append({"slot": slot, "player": p["name"], "team": p.get("team", ""),
                     "opp": p.get("opp_team", ""), "salary": p.get("salary", 0),
                     "proj": round(float(p.get("proj") or 0), 1),
                     "own": p.get("own", 0.0), "leverage": p.get("leverage", 0.0)})
    return rows
