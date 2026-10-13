"""DK College Basketball Classic: the NBA pipeline under college's Spec.

Everything runs through edge/dfs_run_nba.py's build_slate_for -- same
simulator, same theories, same optimiser search -- because the scoring is the
same DK basketball table. What differs is all data, and it is all here:

  * ROSTER  G G G F F F UTIL UTIL (edge/ncaab.py), no centre slot.
  * PROPS ARE THIN. Books post college player props for the top few players
    of the televised games. So the board is mostly DK's own FPPG, scaled by
    tonight's implied team points, and -- unlike the NBA -- DK's SALARY is a
    last resort (a college rotation player a week into the season has no FPPG
    yet, and DK's price is the only projection there is). Every player's
    source is on the board: `props`, `fppg` or `salary`.
  * TEAM NAMES DO NOT MATCH. "DUKE" against "Duke Blue Devils" -- see
    edge/ncaab.py. A game no book line can be matched to is simulated at the
    league average and named on the page; nothing is dropped for it.
  * THE GAME LINE MATTERS MORE than in the NBA: for an FPPG-projected player
    it is the only thing that knows about tonight.

SALARY_VALUE IS A PRIOR: 4.6 DK points per $1K, the value a DK college
salary implies for a rotation player (DK's own pricing runs ~4.5-5x). Fit it
on the first slate's actuals with scripts/nba_calibration.py --sport ncaab.
"""
from __future__ import annotations

from edge import dfs_nba_theory as theory, dfs_opt_nba, nba_sim, ncaab
from edge.dfs_run_nba import (NCAAB_PROFILES, Spec, _ordered, build_slate_for,
                              classic_groups_for, default_slate, resolve_slate_for)

__all__ = ["SPEC", "SPORT", "classic_groups", "resolve_slate", "build_slate", "default_slate"]


def _team(code):
    return (str(code).strip().upper() or None) if code else None


SPEC = Spec(name="ncaab", sport="basketball_ncaab", dk_sport=ncaab.DK_SPORT,
            classic_default=None, league=nba_sim.NCAAB, roster=dfs_opt_nba.NCAAB,
            group_slots=theory.NCAAB_GROUP_SLOTS, profiles=NCAAB_PROFILES,
            positions=lambda s: _ordered(s, ncaab.positions), slots_for=ncaab.slots_for,
            team=_team, team_from_name=None,
            # 10.7: college margin vs the closing spread (nba_sim's eff_sd prior).
            margin_sd=10.7, min_fppg=6.0, salary_value=4.6, salary_floor_mult=1.15)
SPORT = SPEC.sport


def classic_groups(groups: list[dict] | None = None) -> list[dict]:
    """College Classic slates. CBB's Classic GameTypeId has never been read
    live, so there is no default: Classic is whatever the lobby's own
    GameTypes list NAMES "Classic". If the lobby names none, the fallback is
    every multi-game group, which is only ever wrong by including a Tiers or
    snake contest -- the page shows each group's label, and DK's lobby has
    rarely offered either for college."""
    from edge import dfs
    groups = groups if groups is not None else dfs.draft_groups(SPEC.dk_sport)
    rows = classic_groups_for(SPEC, groups)
    if rows or dfs.classic_game_types(SPEC.dk_sport):
        return rows
    out = []
    for g in groups:
        if (g.get("GameCount") or 0) < 2:
            continue
        suffix = (g.get("ContestStartTimeSuffix") or "").strip()
        out.append({"gid": g.get("DraftGroupId"), "games": g.get("GameCount") or 0,
                    "label": suffix.strip("()") or "Main", "start": g.get("StartDate"),
                    "featured": g.get("DraftGroupTag") == "Featured", "unverified_type": True})
    out.sort(key=lambda r: (r["start"] or "", -r["games"]))
    return out


def resolve_slate(draft_group=None, groups: list[dict] | None = None):
    if draft_group:
        return resolve_slate_for(SPEC, draft_group, groups)
    rows = classic_groups(groups)
    if not rows:
        return None, {"error": "DraftKings is listing no college basketball Classic slates."}
    pick = rows[default_slate(rows)]
    return pick["gid"], pick


def build_slate(draft_group=None, client=None, **kw) -> dict:
    groups = kw.pop("groups", None)
    if draft_group is None:
        from edge import dfs
        groups = groups if groups is not None else dfs.draft_groups(SPEC.dk_sport)
        gid, meta = resolve_slate(None, groups)
        if gid is None:
            return {"error": meta.get("error"), "slates": classic_groups(groups)}
        draft_group = gid
    res = build_slate_for(SPEC, draft_group, client, groups=groups, **kw)
    if groups is not None:
        rows = classic_groups(groups)
        res["slates"] = res.get("slates") or rows
        match = next((r for r in rows if r["gid"] == int(draft_group)), None)
        if match and (res.get("meta") or {}).get("label") == "(explicit)":
            res["meta"] = match
    return res
