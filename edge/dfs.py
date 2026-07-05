"""DFS (DraftKings MLB Classic) core: salary fetch + Vegas-implied projections.

The projection edge: DFS salaries are FROZEN for the slate (no sharp correction),
while sportsbook props are the sharpest live player projection available. Convert
props -> projected DK fantasy points, divide by salary -> value. We're competing
with other DFS players + a stale salary, not the book.

DK MLB Classic: roster 2 P / C / 1B / 2B / 3B / SS / 3 OF, $50,000 cap.
"""
from __future__ import annotations

import json
import urllib.request
from statistics import NormalDist

from .oddsmath import devig

_N = NormalDist()
_UA = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}

# DK MLB pitcher scoring (per-out = 2.25/inning ÷ 3). CG/NH ignored (~0 prob).
P_SCORE = {"out": 0.75, "K": 2.0, "win": 4.0, "ER": -2.0, "hit": -0.6, "bb": -0.6}
# typical std devs for turning a single over/under line into an implied mean
P_SIGMA = {"pitcher_outs": 4.0, "pitcher_strikeouts": 2.0, "pitcher_earned_runs": 2.0,
           "pitcher_hits_allowed": 2.5, "pitcher_walks": 1.2}
P_MARKETS = list(P_SIGMA) + ["pitcher_record_a_win"]


def norm(name: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    return "".join(c for c in s.lower() if c.isalnum())


def _get(url):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=30))


# --- salaries (public draftables API; no auth) -------------------------------

def mlb_draft_groups() -> list[dict]:
    return _get("https://www.draftkings.com/lobby/getcontests?sport=MLB").get("DraftGroups", [])


def fetch_draftables(draft_group_id: int) -> dict[str, dict]:
    """{normalized name: {name, salary, position, team}} for a draft group."""
    url = f"https://api.draftkings.com/draftgroups/v1/draftgroups/{draft_group_id}/draftables"
    out = {}
    for p in _get(url).get("draftables", []):
        k = norm(p["displayName"])
        if k not in out:  # dedupe multi-slot rows
            comp = p.get("competition") or {}
            out[k] = {"name": p["displayName"], "salary": p.get("salary"),
                      "position": p.get("position"), "team": p.get("teamAbbreviation"),
                      "game": comp.get("competitionId"), "matchup": comp.get("name"),
                      "start": comp.get("startTime")}
    return out


def parse_pos(s: str) -> set:
    """DraftKings eligible slots from a 'C/1B' style string. SP/RP -> P."""
    out = set()
    for tok in (s or "").split("/"):
        t = tok.strip().upper()
        if t in ("SP", "RP"):
            out.add("P")
        elif t in ("C", "1B", "2B", "3B", "SS", "OF"):
            out.add(t)
    return out


def player_markets(dk_bookmaker: dict, player_name: str) -> dict:
    """{market_key: {side: price, 'point': x}} for one player from a DK payload."""
    out = {}
    for m in dk_bookmaker.get("markets", []):
        d = {}
        for o in m.get("outcomes", []):
            if o.get("description") == player_name:
                d[o["name"]] = o["price"]; d["point"] = o.get("point")
        if d:
            out[m["key"]] = d
    return out


def main_slate_group(groups: list[dict]) -> int | None:
    """Pick the standard full-slate Classic: no special suffix (Showdown/Tiers/
    Snake/season-long all carry a suffix and many are unpriced), soonest start,
    most games."""
    cands = [g for g in groups if not (g.get("ContestStartTimeSuffix") or "").strip()] or groups
    cands.sort(key=lambda g: ((g.get("StartDate") or "9999")[:10], -(g.get("GameCount") or 0)))
    return cands[0].get("DraftGroupId") if cands else None


# --- pitcher projection from props -------------------------------------------

def _mean(over_dec, under_dec, line, sigma):
    p = devig([over_dec, under_dec])[0]
    p = min(max(p, 1e-3), 1 - 1e-3)
    return line + sigma * _N.inv_cdf(p)


# league per-IP rates, to impute components DK didn't post (keeps pitchers comparable)
LEAGUE_PER_IP = {"ER": 4.10 / 9, "hit": 8.4 / 9, "bb": 3.2 / 9}
WIN_DEFAULT = 0.43


