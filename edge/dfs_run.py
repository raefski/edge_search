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

from collections import defaultdict

from edge.client import DryRunBlocked
from edge import dfs, dfs_opt

SPORT = "baseball_mlb"


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
    rates, lg = dfs.pooled_skill_rates((2024, 2025), cache_path=str(root / "data/dfs_skill_2024_2025.json"))
    park = dfs.park_runs(int(date[:4]))
    k9 = dfs.pitcher_k9(2025, cache_path=str(root / "data/dfs_pitch_k9_2025.json"))
    hr_season = dfs.season_hitting(cache_path=str(root / "data/dfs_season_hitting.json"))
    lineups = dfs.lineups_for_date(date)
    abbr = team_abbrev_map()

    pool = []

    # --- pitchers: from props (PAID; skipped when uncached in dry-run) ---
    for ev in client.get_events(SPORT):
        try:
            pp = client.get_event_odds(SPORT, ev["id"], dfs.P_MARKETS, "us")
        except DryRunBlocked:
            continue
        dkp = next((b for b in pp.get("bookmakers", []) if b["key"] == "draftkings"), None)
        if not dkp:
            continue
        for nm in {o["description"] for m in dkp["markets"] for o in m["outcomes"] if o.get("description")}:
            info = salaries.get(dfs.norm(nm))
            if not info or not info.get("salary") or "P" not in dfs.parse_pos(info["position"]):
                continue
            proj = dfs.project_pitcher(dfs.player_markets(dkp, nm))["proj"]
            if proj is None:
                continue
            pool.append({"name": nm, "pos": {"P"}, "salary": info["salary"], "proj": proj,
                         "ceiling": proj, "team": info["team"], "game": info["game"], "conf": "P-prop"})

    # --- hitters: skill model over confirmed lineups (FREE) ---
    for nm, lu in lineups.items():
        info = salaries.get(nm)
        if not info or not info.get("salary"):
            continue
        pos = dfs.parse_pos(info["position"])
        if not pos or "P" in pos:
            continue
        skill = rates.get(str(lu["id"]), lg)
        pk = park.get(str(lu["park_team_id"]), 1.0)
        proj = dfs.project_hitter_skill(skill, lu["slot"], pk, k9.get(str(lu["opp_pitcher_id"])))
        sr = hr_season.get(nm)
        hr_rate = (sr["homeRuns"] / sr["plateAppearances"]) if sr and sr.get("plateAppearances") else 0.03
        ceil = round(proj + 10 * hr_rate * dfs.SLOT_PA.get(lu["slot"], 4.2), 1)
        pool.append({"name": lu["name"], "pos": pos, "salary": info["salary"], "proj": proj, "ceiling": ceil,
                     "team": abbr.get(str(lu["team_id"]), str(lu["team_id"])), "game": lu["game"],
                     "slot": lu["slot"], "conf": f"H-slot{lu['slot']}"})

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
