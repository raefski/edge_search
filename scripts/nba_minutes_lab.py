#!/usr/bin/env python3
"""NBA minutes lab: a live-faithful backtest of edge/nba_minutes.py, diagnostics by
slice, and candidate improvements -- each fitted on 2024-25 and scored on 2025-26
(and on the 2026-27 nights played so far).

    python3 scripts/nba_minutes_lab.py diagnose          # the v1 (10/15) model, by slice
    python3 scripts/nba_minutes_lab.py ablate            # v1 -> the shipped v2, one change at a time
    python3 scripts/nba_minutes_lab.py fit               # re-run the coordinate-descent fit (~30 min)
    python3 scripts/nba_minutes_lab.py salary            # DK salary on top of v2, leave one night out

WHY NOT scripts/nba_minutes_backtest.py ALONE
That backtest starts every season with empty histories and scores only players with
5+ games that season, so it never sees opening week, rookies or players coming back
from a long absence -- three of the known failure modes. This lab replays every
night the way the live build does: histories carry across seasons (2023-24 on),
a player with < 5 games this season uses the SEASON_START_WINDOW role, a player
with < 5 real games gets this preseason's minutes x PRESEASON_MIN_RATIO, and every
active player is scored.

DATA (all free, cached under data/): stats.nba.com player game logs 2023-24 ..
2026-27 + preseasons, NBA.com daily lineups (who started, who was inactive),
stats.nba.com draft history, positions. Nothing here reads DraftKings.

MODES (what is known when the projection is made)
  confirmed   starting fives and inactive lists (NBA.com, ~30 min before tip)
  inactives   inactive lists only (starters not announced)
The legacy backtest's numbers are reproduced by `diagnose --legacy`.

Every number is a mean absolute error (or bias, + = projected too many) in
minutes over active player-games.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import nba_minutes as NM  # noqa: E402
from edge.nba_lineups import CACHE, parse  # noqa: E402

GT = ROOT / "data" / "nba_ground_truth"
SEASONS = ["2023-24", "2024-25", "2025-26", "2026-27"]
EVAL = ["2024-25", "2025-26", "2026-27"]
K = 82                      # history columns kept per row (a full season)
PICK_UNDRAFTED = 70


def season_of(d: str) -> str:
    y = int(d[:4])
    start = y if d[5:7] >= "08" else y - 1
    return f"{start}-{str(start + 1)[2:]}"


# ──────────────────────────────────────────────────────────────────────────────
# data
# ──────────────────────────────────────────────────────────────────────────────
def load(verbose: bool = True) -> dict:
    box: dict = {}
    pts: dict = defaultdict(float)
    box_dates: dict = defaultdict(list)
    for s in SEASONS:
        p = GT / f"player_gamelog_{s}.json"
        if not p.exists():
            continue
        for r in json.loads(p.read_text()):
            pid, gid = str(r["PLAYER_ID"]), r["GAME_ID"]
            box[(pid, gid)] = float(r["MIN"] or 0)
            pts[(gid, r["TEAM_ABBREVIATION"])] += r["PTS"] or 0
            box_dates[r["GAME_DATE"]].append(r)
    pre: dict = defaultdict(lambda: defaultdict(list))
    for s in SEASONS:
        p = GT / f"player_gamelog_{s}_pre.json"
        if p.exists():
            for r in sorted(json.loads(p.read_text()), key=lambda r: r["GAME_DATE"]):
                pre[s][str(r["PLAYER_ID"])].append(float(r["MIN"] or 0))
    draft = {}
    dp = GT / "draft_history.json"
    if dp.exists():
        for r in json.loads(dp.read_text()):
            draft[str(r["PERSON_ID"])] = (int(r["SEASON"]), int(r["OVERALL_PICK"] or PICK_UNDRAFTED))
    pos = json.loads((GT / "positions.json").read_text())

    H = defaultdict(lambda: {"m": [], "s": [], "season": [], "team": [], "date": [], "tgn": []})
    sched = defaultdict(list)          # team -> [(date, season)]
    margin = defaultdict(list)         # (team, season) -> [point margin]
    rows: list = []
    games: list = []
    for d in sorted(box_dates):
        season = season_of(d)
        path = CACHE / f"{d.replace('-', '')}.json"
        tonight = []
        if path.exists():
            for g in parse(json.loads(path.read_text())):
                gid = g["game_id"]
                by_team = defaultdict(list)
                for p in g["players"]:
                    by_team[p["team"]].append(p)
                if len(by_team) != 2:
                    continue
                has_box = any((str(p["id"]), gid) in box for p in g["players"])
                if not has_box:
                    continue
                t1, t2 = list(by_team)
                for team, ps in by_team.items():
                    opp = t2 if team == t1 else t1
                    tonight.append((team, opp, gid, ps, team == g["home"]))
        else:                          # no lineup file: box only, nobody marked as a starter
            by_game = defaultdict(lambda: defaultdict(list))
            for r in box_dates[d]:
                by_game[r["GAME_ID"]][r["TEAM_ABBREVIATION"]].append(
                    {"id": r["PLAYER_ID"], "starter": False, "inactive": False})
            for gid, teams in by_game.items():
                if len(teams) == 2:
                    t1, t2 = list(teams)
                    for team, ps in teams.items():
                        tonight.append((team, t2 if team == t1 else t1, gid, ps, None))
        day = date.fromisoformat(d)
        for team, opp, gid, ps, home in tonight:
            if season not in EVAL or sum(1 for p in ps if p["starter"]) != 5:
                continue
            tg = [x for x in sched[team] if x[1] == season]
            prev = date.fromisoformat(tg[-1][0]) if tg else None
            mt, mo = margin[(team, season)], margin[(opp, season)]
            exp_margin = ((np.mean(mt) - np.mean(mo)) / 2 + (1.5 if home else -1.5)
                          if len(mt) >= 5 and len(mo) >= 5 and home is not None else None)
            idx = []
            for p in ps:
                pid = str(p["id"])
                h = H[pid]
                n = len(h["m"])
                season_games = sum(1 for x in h["season"][-90:] if x == season)
                last_team = h["team"][-1] if n else None
                if n and last_team == team and h["season"][-1] == season:
                    missed = sum(1 for x in tg if x[0] > h["date"][-1])
                elif n and last_team == team:
                    missed = len(tg)                      # new season, same team: games missed so far
                else:
                    missed = 0
                with_team = 0
                for t_ in reversed(h["team"]):
                    if t_ != team:
                        break
                    with_team += 1
                # games played since coming back from a 5+ team-game gap this season
                # (0 = tonight is the first game back); None = no such gap
                since_return, prev_no, k = None, len(tg) + 1, 0
                for j in range(n - 1, -1, -1):
                    if h["season"][j] != season or h["team"][j] != team:
                        break
                    if prev_no - h["tgn"][j] - 1 >= 5:
                        since_return = k
                        break
                    prev_no, k = h["tgn"][j], k + 1
                else:
                    pass
                if since_return is None and n and (k == season_games) and prev_no - 1 >= 5 and last_team == team:
                    since_return = k            # missed the season's first 5+ games
                dr = draft.get(pid)
                rows.append({
                    "pid": pid, "n": n, "team": team, "opp": opp, "date": d, "season": season, "gid": gid,
                    "inactive": bool(p["inactive"]), "starter": bool(p["starter"]),
                    "pos": pos.get(pid), "season_games": season_games, "team_game_no": len(tg) + 1,
                    "new_team": bool(n) and last_team != team, "with_team": with_team, "missed": missed,
                    "nba_games": n, "real_pos_games": sum(1 for x in h["m"] if x > 0),
                    "pick": dr[1] if dr else PICK_UNDRAFTED, "draft_year": dr[0] if dr else None,
                    "pre": list(pre[season].get(pid, [])),
                    "b2b": bool(prev and (day - prev).days == 1), "exp_margin": exp_margin,
                    "since_return": since_return if since_return is not None and since_return <= 10 else None,
                    "actual": None if p["inactive"] else box.get((pid, gid), 0.0),
                })
                idx.append(len(rows) - 1)
            games.append({"date": d, "season": season, "team": team, "gid": gid, "idx": idx})
        # the day's results become history only after every game of the day is built
        for team, opp, gid, ps, home in tonight:
            tgn_now = sum(1 for x in sched[team] if x[1] == season) + 1
            sched[team].append((d, season))
            if (gid, team) in pts and (gid, opp) in pts:
                margin[(team, season)].append(pts[(gid, team)] - pts[(gid, opp)])
            for p in ps:
                if p["inactive"]:
                    continue
                pid = str(p["id"])
                h = H[pid]
                h["m"].append(box.get((pid, gid), 0.0))
                h["s"].append(bool(p["starter"]))
                h["season"].append(season)
                h["team"].append(team)
                h["date"].append(d)
                h["tgn"].append(tgn_now)
    # history matrices, newest in the LAST column, NaN-padded on the left
    N = len(rows)
    M = np.full((N, K), np.nan, dtype=np.float32)
    S = np.zeros((N, K), dtype=np.float32)
    CUR = np.zeros((N, K), dtype=bool)
    SAME = np.zeros((N, K), dtype=bool)
    for i, r in enumerate(rows):
        h = H[r["pid"]]
        lo = max(0, r["n"] - K)
        m = h["m"][lo:r["n"]]
        if not m:
            continue
        k = len(m)
        M[i, K - k:] = m
        S[i, K - k:] = h["s"][lo:r["n"]]
        CUR[i, K - k:] = [x == r["season"] for x in h["season"][lo:r["n"]]]
        SAME[i, K - k:] = [x == r["team"] for x in h["team"][lo:r["n"]]]
    if verbose:
        for s in EVAL:
            gs = [g for g in games if g["season"] == s]
            print(f"  {s}: {len(gs):,} team-games, {sum(len(g['idx']) for g in gs):,} player rows", file=sys.stderr)
    return {"rows": rows, "games": games, "M": M, "S": S, "CUR": CUR, "SAME": SAME}


# ──────────────────────────────────────────────────────────────────────────────
# the model: shipped behaviour by default, every candidate a parameter
# ──────────────────────────────────────────────────────────────────────────────
SHIPPED = {
    # role
    "win": 10, "win_start": 82, "start_games": 5, "decay": None, "prior_disc": 1.0, "prior_decay": None,
    "pre_ratio": NM.__dict__.get("PRESEASON_MIN_RATIO", 0.66), "pre_if": 5,
    # start / bench split (None = off)
    "split": False, "split_k": 4.0, "split_gap": 8.0,
    # rookie prior from draft pick + preseason (None = off)
    "rookie": None, "rookie_k": 5.0,
    # adjustments
    "ramp": (1.0, 1.0, 1.0), "ramp_after": 5, "b2b": 0.0, "blow": 0.0, "blow_min": 25.0,
    # redistribution
    "role_pow": NM.ROLE_POW, "pos_w": NM.POS_W, "promo_w": NM.PROMO_W, "promo_lift": NM.PROMO_LIFT,
    "starter_min": NM.STARTER_MIN, "regular_min": NM.REGULAR_MIN,
    # 240
    "excess_pow": NM.EXCESS_POW, "rank_kappa": 0.0, "curve": None, "full_roster": NM.FULL_ROSTER,
    # "shipped": power share only when over 240, proportional under; "power": always share
    # 240 by m^alloc_pow x (1 + starter_tilt x starting) -- starting = the confirmed flag,
    # else the player's start rate
    "alloc": "shipped", "alloc_pow": 1.15, "starter_tilt": 0.0, "tilt_over": 0.0,
    # "pools": the excess over 240 (or the shortfall) is split between the starter pool
    # and the bench pool -- phi_over / phi_under = the starters' share -- and shared
    # inside each pool by m^pool_q
    "phi_over": 0.5, "phi_under": 0.5, "pool_q": 1.0,
}


def final_params() -> dict:
    """The shipped 2026-10-21 model (edge/nba_minutes.project) in lab terms."""
    return {**SHIPPED, "decay": NM.DECAY, "prior_kappa": NM.PRIOR_KAPPA, "prior_decay": NM.PRIOR_DECAY,
            "split": True, "split_k": NM.SPLIT_K, "split_gap": NM.SPLIT_GAP,
            "rookie": (NM.ROOKIE_PRIOR[0], NM.ROOKIE_PRIOR[1], 0.0), "rookie_k": NM.ROOKIE_K, "ramp": NM.RAMP,
            "role_pow": NM.ROLE_POW_V2, "pos_w": NM.POS_W_V2, "alloc": "power", "alloc_pow": NM.ALLOC_POW,
            "starter_tilt": NM.STARTER_TILT, "tilt_over": 0.0}


def roles(D: dict, prm: dict) -> dict:
    """Vectorised role inputs for every row: base, start rate, start / bench means."""
    M, S, CUR, SAME = D["M"], D["S"], D["CUR"], D["SAME"]
    have = ~np.isnan(M)
    Mz = np.where(have, M, 0.0)
    age = np.arange(K - 1, -1, -1, dtype=np.float32)          # 0 = most recent
    sg = np.array([r["season_games"] for r in D["rows"]])
    if prm["decay"] is None:
        def win_mean(k):
            h = have[:, -k:]
            n = h.sum(1)
            return (np.where(n > 0, Mz[:, -k:].sum(1) / np.maximum(n, 1), 0.0),
                    np.where(n > 0, (S[:, -k:] * h).sum(1) / np.maximum(n, 1), 0.0))
        b10, s10 = win_mean(prm["win"])
        b82, s82 = win_mean(prm["win_start"])
        early = sg < prm["start_games"]
        base, srate = np.where(early, b82, b10), np.where(early, s82, s10)
        w = None
    else:
        # EWMA over this season's games; earlier seasons' games x prior_disc, decaying
        # by prior_decay per game among THEMSELVES (None = the same decay as this season),
        # so a season-opening role reads last season as a whole, not its last week
        if prm.get("prior_kappa") is not None:
            # a PRIOR worth prior_kappa games: last season's role, as a whole (prior_decay
            # per game, ~1 = flat), against this season's decayed games
            prior = (~CUR) & have
            prank = np.cumsum(prior[:, ::-1], axis=1)[:, ::-1] - 1
            pw = np.where(prior, prm.get("prior_decay", 1.0) ** np.maximum(prank, 0), 0.0)
            pw = pw / np.maximum(pw.sum(1, keepdims=True), 1e-9) * prm["prior_kappa"]
            w = np.where(CUR, prm["decay"] ** age[None, :], 0.0) * have + pw
        elif prm.get("prior_decay") is None:
            w = (prm["decay"] ** age)[None, :] * np.where(CUR, 1.0, prm["prior_disc"]) * have
        else:
            prior = (~CUR) & have
            # rank of each prior-season game among the prior-season games, newest = 0
            prank = np.cumsum(prior[:, ::-1], axis=1)[:, ::-1] - 1
            w = np.where(CUR, prm["decay"] ** age[None, :],
                         prm["prior_disc"] * prm["prior_decay"] ** np.maximum(prank, 0)) * have
        ws = w.sum(1)
        base = np.where(ws > 0, (w * Mz).sum(1) / np.maximum(ws, 1e-9), 0.0)
        srate = np.where(ws > 0, (w * S).sum(1) / np.maximum(ws, 1e-9), 0.0)
    out = {"base": base, "srate": srate}
    if prm["split"]:
        ww = w if w is not None else have.astype(np.float32) * (age[None, :] < 20)
        st, bn = ww * S, ww * (1 - S) * have
        ns, nb = st.sum(1), bn.sum(1)
        ms = np.where(ns > 0, (st * Mz).sum(1) / np.maximum(ns, 1e-9), 0.0)
        mb = np.where(nb > 0, (bn * Mz).sum(1) / np.maximum(nb, 1e-9), 0.0)
        k, gap = prm["split_k"], prm["split_gap"]
        # shrink each side toward the other side +/- the typical starter-bench gap
        out["ms"] = (ns * ms + k * np.where(nb > 0, mb + gap, base + gap * (1 - srate))) / (ns + k)
        out["mb"] = (nb * mb + k * np.where(ns > 0, ms - gap, base - gap * srate)) / (nb + k)
        out["ns"], out["nb"] = ns, nb
    return out


def _rookie_role(r: dict, prm: dict) -> float | None:
    """Prior minutes for a player with < 5 NBA games: draft pick + preseason."""
    a, b, c = prm["rookie"]
    pre = [m for m in r["pre"] if m > 0]
    pre_m = float(np.mean(pre)) if pre else 0.0
    return max(0.0, a + b * math.log(max(1, r["pick"])) + c * pre_m)


def project_game(D: dict, g: dict, R: dict, prm: dict, mode: str) -> dict:
    rows = D["rows"]
    idx = g["idx"]
    ps = [rows[i] for i in idx]
    known_starters = mode == "confirmed"
    base, srate = [], []
    for i, r in zip(idx, ps):
        b, s = float(R["base"][i]), float(R["srate"][i])
        # preseason for a player with < pre_if real games (the live rule)
        if r["real_pos_games"] < prm["pre_if"] and r["pre"]:
            hist = [x for x in D["M"][i] if not np.isnan(x)]
            pre = [m * prm["pre_ratio"] for m in r["pre"]]
            seq = hist + pre
            k = prm["win_start"] if r["season_games"] < prm["start_games"] else prm["win"]
            seq = seq[-k:]
            b = float(np.mean(seq)) if seq else 0.0
            s = 0.0
        if prm["rookie"] is not None and r["nba_games"] < 5:
            prior = _rookie_role(r, prm)
            n = r["nba_games"]
            own = float(np.nanmean(D["M"][i])) if n else 0.0
            b = (n * own + prm["rookie_k"] * prior) / (n + prm["rookie_k"])
        if prm["split"] and known_starters and (R["ns"][i] + R["nb"][i]) > 0:
            b = float(R["ms"][i]) if r["starter"] else float(R["mb"][i])
        # returning from a 5+ game absence: ramp over the first games back
        if r["since_return"] is not None and r["since_return"] < len(prm["ramp"]):
            b *= prm["ramp"][r["since_return"]]
        if prm["b2b"] and r["b2b"] and b >= prm["blow_min"]:
            b *= 1 - prm["b2b"]
        if prm["blow"] and r["exp_margin"] is not None and b >= prm["blow_min"]:
            b *= max(0.5, 1 - prm["blow"] * abs(r["exp_margin"]) / 10)
        base.append(max(0.0, b))
        srate.append(s)
    team = [{"id": i, "position": r["pos"], "inactive": r["inactive"],
             "starter": r["starter"] if known_starters else None,
             "starting": (1.0 if r["starter"] else 0.0) if known_starters else s_}
            for i, r, s_ in zip(idx, ps, srate)]
    return _redistribute(team, base, srate, prm)


def _groups(position):
    return set((position or "").replace("-", " ").split())


def _redistribute(team: list, base: list, srate: list, p: dict) -> dict:
    """edge/nba_minutes.project with roles passed in and the 240 step parametrised."""
    roles_ = {pl["id"]: (b, s) for pl, b, s in zip(team, base, srate)}
    out_pl = [pl for pl in team if pl["inactive"] and roles_[pl["id"]][0] >= p["regular_min"]]
    missing = sum(roles_[pl["id"]][0] for pl in out_pl)
    lost: dict = {}
    for pl in out_pl:
        for gr in _groups(pl["position"]):
            lost[gr] = lost.get(gr, 0.0) + roles_[pl["id"]][0]
    active = [pl for pl in team if not pl["inactive"]]
    weights, promoted = {}, {}
    for pl in active:
        b, s = roles_[pl["id"]]
        promo = bool(pl.get("starter")) and s < 0.5 and not p["split"]
        promoted[pl["id"]] = promo
        ps_ = sum(v for gr, v in lost.items() if gr in _groups(pl["position"])) / missing if missing else 0.0
        weights[pl["id"]] = (max(b, 1.0) ** p["role_pow"]) * (1 + p["pos_w"] * min(1.0, ps_)) * (1 + p["promo_w"] * promo)
    tw = sum(weights.values()) or 1.0
    mins = {}
    for pl in active:
        m = roles_[pl["id"]][0] + missing * weights[pl["id"]] / tw
        if promoted[pl["id"]]:
            m += p["promo_lift"] * max(0.0, p["starter_min"] - m)
        mins[pl["id"]] = min(48.0, max(0.0, m))
    s = sum(mins.values())
    if s > 0 and len(active) >= p["full_roster"] and p["alloc"] == "power":
        start = {pl["id"]: pl.get("starting") or 0.0 for pl in active}
        # the starters' tilt grows with how far the roles overflow 240
        tilt = p["starter_tilt"] + p["tilt_over"] * max(0.0, s / NM.TEAM_MIN - p.get("tilt_from", 1.0))
        w = {i: max(m, 0.0) ** p["alloc_pow"] * (1 + tilt * start[i]) for i, m in mins.items()}
        free, fixed = dict(w), {}
        for _ in range(5):                      # nobody past 48: cap and re-share the rest
            room = NM.TEAM_MIN - sum(fixed.values())
            ws = sum(free.values()) or 1.0
            trial = {i: room * x / ws for i, x in free.items()}
            over = [i for i, m in trial.items() if m > 48.0]
            if not over:
                break
            for i in over:
                fixed[i] = 48.0
                free.pop(i)
        mins = {**trial, **fixed}
    elif s > 0 and len(active) >= p["full_roster"] and p["alloc"] == "pools":
        mins = _pools(mins, {pl["id"]: pl.get("starting") or 0.0 for pl in active}, p)
    elif s > 0 and len(active) >= p["full_roster"]:
        if s > NM.TEAM_MIN:
            over = s / NM.TEAM_MIN - 1
            if p["rank_kappa"] and p["curve"] is not None:
                a = min(1.0, p["rank_kappa"] * over)
                order = sorted(mins, key=lambda i: -mins[i])
                curve = p["curve"]
                mins = {i: (1 - a) * mins[i] + a * curve[min(r, len(curve) - 1)] for r, i in enumerate(order)}
            w = {i: max(m, 0.0) ** p["excess_pow"] for i, m in mins.items()}
            ws = sum(w.values()) or 1.0
            mins = {i: min(48.0, NM.TEAM_MIN * w[i] / ws) for i in mins}
        else:
            k = NM.TEAM_MIN / s
            mins = {i: min(48.0, m * k) for i, m in mins.items()}
    return mins


def _pools(mins: dict, start: dict, p: dict) -> dict:
    """Move the team to 240 by taking (or giving) the difference from two pools:
    starters get phi of it, the bench 1 - phi, each pool shared by m^pool_q.
    `start` is 1/0 for announced starters, else the player's start rate (soft)."""
    diff = sum(mins.values()) - NM.TEAM_MIN            # > 0: too many roles
    phi = p["phi_over"] if diff > 0 else p["phi_under"]
    q = p["pool_q"]
    ws = {i: start[i] * max(m, 0.0) ** q for i, m in mins.items()}
    wb = {i: (1 - start[i]) * max(m, 0.0) ** q for i, m in mins.items()}
    ss, sb = sum(ws.values()), sum(wb.values())
    if ss <= 0 or sb <= 0:
        phi = 1.0 if sb <= 0 else 0.0
    out = {i: m - diff * (phi * ws[i] / (ss or 1.0) + (1 - phi) * wb[i] / (sb or 1.0)) for i, m in mins.items()}
    out = {i: min(48.0, max(0.0, m)) for i, m in out.items()}
    tot = sum(out.values())
    return {i: min(48.0, m * NM.TEAM_MIN / tot) for i, m in out.items()} if tot > 0 else out