def project_pitcher(pmkts: dict) -> dict:
    """Requires the core markets (outs + strikeouts); imputes any missing
    ER/hits/walks/win from projected innings so every pitcher is scored on the
    same components. Returns proj pts, breakdown, and which fields were imputed."""
    o, k = pmkts.get("pitcher_outs"), pmkts.get("pitcher_strikeouts")
    if not (o and "Over" in o and k and "Over" in k):
        return {"proj": None, "components": {}, "have": [], "imputed": []}
    outs = _mean(o["Over"], o["Under"], o["point"], P_SIGMA["pitcher_outs"])
    Km = _mean(k["Over"], k["Under"], k["point"], P_SIGMA["pitcher_strikeouts"])
    ip = outs / 3.0
    comp = {"out": round(P_SCORE["out"] * outs, 1), "K": round(P_SCORE["K"] * Km, 1)}
    imputed = []
    # skill factor from strikeout-implied K/9: aces suppress ER/hits below league
    # average, so scale IMPUTED negatives down for them (backtest fix: removed the
    # systematic under-projection of high-end arms).
    k9 = 27 * Km / outs if outs else 8.5
    sf = min(1.35, max(0.55, 1 - 0.5 * (k9 - 8.5) / 8.5))

    for mk, key in (("pitcher_earned_runs", "ER"), ("pitcher_hits_allowed", "hit"),
                    ("pitcher_walks", "bb")):
        d = pmkts.get(mk)
        if d and "Over" in d and "Under" in d:
            m = _mean(d["Over"], d["Under"], d["point"], P_SIGMA[mk])
        else:
            m = ip * LEAGUE_PER_IP[key] * (sf if key in ("ER", "hit") else 1.0)
            imputed.append(key)
        comp[key] = round(P_SCORE[key] * m, 1)

    w = pmkts.get("pitcher_record_a_win")
    if w and "Yes" in w and "No" in w:
        pw = devig([w["Yes"], w["No"]])[0]
    else:
        pw = WIN_DEFAULT; imputed.append("win")
    comp["win"] = round(P_SCORE["win"] * pw, 1)

    return {"proj": round(sum(comp.values()), 1), "components": comp,
            "have": [x for x in comp if x not in imputed], "imputed": imputed}


# === HITTERS ================================================================
import os as _os
import time as _time

H_SIGMA = {"batter_hits": 0.9, "batter_total_bases": 1.3, "batter_rbis": 0.9,
           "batter_runs_scored": 0.85, "batter_walks": 0.7, "batter_stolen_bases": 0.6}
B_MARKETS = list(H_SIGMA)
TEAM_TOTAL_AVG = 4.3
# league per-PA fallbacks when a hitter has no season data
_LG = {"hits": 0.24, "totalBases": 0.40, "rbi": 0.115, "runs": 0.125,
       "baseOnBalls": 0.085, "stolenBases": 0.018, "homeRuns": 0.032}


def season_hitting(season: int = 2026, cache_path: str | None = None, max_age=86400) -> dict:
    """{norm name: season hitting totals} from MLB statsapi (free). Cached to disk."""
    if cache_path and _os.path.exists(cache_path) and _time.time() - _os.path.getmtime(cache_path) < max_age:
        return json.load(open(cache_path))
    out = {}
    for t in _get("https://statsapi.mlb.com/api/v1/teams?sportId=1")["teams"]:
        try:
            r = _get(f"https://statsapi.mlb.com/api/v1/teams/{t['id']}/roster?rosterType=active"
                     f"&hydrate=person(stats(group=[hitting],type=[season],season={season}))")
        except Exception:
            continue
        for p in r.get("roster", []):
            per = p["person"]
            for s in per.get("stats", []):
                sp = s.get("splits", [])
                if sp and "plateAppearances" in sp[0]["stat"]:
                    x = sp[0]["stat"]
                    out[norm(per["fullName"])] = {k: x.get(k, 0) for k in
                        ("plateAppearances", "homeRuns", "totalBases", "hits", "rbi", "runs", "baseOnBalls", "stolenBases")}
                    break
    if cache_path:
        json.dump(out, open(cache_path, "w"))
    return out


def _rate(sr, key, pa):
    if sr and sr.get("plateAppearances"):
        return sr.get(key, 0) / sr["plateAppearances"] * pa
    return _LG[key] * pa


