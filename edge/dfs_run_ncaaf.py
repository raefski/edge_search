"""Build one DK College Football Classic slate end to end: pool, cash, GPP.

The NCAAF analogue of edge/dfs_run_nfl.py, and the single entry point both
scripts/dfs_lineups_ncaaf.py and the Streamlit page call, so a lineup on the
phone and a lineup on the desktop are the same lineup.

THE SLATE IS DEFINED BY DRAFTKINGS, NOT BY THE ODDS FEED
Same rule and the same reason as the NFL builder: DK's own draftables carry
`matchup`, `competitionId` and `start` for every player, and that IS the slate.
A scraped scan holds every college event the books have posted -- 70 of them on
2026-09-24 against a 12-game main slate -- and inferring the slate from that is
how the NFL builder once gave 21 of 28 teams the wrong opponent.

WHAT IS MUCH EASIER HERE THAN IN THE NFL, AND WHY
That NFL bug was expensive because a wrong opponent broke every DST projection
(its only input is the opponent's implied team total) AND silently disabled the
one hard correlation rule, which compares `team` against `opp_team`. College DK
has no DST slot at all. So:

  * no game line is needed to build a pool -- every player is projected purely
    from his own ladders;
  * a book that has not posted a total costs NOTHING rather than costing that
    game's defences;
  * the opponent is used only for the GPP bring-back, where being wrong makes
    a lineup slightly worse rather than illegal.

Game totals and spreads are still read, for display and for the board's game
environment column, and they are allowed to be missing.

THE PRICE PATH IS THE PART THAT IS DIFFERENT, AND IT IS NOT OPTIONAL
DraftKings posts college player props ONLY as one-sided milestone ladders.
`dfs_project.project` cannot read them -- it needs an Over/Under pair, finds
none, and returns proj=None for every player on the board. So this module
reads the FULL ladder (a client built with main_line_only=False) and runs it
through edge/dfs_ladder.py instead.

Reading this profile with main_line_only left ON is the failure mode to watch
for: it is not an error, it returns one rung per player, and one rung projects
a nonsense mean. `build_pool` counts `one_rung` players separately for exactly
that reason -- a slate where that number is large is a misconfigured client,
not a thin board.
"""
from __future__ import annotations

import csv
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from edge import dfs, dfs_ladder, dfs_opt_ncaaf, dfs_project, dfs_sport, ncaaf

ROOT = Path(__file__).resolve().parents[1]

log = logging.getLogger("edge.dfs_run_ncaaf")

SPORT = "americanfootball_ncaaf"
DK_SPORT = ncaaf.DK_SPORT                 # "CFB", NOT "NCAAF"
CLASSIC_GAME_TYPE = ncaaf.CLASSIC_GAME_TYPE

#: How far a book's kickoff may sit from DraftKings' for the same game.
MATCH_TOLERANCE = timedelta(hours=6)

#: Markets whose ladders are read for a projection. `player_pass_attempts` is
#: here although it scores nothing: college has no interception market at any
#: book, and edge/dfs_sport._ncaaf_impute estimates interceptions off attempts
#: (1 per 48.5) in preference to off yards (1 per 358), because an interception
#: is a per-throw risk.
LADDER_MARKETS = ("player_pass_yds", "player_pass_tds", "player_pass_attempts",
                  "player_rush_yds", "player_reception_yds", "player_receptions")

#: DraftKings writes a prop subject as "Jeremiah Smith (OSU)" and a draftable
#: as "Jeremiah Smith". Both are DraftKings, so the join is exact once the
#: team suffix is off -- no fuzzy matching, unlike the NFL's team-name aliasing.
_TEAM_SUFFIX = re.compile(r"\s*\([^)]*\)\s*$")


def subject_key(subject: str) -> str:
    return ncaaf.norm(_TEAM_SUFFIX.sub("", subject or ""))


def _parse_time(value) -> datetime | None:
    """DK's '...0000000Z' and a book's '+00:00' into one comparable instant."""
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