# ──────────────────────────────────────────────────────────────────────────────
# scoring
# ──────────────────────────────────────────────────────────────────────────────
def slices(r: dict, R: dict, i: int, team_scratch: bool) -> list[str]:
    out = ["all"]
    out.append("opening (team games 1-5)" if r["team_game_no"] <= 5 else "after opening")
    out.append("season games " + ("0" if r["season_games"] == 0 else "1-4" if r["season_games"] < 5
                                  else "5-9" if r["season_games"] < 10 else "10+"))
    if r["nba_games"] < 5:
        out.append("rookie / no NBA history")
        if r["pick"] <= 14:
            out.append("lottery rookie")
    if r["new_team"] or (r["with_team"] < 5 and r["nba_games"] >= 5):
        out.append("new team (first 5)")
    if r["since_return"] == 0:
        out.append("back from 5+ missed")
    elif r["since_return"] in (1, 2):
        out.append("2nd-3rd game back")
    if team_scratch:
        out.append("teammate of a scratch")
    if r["starter"] and R["srate"][i] < 0.5:
        out.append("promoted starter")
    if R["base"][i] >= 28:
        out.append("high role (base 28+)")
        if r["team_game_no"] <= 5:
            out.append("opening x high role")
    ov = r.get("_over")
    if ov is not None:
        band = "<=1.05" if ov <= 1.05 else "1.05-1.15" if ov <= 1.15 else ">1.15"
        out.append(f"roles/240 {band}, {'starter' if r['starter'] else 'bench'}")
    if r["b2b"]:
        out.append("back-to-back")
    if r["exp_margin"] is not None and abs(r["exp_margin"]) >= 7:
        out.append("lopsided (|exp margin| 7+)")
    return out