def _hmean(d, sigma, default):
    if d and "Over" in d and "Under" in d:
        return _mean(d["Over"], d["Under"], d["point"], sigma)
    return default


def _hitter_points(hits, tb, hr, rbi, runs, bb, sb):
    hits = max(hits, hr); tb = max(tb, hits)          # keep hits>=hr, tb>=hits
    D = max(0.0, tb - hits - 3 * hr)                  # doubles (triples folded in)
    S = max(0.0, hits - D - hr)                       # singles
    return 3 * S + 5 * D + 10 * hr + 2 * rbi + 2 * runs + 2 * bb + 5 * sb


def project_hitter(hmkts: dict, sr: dict, pa: float = 4.3) -> dict:
    """Prop where DK posts it (sharp), season-rate where not (HR is always
    season — DK posts no HR prop)."""
    hits = _hmean(hmkts.get("batter_hits"), H_SIGMA["batter_hits"], _rate(sr, "hits", pa))
    tb = _hmean(hmkts.get("batter_total_bases"), H_SIGMA["batter_total_bases"], _rate(sr, "totalBases", pa))
    rbi = _hmean(hmkts.get("batter_rbis"), H_SIGMA["batter_rbis"], _rate(sr, "rbi", pa))
    runs = _hmean(hmkts.get("batter_runs_scored"), H_SIGMA["batter_runs_scored"], _rate(sr, "runs", pa))
    bb = _hmean(hmkts.get("batter_walks"), H_SIGMA["batter_walks"], _rate(sr, "baseOnBalls", pa))
    sb = _hmean(hmkts.get("batter_stolen_bases"), H_SIGMA["batter_stolen_bases"], _rate(sr, "stolenBases", pa))
    hr = _rate(sr, "homeRuns", pa)
    backed = sum(1 for m in ("batter_hits", "batter_rbis", "batter_runs_scored", "batter_walks") if hmkts.get(m))
    return {"proj": round(_hitter_points(hits, tb, hr, rbi, runs, bb, sb), 1),
            "prop_backed": backed, "hr_pts": round(10 * hr, 1)}


def allocate_hitter(sr: dict, team_total: float | None, pa: float = 4.3) -> dict | None:
    """No props at all: project from season rate, scaling run/RBI by team context."""
    if not sr or not sr.get("plateAppearances"):
        return None
    tf = (team_total or TEAM_TOTAL_AVG) / TEAM_TOTAL_AVG
    hits, tb, hr = _rate(sr, "hits", pa), _rate(sr, "totalBases", pa), _rate(sr, "homeRuns", pa)
    rbi, runs = _rate(sr, "rbi", pa) * tf, _rate(sr, "runs", pa) * tf
    bb, sb = _rate(sr, "baseOnBalls", pa), _rate(sr, "stolenBases", pa)
    return {"proj": round(_hitter_points(hits, tb, hr, rbi, runs, bb, sb), 1), "prop_backed": 0}


# === OWNERSHIP (modeled; real projected ownership needs a paid feed) =========
# Industry drivers: value (dominant) > salary tier > offense environment > order
# > recency/news. Expressed as % of lineups rostering the player, so each
# position normalizes to (roster slots x 100%). We power-softmax appeal within
# position -- the no-training-data stand-in for a field-construction simulation.
_OWN_SLOTS = {"P": 2, "C": 1, "1B": 1, "2B": 1, "3B": 1, "SS": 1, "OF": 3}


