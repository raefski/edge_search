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

from edge.dfs_slate import parse_time as _parse_time, slate_games
from edge import dfs_slate
from edge import dfs_forward_log
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
_TEAM_SUFFIX_CAP = re.compile(r"\(([^)]*)\)\s*$")


def subject_key(subject: str) -> str:
    return ncaaf.norm(_TEAM_SUFFIX.sub("", subject or ""))


def salary_index(salaries: dict) -> tuple[dict, list]:
    """(DK salaries keyed the way a prop subject is, ambiguous keys).

    edge/dfs.fetch_draftables keys with edge.dfs.norm, which KEEPS "Jr." and
    "III"; a college prop subject drops the suffix ("Michael Hawkins (WVU)"),
    and subject_key strips it anyway. Joined directly, every suffixed player
    fell out of the pool: 10 of the 75 with ladders on the 2026-09-26 Night
    slate, including the quarterback 91% of the cash field played. A key two
    DK players share is left out rather than guessed.
    """
    out: dict = {}
    dup: set = set()
    for info in salaries.values():
        k = ncaaf.norm(info.get("name") or "")
        if k in out:
            dup.add(k)
        out[k] = info
    for k in dup:
        out.pop(k, None)
    return out, sorted(dup)


# ---------------------------------------------------------------------------
# The slate, as DraftKings describes it
# ---------------------------------------------------------------------------
def classic_groups(groups: list[dict] | None = None) -> list[dict]:
    """The lobby's Classic slates, earliest first (edge/dfs_slate.py)."""
    groups = groups if groups is not None else dfs.draft_groups(DK_SPORT)
    return dfs_slate.classic_rows(groups, CLASSIC_GAME_TYPE)


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
        return dfs_slate.explicit(rows, draft_group)
    main = [r for r in rows if r["label"] == "Main"]
    pick = max(main or rows, key=lambda r: (r["games"], r["featured"]))
    return pick["gid"], pick


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
    by_event, _ = collect_ladders_by_event(client, markets, book)
    out: dict = {}
    teams: dict = {}
    for ev, players in by_event.items():
        for key, ladders in players.items():
            out.setdefault(key, {}).update(ladders)
        if players:
            teams[ev] = set(players)
    return out, teams


def collect_ladders_by_event(client, markets=LADDER_MARKETS,
                             book: str = "draftkings",
                             subjects: dict | None = None) -> tuple[dict, dict]:
    """({event id: {player key: {market: rungs}}}, {event id: commence time}).

    Kept PER EVENT so a ladder can be joined to a draftable only through the
    event that is that player's own game (edge/dfs_slate.subject_events): a
    name alone joined Missouri's QB to Washington's $3,000 namesake on 10/16.
    A subject the book names with no usable rungs still counts as one of the
    event's subjects -- it is evidence of whose game the event is.

    `subjects`, when given, is filled with {event id: {player key: the book's
    own spelling}} ("Braylon McReynolds (UAB)"), which alias_joins reads.
    """
    by_event: dict = {}
    kickoffs: dict = {}
    for ev in client.get_events(SPORT):
        kickoffs[ev["id"]] = ev.get("commence_time")
        payload = client.get_event_odds(SPORT, ev["id"], list(markets), "us")
        players: dict = {}
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
                    slot = players.setdefault(subject_key(subject), {})
                    if subjects is not None:
                        subjects.setdefault(ev["id"], {})[subject_key(subject)] = subject
                    rungs = dfs_ladder.rungs_from_outcomes(outcomes, subject)
                    if rungs:
                        slot[key] = rungs
        if players:
            by_event[ev["id"]] = players
    return by_event, kickoffs


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


def on_slate_keys(event_players: dict, by_key: dict, kickoffs: dict | None = None,
                  games: dict | None = None) -> tuple[set, list]:
    """(prop-subject keys whose ladder belongs to their DK player's own game,
    names refused) -- edge/dfs_slate.subject_events, which every sport's prop
    join now goes through. Found here first: "Fredrick Moore (MSU)" joined
    Alabama's $4,000 RB on 10/3 and 10/17, and on 10/16 Missouri QB "Austin
    Simmons (MIZZ)" became a $3,000 Washington WR projected 16.3 that both
    model lineups played (0 points)."""
    owner, refused = dfs_slate.subject_events(event_players, by_key.get, kickoffs,
                                              games, MATCH_TOLERANCE)
    return set(owner), refused