# ---------------------------------------------------------------------------
# The slate, as DraftKings describes it
# ---------------------------------------------------------------------------
def classic_groups(groups: list[dict] | None = None) -> list[dict]:
    """Every CFB Classic draft group DK is listing, soonest first.

    Filtered on GameTypeId rather than on the suffix text, the same way the NFL
    builder does it: a college lobby also carries Showdown (95, one game, a
    captain slot) and a Snake draft with no salary cap at all (377), and both
    would otherwise reach a Classic picker.
    """
    groups = groups if groups is not None else dfs.draft_groups(DK_SPORT)
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

    "Main" is a property, not a name: among Classic groups DraftKings leaves
    the main slate's ContestStartTimeSuffix empty and suffixes every variant.
    Verified on the live 2026-09-24 lobby, where the 12-game Saturday group
    153831 had no suffix and every other Classic group did.
    """
    rows = classic_groups(groups)
    if not rows:
        return None, {"error": "DraftKings is listing no CFB Classic slates."}
    if draft_group:
        match = next((r for r in rows if r["gid"] == int(draft_group)), None)
        return int(draft_group), (match or {"gid": int(draft_group),
                                            "label": "(explicit)", "games": 0})
    main = [r for r in rows if r["label"] == "Main"]
    pick = max(main or rows, key=lambda r: (r["games"], r["featured"]))
    return pick["gid"], pick


def slate_games(salaries: dict) -> dict:
    """{game_id: {home, away, start, matchup}} from the draftables."""
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


# ---------------------------------------------------------------------------
# The ladders
# ---------------------------------------------------------------------------
def collect_ladders(client, markets=LADDER_MARKETS,
                    book: str = "draftkings") -> tuple[dict, dict]:
    """({player_key: {market: rungs}}, {event_id: {team abbreviations}}).

    ONE call per event through the client's own interface, which is what keeps
    this substitutable between the live store and the committed snapshot. The
    client must have been built with main_line_only=False -- see the module
    docstring on why that is not a detail.

    The second return value is how an odds event is matched to a slate game,
    and both obvious approaches are worse than this one.

    Matching on team NAMES means reconciling "Ohio State Buckeyes" (what
    DraftKings' sportsbook calls the team) with "OSU" (what DraftKings' DFS
    lobby calls it) across 130+ FBS programmes, by hand, forever.

    Matching on the team ABBREVIATION in the prop subject -- "Jeremiah Smith
    (OSU)" -- looks like it should be exact, since both strings come from
    DraftKings. It is not: DraftKings' sportsbook and its DFS lobby use
    DIFFERENT abbreviations for the same programme. Measured on the
    2026-09-24 board, six of the twelve main-slate games failed on exactly
    this -- ILL/IL, COLO/COL, LOU/UL, OKLA/OU, FLA/UF, VAN/VAND -- and the
    other six matched, which is the worst possible outcome because it looks
    like it mostly works.

    So the match is on the PLAYERS. Every prop subject resolves to a
    draftable, every draftable carries its own competitionId, and a college
    player appears in exactly one game. No mapping table, nothing to maintain
    as conferences realign, and it cannot half-work.
    """
    out: dict = {}
    teams: dict = {}
    for ev in client.get_events(SPORT):
        payload = client.get_event_odds(SPORT, ev["id"], list(markets), "us")
        seen: set = set()
        for bk in payload.get("bookmakers", []):
            if bk.get("key") != book:
                continue
            for m in bk.get("markets", []):
                key = m.get("key")
                if key not in markets:
                    continue
                by_player: dict = {}
                for o in m.get("outcomes", []):
                    subject = o.get("description")
                    if not subject:
                        continue
                    by_player.setdefault(subject, []).append(o)
                for subject, outcomes in by_player.items():
                    seen.add(subject_key(subject))
                    rungs = dfs_ladder.rungs_from_outcomes(outcomes, subject)
                    if rungs:
                        out.setdefault(subject_key(subject), {})[key] = rungs
        if seen:
            teams[ev["id"]] = seen
    return out, teams


def project_player(ladders: dict, sport, position: str | None = None) -> dict:
    """One player's ladders -> a DK projection, through the shared assembler.

    Two halves, and the split is the point:

      * edge/dfs_ladder.py turns each ladder into a MEAN, which is the college
        replacement for devigging an Over/Under pair;
      * edge/dfs_project.points_from_means turns means into POINTS, and it is
        the identical function MLB and the NFL go through -- same imputation
        contract, same rounding, same component breakdown.

    That is exactly the separation points_from_means was extracted for, and it
    means the only variable between this sport and the others is where the
    means came from.

    THE BONUSES ARE READ OFF THE MARKET RATHER THAN OFF A NORMAL, which is the
    one place this path is strictly better than the NFL's. DraftKings usually
    prices the bonus threshold outright -- a rushing ladder spans 25+ to 195+
    and so contains "100+" -- and a devigged price for the exact event beats a
    fitted normal's right tail, which edge/dfs_sport.py measured
    under-predicting P(100+) by 1.3-1.7 percentage points.
    """
    means: dict = {}
    detail: dict = {}
    for market, rungs in ladders.items():
        res = dfs_ladder.project_market(rungs, market)
        if res.get("mean") is None:
            continue
        means[market] = res["mean"]
        detail[market] = {"rungs": res["rungs"], "band": round(res.get("band", 0.0), 2),
                          "priced": round(res.get("priced", 0.0), 2)}

    if not any(m in means for m in sport.required):
        return {"proj": None}

    bonus_probs = {}
    for b in sport.bonuses:
        rungs = ladders.get(b.market)
        if not rungs:
            continue
        p = dfs_ladder.exceed(rungs, b.threshold,
                              dfs_ladder.LADDER_OVERROUND.get(
                                  b.market, dfs_ladder.DEFAULT_OVERROUND))
        if p is not None:
            bonus_probs[b.market] = p

    out = dfs_project.points_from_means(means, {}, sport, position,
                                        bonus_probs=bonus_probs)
    out["ladders"] = detail
    out["priced_bonuses"] = sorted(bonus_probs)
    # How much of this projection rests on the unpriced head of a ladder,
    # in DK points. Carried onto the player so the board can show it and the
    # optimiser could one day discount it.
    out["band_points"] = round(sum(
        detail[m]["band"] * abs(s.points)
        for m in detail for s in sport.stats if s.market == m), 2)
    return out


# ---------------------------------------------------------------------------
# The pool
# ---------------------------------------------------------------------------
def game_lines(client, games: dict, event_players: dict,
               salaries: dict) -> tuple[dict, dict]:
    """{game_id: {total, spread_home, home}} for the slate's games only.

    DISPLAY ONLY. Nothing in a college projection depends on this, which is why
    a miss is reported and then ignored rather than dropping players -- the
    opposite of the NFL builder, where a missing line costs that game's
    defences because project_dst has no other input.

    An event is matched to a slate game by which slate PLAYERS its own prop
    subjects resolve to (see collect_ladders), and then only if its kickoff is
    within MATCH_TOLERANCE of DraftKings' start time for that game. Both tests
    matter: a scan holds several weeks of events at once, and players alone
    would let a later meeting of the same teams overwrite this one -- the exact
    failure edge/dfs_run_nfl.py documents, where 21 of 28 teams ended up with
    the wrong opponent and the wrong total.
    """
    lines: dict = {}
    report = {"unmatched_events": 0, "missing_games": []}
    try:
        events = client.get_featured_odds(SPORT, ["totals", "spreads"], "us")
    except Exception as exc:                                # noqa: BLE001
        log.warning("dfs_run_ncaaf: no game lines: %s", exc)
        report["missing_games"] = sorted(g["matchup"] for g in games.values())
        return lines, report

    for ev in events:
        # The game most of this event's players are on the slate in. A vote
        # rather than a lookup because a prop subject can fail to resolve --
        # a walk-on with a ladder and no salary, say -- and one stray name
        # must not decide the match.
        votes: dict = {}
        for key in event_players.get(ev.get("id"), ()):
            info = salaries.get(key)
            if info and info.get("game"):
                votes[info["game"]] = votes.get(info["game"], 0) + 1
        gid = max(votes, key=votes.get) if votes else None
        if gid is None or gid not in games:
            report["unmatched_events"] += 1
            continue
        kickoff = _parse_time(ev.get("commence_time"))
        scheduled = games[gid]["start"]
        if kickoff and scheduled and abs(kickoff - scheduled) > MATCH_TOLERANCE:
            report["unmatched_events"] += 1
            continue
        home = games[gid]["home"]
        total = spread_home = None
        for bk in ev.get("bookmakers", []):
            for m in bk.get("markets", []):
                for o in m.get("outcomes", []):
                    if m["key"] == "totals" and total is None and o.get("point") is not None:
                        total = float(o["point"])
        if total is not None and gid not in lines:
            lines[gid] = {"total": total, "spread_home": spread_home, "home": home}
    report["missing_games"] = sorted(g["matchup"] for gid, g in games.items()
                                     if gid not in lines)
    return lines, report


def build_pool(client, salaries: dict, book: str = "draftkings") -> tuple[list, dict]:
    """The projected player pool for one slate."""
    sport = dfs_sport.get(SPORT)
    games = slate_games(salaries)
    opponents = team_opponents(games)
    ladders, event_players = collect_ladders(client, book=book)

    pool: list[dict] = []
    stats = {"projected": 0, "no_proj": 0, "no_salary": 0, "not_on_slate": 0,
             "one_rung": 0, "priced_players": len(ladders),
             "slate_players": len(salaries)}

    for key, by_market in ladders.items():
        info = salaries.get(key)
        if not info:
            stats["not_on_slate"] += 1
            continue
        if not info.get("salary"):
            stats["no_salary"] += 1
            continue
        if max((len(r) for r in by_market.values()), default=0) <= 1:
            # One rung per market is the signature of a client built with
            # main_line_only left ON. Counted, not silently projected.
            stats["one_rung"] += 1
            continue
        res = project_player(by_market, sport, position=info.get("position"))
        if res.get("proj") is None:
            stats["no_proj"] += 1
            continue
        team = info.get("team")
        opp, gid = opponents.get(team, (None, info.get("game")))
        pool.append({
            "name": info["name"],
            "pos": dfs_opt_ncaaf.eligible_slots(info.get("position")),
            "salary": info["salary"], "proj": res["proj"], "team": team,
            "opp_team": opp, "game": gid, "dk_pos": info.get("position") or "",
            "components": res.get("components", {}), "means": res.get("means", {}),
            "imputed": res.get("imputed", []), "ladders": res.get("ladders", {}),
            "band_points": res.get("band_points", 0.0),
            "dk_fppg": info.get("dk_fppg"),
        })
        stats["projected"] += 1

    lines, line_report = game_lines(client, games, event_players, salaries)
    for p in pool:
        line = lines.get(p.get("game"))
        if line:
            p["total"] = line["total"]
    stats.update(line_report)
    return pool, stats


# ---------------------------------------------------------------------------
# The whole slate, in one call
# ---------------------------------------------------------------------------
PROJ_LOG_COLS = ("date", "gid", "player", "team", "opp_team", "dk_pos", "salary",
                 "proj", "sd", "own", "leverage", "band_points", "game", "games")


def _slate_date(meta: dict) -> str | None:
    dt = _parse_time(meta.get("start"))
    if dt is None:
        return None
    try:
        return dt.astimezone(ZoneInfo("America/New_York")).date().isoformat()
    except Exception:                                       # noqa: BLE001
        return dt.date().isoformat()


def log_forward_test(pool: list, cash: dict | None, gpp: dict | None,
                     gid, meta: dict, root: Path | None = None) -> dict:
    """Persist a build so a real contest result has something to be joined to.

    This is the clock that has to start before any of the unvalidated constants
    in this build can be validated -- the ownership prior in
    edge/dfs_ncaaf_theory.py, the ladder overrounds in edge/dfs_ladder.py, and
    the spreads in SD_FIT that were regressed against a season mean because no
    projection log existed. It does nothing for a week already gone, which is
    exactly why it ships with the first build rather than after the first
    disappointment.

    A build for a PAST date never overwrites the log -- the same guard MLB's
    and the NFL's versions carry, after a --date review rebuild clobbered real
    forward-test data once already.
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
            yield {"date": date, "gid": gid, "player": p["name"],
                   "team": p.get("team", ""), "opp_team": p.get("opp_team", ""),
                   "dk_pos": p.get("dk_pos", ""), "salary": p.get("salary", ""),
                   "proj": p.get("proj", ""), "sd": p.get("_sd", ""),
                   "own": p.get("own", ""), "leverage": p.get("leverage", ""),
                   "band_points": p.get("band_points", ""),
                   "game": p.get("game", ""),
                   "games": games if games is not None else ""}

    plog = root / "data/dfs_proj_log_ncaaf.csv"
    prior = list(csv.DictReader(open(plog))) if plog.exists() else []
    with plog.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(list(PROJ_LOG_COLS))
        for r in [r for r in prior if r.get("date") != date] + list(_rows()):
            w.writerow([r.get(c, "") for c in PROJ_LOG_COLS])
    result["logged"] = True
    result["n"] = len(pool)

    if cash or gpp:
        lpath = root / f"data/dfs_lineups_ncaaf_{date}.csv"
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
                stack_n: int | None = None, bring_back: int | None = None,
                own_gamma: float | None = None, own_weight: float = 0.0,
                groups: list[dict] | None = None, persist: bool = True) -> dict:
    """Pool + a CASH lineup + a GPP lineup for one DK CFB Classic slate.

    Returns {gid, meta, slates, pool, stats, cash, gpp, games, teams, log} or,
    when the slate is not priced yet, {unpriced: True, ...}. A `cash` or `gpp`
    of None means no legal lineup exists under the cap -- a real answer on a
    thin board, deliberately distinguishable from a bad one.
    """
    from edge import dfs_ncaaf_theory as theory

    all_groups = groups if groups is not None else dfs.draft_groups(DK_SPORT)
    gid, meta = resolve_slate(draft_group, all_groups)
    slates = classic_groups(all_groups)
    if gid is None:
        return {"error": meta.get("error"), "slates": slates}

    salaries = dfs.fetch_draftables(gid)
    priced = {k: v for k, v in salaries.items() if v.get("salary")}
    if not priced:
        return {"unpriced": True, "gid": gid, "meta": meta, "slates": slates}

    pool, stats = build_pool(client, salaries, book=book)
    games = slate_games(salaries)
    theory.add_ownership(pool, **({"gamma": own_gamma} if own_gamma else {}))

    kw = {}
    if stack_n is not None:
        kw["stack_n"] = stack_n
    if bring_back is not None:
        kw["bring_back"] = bring_back

    cash = dfs_opt_ncaaf.optimize(pool, mode="cash", iters=iters, seed=0)
    gpp = dfs_opt_ncaaf.optimize(pool, mode="gpp", iters=iters, seed=0,
                                 own_weight=own_weight, **kw)
    log_result = None
    if persist:
        try:
            log_result = log_forward_test(pool, cash, gpp, gid, meta)
        except Exception as exc:                            # noqa: BLE001
            # A logging failure must never take the lineups down with it.
            log.warning("dfs_run_ncaaf: forward-test logging failed: %s", exc)
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
    order = {s: i for i, s in enumerate(dfs_opt_ncaaf.SLOTS)}
    rows = []
    for p, slot in sorted(result["lineup"], key=lambda t: order[t[1]]):
        rows.append({"slot": slot, "player": p["name"], "team": p.get("team", ""),
                     "opp": p.get("opp_team", ""), "salary": p.get("salary", 0),
                     "proj": round(float(p.get("proj") or 0), 1),
                     "own": p.get("own", 0.0), "leverage": p.get("leverage", 0.0),
                     "band": p.get("band_points", 0.0)})
    return rows