def evaluate(D: dict, prm: dict, mode: str = "confirmed", seasons=EVAL) -> dict:
    R = roles(D, prm)
    rows = D["rows"]
    err: dict = defaultdict(lambda: defaultdict(list))
    for g in D["games"]:
        if g["season"] not in seasons:
            continue
        proj = project_game(D, g, R, prm, mode)
        act_roles = sum(float(R["base"][i]) for i in g["idx"] if not rows[i]["inactive"])
        for i in g["idx"]:
            rows[i]["_over"] = act_roles / NM.TEAM_MIN
        scratch = any(rows[i]["inactive"] and R["base"][i] >= NM.REGULAR_MIN for i in g["idx"])
        for i in g["idx"]:
            r = rows[i]
            if r["inactive"] or r["actual"] is None:
                continue
            e = proj.get(i, 0.0) - r["actual"]
            for sl in slices(r, R, i, scratch):
                err[g["season"]][sl].append((e, r["actual"]))
    out = {}
    for s, d in err.items():
        out[s] = {}
        for sl, v in d.items():
            e = np.array([x for x, _ in v])
            a = np.array([y for _, y in v])
            out[s][sl] = {"mae": float(np.abs(e).mean()), "bias": float(e.mean()), "n": len(e),
                          "rmse": float(np.sqrt((e ** 2).mean())),
                          "corr": float(np.corrcoef(e + a, a)[0, 1]) if len(e) > 2 else float("nan")}
    return out