def project_ownership(pool: list[dict], team_proj: dict | None = None, gamma: float = 3.5,
                      pitcher_gamma: float = 6.0, bo_exp: float = 3.0) -> list[dict]:
    # pitcher_gamma > gamma: on a full slate the field jams the top 1-2 arms far
    # harder than it clusters hitters. Calibrated to real 6/30 ownership (elite SP
    # hit ~36-48%, which a hitter-level gamma badly under-predicted).
    avg_team = (sum(team_proj.values()) / len(team_proj)) if team_proj else None
    for p in pool:
        val = p["proj"] / (p["salary"] / 1000.0) if p.get("salary") else 0.0
        f = 1.0
        if p["salary"] <= 3500 and p["proj"] > 3:        # min-priced value = punt chalk
            f *= 1.25
        elif p["salary"] >= 9000:                        # studs draw name ownership
            f *= 1.08
        if "P" not in p["pos"] and team_proj and avg_team:  # hitters: people stack good offenses
            # NOTE: the team-stack LEVEL effect is real & huge (7/1: top stack 147% vs 37% median),
            # but a stronger multiplier here FAILED verification on 6/30 anchors (MAE 4.3->7.7): it
            # amplifies value-order within a stack, while the field orders by batting slot/name.
            # Fixing needs a batting-order-aware term + more multi-slate ownership. Kept mild.
            f *= max(0.7, min(1.4, team_proj.get(p["team"], avg_team) / avg_team))
        if "P" not in p["pos"] and p.get("slot"):   # batting order: field heavily favors top-of-order.
            f *= (SLOT_PA[p["slot"]] / 4.2) ** bo_exp   # cross-validated on 6/30 (222 players): MAE 3.82->3.65
        p["_appeal"] = max(0.01, val * f)

    from collections import defaultdict
    grp = defaultdict(list)
    for p in pool:
        prim = next((s for s in ("P", "C", "1B", "2B", "3B", "SS", "OF") if s in p["pos"]), None)
        if prim:
            grp[prim].append(p)
    for pos, players in grp.items():
        g = pitcher_gamma if pos == "P" else gamma
        denom = sum(x["_appeal"] ** g for x in players)
        for x in players:
            raw = _OWN_SLOTS[pos] * 100 * (x["_appeal"] ** g) / denom if denom else 0
            x["own"] = round(min(raw, 65.0), 1)   # cap the chalkiest
    return pool


# === ACTUAL DK POINTS (for forward calibration: proj vs actual) =============
def ip_to_outs(ip) -> int:
    try:
        w = int(float(ip)); frac = round((float(ip) - w) * 10)
        return w * 3 + frac
    except Exception:
        return 0


def actual_hitter_points(b: dict) -> float:
    g = lambda k: b.get(k, 0) or 0
    singles = g("hits") - g("doubles") - g("triples") - g("homeRuns")
    return (3 * singles + 5 * g("doubles") + 8 * g("triples") + 10 * g("homeRuns")
            + 2 * g("rbi") + 2 * g("runs") + 2 * g("baseOnBalls") + 2 * g("hitByPitch")
            + 5 * g("stolenBases"))


def actual_pitcher_points(p: dict, won: bool = False) -> float:
    g = lambda k: p.get(k, 0) or 0
    return (0.75 * ip_to_outs(p.get("inningsPitched", "0")) + 2 * g("strikeOuts")
            + (4 if won else 0) - 2 * g("earnedRuns") - 0.6 * g("hits") - 0.6 * g("baseOnBalls"))


# === SKILL RATES (leakage-safe: prior-season DK pts per PA, the differentiator) ===
def skill_rates(season: int, min_pa: int = 80, cache_path: str | None = None) -> tuple[dict, float]:
    """({playerId: dk_pts_per_PA}, league_avg_dkpp) from a full prior season.
    Players below min_pa fall back to league average (unreliable small samples)."""
    if cache_path and _os.path.exists(cache_path):
        d = json.load(open(cache_path))
        return {k: v for k, v in d["rates"].items()}, d["lg"]
    url = (f"https://statsapi.mlb.com/api/v1/stats?stats=season&season={season}"
           "&group=hitting&sportId=1&limit=3000&playerPool=All")
    splits = _get(url)["stats"][0]["splits"]
    rates, tot_pts, tot_pa = {}, 0.0, 0
    for s in splits:
        st = s["stat"]; pa = st.get("plateAppearances", 0) or 0
        if pa < 1:
            continue
        pts = actual_hitter_points(st)
        tot_pts += pts; tot_pa += pa
        if pa >= min_pa:
            rates[str(s["player"]["id"])] = pts / pa
    lg = tot_pts / tot_pa if tot_pa else 1.4
    if cache_path:
        json.dump({"rates": rates, "lg": lg}, open(cache_path, "w"))
    return rates, lg


# === PRODUCTION HITTER MODEL (skill x opportunity x park x matchup x team-total) ===
# Backtested out-of-sample on ~19k 2025 hitter-games: corr 0.16 & monotonic ranking,
# vs the flat prop-only model's 0.02. Skill is the dominant signal; matchup/park/total
# are smaller multipliers. team_total is production-only (not backtestable from cache).
SLOT_PA = {1: 4.65, 2: 4.55, 3: 4.45, 4: 4.35, 5: 4.25, 6: 4.15, 7: 4.05, 8: 3.95, 9: 3.85}
LG_K9 = 8.6


