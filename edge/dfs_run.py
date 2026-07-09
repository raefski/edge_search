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
                     cash: dict | None, gpp: dict | None) -> dict:
    """Persist a build to disk for forward-testing, regardless of whether the
    CLI or the phone app produced it — a single source of truth so scripts/dfs_grade.py
    always has something to grade.

    * data/dfs_proj_log.csv: every hitter/pitcher projection for `date` (only for
      the MAIN slate — a sub-slate like Turbo/Night must not clobber it with a
      smaller player set). Re-running for the same date overwrites that date's
      rows in place; other dates are untouched.
    * data/dfs_lineups_<date>[_g<gid>].csv: the built CASH/GPP lineups, if any.

    Returns {"logged_projections": bool, "n": int, "lineup_file": str|None}.
    """
    result = {"logged_projections": False, "n": 0, "lineup_file": None}
    (root / "data").mkdir(parents=True, exist_ok=True)

    if is_main:
        plog = root / "data/dfs_proj_log.csv"
        prior = [r for r in csv.DictReader(open(plog))] if plog.exists() else []
        cols = ("date", "player", "team", "pos", "salary", "proj", "ceiling", "own", "conf", "dk_fppg")
        with plog.open("w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(list(cols))
            for r in prior:
                if r["date"] != date:
                    w.writerow([r.get(k, "") for k in cols])
            for p in pool:
                w.writerow([date, p["name"], p["team"], "/".join(sorted(p["pos"])), p["salary"],
                            p["proj"], p.get("ceiling"), p.get("own", ""), p["conf"], p.get("dk_fppg", "")])
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
    t = dfs._get("https://statsapi.mlb.com/api/v1/teams?sportId=1")["teams"]
    return {str(x["id"]): x["abbreviation"] for x in t}


def resolve_slate(draft_group, groups=None):
    """Return (gid, is_main, meta). meta carries a human label / error info.

    draft_group=None -> auto main slate. A name (Main/Early/Turbo/Night/...) or a
    numeric id resolves via edge.dfs. On a bad name, meta['error'] is set and
    meta['available'] lists slates open now.
    """
    groups = groups if groups is not None else dfs.mlb_draft_groups()
    is_main = draft_group is None or str(draft_group).strip().lower() in ("main", "classic", "full")
    if draft_group is None:
        return dfs.main_slate_group(groups), True, {"label": "Main (auto)"}
    g = dfs.resolve_draft_group(draft_group)
    if not g:
        names = sorted({n for n, *_ in dfs.list_slate_names(groups)})
        return None, is_main, {"error": f"slate {draft_group!r} not found", "available": names}
    return g["DraftGroupId"], is_main, {
        "label": f"{draft_group} -> group {g['DraftGroupId']}",
        "start": g.get("StartDate", "")[:16], "games": g.get("GameCount"),
    }


def build_slate(client, date, draft_group=None, iters=800):
    """Run the full pipeline for one slate and return a structured result dict.

    Keys: gid, is_main, meta, salaries_n, skill_n, lineup_hitters_n, pool,
    pitchers, hitters, stack_team, cash, gpp, spent, remaining. When the slate
    isn't priced yet: {'unpriced': True, 'upcoming': [...]}. On a bad slate name:
    {'error': ..., 'available': [...]}.
    """
    groups = dfs.mlb_draft_groups()
    gid, is_main, meta = resolve_slate(draft_group, groups)
    if gid is None:
        return {"error": meta.get("error"), "available": meta.get("available", [])}

    salaries = dfs.fetch_draftables(gid)
    if not salaries:
        return {"unpriced": True, "gid": gid, "is_main": is_main, "meta": meta,
                "upcoming": dfs.list_slate_names(groups)[:12]}

    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    yr = int(date[:4])
    # include the CURRENT season (not just prior completed ones) -- backtested
    # 2026-07-08 on 5,146 held-out 2025 hitter-games: MAE 5.604->5.577, corr
    # +0.166->+0.181 vs freezing on the two seasons before the current one.
    # max_age so the cache actually refreshes as the season progresses instead
    # of freezing on whatever was true the first time it was pulled.
    skill_seasons = (yr - 2, yr - 1, yr)
    rates, lg = dfs.pooled_skill_rates(skill_seasons, cache_path=str(root / f"data/dfs_skill_{'_'.join(map(str, skill_seasons))}.json"),
                                       max_age=21600)
    park = dfs.park_runs(yr)
    k9 = dfs.pitcher_k9((yr - 1, yr), cache_path=str(root / f"data/dfs_pitch_k9_{yr-1}_{yr}.json"), max_age=21600)
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

    # --- pitchers: from props (PAID; skipped when uncached in dry-run) ---
    for ev in client.get_events(SPORT):
        try:
            pp = client.get_event_odds(SPORT, ev["id"], dfs.P_MARKETS, "us")
        except DryRunBlocked:
            continue
        except Exception:
            # A single event's odds call failing (the Odds API 404s an event with
            # no markets posted yet, or a transient 429/500/timeout) must not take
            # down the whole build — skip that one event and keep going.
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
            proj = dfs.project_pitcher(dfs.player_markets(dkp, nm))["proj"]
            if proj is None:
                continue
            pool.append({"name": nm, "pos": {"P"}, "salary": info["salary"], "proj": proj,
                         "ceiling": proj, "floor": proj,  # no validated pitcher-specific floor signal yet
                         "team": info["team"], "game": info["game"],
                         "opp_team": team_opp_abbr.get(info["team"]), "conf": "P-prop",
                         "dk_fppg": info.get("dk_fppg")})

    # --- hitters: skill model over confirmed lineups (FREE) ---
    bullpen_cache_dir = root / "data/bullpen_cache"
    bullpen_cache_dir.mkdir(parents=True, exist_ok=True)
    _bullpen_memo = {}

    def bullpen_for(team_id):
        if team_id not in _bullpen_memo:
            _bullpen_memo[team_id] = dfs.team_pitcher_stats(
                team_id, (yr - 1, yr), cache_path=str(bullpen_cache_dir / f"{team_id}.json"), max_age=21600)
        return _bullpen_memo[team_id]

    for nm, lu in lineups.items():
        info = salaries.get(nm)
        if not info or not info.get("salary"):
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
        proj = dfs.project_hitter_skill(skill, lu["slot"], pk, matchup_k9)
        sr = hr_season.get(nm)
        pa_slot = dfs.SLOT_PA.get(lu["slot"], 4.2)
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
                     "team": abbr.get(str(lu["team_id"]), str(lu["team_id"])), "game": lu["game"],
                     "opp_team": abbr.get(str(lu.get("opp_team_id")), None),
                     "slot": lu["slot"], "confirmed": confirmed,
                     "conf": f"H-slot{lu['slot']}" + ("" if confirmed else "*PROJ"),
                     "dk_fppg": info.get("dk_fppg")})

    ph = [p for p in pool if "P" in p["pos"]]
    hh = [p for p in pool if "P" not in p["pos"]]

    stack_team, cash, gpp = None, None, None
    if len(ph) >= 2 and len(hh) >= 8:
        team_proj = defaultdict(float)
        for h in hh:
            team_proj[h["team"]] += h["proj"]
        stack_team = max(team_proj, key=team_proj.get)
        dfs.project_ownership(pool, team_proj)
        for p in pool:
            p["lev"] = round(p.get("ceiling", p["proj"]) - 0.1 * p.get("own", 0), 1)
        cash = dfs_opt.optimize(pool, mode="cash", iters=iters)
        gpp = dfs_opt.optimize(pool, mode="gpp", stack_team=stack_team, stack_n=4, iters=iters)

    return {"gid": gid, "is_main": is_main, "meta": meta,
            "salaries_n": len(salaries), "skill_n": len(rates), "lineup_hitters_n": len(lineups),
            "pool": pool, "pitchers": ph, "hitters": hh, "stack_team": stack_team,
            "cash": cash, "gpp": gpp,
            "spent": client.spent_this_session, "remaining": client.remaining_credits()}