SLICE_ORDER = ["all", "opening (team games 1-5)", "after opening", "season games 0", "season games 1-4",
               "season games 5-9", "season games 10+", "rookie / no NBA history", "lottery rookie",
               "new team (first 5)", "back from 5+ missed", "2nd-3rd game back", "teammate of a scratch",
               "promoted starter", "high role (base 28+)", "opening x high role", "back-to-back",
               "lopsided (|exp margin| 7+)",
               "roles/240 <=1.05, starter", "roles/240 <=1.05, bench", "roles/240 1.05-1.15, starter",
               "roles/240 1.05-1.15, bench", "roles/240 >1.15, starter", "roles/240 >1.15, bench"]


def table(res: dict, title: str, seasons=EVAL) -> str:
    lines = [f"\n### {title}\n", "| slice | " + " | ".join(f"{s} MAE (bias) n" for s in seasons) + " |",
             "|---|" + "---|" * len(seasons)]
    for sl in SLICE_ORDER:
        cells = []
        for s in seasons:
            c = res.get(s, {}).get(sl)
            cells.append(f"{c['mae']:.2f} ({c['bias']:+.1f}) {c['n']:,}" if c else "-")
        lines.append(f"| {sl} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def curve_from(D: dict, prm: dict, season: str) -> list:
    """Mean actual minutes by projected rank within the team (train season only)."""
    R = roles(D, prm)
    rows = D["rows"]
    by_rank = defaultdict(list)
    for g in D["games"]:
        if g["season"] != season:
            continue
        act = [i for i in g["idx"] if not rows[i]["inactive"] and rows[i]["actual"] is not None]
        for r_, i in enumerate(sorted(act, key=lambda i: -R["base"][i])):
            by_rank[r_].append(rows[i]["actual"])
    return [float(np.mean(by_rank[r])) for r in sorted(by_rank)]


KEY = ["all", "opening (team games 1-5)", "rookie / no NBA history", "lottery rookie", "new team (first 5)",
       "back from 5+ missed", "2nd-3rd game back", "teammate of a scratch", "promoted starter",
       "high role (base 28+)", "opening x high role", "roles/240 >1.15, starter", "roles/240 >1.15, bench"]


def run(D, prm, modes=("confirmed", "inactives"), seasons=EVAL):
    return {m: evaluate(D, prm, m, seasons) for m in modes}


METRIC = "rmse"     # fit on squared error: DFS needs MEAN minutes (expected points), not medians


def obj(res, season, slice_="all"):
    return float(np.mean([res[m][season][slice_][METRIC] for m in res]))


def coord_fit(D, prm, grid: dict, season="2024-25", slice_="all", rounds=2, verbose=True):
    """Coordinate descent over `grid` on the train season's MAE (both modes)."""
    prm = dict(prm)
    best = obj(run(D, prm, seasons=[season]), season, slice_)
    for _ in range(rounds):
        moved = False
        for k, vals in grid.items():
            for v in vals:
                if v == prm[k]:
                    continue
                trial = {**prm, k: v}
                o = obj(run(D, trial, seasons=[season]), season, slice_)
                if o < best - 1e-4:
                    best, prm, moved = o, trial, True
        if verbose:
            print(f"  fit on {season} [{slice_}]: {best:.4f} " + ", ".join(f"{k}={prm[k]}" for k in grid), file=sys.stderr)
        if not moved:
            break
    return prm


def fit_rookie(D, prm, season="2024-25"):
    """OLS of first-5-game minutes on log(draft pick) and preseason minutes."""
    X, y = [], []
    for g in D["games"]:
        if g["season"] != season:
            continue
        for i in g["idx"]:
            r = D["rows"][i]
            if r["inactive"] or r["actual"] is None or r["nba_games"] >= 5:
                continue
            pre = [m for m in r["pre"] if m > 0]
            X.append([1.0, math.log(max(1, r["pick"])), float(np.mean(pre)) if pre else 0.0])
            y.append(r["actual"])
    beta = np.linalg.lstsq(np.array(X), np.array(y), rcond=None)[0]
    print(f"  rookie prior on {season} ({len(y)} games): minutes = {beta[0]:.2f} {beta[1]:+.2f} ln(pick) "
          f"{beta[2]:+.3f} x preseason min", file=sys.stderr)
    return {**prm, "rookie": tuple(float(b) for b in beta)}


def compact(name, res, seasons=("2024-25", "2025-26", "2026-27")) -> list[str]:
    out = []
    for m in res:
        for s in seasons:
            if s not in res[m]:
                continue
            c = res[m][s]
            out.append(f"| {name} | {m} | {s} | {c['all']['rmse']:.2f} | " + " | ".join(
                f"{c[k]['mae']:.2f} ({c[k]['bias']:+.1f})" if k in c else "-" for k in KEY) + " |")
    return out


def salary_test(D: dict, prm: dict) -> list[str]:
    """Does DK's salary carry minutes information beyond the model? Only nights with
    cached DK draftables can say (the 2025-26 contest nights + 10/20/26): OLS of actual
    minutes on [model minutes, salary $k], leave one night out, vs the model alone."""
    from edge import nba
    from edge.dfs_run_nba import _sig
    from edge.names import norm
    from edge.dfs_slate import parse_time
    names = {}
    for s_ in SEASONS:
        p = GT / f"player_gamelog_{s_}.json"
        if p.exists():
            for r in json.loads(p.read_text()):
                names[str(r["PLAYER_ID"])] = r["PLAYER_NAME"]
    R = roles(D, prm)
    by_date = defaultdict(list)
    for g in D["games"]:
        by_date[g["date"]].append(g)
    pts = []
    for f in sorted((ROOT / "data" / "cache" / "nba_draftables").glob("*.json")):
        dk = json.loads(f.read_text())
        starts = sorted(x["competition"]["startTime"] for x in dk if x.get("competition"))
        if not starts:
            continue
        from zoneinfo import ZoneInfo
        d = parse_time(starts[0]).astimezone(ZoneInfo("America/New_York")).date().isoformat()
        sal = {}
        for x in dk:
            t = nba.team(x.get("teamAbbreviation"))
            sal.setdefault((t, norm(x["displayName"])), x["salary"])
            sal.setdefault((t, "sig", _sig(x["displayName"])), x["salary"])
        for g in by_date.get(d, []):
            proj = project_game(D, g, R, prm, "confirmed")
            for i in g["idx"]:
                r = D["rows"][i]
                if r["inactive"] or r["actual"] is None:
                    continue
                nm = names.get(r["pid"], "")
                sv = sal.get((r["team"], norm(nm))) or sal.get((r["team"], "sig", _sig(nm)))
                if sv:
                    pts.append((d, proj.get(i, 0.0), sv / 1000.0, r["actual"], r["season_games"] < 5 or r["new_team"]))
    if not pts:
        return ["no salary nights"]
    nights = sorted({p[0] for p in pts})
    e0, e1 = [], []
    for n_ in nights:
        tr = [p for p in pts if p[0] != n_]
        te = [p for p in pts if p[0] == n_]
        X = np.array([[1, p[1], p[2]] for p in tr])
        y = np.array([p[3] for p in tr])
        b = np.linalg.lstsq(X, y, rcond=None)[0]
        for p in te:
            e0.append(p[1] - p[3])
            e1.append(b[0] + b[1] * p[1] + b[2] * p[2] - p[3])
    e0, e1 = np.array(e0), np.array(e1)
    X = np.array([[1, p[1], p[2]] for p in pts])
    b = np.linalg.lstsq(X, np.array([p[3] for p in pts]), rcond=None)[0]
    return [f"\n## DK salary as a minutes signal ({len(pts):,} player-games, {len(nights)} nights, leave one night out)\n",
            f"model alone: MAE {np.abs(e0).mean():.3f}, RMSE {np.sqrt((e0 ** 2).mean()):.3f}",
            f"model + salary: MAE {np.abs(e1).mean():.3f}, RMSE {np.sqrt((e1 ** 2).mean()):.3f}",
            f"all nights: minutes = {b[0]:.2f} + {b[1]:.3f} x model + {b[2]:+.3f} x salary($k)"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["diagnose", "ablate", "fit", "salary"])
    ap.add_argument("--mode", default="confirmed", choices=["confirmed", "inactives"])
    a = ap.parse_args()
    D = load()
    if a.cmd == "diagnose":
        for mode in ("confirmed", "inactives"):
            print(table(evaluate(D, SHIPPED, mode), f"shipped model, {mode}"))
        return 0
    head = ["| model | mode | season | RMSE all | " + " | ".join(KEY) + " |", "|---|---|---|---|" + "---|" * len(KEY)]
    if a.cmd == "salary":
        print("\n".join(salary_test(D, final_params()) + salary_test(D, SHIPPED)))
        return 0
    F = final_params()
    if a.cmd == "ablate":
        # v1 -> v2 one change at a time, each with the shipped (fitted) constants
        role_only = {**SHIPPED, "decay": F["decay"], "prior_kappa": F["prior_kappa"], "prior_decay": F["prior_decay"]}
        split = {**role_only, "split": True, "split_k": F["split_k"], "split_gap": F["split_gap"]}
        share = {**split, "alloc": "power", "alloc_pow": F["alloc_pow"], "starter_tilt": F["starter_tilt"]}
        ramp = {**share, "ramp": F["ramp"]}
        rookie = {**ramp, "rookie": F["rookie"], "rookie_k": F["rookie_k"]}
        steps = [("v1 (10/15)", SHIPPED), ("+ weighted role, last-season prior", role_only),
                 ("+ start/bench split", split), ("+ 240 share", share), ("+ return ramp", ramp),
                 ("+ rookie draft prior", rookie), ("= v2: + redistribution refit", F)]
    else:
        # the coordinate-descent chain that produced the constants (fit on 2024-25 only)
        steps = [("v1 (10/15)", dict(SHIPPED))]
        p = {**SHIPPED, "alloc": "power", "alloc_pow": 1.0, "starter_tilt": 0.1, "decay": 0.85,
             "prior_kappa": 1.0, "prior_decay": 1.0}
        p = coord_fit(D, p, {"decay": [0.75, 0.8, 0.85, 0.9], "prior_kappa": [0.5, 1.0, 2.0, 4.0]})
        steps.append(("weighted role, last-season prior", p))
        p = coord_fit(D, {**p, "split": True, "split_k": 1.0, "split_gap": 10.0},
                      {"split_k": [0.5, 1.0, 2.0, 4.0], "split_gap": [6.0, 8.0, 10.0, 12.0]})
        steps.append(("+ start/bench split", p))
        p = coord_fit(D, p, {"alloc_pow": [0.9, 1.0, 1.15, 1.3], "starter_tilt": [0.0, 0.05, 0.1, 0.2, 0.35]})
        steps.append(("+ 240 share", p))
        p = coord_fit(D, p, {"ramp": [(r0, r1, r2) for r0 in (0.75, 0.8, 0.85, 0.9, 1.0)
                                       for r1 in (0.85, 0.9, 0.95, 1.0) for r2 in (0.95, 1.0)]},
                      slice_="back from 5+ missed", rounds=1)
        steps.append(("+ return ramp", p))
        p = fit_rookie(D, p)
        p = coord_fit(D, p, {"rookie_k": [0.5, 1.0, 3.0, 5.0]}, slice_="rookie / no NBA history", rounds=1)
        steps.append(("+ rookie prior", p))
        p = coord_fit(D, p, {"role_pow": [0.0, 0.25, 0.5, 1.0], "pos_w": [0.0, 0.25, 0.5, 1.0]})
        steps.append(("+ redistribution refit", p))
    rows = []
    for name, p in steps:
        rows += compact(name, run(D, p))
    print("\n".join(head + rows))
    print("\nlast step's params:", {k: v for k, v in steps[-1][1].items() if k != "curve"})
    return 0

if __name__ == "__main__":
    sys.exit(main())