def pooled_skill_rates(seasons=(2024, 2025), min_pa: int = 120, cache_path: str | None = None) -> tuple[dict, float]:
    """Leakage-safe skill: pooled DK-pts-per-PA over PRIOR seasons. Pool PA across
    years for stability; players below min_pa fall back to league average."""
    if cache_path and _os.path.exists(cache_path):
        d = json.load(open(cache_path)); return d["rates"], d["lg"]
    pts, pa = {}, {}
    for yr in seasons:
        try:
            sp = _get(f"https://statsapi.mlb.com/api/v1/stats?stats=season&season={yr}"
                      "&group=hitting&sportId=1&limit=3000&playerPool=All")["stats"][0]["splits"]
        except Exception:
            continue
        for s in sp:
            st = s["stat"]; a = st.get("plateAppearances", 0) or 0
            if a < 1:
                continue
            pid = str(s["player"]["id"])
            pts[pid] = pts.get(pid, 0) + actual_hitter_points(st); pa[pid] = pa.get(pid, 0) + a
    tot_p = sum(pts.values()); tot_a = sum(pa.values())
    rates = {pid: pts[pid] / pa[pid] for pid in pts if pa[pid] >= min_pa}
    lg = tot_p / tot_a if tot_a else 1.7
    if cache_path:
        json.dump({"rates": rates, "lg": lg}, open(cache_path, "w"))
    return rates, lg


def park_runs(year: int) -> dict:
    """{team_id(str): run index/100} from park_factors.json (3yr rolling). Prefers
    the copy vendored into this repo's data/ (so it works on Streamlit Cloud where
    the strikeouts path doesn't exist), then the local strikeouts file."""
    _here = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    pf = None
    for _p in (_os.path.join(_here, "data", "park_factors.json"),
               "/home/asr/Downloads/strikeouts/data/park_factors.json"):
        try:
            pf = json.load(open(_p)); break
        except Exception:
            continue
    if pf is None:
        return {}
    return {str(v["main_team_id"]): int(v["index_runs"]) / 100.0
            for v in pf.get(str(year), []) if v.get("index_runs")}


def pitcher_k9(season: int, cache_path: str | None = None) -> dict:
    """{pid(str): K/9} for opposing-SP matchup."""
    if cache_path and _os.path.exists(cache_path):
        return json.load(open(cache_path))
    out = {}
    try:
        sp = _get(f"https://statsapi.mlb.com/api/v1/stats?stats=season&season={season}"
                  "&group=pitching&sportId=1&limit=2000&playerPool=All")["stats"][0]["splits"]
    except Exception:
        return out
    for s in sp:
        st = s["stat"]
        try:
            ipf = float(st.get("inningsPitched"))
        except (TypeError, ValueError):
            continue
        if ipf >= 30:
            out[str(s["player"]["id"])] = 9 * (st.get("strikeOuts", 0) or 0) / ipf
    if cache_path:
        json.dump(out, open(cache_path, "w"))
    return out


def project_hitter_skill(skill: float, slot: int, park: float = 1.0,
                         opp_k9: float | None = None, team_total: float | None = None,
                         w_match: float = 0.3) -> float:
    """DK fantasy points = skill(DKpts/PA) x PA(slot) x park x matchup x team-env."""
    pa = SLOT_PA.get(slot, 4.2)
    of = 1.0
    if opp_k9:
        of = min(1.18, max(0.82, 1 - w_match * (opp_k9 / LG_K9 - 1)))
    tf = 1.0
    if team_total:
        tf = min(1.30, max(0.75, team_total / TEAM_TOTAL_AVG))
    return round(skill * pa * park * of * tf, 1)


