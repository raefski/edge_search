"""Shared DK MLB DFS slate builder used by both the CLI (scripts/dfs_lineups.py)
and the portable Streamlit app (app.py).

`build_slate` is the single source of truth for the pipeline that used to live
inside scripts/dfs_lineups.py::main:

  * pitchers  -> Vegas-implied projection from DK sportsbook props (Odds API, PAID)
  * hitters   -> skill x opportunity x park x matchup over confirmed lineups
                 (statsapi, FREE — empty until lineups post ~3-4h pregame)
  * optimizer -> dependency-free CASH (mean) and GPP (stack + ceiling) lineups

Credit discipline is entirely delegated to the OddsAPIClient the caller passes
in. In CACHE mode the caller builds the client with dry_run=True and a huge
live_ttl, so paid prop calls are served from data/cache/ (0 credits) and any
uncached pitcher is simply skipped (DryRunBlocked caught below). DK salaries and
confirmed lineups come from FREE public endpoints, so they always refresh live —
which is exactly what you want for the freshest lineups before lock.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

from edge.client import DryRunBlocked
from edge import dfs, dfs_opt

SPORT = "baseball_mlb"


def log_forward_test(root: Path, date: str, is_main: bool, gid, pool: list,
                     cash: dict | None, gpp: dict | None, games: int | None = None) -> dict:
    """Persist a build to disk for forward-testing, regardless of whether the
    CLI or the phone app produced it — a single source of truth so scripts/dfs_grade.py
    always has something to grade.

    * data/dfs_proj_log.csv: every hitter/pitcher projection for `date` (only for
      the MAIN slate — a sub-slate like Turbo/Night must not clobber it with a
      smaller player set). Re-running for the same date overwrites that date's
      rows in place; other dates are untouched.
    * data/dfs_proj_log_<date>_g<gid>.csv: the SAME projections for a sub-slate
      build, instead of just being dropped. Found live 2026-07-10 (a cash
      Night slate) and again 2026-07-17 (a Night slate the user actually
      played, GPP finish 84th percentile / cash finish 8th) -- both times a
      real forward-test opportunity was lost because sub-slate projections
      were never logged anywhere at all, only main-slate ones. dfs_calibration.py
      now scans these files too, so any played sub-slate can be graded like a
      main slate going forward (it just can't recover the two dates above).
    * data/dfs_lineups_<date>[_g<gid>].csv: the built CASH/GPP lineups, if any.

    games: DK's own declared GameCount for the resolved draft group (from
    resolve_slate's meta) -- logged per row so dfs_calibration.py can track
    slate size going forward (2026-07-11, per user request, to eventually
    test whether ownership concentration/gamma should vary with slate size --
    see DFS_METHODOLOGY.md §17). None for callers that don't have it; rows
    logged before this existed are blank, not backfilled here (dfs_calibration.py
    falls back to counting distinct teams in the pool for those).

    Returns {"logged_projections": bool, "n": int, "lineup_file": str|None}.
    """
    result = {"logged_projections": False, "n": 0, "lineup_file": None}
    (root / "data").mkdir(parents=True, exist_ok=True)
    # A build for a PAST date must never overwrite the forward-test log: DK's
    # events/draftables have moved on by then, so a past-date rebuild always
    # produces a thinner/different pool than what was really buildable pre-lock
    # -- and the main log's per-date overwrite would silently replace REAL
    # forward-test data with it. This exact incident happened 2026-07-19 (a
    # --date 2026-07-18 verification rebuild clobbered the user's real 7/18
    # Main-slate rows, 134 rows w/ 8 pitchers -> 144 rows w/ 0; restored from
    # git). Same contamination class as §25's stray same-date build.
    import datetime as _dt
    from zoneinfo import ZoneInfo as _ZI
    try:
        today_et = _dt.datetime.now(_ZI("America/New_York")).date().isoformat()
    except Exception:
        today_et = _dt.date.today().isoformat()
    if date < today_et:
        result["skipped_past_date"] = True
        return result
    # outs_mean/k_mean (pitchers only): the prop-implied distribution anchors
    # the field simulator draws around -- logged so PAST slates can be
    # re-simulated faithfully instead of falling back to generic defaults
    # (added 2026-07-19 per DFS_IMPROVEMENT_PLAN §1; blank for hitters and for
    # rows logged before this existed -- readers use DictReader, so the new
    # columns are backward/forward compatible in both directions).
    cols = ("date", "player", "team", "pos", "salary", "proj", "ceiling", "own", "conf", "dk_fppg", "games",
            "outs_mean", "k_mean")

    def _write_rows(fh, rows):
        w = csv.writer(fh)
        w.writerow(list(cols))
        for r in rows:
            w.writerow([r.get(k, "") for k in cols])

    def _pool_rows():
        for p in pool:
            yield {"date": date, "player": p["name"], "team": p["team"],
                  "pos": "/".join(sorted(p["pos"])), "salary": p["salary"], "proj": p["proj"],
                  "ceiling": p.get("ceiling"), "own": p.get("own", ""), "conf": p["conf"],
                  "dk_fppg": p.get("dk_fppg", ""), "games": games if games is not None else "",
                  "outs_mean": p.get("outs_mean", ""), "k_mean": p.get("k_mean", "")}

    if is_main:
        plog = root / "data/dfs_proj_log.csv"
        prior = [r for r in csv.DictReader(open(plog))] if plog.exists() else []
        with plog.open("w", newline="") as fh:
            _write_rows(fh, [r for r in prior if r["date"] != date] + list(_pool_rows()))
        result["logged_projections"] = True
        result["n"] = len(pool)
    else:
        # sub-slate: a fixed per-(date,gid) file, so re-running the SAME
        # sub-slate build overwrites in place (like the main log does per
        # date) without clobbering a DIFFERENT sub-slate or the main log.
        splog = root / f"data/dfs_proj_log_{date}_g{gid}.csv"
        with splog.open("w", newline="") as fh:
            _write_rows(fh, list(_pool_rows()))
        result["logged_projections"] = True
        result["n"] = len(pool)

    if cash or gpp:
        fname = f"data/dfs_lineups_{date}.csv" if is_main else f"data/dfs_lineups_{date}_g{gid}.csv"
        with (root / fname).open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["mode", "slot", "player", "team", "salary", "proj", "ceiling", "own", "pos", "game", "conf"])
            for mode, r in (("cash", cash), ("gpp", gpp)):
                for p, slot in sorted(r["lineup"], key=lambda x: dfs_opt.SLOTS.index(x[1])) if r else []:
                    w.writerow([mode, slot, p["name"], p["team"], p["salary"], p["proj"], p["ceiling"],
                                p.get("own"), "/".join(sorted(p["pos"])), p.get("game", ""), p.get("conf", "")])
        result["lineup_file"] = fname
    return result


def team_abbrev_map() -> dict[str, str]:
    """{team_id: DK-style abbreviation}. Normalized via dfs._STATSAPI_TO_DK_ABBR
    -- caught live 2026-07-09: statsapi says "AZ" for Arizona, DK's draftables
    say "ARI". Hitters' pool "team" came from this function (statsapi-based)
    while pitchers' came directly from DK draftables, so Arizona hitters and
    Arizona's own pitcher silently never matched on team string -- which
    quietly broke the pitcher-vs-own-hitters constraint (edge/dfs_opt.py
    _valid()) for this one team specifically, since opp_team lookups keyed
    on the DK spelling never found the statsapi-spelled entry.

    Thin wrapper: this used to fetch /teams?sportId=1 itself, independently
    of team_game_status()'s own identical fetch -- both ran on every single
    build_slate() call for a resource that never changes intra-build. Both
    now share dfs.team_id_to_abbr()'s in-process memo. Kept as a named
    function (not inlined at call sites) since tests target it directly."""
    return dfs.team_id_to_abbr()


def resolve_slate(draft_group, groups=None, date=None):
    """Return (gid, is_main, meta). meta carries a human label / error info.

    draft_group=None -> auto main slate. A name (Main/Early/Turbo/Night/...) or a
    numeric id resolves via edge.dfs. On a bad name, meta['error'] is set and
    meta['available'] lists slates open now.

    date, when given, is forwarded to dfs.resolve_draft_group so a NAMED
    slate (e.g. "Early") only considers TODAY's Early group, not whichever
    same-named group across every date sorts soonest. Bug found live
    2026-07-11: this was previously dropped entirely -- build_slate HAD the
    real date but never passed it here -- so if DK's lobby already listed a
    same-named slate for a different date, name resolution could silently
    grab the wrong one. Confirmed live: STL/LAD (not part of that day's
    Early window) turned up in an "Early" build's player pool.
    """
    groups = groups if groups is not None else dfs.mlb_draft_groups()
    is_main = draft_group is None or str(draft_group).strip().lower() in ("main", "classic", "full")
    if draft_group is None:
        gid = dfs.main_slate_group(groups)
        # bug fix: this path never populated games/start (only the named-slate
        # path below did) -- meant the auto "Main (auto)" build (the app's
        # default) never carried slate size, which the calibration pipeline
        # now needs (see build_slate's "games" key).
        g = next((x for x in groups if x.get("DraftGroupId") == gid), None)
        meta = {"label": "Main (auto)"}
        if g:
            meta["start"] = g.get("StartDate", "")[:16]
            meta["games"] = g.get("GameCount")
        return gid, True, meta
    g = dfs.resolve_draft_group(draft_group, date=date)
    if not g:
        names = sorted({n for n, *_ in dfs.list_slate_names(groups)})
        return None, is_main, {"error": f"slate {draft_group!r} not found", "available": names}
    return g["DraftGroupId"], is_main, {
        "label": f"{draft_group} -> group {g['DraftGroupId']}",
        "start": g.get("StartDate", "")[:16], "games": g.get("GameCount"),
    }


def team_list_for_slate(date, draft_group=None, groups=None) -> tuple[list[str], dict, str | None]:
    """Cheap (free-endpoints-only, no optimizer) team discovery for a slate --
    -> (all_teams, team_status, error). Lets a caller learn which teams are in
    play (and their game-status flags) WITHOUT running the full paid/optimizer
    build_slate pipeline, and without exclude_teams as an input (this answers
    "what teams exist", not "build with these excluded").

    Added 2026-07-18: the app's "Exclude teams" sidebar widget used to only
    learn its options from st.session_state, written by the PREVIOUS build --
    empty (and un-clickable, "No options to select") on the very first build
    of a session, since nothing had run yet to populate it. This gives the
    sidebar a same-run answer instead of a one-run-stale one.
    """
    groups = groups if groups is not None else dfs.mlb_draft_groups()
    gid, is_main, meta = resolve_slate(draft_group, groups, date=date)
    if gid is None:
        return [], {}, meta.get("error")
    salaries = dfs.fetch_draftables(gid)
    if not salaries:
        return [], {}, None
    all_teams = sorted({info["team"] for info in salaries.values() if info.get("team")})
    team_status = dfs.team_game_status(date)
    return all_teams, team_status, None


def build_slate(client, date, draft_group=None, iters=800, exclude_teams=None):
    """Run the full pipeline for one slate and return a structured result dict.

    exclude_teams: optional set/list of team abbreviations (e.g. {"BAL","CHC"})
    to drop from the pool entirely -- for when DK voids/doesn't count specific
    games (postponement, suspension, a contest-scoring exclusion that doesn't
    necessarily show up as a game-status change). Caught live 2026-07-09: DK
    told the user BAL@CHC wouldn't count for a contest, but the generator had
    no way to know and used those players anyway.

    Keys: gid, is_main, meta, salaries_n, skill_n, lineup_hitters_n, pool,
    pitchers, hitters, stack_team, cash, gpp, spent, remaining, all_teams
    (every team in the slate before exclusion, with a game_status_flag per
    team -- "" for normal, else the game's detailedState, e.g. "Postponed" --
    so the caller can show an informed warning even when DK's own "won't
    count" designation isn't visible from the game's status alone).
    When the slate isn't priced yet: {'unpriced': True, 'upcoming': [...]}.
    On a bad slate name: {'error': ..., 'available': [...]}.
    """
    exclude_teams = set(exclude_teams or ())
    groups = dfs.mlb_draft_groups()
    gid, is_main, meta = resolve_slate(draft_group, groups, date=date)
    if gid is None:
        return {"error": meta.get("error"), "available": meta.get("available", [])}

    salaries = dfs.fetch_draftables(gid)
    if not salaries:
        return {"unpriced": True, "gid": gid, "is_main": is_main, "meta": meta,
                "upcoming": dfs.list_slate_names(groups)[:12]}

    all_teams = sorted({info["team"] for info in salaries.values() if info.get("team")})
    team_status = dfs.team_game_status(date)  # {team_abbr: "" or e.g. "Postponed"}
    # Defensive cross-check: the resolved slate's OWN declared GameCount vs
    # how many teams actually showed up in its salaries. Not a fix for any
    # specific root cause (DK's draftables were confirmed correctly scoped in
    # every case checked directly) -- it's a safety net so a wrong-slate
    # resolution (see resolve_slate's date fix, and the "Main" duplicate
    # tie-break fix) surfaces as a visible warning instead of a silently
    # contaminated pool the user has to spot and exclude by hand, which is
    # exactly what happened live 2026-07-11 with an "Early" build that
    # included STL/LAD.
    slate_mismatch = None
    expected_games = meta.get("games")
    if expected_games and abs(len(all_teams) // 2 - expected_games) >= 1:
        slate_mismatch = (f"resolved slate claims {expected_games} game(s) but salaries cover "
                          f"{len(all_teams)} teams (~{len(all_teams) // 2} games) -- double-check "
                          f"the Slate picker matches what you're actually entering on DK")

    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    yr = int(date[:4])
    # include the CURRENT season (not just prior completed ones) -- backtested
    # 2026-07-08 on 5,146 held-out 2025 hitter-games: MAE 5.604->5.577, corr
    # +0.166->+0.181 vs freezing on the two seasons before the current one.
    # max_age so the cache actually refreshes as the season progresses instead
    # of freezing on whatever was true the first time it was pulled.
    skill_seasons = (yr - 2, yr - 1, yr)
    # shrink_k=60: EB shrinkage toward league average instead of the min-120-PA
    # cutoff (see pooled_skill_rates docstring for the 2026-07-10 backtest).
    # Versioned cache filename so a pre-shrinkage cache is never misread.
    # season_weights: Marcel-style decay -- the two-seasons-back year at half
    # weight (backtested 2026-07-18 as part of the marcel+home+platoon combo,
    # incremental t=3.48 over the §18 baseline on 13,801 held-out hitter-games).
    # Versioned cache name (_mw) so a flat-pooled cache is never misread.
    rates, lg = dfs.pooled_skill_rates(skill_seasons, shrink_k=60,
                                       season_weights=(0.5, 1.0, 1.0),
                                       cache_path=str(root / f"data/dfs_skill_{'_'.join(map(str, skill_seasons))}_eb60_mw.json"),
                                       max_age=21600)
    park = dfs.park_runs(yr)
    k9 = dfs.pitcher_k9((yr - 1, yr), cache_path=str(root / f"data/dfs_pitch_k9_{yr-1}_{yr}.json"), max_age=21600)
    era = dfs.pitcher_era((yr - 1, yr), cache_path=str(root / f"data/dfs_pitch_era_{yr-1}_{yr}.json"), max_age=21600)
    hr_season = dfs.season_hitting(cache_path=str(root / "data/dfs_season_hitting.json"))
    lineups = dfs.lineups_for_date(date)
    abbr = team_abbrev_map()
    # pitcher pool entries come from Odds-API events (no statsapi gamePk), so we
    # can't match pitcher-vs-hitter matchups by game id across the two sources.
    # Team abbreviation is the one thing both sides share -- build team -> opponent
    # abbreviation from the hitters' data (which already knows real matchups) and
    # apply it to pitchers by their own team, so _valid() can compare on teams.
    team_opp_abbr = {abbr.get(str(lu["team_id"])): abbr.get(str(lu["opp_team_id"]))
                     for lu in lineups.values() if lu.get("opp_team_id")}

    pool = []
    # Surfaced to the caller (not just swallowed) so "why are there 0
    # pitchers" is answerable without re-deriving it by hand every time --
    # found 2026-07-17: a user reported a key entered but no props pulled,
    # and there was no way to tell from the app whether that meant "no key,"
    # "key rejected," "network failure," or something else -- every one of
    # those degraded identically to a silent empty pitcher pool.
    pitcher_fetch_error = None

    # --- pitchers: from props (PAID; skipped when uncached in dry-run) ---
    try:
        events = client.get_events(SPORT)
    except Exception as e:
        # get_events() is a real network call even in CACHE/dry-run mode (it's
        # cost=0, so dry_run doesn't gate it) -- a missing/invalid API key or a
        # transient network failure here used to crash the ENTIRE build,
        # including the free salary/hitter data that never needed a key at
        # all. Found live 2026-07-11: a Streamlit Cloud reboot lost the key
        # (only ever pasted into the sidebar, not a persistent secret) and
        # every mode broke, not just the paid one. Same principle as the
        # per-event handling just below -- one missing/failed piece degrades
        # to "no pitcher props this build," not a dead page.
        events = []
        pitcher_fetch_error = f"{type(e).__name__}: {e}"
    event_errors = 0
    last_event_error = None
    for ev in events:
        try:
            pp = client.get_event_odds(SPORT, ev["id"], dfs.P_MARKETS, "us")
        except DryRunBlocked:
            continue
        except Exception as e:
            # A single event's odds call failing (the Odds API 404s an event with
            # no markets posted yet, or a transient 429/500/timeout) must not take
            # down the whole build — skip that one event and keep going. Tracked
            # (not just swallowed) so a SYSTEMATIC failure (bad key, credit floor,
            # network outage) is distinguishable from the normal "this one event
            # just doesn't have markets yet" case.
            event_errors += 1
            last_event_error = f"{type(e).__name__}: {e}"
            continue
        dkp = next((b for b in pp.get("bookmakers", []) if b["key"] == "draftkings"), None)
        if not dkp:
            continue
        # sorted() so pool order is reproducible across machines/processes (a bare
        # set iterates in hash-randomized order, which can shift optimizer tie-breaks).
        for nm in sorted({o["description"] for m in dkp["markets"] for o in m["outcomes"] if o.get("description")}):
            info = salaries.get(dfs.norm(nm))
            if not info or not info.get("salary") or "P" not in dfs.parse_pos(info["position"]):
                continue
            pp_proj = dfs.project_pitcher(dfs.player_markets(dkp, nm))
            proj = pp_proj["proj"]
            if proj is None:
                continue
            pool.append({"name": nm, "pos": {"P"}, "salary": info["salary"], "proj": proj,
                         "ceiling": proj, "floor": proj,  # no validated pitcher-specific floor signal yet
                         "team": info["team"], "game": info["game"],
                         "opp_team": team_opp_abbr.get(info["team"]), "conf": "P-prop",
                         "dk_fppg": info.get("dk_fppg"),
                         # prop-implied anchors for the field simulator
                         "outs_mean": pp_proj.get("outs_mean"), "k_mean": pp_proj.get("k_mean")})

    # every single event failing (not just some -- a normal day has SOME
    # events with no markets posted yet) is the systematic-failure signal:
    # bad/rejected key, credit floor hit, or a real network/API outage.
    if events and event_errors == len(events) and pitcher_fetch_error is None:
        pitcher_fetch_error = f"all {event_errors} event odds calls failed, e.g. {last_event_error}"

    # --- hitters: skill model over confirmed lineups (FREE) ---
    bullpen_cache_dir = root / "data/bullpen_cache"
    bullpen_cache_dir.mkdir(parents=True, exist_ok=True)
    _bullpen_memo = {}

    def bullpen_for(team_id):
        if team_id not in _bullpen_memo:
            _bullpen_memo[team_id] = dfs.team_pitcher_stats(
                team_id, (yr - 1, yr), cache_path=str(bullpen_cache_dir / f"{team_id}.json"), max_age=21600)
        return _bullpen_memo[team_id]

    # Handedness for the PLATOON_CELL factor: batter sides + opposing starters'
    # throwing hands, one bulk /people call per ~100 uncached ids, disk-cached
    # permanently (hands don't change). Missing entries degrade to neutral.
    _hand_ids = [lu["id"] for lu in lineups.values()] + \
                [lu.get("opp_pitcher_id") for lu in lineups.values() if lu.get("opp_pitcher_id")]
    hands = dfs.player_hands(_hand_ids, cache_path=str(root / "data/dfs_hands.json"))

    for (team_id, nm), lu in lineups.items():
        team_abbr = abbr.get(str(team_id), str(team_id))
        info = salaries.get(nm)
        if not info or not info.get("salary"):
            continue
        # Name collision guard: two different active players can share a
        # normalized name (confirmed live 2026-07-12 -- LAD's and ATH's Max
        # Muncy, both real). salaries is keyed by name alone, so it may hold
        # a DIFFERENT team's player under this same key than the one
        # lineups_for_date resolved for THIS team_id -- if DK's own team for
        # that salary entry doesn't match, this isn't a real match, skip it
        # rather than merge one player's price with another's team/opponent/
        # batting-slot/skill data.
        if info.get("team") != team_abbr:
            continue
        pos = dfs.parse_pos(info["position"])
        if not pos or "P" in pos:
            continue
        skill = rates.get(str(lu["id"]), lg)
        pk = park.get(str(lu["park_team_id"]), 1.0)
        # matchup = 60% opposing starter's K9, 40% opposing bullpen's K9 (a hitter
        # sees roughly the back third of the game against relief pitching) --
        # backtested 2026-07-08, MAE 5.577->5.547 with correlation unchanged.
        starter_k9 = k9.get(str(lu["opp_pitcher_id"]), dfs.LG_K9)
        opp_team_id = lu.get("opp_team_id")
        bp_k9 = dfs.bullpen_k9(bullpen_for(opp_team_id), exclude_pid=lu.get("opp_pitcher_id")) if opp_team_id else dfs.LG_K9
        matchup_k9 = 0.6 * starter_k9 + 0.4 * bp_k9
        # home/away PA table + opposing starter's ERA -- both backtested
        # 2026-07-10 (13,801 held-out 2025 hitter-games): together with the
        # EB-shrunk skill rates, MAE 5.565->5.456 (better on 56/57 test dates)
        # and corr 0.166->0.177 vs the previous production shape.
        is_home = str(lu["team_id"]) == str(lu["park_team_id"])
        starter_era = era.get(str(lu["opp_pitcher_id"]))
        bhand = (hands.get(str(lu["id"])) or {}).get("bat")
        opp_hand = (hands.get(str(lu.get("opp_pitcher_id"))) or {}).get("throw")
        proj = dfs.project_hitter_skill(skill, lu["slot"], pk, matchup_k9,
                                        home=is_home, opp_era=starter_era,
                                        bhand=bhand, opp_hand=opp_hand)
        sr = hr_season.get((team_id, nm))
        pa_slot = (dfs.SLOT_PA_HOME if is_home else dfs.SLOT_PA_AWAY).get(lu["slot"], 4.0)
        hr_rate = (sr["homeRuns"] / sr["plateAppearances"]) if sr and sr.get("plateAppearances") else 0.03
        bb_rate = (sr["baseOnBalls"] / sr["plateAppearances"]) if sr and sr.get("plateAppearances") else 0.08
        ceil = round(proj + 10 * hr_rate * pa_slot, 1)
        # CASH-mode selection nudge, not a changed point estimate: walk rate
        # predicts real-game consistency beyond what mean skill already
        # implies (see BB_FLOOR_WEIGHT's comment). Only the optimizer's cash
        # objective reads this -- proj/ceiling (display, logging, calibration)
        # are untouched.
        floor = round(proj + dfs.BB_FLOOR_WEIGHT * bb_rate * pa_slot, 1)
        confirmed = lu.get("confirmed", True)
        pool.append({"name": lu["name"], "pos": pos, "salary": info["salary"], "proj": proj, "ceiling": ceil,
                     "floor": floor,
                     "team": team_abbr, "game": lu["game"],
                     "opp_team": abbr.get(str(lu.get("opp_team_id")), None),
                     "slot": lu["slot"], "home": is_home, "confirmed": confirmed,
                     "conf": f"H-slot{lu['slot']}" + ("" if confirmed else "*PROJ"),
                     "dk_fppg": info.get("dk_fppg")})

    if exclude_teams:
        pool = [p for p in pool if p["team"] not in exclude_teams]

    # Drop anyone not on their team's active roster (IL, optioned, etc) --
    # catches the case where a stale PROJECTED lineup fallback (today's real
    # lineup not posted yet) reuses a player's last game slot even though
    # they've since gone on IL. See dfs.inactive_players for the validated
    # active-vs-40Man signal (the roster `status` field alone can't be trusted).
    inactive_cache_dir = root / "data/inactive_cache"
    inactive_cache_dir.mkdir(parents=True, exist_ok=True)
    _inactive_memo = {}
    id_by_abbr = {v: k for k, v in abbr.items()}

    def inactive_for_team(team_abbr):
        team_id = id_by_abbr.get(team_abbr)
        if not team_id:
            return set()
        if team_id not in _inactive_memo:
            _inactive_memo[team_id] = dfs.inactive_players(
                team_id, cache_path=str(inactive_cache_dir / f"{team_id}.json"), max_age=21600)
        return _inactive_memo[team_id]

    pool = [p for p in pool if dfs.norm(p["name"]) not in inactive_for_team(p["team"])]

    ph = [p for p in pool if "P" in p["pos"]]
    hh = [p for p in pool if "P" not in p["pos"]]

    stack_team, stack2_team, cash, gpp = None, None, None, None
    if len(ph) >= 2 and len(hh) >= 8:
        team_proj = defaultdict(float)
        for h in hh:
            team_proj[h["team"]] += h["proj"]
        dfs.project_ownership(pool, team_proj)
        # GPP construction, replay-backtested 2026-07-10 over the 8 logged
        # slates x 5 optimizer seeds (plus a 2,782-team-game 2025 stack-shape
        # backtest for the mechanism -- scripts/dfs_stack_shape_backtest.py):
        #   * ownership fade 0.1 -> 0.3 and stack team picked by LEVERAGE
        #     (sum ceiling - 0.3*own) instead of raw projection (= chalk);
        #   * primary stack 4 -> 5 (5-stacks gave P95 79 vs 76, P99 97 vs 96
        #     per 2025 team-game at ~1 pt of mean); DK's own max-5 rule caps it;
        #   * secondary 3-stack from the next-best team (correlated 3 beats 3
        #     scattered one-offs: P99 137.0 vs 131.2, same mean, on 2025 data).
        #   Replay percentile-in-real-field: 59.6% (old shape) -> 74-78% (new),
        #   consistent across all 5 seeds; n=8 slates, so directional evidence
        #   corroborated by the 2025 mechanism tests, not proof on its own.
        for p in pool:
            p["lev"] = round(p.get("ceiling", p["proj"]) - 0.3 * p.get("own", 0), 1)
        team_lev = defaultdict(float)
        for h in hh:
            team_lev[h["team"]] += h.get("ceiling", h["proj"]) - 0.3 * h.get("own", 0)
        rank = sorted(team_lev, key=team_lev.get, reverse=True)
        stack_team = rank[0]
        stack2_team = rank[1] if len(rank) > 1 else None
        cash = dfs_opt.optimize(pool, mode="cash", iters=iters)
        gpp = dfs_opt.optimize(pool, mode="gpp", stack_team=stack_team, stack_n=5, iters=iters,
                               stack2_team=stack2_team, stack2_n=3)

    return {"gid": gid, "is_main": is_main, "meta": meta, "games": meta.get("games"),
            "salaries_n": len(salaries), "skill_n": len(rates), "lineup_hitters_n": len(lineups),
            "pool": pool, "pitchers": ph, "hitters": hh, "stack_team": stack_team,
            "stack2_team": stack2_team,
            "cash": cash, "gpp": gpp, "all_teams": all_teams, "team_status": team_status,
            "excluded_teams": sorted(exclude_teams), "slate_mismatch": slate_mismatch,
            "pitcher_fetch_error": pitcher_fetch_error,
            "spent": client.spent_this_session, "remaining": client.remaining_credits()}


def log_sim_prediction(root: Path, date: str, gid, mode: str, eq: dict) -> None:
    """Append/refresh one row in data/dfs_sim_log.csv -- the simulator's own
    forward-test log (DFS_IMPROVEMENT_PLAN §1.3): every pre-lock sim prediction
    for an entered lineup gets recorded so realized finishes can later be
    checked against the predicted distribution (the exact mechanism that
    validated the mean model). Upserts on (date, gid, mode) like the proj log
    does on date, so re-simming the same build overwrites in place."""
    p = root / "data/dfs_sim_log.csv"
    cols = ("date", "gid", "mode", "n_sims", "field_n", "mean_pct", "median_pct",
            "p_cash", "p_top", "our_mean", "our_p95", "field_p50", "field_p90", "field_p99")
    row = {"date": date, "gid": gid, "mode": mode,
           "n_sims": eq.get("n_sims", ""), "field_n": eq.get("field_n", ""),
           "mean_pct": eq.get("mean_pct"), "median_pct": eq.get("median_pct"),
           "p_cash": eq.get("p_cash"), "p_top": eq.get("p_top"),
           "our_mean": eq.get("our_mean"), "our_p95": eq.get("our_p95"),
           "field_p50": eq.get("field_q", {}).get(50), "field_p90": eq.get("field_q", {}).get(90),
           "field_p99": eq.get("field_q", {}).get(99)}
    prior = [r for r in csv.DictReader(open(p))] if p.exists() else []
    prior = [r for r in prior
             if not (r.get("date") == date and str(r.get("gid")) == str(gid) and r.get("mode") == mode)]
    with p.open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(list(cols))
        for r in prior + [row]:
            w.writerow([r.get(k, "") for k in cols])


def simulate_lineup_vs_field(result, mode="gpp", n_sims=3000, field_size=800, seed=1,
                             log_date: str | None = None, cash_line_pct: float | None = None):
    """Field-equity layer over a finished build_slate() result: simulate the
    whole slate jointly (edge/dfs_sim: correlated hitters, pitcher-vs-stack
    anti-correlation), sample a plausible ownership-driven FIELD, and score
    this build's lineup against it world by world.

    Returns the contest_equity dict (mean/median finish percentile, P(cash),
    P(top 1%), field score quantiles) plus sim disposition data, or an
    {"error": ...} dict when the build has no usable lineup/pool. Free (no
    API calls): player event shapes come from the season_hitting disk cache
    the build already refreshed."""
    from edge import dfs_sim

    lu = (result.get(mode) or {}).get("lineup")
    pool = result.get("pool") or []
    if not lu or not pool:
        return {"error": "no lineup/pool to simulate"}
    ix_by_name = {p["name"]: i for i, p in enumerate(pool)}
    our_ix = []
    for entry in lu:
        # optimize() lineups are (player_dict, slot) pairs
        p = entry[0] if isinstance(entry, (list, tuple)) else entry
        nm = p["name"] if isinstance(p, dict) else str(p)
        if nm in ix_by_name:
            our_ix.append(ix_by_name[nm])
    if len(our_ix) < 9:
        return {"error": f"lineup only matched {len(our_ix)}/10 pool entries"}

    root = Path(__file__).resolve().parents[1]
    season_rates = {}
    try:
        import json as _json
        raw = _json.loads((root / "data/dfs_season_hitting.json").read_text())
        by_norm = {}
        for k, v in raw.items():
            if isinstance(v, dict) and "|" in k:
                by_norm[k.split("|", 1)[1]] = v
        for p in pool:
            if "P" not in p["pos"]:
                sr = by_norm.get(dfs.norm(p["name"]))
                if sr:
                    season_rates[p["name"]] = sr
    except Exception:
        pass  # league-shape fallback inside the sim is fine

    scores, _meta = dfs_sim.simulate_slate(pool, n_sims=n_sims, seed=seed,
                                           season_rates=season_rates)
    import numpy as _np
    field = dfs_sim.generate_field(pool, field_size, rng=_np.random.default_rng(seed))
    if len(field) < 50:
        return {"error": f"field generation produced only {len(field)} lineups"}
    # mode-appropriate payout line: a double-up pays ~44% of the field, a GPP
    # pays ~18-25% (user-confirmed vs real contests, e.g. 285/1136 = 25% on the
    # 7/18 $1K Daily Dollar; 10/23 = 43% on the $1 double-up)
    if cash_line_pct is None:
        cash_line_pct = 0.44 if mode == "cash" else 0.22
    eq = dfs_sim.contest_equity(scores, our_ix, field, cash_line_pct=cash_line_pct)
    eq["n_sims"] = n_sims
    eq["field_n"] = len(field)
    eq["cash_line_pct"] = cash_line_pct
    if log_date:
        try:
            log_sim_prediction(root, log_date, result.get("gid"), mode, eq)
        except Exception:
            pass  # logging must never take down the sim result itself
    return eq


def gpp_candidates(pool, meta_seed=0, n_teams=4, iters=500):
    """Diverse GPP candidate lineups for the sim-EV selector: stack team x
    ownership-fade x stack shape (optimizer seeds are nearly deterministic per
    construction on real pool sizes -- measured 2026-07-19, 5 seeds produced 1
    unique lineup per stack team -- so diversity must come from construction
    parameters). Restores the production fade (0.3) on every pool entry before
    returning, so callers' later optimize() runs see unchanged state."""
    from collections import defaultdict
    team_lev = defaultdict(float)
    for h in pool:
        if "P" not in h["pos"]:
            team_lev[h["team"]] += h.get("ceiling", h["proj"]) - 0.3 * h.get("own", 0.0)
    rank = sorted(team_lev, key=team_lev.get, reverse=True)
    cands, seen = [], set()
    for ti in range(min(n_teams, len(rank))):
        stack_team = rank[ti]
        stack2 = rank[ti + 1] if len(rank) > ti + 1 else (rank[0] if ti else None)
        for fade in (0.1, 0.3):
            for stack_n, s2n in ((5, 3), (4, 0)):
                for p in pool:
                    p["lev"] = round(p.get("ceiling", p["proj"]) - fade * p.get("own", 0.0), 1)
                r = dfs_opt.optimize(pool, mode="gpp", stack_team=stack_team,
                                     stack_n=stack_n, iters=iters,
                                     seed=meta_seed + 17 * ti,
                                     stack2_team=stack2 if s2n else None, stack2_n=s2n)
                if not r:
                    continue
                key = tuple(sorted(p["name"] for p, _s in r["lineup"]))
                if key not in seen:
                    seen.add(key)
                    cands.append(r)
    for p in pool:
        p["lev"] = round(p.get("ceiling", p["proj"]) - 0.3 * p.get("own", 0.0), 1)
    return cands


def gpp_sim_ev_lineup(result, n_sims=4000, seed=1, field_size=800,
                      contest_entries=None, payouts=None, entry_fee=5.0):
    """OPT-IN sim-EV GPP construction (DFS_IMPROVEMENT_PLAN §4): generate
    diverse candidate lineups, simulate ONE shared world-set + field, pick the
    candidate with the best expected payout under a top-heavy GPP curve.

    Replay-validated 2026-07-19 vs the incumbent 5-3 leverage builder on 8 real
    contest slates x 3 seeds: sim-EV mean percentile-in-real-field 53.6% vs
    42.3%, wins 5 slates / loses 2 / ties 1 -- directionally positive but
    paired t=0.80 at n=8, NOT significant, so this does NOT replace the
    default GPP construction; it's an opt-in whose forward results accumulate
    via log_sim_prediction. Returns the winning optimize()-shaped dict with
    ev/mean_pct/p_top1/n_cands added, or {"error": ...}."""
    from edge import dfs_sim
    import numpy as _np

    pool = result.get("pool") or []
    if sum(1 for p in pool if "P" in p["pos"]) < 2:
        return {"error": "need >=2 pitchers in pool"}
    cands = gpp_candidates(pool, meta_seed=seed)
    if not cands:
        return {"error": "no candidate lineups could be built"}
    gpp = result.get("gpp")
    if gpp:
        keys = {tuple(sorted(p["name"] for p, _s in c["lineup"])) for c in cands}
        if tuple(sorted(p["name"] for p, _s in gpp["lineup"])) not in keys:
            cands.append(gpp)

    root = Path(__file__).resolve().parents[1]
    season_rates = {}
    try:
        import json as _json
        raw = _json.loads((root / "data/dfs_season_hitting.json").read_text())
        by_norm = {dfs.norm(k.split("|", 1)[1]): v for k, v in raw.items()
                   if isinstance(v, dict) and "|" in k}
        season_rates = {p["name"]: by_norm.get(dfs.norm(p["name"]))
                        for p in pool if "P" not in p["pos"]}
    except Exception:
        pass

    scores, _m = dfs_sim.simulate_slate(pool, n_sims=n_sims, seed=seed,
                                        season_rates=season_rates)
    field = dfs_sim.generate_field(pool, field_size, rng=_np.random.default_rng(seed + 11))
    if len(field) < 50:
        return {"error": f"field generation produced only {len(field)} lineups"}
    entries = contest_entries or max(2 * field_size, 200)
    pays = payouts or dfs_sim.synthetic_gpp_payouts(entries, entry_fee=entry_fee)
    ix_by_name = {p["name"]: i for i, p in enumerate(pool)}
    cand_ix = [[ix_by_name[p["name"]] for p, _s in c["lineup"]] for c in cands]
    best_i, evs = dfs_sim.pick_by_sim_ev(scores, cand_ix, field, pays, entries)
    out = dict(cands[best_i])
    out.update(evs[best_i])
    out["n_cands"] = len(cands)
    return out