#: alias_joins' Tier B (same first initial) is allowed only when one of the two
#: first names is this short -- a nickname or initials ("MK", "Bam", "Ty").
#: Chosen on the train slates (before 2026-10-11) of scripts/ncaaf_names_lab.py:
#: the one true initial-only alias there was MK = Makhilyn (2 letters), and every
#: wrong pairing the rule would have made with the true player removed had two
#: first names of 4+ letters (King/Kayne Miller, Jeremy/James Scott, Dakorien/
#: Dante Moore, Dylan/Dayton Raiola: 4 placebo false joins of 487). 3 sits
#: between; 2 would also have passed train (judgment call, 2026-10-31).
ALIAS_SHORT_FIRST = 3
_NAME_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _first_last(name: str) -> tuple[str, set]:
    """(normalised first name, {normalised last name and each hyphen part})."""
    toks = [t for t in _TEAM_SUFFIX.sub("", name or "").replace(".", " ").split()]
    while len(toks) > 1 and ncaaf.norm(toks[-1]) in _NAME_SUFFIXES | {""}:
        toks.pop()
    if not toks:
        return "", set()
    last = toks[-1]
    return (ncaaf.norm(toks[0]),
            {ncaaf.norm(last)} | {ncaaf.norm(p) for p in last.split("-") if p})


def alias_joins(by_event: dict, subjects: dict, owner: dict, ev_game: dict,
                salaries: dict) -> dict:
    """{(event id, player key): DK salary entry} for ladder subjects the exact
    name join left out, matched inside the event's own game only.

    Both sides are DraftKings but they do not spell players alike: the salary
    list carries nicknames and full surnames the sportsbook does not ("Bam
    McReynolds" / "Braylon McReynolds (UAB)", "Rod Robinson II" / "Roderick",
    "Ty Mims" / "Tiaquelin", "Makhilyn Young" / "MK Young", "Dominic
    Lee-Knicely" / "Dominic Knicely" -- a $6,800 RB 38% owned in the 10/16
    GPP that our board could not see), and a namesake ANYWHERE on the slate
    (Fredrick Moore of MSU and of Alabama) dropped both from salary_index. In
    the names lab (critique cycle 2) those were every missed subject: 2 of 489
    train, 7 of 931 test, 67 projected points.

    `subjects` = {event id: {player key: the book's spelling}}
    (collect_ladders_by_event). A subject joins a DK player only when all hold:
      * the book's team suffix maps to a DK team in that game -- by the vote
        of the event's exactly joined subjects, or as the one suffix and the
        one DK team both left unmatched;
      * the last names agree (a hyphen part counts: Knicely ~ Lee-Knicely);
      * Tier A -- one first name is a prefix of the other (2+ letters; an
        identical name settles a slate-wide namesake), or else Tier B -- same
        first initial with one first name ALIAS_SHORT_FIRST letters or fewer;
        and exactly ONE player on that DK team passes the tier;
      * that DK player was not exactly joined, nor claimed by another alias;
      * a subject with a passing-yards ladder is a DK QB.
    Brothers on one roster (Dylan / Dayton Raiola) are what the uniqueness
    and short-name rules are for; every alias is listed (stats["name_alias"]).
    """
    roster: dict = {}
    for info in salaries.values():
        roster.setdefault(info.get("game"), []).append(info)
    by_key, _ = salary_index(salaries)
    taken = {id(by_key[k]) for k in owner if k in by_key}
    out: dict = {}
    for ev, game in ev_game.items():
        spelled = subjects.get(ev, {})
        sfx = {k: m.group(1) for k, s in spelled.items()
               if (m := _TEAM_SUFFIX_CAP.search(s))}
        votes: dict = {}
        for key in by_event.get(ev, {}):
            if owner.get(key) == ev and key in sfx:
                team = by_key[key].get("team")
                votes.setdefault(sfx[key], {}).setdefault(team, 0)
                votes[sfx[key]][team] += 1
        tmap = {s: max(v.items(), key=lambda kv: kv[1])[0] for s, v in votes.items()}
        open_s = set(sfx.values()) - set(tmap)
        open_t = {i.get("team") for i in roster.get(game, [])} - set(tmap.values())
        if len(open_s) == 1 and len(open_t) == 1:
            tmap[open_s.pop()] = open_t.pop()
        for key, by_market in by_event.get(ev, {}).items():
            team = tmap.get(sfx.get(key))
            if owner.get(key) == ev or not by_market or team is None:
                continue
            first, lasts = _first_last(spelled[key])
            if not first or not lasts:
                continue
            same = [(i, _first_last(i.get("name") or "")[0]) for i in roster.get(game, [])
                    if i.get("team") == team and _first_last(i.get("name") or "")[1] & lasts]
            tier_a = [i for i, f in same if min(len(f), len(first)) >= 2
                      and (f.startswith(first) or first.startswith(f))]
            tier_b = [i for i, f in same if f[:1] == first[:1]
                      and min(len(f), len(first)) <= ALIAS_SHORT_FIRST]
            pick = tier_a if tier_a else tier_b
            if len(pick) != 1:
                continue
            info = pick[0]
            if id(info) in taken or not info.get("salary"):
                continue
            if "player_pass_yds" in by_market and "QB" not in (info.get("position") or ""):
                continue
            taken.add(id(info))
            out[(ev, key)] = info
    return out