def _team_recent_lineup(team_id, before_date: str) -> list:
    """A team's most-recent posted batting order (last Final game in the prior ~2
    weeks) -> [(player_id, name, slot)]. Naturally excludes IL players (they
    weren't in that lineup); the residual risk is a same-day rest/scratch."""
    import datetime as _d
    d0 = _d.date.fromisoformat(before_date)
    sch = _get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&teamId={team_id}"
               f"&startDate={(d0 - _d.timedelta(days=14)).isoformat()}&endDate={(d0 - _d.timedelta(days=1)).isoformat()}")
    games = [(g.get("gameDate", ""), g["gamePk"]) for dd in sch.get("dates", []) for g in dd.get("games", [])
             if g.get("status", {}).get("abstractGameState") == "Final"]
    if not games:
        return []
    try:
        box = _get(f"https://statsapi.mlb.com/api/v1/game/{max(games)[1]}/boxscore")
    except Exception:
        return []
    for side in ("home", "away"):
        t = box["teams"][side]
        if str(t["team"]["id"]) == str(team_id):
            starters = [(pl["person"]["id"], pl["person"]["fullName"], int(pl["battingOrder"]) // 100)
                        for pl in t["players"].values()
                        if pl.get("battingOrder") and int(pl["battingOrder"]) % 100 == 0]
            return sorted(starters, key=lambda x: x[2])
    return []


def lineups_for_date(date: str, project: bool = True) -> dict:
    """{norm name: {id, name, slot, team_id, park_team_id, opp_pitcher_id, game, confirmed}}.
    Confirmed starters from statsapi; if a team's lineup isn't posted yet and
    project=True, fill a PROJECTED order from its most-recent game (confirmed=False),
    so you can target a team before its official lineup drops."""
    s = _get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&hydrate=lineups,probablePitcher")
    out = {}
    for d in s.get("dates", []):
        for g in d.get("games", []):
            lu = g.get("lineups", {})
            home_id = g["teams"]["home"]["team"]["id"]
            away_id = g["teams"]["away"]["team"]["id"]
            hpp = (g["teams"]["home"].get("probablePitcher") or {}).get("id")
            app = (g["teams"]["away"].get("probablePitcher") or {}).get("id")
            for key, team_id, opp_pid in (("homePlayers", home_id, app), ("awayPlayers", away_id, hpp)):
                players = lu.get(key) or []
                if players:
                    for i, pl in enumerate(players[:9]):
                        out[norm(pl["fullName"])] = {"id": pl["id"], "name": pl["fullName"], "slot": i + 1,
                                                     "team_id": team_id, "park_team_id": home_id,
                                                     "opp_pitcher_id": opp_pid, "game": g["gamePk"], "confirmed": True}
                elif project:
                    for pid, name, slot in _team_recent_lineup(team_id, date):
                        if norm(name) not in out:
                            out[norm(name)] = {"id": pid, "name": name, "slot": slot, "team_id": team_id,
                                               "park_team_id": home_id, "opp_pitcher_id": opp_pid,
                                               "game": g["gamePk"], "confirmed": False}
    return out


import datetime as _dt
# slate suffixes that aren't standard salary-cap Classic (skip when resolving by name)
_NONCLASSIC = ("snake", "tiers", "home runs", "@")


def list_slate_names(groups: list[dict], date: str | None = None) -> list[tuple]:
    """(name, id, startZ, games) for priced-ish Classic slates, optionally for a date."""
    out = []
    for g in groups:
        suf = (g.get("ContestStartTimeSuffix") or "").strip().strip("()")
        if any(k in suf.lower() for k in _NONCLASSIC):
            continue
        if date and (g.get("StartDate") or "")[:10] != date:
            continue
        out.append((suf or "Main", g.get("DraftGroupId"), (g.get("StartDate") or "")[11:16], g.get("GameCount")))
    return sorted(out, key=lambda x: x[2])


def resolve_draft_group(spec, date: str | None = None) -> dict | None:
    """Resolve a draft group from a numeric id OR a slate name (Main/Early/Turbo/
    Night/Afternoon). Among same-name slates picks the soonest UPCOMING one."""
    groups = mlb_draft_groups()
    if str(spec).strip().isdigit():
        return next((g for g in groups if g.get("DraftGroupId") == int(spec)), None)
    name = str(spec).strip().lower()
    if name in ("main", "classic", "full", ""):
        name = ""

    def suf(g):
        return (g.get("ContestStartTimeSuffix") or "").strip().strip("()").lower()

    cands = [g for g in groups if suf(g) == name]
    if date:
        cands = [g for g in cands if (g.get("StartDate") or "")[:10] == date] or cands
    if not cands:
        return None
    now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    future = [g for g in cands if (g.get("StartDate") or "") >= now]
    return sorted(future or cands, key=lambda g: (g.get("StartDate") or ""))[0]