def join_ladders(by_event: dict, kickoffs: dict, salaries: dict, subjects: dict,
                 stats: dict) -> list[tuple[dict, dict]]:
    """[(one player's rungs, his DK salary entry)] -- the whole name join, which
    scripts/ncaaf_names_lab.py replays. Fills stats: priced_players,
    not_on_slate, ambiguous_names, wrong_game, name_alias."""
    games = slate_games(salaries)
    event_players = {ev: set(players) for ev, players in by_event.items()}
    by_key, ambiguous = salary_index(salaries)
    owner, wrong_game = dfs_slate.subject_events(event_players, by_key.get, kickoffs,
                                                 games, MATCH_TOLERANCE)
    ev_game = dfs_slate.event_games(event_players, by_key.get, kickoffs, games,
                                    MATCH_TOLERANCE)
    alias = alias_joins(by_event, subjects, owner, ev_game, salaries)
    # Each player's rungs from his own game's event only (exactly, or through
    # an alias in that game); everyone else counted not_on_slate.
    joins: list = []
    stray: set = set()
    for ev, players in by_event.items():
        for key, by_market in players.items():
            if not by_market:
                continue
            if owner.get(key) == ev:
                joins.append((by_market, by_key[key]))
            elif (ev, key) in alias:
                joins.append((by_market, alias[(ev, key)]))
            elif key not in owner:
                stray.add(key)
    stray -= {k for _, k in alias}
    stats.update({"priced_players": len(joins) + len(stray), "not_on_slate": len(stray),
                  "ambiguous_names": ambiguous, "wrong_game": wrong_game,
                  "name_alias": sorted(f"{info['name']} = {_TEAM_SUFFIX.sub('', subjects[ev][k])}"
                                       for (ev, k), info in alias.items())})
    return joins


def build_pool(client, salaries: dict, book: str = "draftkings") -> tuple[list, dict]:
    """The projected player pool for one slate."""
    sport = dfs_sport.get(SPORT)
    games = slate_games(salaries)
    opponents = team_opponents(games)
    subjects: dict = {}
    by_event, kickoffs = collect_ladders_by_event(client, book=book, subjects=subjects)
    stats = {"projected": 0, "no_proj": 0, "no_salary": 0, "not_on_slate": 0,
             "one_rung": 0, "slate_players": len(salaries)}
    joins = join_ladders(by_event, kickoffs, salaries, subjects, stats)

    pool: list[dict] = []
    for by_market, info in joins:
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

    event_players = {ev: set(players) for ev, players in by_event.items()}
    lines, line_report = game_lines(client, games, event_players, salary_index(salaries)[0])
    for p in pool:
        line = lines.get(p.get("game"))
        if line:
            p["total"] = line["total"]
    stats.update(line_report)
    return pool, stats


# ---------------------------------------------------------------------------
# The whole slate, in one call
# ---------------------------------------------------------------------------
#: `dk_fppg` is logged because ownership reads it (dfs_ncaaf_theory.
#: OWNERSHIP_FPPG_WEIGHT) and DK serves no past draftables: it was fitted on a
#: cfbfastR reconstruction, and a refit should use the number the field saw.
PROJ_LOG_COLS = ("date", "gid", "player", "team", "opp_team", "dk_pos", "salary",
                 "proj", "sd", "own", "leverage", "band_points", "game", "games",
                 "dk_fppg")


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
    """Persist a build so a real contest result has something to be JOINED to
    later -- every player in the pool, one row each, for this draft group, and
    the CASH/GPP lineups the app recommended. edge/dfs_forward_log.py holds the
    rules (a past date is never logged; only this draft group's rows are
    replaced); the columns are this sport's."""
    date = _slate_date(meta)
    games = meta.get("games")
    rows = [{"date": date, "gid": gid, "player": p["name"], "team": p.get("team", ""),
             "opp_team": p.get("opp_team", ""), "dk_pos": p.get("dk_pos", ""),
             "salary": p.get("salary", ""), "proj": p.get("proj", ""), "sd": p.get("_sd", ""),
             "own": p.get("own", ""), "leverage": p.get("leverage", ""),
             "band_points": p.get("band_points", ""),
             "game": p.get("game", ""), "games": games if games is not None else "",
             "dk_fppg": "" if p.get("dk_fppg") is None else p["dk_fppg"]}
            for p in pool]
    lineups = None
    if cash or gpp:
        lineups = [{"mode": mode, "slot": row["slot"], "player": row["player"], "team": row["team"],
                    "opp_team": row["opp"], "salary": row["salary"], "proj": row["proj"],
                    "own": row["own"]}
                   for mode, r in (("cash", cash), ("gpp", gpp)) for row in (lineup_rows(r) if r else [])]
    return dfs_forward_log.write("ncaaf", date, gid, PROJ_LOG_COLS, rows,
                                 ("mode", "slot", "player", "team", "opp_team", "salary", "proj",
                                  "own"), lineups, root=root)


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

    try:
        salaries = dfs.fetch_draftables(gid)
    except dfs.DraftablesUnavailable as exc:
        return {"unpriced": True, "unpriced_reason": str(exc), "gid": gid,
                "meta": meta, "slates": slates}
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
