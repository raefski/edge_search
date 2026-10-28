#!/usr/bin/env python3
"""NBA minutes lab: a live-faithful backtest of edge/nba_minutes.py, diagnostics by
slice, and candidate improvements -- each fitted on 2024-25 and scored on 2025-26
(and on the 2026-27 nights played so far).

    python3 scripts/nba_minutes_lab.py diagnose          # the v1 (10/15) model, by slice
    python3 scripts/nba_minutes_lab.py ablate            # v1 -> the shipped v2, one change at a time
    python3 scripts/nba_minutes_lab.py fit               # re-run the coordinate-descent fit (~30 min)
    python3 scripts/nba_minutes_lab.py salary            # DK salary on top of v2, leave one night out
    python3 scripts/nba_minutes_lab.py report            # the shipped model's standard table (critique loop)
    python3 scripts/nba_minutes_lab.py live              # the information state the app actually has (cycle 3)
    python3 scripts/nba_minutes_lab.py cycle4            # NBA.com's Expected five: blend weight + held-out table
    python3 scripts/nba_minutes_lab.py cycle5            # the starting role's own memory (grid on 2024-25)
    python3 scripts/nba_minutes_lab.py cycle6            # players who sat last game vs an imperfect Out list

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
import time
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
def _inputs_key(phantom: bool, report_skip: bool) -> str:
    """Everything load() reads: box scores, lineups, injury reports, and the code that
    turns them into D. Any change -> a new key -> a fresh load."""
    import hashlib
    from edge import nba_injury_report as IR
    from edge import nba_lineups as NL
    h = hashlib.sha1(f"{phantom}|{report_skip}".encode())
    for f in (Path(__file__), Path(NM.__file__), Path(NM.__file__).with_name("nba_minutes_gap.py")):
        h.update(f.read_bytes())
    for d in (GT, NL.CACHE, IR.CACHE):
        for f in sorted(d.glob("*")) if d.exists() else []:
            st = f.stat()
            h.update(f"{f.name}|{st.st_size}|{st.st_mtime_ns}".encode())
    return h.hexdigest()[:16]


def load(verbose: bool = True, phantom: bool = False, report_skip: bool = True) -> dict:
    """load_fresh(), cached (Musk "accelerate", 2026-10-28): the build takes ~325 s, a
    model run ~9 s, so every experiment was 97% loading. data/cache/nba_minutes_lab/
    holds one pickle per inputs key (_inputs_key); a changed box score, lineup file,
    injury report or lab / model source misses the cache and rebuilds."""
    import pickle
    d = ROOT / "data" / "cache" / "nba_minutes_lab"
    path = d / f"D_{_inputs_key(phantom, report_skip)}.pkl"
    if path.exists():
        try:
            with path.open("rb") as fh:
                return pickle.load(fh)
        except Exception:                                    # noqa: BLE001 -- rebuild a bad file
            pass
    D = load_fresh(verbose, phantom, report_skip)
    d.mkdir(parents=True, exist_ok=True)
    for old in d.glob("D_*.pkl"):
        if old.stat().st_mtime < time.time() - 7 * 86400:
            old.unlink()
    tmp = path.with_suffix(".tmp")
    with tmp.open("wb") as fh:
        pickle.dump(D, fh, protocol=5)
    tmp.replace(path)
    return D


def load_fresh(verbose: bool = True, phantom: bool = False, report_skip: bool = True) -> dict:
    """`phantom` rebuilds HISTORIES the way the 2026-27 NBA.com feed did before critique
    cycle 3: no inactive flags, so a missed game is a 0-minute appearance (tonight's
    flags and the scoring are unchanged)."""
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
            from edge.nba_availability import apply as availability
            for g in parse(json.loads(path.read_text())):
                gid = g["game_id"]
                g = {**g, "players": availability(g["players"], gid)}   # box-score truth where cached
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
        from edge import nba_injury_report as IR
        from edge.names import norm
        for team, opp, gid, ps, home in tonight:
            tgn_now = sum(1 for x in sched[team] if x[1] == season) + 1
            sched[team].append((d, season))
            if (gid, team) in pts and (gid, opp) in pts:
                margin[(team, season)].append(pts[(gid, team)] - pts[(gid, opp)])
            out = IR.out_at_tip(gid) if report_skip and not phantom else None
            for p in ps:
                if p["inactive"] and not phantom:
                    continue
                pid = str(p["id"])
                # cycle 8: listed Out by the official report yet "active" with 0 minutes
                if out and not box.get((pid, gid)) and norm(p["name"]) in out:
                    continue
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
    # critique cycle 2: a non-starter's role reads his LAST game (recency_lam) and a regular
    # starter left out of tonight's five is cut (drop_q); None = off
    "recency_lam": None, "recency_tau": 0.8, "drop_q": None, "drop_tau": 0.8, "recency_guard": True,
    # critique cycle 5: the STARTING role (ms) with its own, longer memory -- this season's
    # decay ms_decay, last season worth ms_kappa games; None = the shared DECAY / PRIOR_KAPPA
    "ms_decay": None, "ms_kappa": None,
}


def final_params() -> dict:
    """The shipped 2026-10-21 model (edge/nba_minutes.project) in lab terms."""
    return {**SHIPPED, "decay": NM.DECAY, "prior_kappa": NM.PRIOR_KAPPA, "prior_decay": NM.PRIOR_DECAY,
            "split": True, "split_k": NM.SPLIT_K, "split_gap": NM.SPLIT_GAP,
            "rookie": (NM.ROOKIE_PRIOR[0], NM.ROOKIE_PRIOR[1], 0.0), "rookie_k": NM.ROOKIE_K, "ramp": NM.RAMP,
            "role_pow": NM.ROLE_POW_V2, "pos_w": NM.POS_W_V2, "alloc": "power", "alloc_pow": 1.0,
            "starter_tilt": 0.05, "tilt_over": 0.0, **_shipped_alloc(),
            "recency_lam": getattr(NM, "RECENCY_LAM", None), "recency_tau": getattr(NM, "RECENCY_TAU", 0.8),
            "drop_q": getattr(NM, "DROP_Q", None), "drop_tau": getattr(NM, "DROP_TAU", 0.8),
            "ms_decay": getattr(NM, "MS_DECAY", None), "ms_kappa": getattr(NM, "MS_KAPPA", None)}


def _shipped_alloc() -> dict:
    """The shipped 240 step: critique cycle 1's rank-gap allocation (edge/nba_minutes_gap.py)."""
    from edge.nba_minutes_gap import GAP_COEF
    return {"alloc": "rank_gap", "gap_coef": GAP_COEF}


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
        if prm.get("ms_decay") is not None:
            # critique cycle 5: the starting role reads further back than the bench role
            long_ = {**prm, "decay": prm["ms_decay"], "prior_kappa": prm["ms_kappa"], "ms_decay": None}
            out["ms"] = roles(D, long_)["ms"]
    return out


def _rookie_role(r: dict, prm: dict) -> float | None:
    """Prior minutes for a player with < 5 NBA games: draft pick + preseason."""
    a, b, c = prm["rookie"]
    pre = [m for m in r["pre"] if m > 0]
    pre_m = float(np.mean(pre)) if pre else 0.0
    return max(0.0, a + b * math.log(max(1, r["pick"])) + c * pre_m)


def project_game(D: dict, g: dict, R: dict, prm: dict, mode: str) -> dict:
    team, base, srate = game_roles(D, g, R, prm, mode)
    if prm.get("alloc") == "rank_gap" and sum(1 for pl in team if not pl["inactive"]) >= prm["full_roster"]:
        return _rank_gap(team, base, prm["gap_coef"][mode])
    return _redistribute(team, base, srate, prm)


GAP_RANKS = 13          # ranks 1-13 get their own cell, 14+ pooled with 13 (0-based cell 13 = rank 14+)


def _rank_gap(team: list, base: list, coef: dict) -> dict:
    """Critique cycle 1: close the team's gap to 240 by ROLE RANK, not in proportion to
    role. G = 240 - sum of active roles; player at rank k gets a(k, sign G) + b(k, sign G) x G,
    then the remaining difference is shared by the slopes, clipped to 0-48 and re-shared."""
    act = [(pl["id"], b) for pl, b in zip(team, base) if not pl["inactive"]]
    G = NM.TEAM_MIN - sum(b for _, b in act)
    order = sorted(act, key=lambda x: -x[1])
    d, slope = {}, {}
    u = {pl["id"]: pl.get("u", 0.0) for pl in team}
    for rk, (i, b) in enumerate(order):
        a0, a1, *a2 = coef[(min(rk, GAP_RANKS), G > 0)]
        d[i], slope[i] = a0 + a1 * G + (a2[0] * u[i] if a2 else 0.0), max(a1, 1e-3)
    role = dict(act)
    out = {i: role[i] + d[i] for i in role}
    for _ in range(5):                          # close to 240 by the slopes; nobody outside 0-48
        miss = NM.TEAM_MIN - sum(out.values())
        free = {i: slope[i] for i in out if (miss > 0 and out[i] < 48.0) or (miss < 0 and out[i] > 0.0)}
        if abs(miss) < 1e-6 or not free:
            break
        ss = sum(free.values())
        for i in free:
            out[i] += miss * free[i] / ss
        out = {i: min(48.0, max(0.0, m)) for i, m in out.items()}
    return out


def gap_rows(D: dict, prm: dict, season: str, mode: str) -> list:
    """(cell, G > 0, G, actual - role) for every active player of a full team-game."""
    R = roles(D, prm)
    out = []
    for g in D["games"]:
        if g["season"] != season:
            continue
        team, base, _ = game_roles(D, g, R, prm, mode)
        act = [(pl["id"], b) for pl, b in zip(team, base) if not pl["inactive"]]
        if len(act) < prm["full_roster"]:
            continue
        G = NM.TEAM_MIN - sum(b for _, b in act)
        u = {pl["id"]: pl.get("u", 0.0) for pl in team}
        for rk, (i, b) in enumerate(sorted(act, key=lambda x: -x[1])):
            a = D["rows"][i]["actual"]
            if a is not None:
                out.append((min(rk, GAP_RANKS), G > 0, G, a - b, u[i]))
    return out


def fit_gap(D: dict, prm: dict, season: str = "2024-25") -> dict:
    """OLS of (actual - role) on G in each (rank cell, sign of G), per mode."""
    coef = {}
    for mode in ("confirmed", "inactives"):
        cells = defaultdict(list)
        for k, pos_, G, y, u in gap_rows(D, prm, season, mode):
            cells[(k, pos_)].append((G, y, u))
        c = {}
        withu = mode == "inactives" and prm.get("pstart_w") is not None
        for key, v in cells.items():
            v = np.array(v)
            X = np.c_[np.ones(len(v)), v[:, 0], v[:, 2]] if withu and np.any(v[:, 2]) else np.c_[np.ones(len(v)), v[:, 0]]
            c[key] = tuple(float(x) for x in np.linalg.lstsq(X, v[:, 1], rcond=None)[0])
        coef[mode] = c
    return coef


def write_gap_module(D: dict, coef: dict, prm: dict, cycle: int = 1, extra: str = "", cmd: str | None = None) -> None:
    """edge/nba_minutes_gap.py: the rank-gap coefficients with their provenance."""
    from datetime import date as _date
    n = {m: len(gap_rows(D, prm, "2024-25", m)) for m in coef}
    lines = [f'"""Rank-gap allocation coefficients for edge/nba_minutes.project (critique cycle {cycle}).',
             "",
             f"GENERATED by `python3 scripts/nba_minutes_lab.py {cmd or f'cycle{cycle}'} --write` -- do not edit by hand.",
             *([f"Roles as of cycle {cycle}: {extra}."] if extra else []),
             f"Fitted {_date.today().isoformat()} on 2024-25: OLS of (actual minutes - role) on",
             "G = 240 - sum of the team's active roles, in each (role rank, sign of G) cell -- ranks",
             f"1-13 by role, 14+ pooled into cell 13 -- separately for the two information states",
             f"(player-games: confirmed {n['confirmed']:,}, inactives {n['inactives']:,}).",
             "Key: (0-based rank cell, G > 0) -> (intercept a, slope b); minutes = role + a + b x G,",
             "then the team is closed to 240 by the slopes. NBA_MINUTES_CRITIQUE_LOG.md has the numbers.",
             '"""', "", "GAP_COEF = {"]
    for mode, c in coef.items():
        lines.append(f'    "{mode}": {{')
        for key in sorted(c):
            a0, a1 = c[key]
            lines.append(f"        ({key[0]}, {key[1]}): ({a0:.4f}, {a1:.5f}),")
        lines.append("    },")
    lines.append("}")
    (ROOT / "edge" / "nba_minutes_gap.py").write_text("\n".join(lines) + "\n")
    print("wrote edge/nba_minutes_gap.py", file=sys.stderr)


def game_roles(D: dict, g: dict, R: dict, prm: dict, mode: str):
    """(team, adjusted roles, start rates) for one team-game, before any allocation."""
    rows = D["rows"]
    idx = g["idx"]
    ps = [rows[i] for i in idx]
    known_starters = mode == "confirmed"
    base, srate = [], []
    for i, r in zip(idx, ps):
        b, s = float(R["base"][i]), float(R["srate"][i])
        special = False
        # preseason for a player with < pre_if real games (the live rule)
        if r["real_pos_games"] < prm["pre_if"] and r["pre"]:
            special = True
            hist = [x for x in D["M"][i] if not np.isnan(x)]
            pre = [m * prm["pre_ratio"] for m in r["pre"]]
            seq = hist + pre
            k = prm["win_start"] if r["season_games"] < prm["start_games"] else prm["win"]
            seq = seq[-k:]
            b = float(np.mean(seq)) if seq else 0.0
            s = 0.0
        if prm["rookie"] is not None and r["nba_games"] < 5:
            special = True
            prior = _rookie_role(r, prm)
            n = r["nba_games"]
            own = float(np.nanmean(D["M"][i])) if n else 0.0
            b = (n * own + prm["rookie_k"] * prior) / (n + prm["rookie_k"])
        if prm["split"] and known_starters and (R["ns"][i] + R["nb"][i]) > 0:
            b = float(R["ms"][i]) if r["starter"] else float(R["mb"][i])
        # critique cycle 2: is the coach playing him right now?
        sr0 = float(R["srate"][i])
        guard = not prm["recency_guard"] or (r["season_games"] >= 1 and r["since_return"] is None)
        benchish = (known_starters and not r["starter"]) or (not known_starters and sr0 < 0.5)
        last = D["M"][i, -1]
        if (prm["recency_lam"] and benchish and not special and sr0 < prm["recency_tau"] and guard
                and not np.isnan(last)):
            b = b + prm["recency_lam"] * (float(last) - b)
        if (prm["drop_q"] is not None and known_starters and not r["starter"] and sr0 >= prm["drop_tau"]
                and (not prm["recency_guard"] or (r["since_return"] is None and r["season_games"] >= 3))):
            b *= prm["drop_q"]
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
    if not known_starters and prm.get("pstart_w") is not None:
        for pl, (i, p, u) in zip(team, pstart_game(D, g, R, prm)):
            pl["u"] = u
    return team, base, srate


def pstart_feats(D: dict, g: dict, R: dict) -> dict:
    """Critique cycle 10 (target: substitutions): with no five, who inherits a scratched
    starter's start? Features per ACTIVE player of tonight's team (reads the current
    inactive flags, so the official-report replay moves them): [1, start rate, started
    last game, vac x (1 - s), open x (1 - s), role / 36], vac = the start-rate total of the
    out players sharing one of his position groups (cap 1), open = max(0, 5 - sum of the
    active players' start rates)."""
    rows, M, S = D["rows"], D["M"], D["S"]
    lost: dict = defaultdict(float)
    for j in g["idx"]:
        if rows[j]["inactive"]:
            for gr in _groups(rows[j]["pos"]):
                lost[gr] += float(R["srate"][j])
    act = [i for i in g["idx"] if not rows[i]["inactive"]]
    open_ = max(0.0, 5 - sum(float(R["srate"][i]) for i in act))
    out = {}
    for i in act:
        sr = float(R["srate"][i])
        last = 0.0 if np.isnan(M[i, -1]) else float(S[i, -1])
        vac = min(1.0, max([lost.get(gr, 0.0) for gr in _groups(rows[i]["pos"])] + [0.0]))
        out[i] = [1.0, sr, last, vac * (1 - sr), open_ * (1 - sr), float(R["base"][i]) / 36]
    return out


def pstart_game(D: dict, g: dict, R: dict, prm: dict) -> list:
    """(id, P(start), u) per team member; u = (P - start rate) x (starting role - bench role),
    the minutes the start-probability uplift is worth, for the rank-gap step's third
    coefficient. 0 for the inactive, preseason-role, < 5 NBA games and back-from-absence
    players (their roles are not the start/bench split)."""
    rows, w = D["rows"], np.asarray(prm["pstart_w"])
    X = pstart_feats(D, g, R)
    out = []
    for i in g["idx"]:
        r = rows[i]
        if (i not in X or (R["ns"][i] + R["nb"][i]) <= 0 or r["nba_games"] < 5 or r["since_return"] is not None
                or (r["real_pos_games"] < prm["pre_if"] and r["pre"])):
            out.append((i, None, 0.0))
            continue
        p = float(1 / (1 + np.exp(-np.dot(X[i], w))))
        out.append((i, p, (p - float(R["srate"][i])) * (float(R["ms"][i]) - float(R["mb"][i]))))
    return out


def fit_pstart(D: dict, prm: dict, season: str = "2024-25") -> list:
    """Logistic P(start) on pstart_feats, Newton, fitted on one season's active rows."""
    R = roles(D, prm)
    Xs, ys = [], []
    for g in D["games"]:
        if g["season"] != season:
            continue
        for i, x in pstart_feats(D, g, R).items():
            Xs.append(x)
            ys.append(1.0 if D["rows"][i]["starter"] else 0.0)
    X, y = np.array(Xs), np.array(ys)
    w = np.zeros(X.shape[1])
    for _ in range(30):
        p = 1 / (1 + np.exp(-X @ w))
        w += np.linalg.solve((X * (p * (1 - p))[:, None]).T @ X + 1e-6 * np.eye(len(w)), X.T @ (y - p))
    p = 1 / (1 + np.exp(-X @ w))
    print(f"  P(start) on {season} (n {len(y):,}): w {np.round(w, 3).tolist()}, log loss "
          f"{float(-(y * np.log(p) + (1 - y) * np.log(1 - p)).mean()):.4f}", file=sys.stderr, flush=True)
    return [float(x) for x in w]


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


REPORT_SLICES = ["all", "opening (team games 1-5)", "rookie / no NBA history", "lottery rookie",
                 "new team (first 5)", "back from 5+ missed", "teammate of a scratch", "promoted starter",
                 "high role (base 28+)", "roles/240 >1.15, starter"]


def report(D: dict, prm: dict, legacy: bool = True) -> str:
    """The standard table of the critique loop: RMSE and MAE (bias) by slice, both modes,
    every season; plus the legacy protocol (scripts/nba_minutes_backtest.py) through the
    production edge/nba_minutes.project."""
    res = run(D, prm)
    out = ["| mode | season | n | RMSE all | " + " | ".join(REPORT_SLICES) + " |",
           "|---|---|---|---|" + "---|" * len(REPORT_SLICES)]
    for m in res:
        for s_ in EVAL:
            c = res[m].get(s_)
            if not c:
                continue
            out.append(f"| {m} | {s_} | {c['all']['n']:,} | {c['all']['rmse']:.3f} | " + " | ".join(
                f"{c[k]['mae']:.2f} ({c[k]['bias']:+.1f})" if k in c else "-" for k in REPORT_SLICES) + " |")
    if not legacy:
        return "\n".join(out)
    try:
        import scripts.nba_minutes_backtest as BT
        test = BT.build("2025-26")
        for known, lab in ((True, "confirmed"), (False, "inactives")):
            sc = BT.score(test, {"SALARY_NUDGE": None}, starters_known=known)
            out.append(f"\nlegacy protocol 2025-26, {lab}: all {sc['all'][0]:.3f}, teammates of a scratch "
                       f"{sc['teammates of a scratch'][0]:.3f}, promoted starters {sc['promoted starters'][0]:.3f} "
                       f"({sc['promoted starters'][1]:+.2f}), n {sc['all'][2]:,}")
    except Exception as exc:  # noqa: BLE001
        out.append(f"(legacy protocol unavailable: {exc})")
    return "\n".join(out)


def _prev_inactive(D: dict) -> dict:
    """{row: was he inactive in his team's previous game (None = no previous game)}."""
    rows, prev, last = D["rows"], {}, {}
    for g in sorted(D["games"], key=lambda g: g["date"]):
        for i in g["idx"]:
            prev[i] = last.get((g["team"], rows[i]["pid"]))
        for i in g["idx"]:
            last[(g["team"], rows[i]["pid"])] = rows[i]["inactive"]
    return prev


def _live_out(D: dict, R: dict, g: dict, prev: dict, known: str, cut: int | None) -> dict:
    """{row: treated as not dressing} under live_replay's Out list `known` and `cut`."""
    rows = D["rows"]
    K = set()
    for i in g["idx"]:
        if not rows[i]["inactive"]:
            continue
        b = float(R["base"][i])
        if known == "all" or (known == "role10" and b >= 10) or (known == "stale10" and b >= 10 and prev[i] in (True, None)):
            K.add(i)
    cand = [i for i in g["idx"] if i not in K]
    if cut:
        ranked = sorted(cand, key=lambda i: (not rows[i]["starter"], -float(R["base"][i])))
        keep = set(ranked[:cut])
    else:
        keep = set(cand)
    return {i: i not in keep for i in g["idx"]}


def live_replay(Dt: dict, Dh: dict, prm: dict, season: str, known: str, cut: int | None = 13,
                mode: str = "confirmed") -> dict:
    """Critique cycle 3: score the LIVE information state on a past season. Histories (and
    roles) come from `Dh` (clean, or phantom = the 2026-27 feed's missing inactive list);
    flags and actual minutes from the truth `Dt`. Tonight's Out list:
      "all"     every inactive (the lab's usual assumption)
      "role10"  inactives with a role >= 10 (what an injury report / ESPN's Out list covers)
      "stale10" inactives with a role >= 10 who were also out in their team's last game
                (about what DK's OUT flags covered on 2026-10-20)
    then, with `cut`, each team keeps its `cut` largest roles (announced starters first),
    the live rule before NBA.com's inactive list. Rows Dt / Dh align one to one."""
    rows, R = Dt["rows"], roles(Dh, prm)
    prev = _prev_inactive(Dt)
    E = []
    for g in Dt["games"]:
        if g["season"] != season:
            continue
        sim = _live_out(Dt, R, g, prev, known, cut)
        saved = {i: Dh["rows"][i]["inactive"] for i in g["idx"]}
        for i in g["idx"]:
            Dh["rows"][i]["inactive"] = sim[i]
        proj = project_game(Dh, g, R, prm, mode)
        for i in g["idx"]:
            Dh["rows"][i]["inactive"] = saved[i]
        for i in g["idx"]:
            r = rows[i]
            if r["inactive"] or r["actual"] is None:
                continue
            e = (0.0 if sim[i] else proj.get(i, 0.0)) - r["actual"]
            E.append(e)
    E = np.array(E)
    return {"rmse": float(np.sqrt((E ** 2).mean())), "mae": float(np.abs(E).mean()), "bias": float(E.mean()), "n": len(E)}


def expected_preds(D: dict, prm: dict, seasons=("2024-25", "2025-26"), live: tuple | None = None) -> dict:
    """Critique cycle 4: the EXPECTED state -- NBA.com's afternoon five, which the live
    build used as if confirmed. On 2026-10-20/21 the last Expected five missed a starter
    in 7 of 28 regular-season team-games (8 of 140 starters; data/nba_lineup_log.csv).
    Every team-game is projected three ways, so any error rate rho is an exact mixture
    rather than one random draw:
      right  the Expected five is the true five ("confirmed" mode);
      wrong  one miss: the true starter with the lowest start rate is benched and the
             active non-starter with the highest start rate starts in his place (the
             surprise starter NBA.com misses -- 2026-27's misses look like this:
             Jaylin Williams for Jalen Williams, Sion James for Grayson Allen);
      none   no five at all ("inactives" mode).
    `live` = (known, cut) replays the app's Out list and roster cut (live_replay) instead
    of the full inactive list; a player cut who did play is projected 0.
    Returns {season: {"right", "wrong", "none", "y", "pr"}} arrays over active
    player-games; "pr" flags the promoted starters of the right five (start rate < 0.5)."""
    R = roles(D, prm)
    rows = D["rows"]
    prev = _prev_inactive(D) if live else None
    acc: dict = defaultdict(lambda: defaultdict(list))
    for g in D["games"]:
        if g["season"] not in seasons:
            continue
        truth = {i: rows[i]["inactive"] for i in g["idx"]}
        sim = _live_out(D, R, g, prev, *live) if live else truth
        for i in g["idx"]:
            rows[i]["inactive"] = sim[i]
        right = project_game(D, g, R, prm, "confirmed")
        none = project_game(D, g, R, prm, "inactives")
        T = [i for i in g["idx"] if rows[i]["starter"]]
        B = [i for i in g["idx"] if not rows[i]["starter"] and not rows[i]["inactive"]]
        wrong = right
        if len(T) == 5 and B:
            out = min(T, key=lambda i: float(R["srate"][i]))
            inn = max(B, key=lambda i: float(R["srate"][i]))
            rows[out]["starter"], rows[inn]["starter"] = False, True
            try:
                wrong = project_game(D, g, R, prm, "confirmed")
            finally:
                rows[out]["starter"], rows[inn]["starter"] = True, False
        for i in g["idx"]:
            rows[i]["inactive"] = truth[i]
        a = acc[g["season"]]
        for i in g["idx"]:
            r = rows[i]
            if r["inactive"] or r["actual"] is None:
                continue
            a["right"].append(right.get(i, 0.0))
            a["wrong"].append(wrong.get(i, 0.0))
            a["none"].append(none.get(i, 0.0))
            a["y"].append(r["actual"])
            a["pr"].append(bool(r["starter"]) and float(R["srate"][i]) < 0.5)
    return {s: {k: np.array(v, dtype=float if k != "pr" else bool) for k, v in a.items()} for s, a in acc.items()}


def expected_score(P: dict, w: float, rho: float) -> dict:
    """RMSE / MAE / bias of minutes = w x project(Expected five) + (1 - w) x project(no five)
    when a share rho of Expected fives miss one starter -- the exact mixture over states."""
    y, n = P["y"], P["none"]
    eR, eW = w * P["right"] + (1 - w) * n - y, w * P["wrong"] + (1 - w) * n - y
    mix = lambda f: (1 - rho) * f(eR) + rho * f(eW)  # noqa: E731
    pr = P["pr"]
    return {"rmse": math.sqrt(mix(lambda e: float((e ** 2).mean()))), "mae": mix(lambda e: float(np.abs(e).mean())),
            "bias": mix(lambda e: float(e.mean())), "n": len(y),
            "right": math.sqrt(float((eR ** 2).mean())), "wrong": math.sqrt(float((eW ** 2).mean())),
            "promoted": (float(np.abs(eR[pr]).mean()), float(eR[pr].mean()), int(pr.sum()))}


def expected_w(P: dict, rho: float) -> float:
    """The squared-error-optimal w at error rate rho, in closed form (clipped to 0-1)."""
    e, dR, dW = P["none"] - P["y"], P["right"] - P["none"], P["wrong"] - P["none"]
    num = (1 - rho) * float((e * dR).mean()) + rho * float((e * dW).mean())
    den = (1 - rho) * float((dR ** 2).mean()) + rho * float((dW ** 2).mean())
    return min(1.0, max(0.0, -num / den))



def _u(i: int) -> float:
    """A fixed pseudo-random uniform per row (which scratches the Out list misses)."""
    import hashlib
    return int(hashlib.md5(str(i).encode()).hexdigest()[:8], 16) / 2 ** 32


def avail_preds(D: dict, prm: dict, season: str, mode: str, miss: float, cut: int = 15,
                opener_stale: bool = True) -> dict:
    """Critique cycle 6: the live Out list misses scratches. Each inactive with role >= 10
    is on the Out list unless _u(row) < miss (2026-27's DTD / unlisted players: 29 of 47
    role-10+ scratches of 10/20-21 were not 'Out' on ESPN's 10/22 feed); the cut keeps
    `cut` (starters first, then role). A kept player who did not dress in his team's last
    game and is not an announced starter is STALE: he may be out again. Each team-game
    is projected with every stale player in (A) and out (B, the next-largest roles kept).
    Rows = everyone the board projects or who dressed (a phantom is a 0-minute row).
    Returns arrays: a, b (projection with stale in / out, 0 if not kept), stale, dressed,
    y (0 when he did not dress), big (role >= 10)."""
    R = roles(D, prm)
    rows = D["rows"]
    prev = _prev_inactive(D)
    out = defaultdict(list)
    for g in D["games"]:
        if g["season"] != season:
            continue
        K = {i for i in g["idx"] if rows[i]["inactive"] and float(R["base"][i]) >= 10 and _u(i) >= miss}
        cand = [i for i in g["idx"] if i not in K]
        rank = lambda c: set(sorted(c, key=lambda i: (not rows[i]["starter"], -float(R["base"][i])))[:cut])  # noqa: E731
        keep = rank(cand)
        stale = {i for i in keep if prev[i] is True and not (mode == "confirmed" and rows[i]["starter"])
                 and (opener_stale or rows[i]["team_game_no"] > 1)}
        truth = {i: rows[i]["inactive"] for i in g["idx"]}

        def proj(kept):
            for i in g["idx"]:
                rows[i]["inactive"] = i not in kept
            try:
                return project_game(D, g, R, prm, mode)
            finally:
                for i in g["idx"]:
                    rows[i]["inactive"] = truth[i]
        pa = proj(keep)
        keep_b = rank([i for i in cand if i not in stale]) if stale else keep
        pb = proj(keep_b) if stale else pa
        for i in g["idx"]:
            dressed = not truth[i]
            if not dressed and i not in keep:
                continue
            if dressed and rows[i]["actual"] is None:
                continue
            out["a"].append(pa.get(i, 0.0) if i in keep else 0.0)
            out["b"].append(pb.get(i, 0.0) if i in keep_b else 0.0)
            out["stale"].append(i in stale)
            out["dressed"].append(dressed)
            out["y"].append(float(rows[i]["actual"]) if dressed else 0.0)
            out["big"].append(float(R["base"][i]) >= 10)
    return {k: np.array(v, dtype=bool if k in ("stale", "dressed", "big") else float) for k, v in out.items()}


def avail_score(P: dict, w: float | None, p: tuple | None) -> dict:
    """minutes = w x (stale in) + (1 - w) x (stale out) for everyone else; a stale player's
    own minutes stay 'if he dresses' (a), his DFS mean is p x a (p by role < 10 / >= 10).
    w / p None = shipped (everyone in, full minutes). Two metrics:
      active  RMSE of minutes over players who dressed (the loop's metric)
      board   RMSE of the DFS mean (p x minutes for stale) over the board, phantoms = 0."""
    a, b, st, dr, y, big = P["a"], P["b"], P["stale"], P["dressed"], P["y"], P["big"]
    w_ = 1.0 if w is None else w
    m = np.where(st, a, w_ * a + (1 - w_) * b)
    pp = np.ones_like(a) if p is None else np.where(big, p[1], p[0])
    mean = np.where(st, pp * m, m)
    ea, eb, es = (m - y)[dr], mean - y, (mean - y)[dr]
    return {"active": float(np.sqrt((ea ** 2).mean())), "active_bias": float(ea.mean()), "n_active": int(dr.sum()),
            "active_mean": float(np.sqrt((es ** 2).mean())),
            "board": float(np.sqrt((eb ** 2).mean())), "board_bias": float(eb.mean()), "n_board": len(y),
            "phantoms": int((~dr).sum()), "stale": int(st.sum()), "stale_dressed": float(dr[st].mean()) if st.any() else float("nan")}


def avail_fit(P: dict) -> tuple:
    """On a TRAIN season: w = the closed-form squared-error optimum for the non-stale rows
    who dressed; p = each role bucket's share of stale players who dressed (the
    squared-error optimum of p x a against y when a is right)."""
    a, b, st, dr, y, big = P["a"], P["b"], P["stale"], P["dressed"], P["y"], P["big"]
    k = ~st & dr
    d, e = (a - b)[k], (b - y)[k]
    w = float(np.clip(-(d * e).sum() / max((d * d).sum(), 1e-9), 0, 1))
    p = tuple(float((a[st & (big == bb)] * y[st & (big == bb)]).sum() / max((a[st & (big == bb)] ** 2).sum(), 1e-9))
              for bb in (False, True))
    return w, p


# ── critique cycle 7: the NBA's official injury report ────────────────────────
REPORT_CATS = ("Doubtful", "Questionable", "Probable", "stale")


def fetch_tips(seasons=("2024-25", "2025-26")) -> None:
    """data/nba_ground_truth/schedule_tips.json: {game id: tip 'YYYY-MM-DDTHH:MM:SSZ'} --
    the 'Z' is NBA.com's label, the clock is Eastern. One stats.nba.com call a season."""
    import time as _time
    import urllib.request
    from edge.nba_availability import _headers
    tips = {}
    for s_ in seasons:
        url = f"https://stats.nba.com/stats/scheduleleaguev2?Season={s_}&LeagueID=00"
        with urllib.request.urlopen(urllib.request.Request(url, headers=_headers()), timeout=60) as r:
            d = json.load(r)
        tips.update({g["gameId"]: g["gameDateTimeEst"] for day in d["leagueSchedule"]["gameDates"]
                     for g in day["games"] if g["gameId"].startswith("002")})
        _time.sleep(1.5)
    (GT / "schedule_tips.json").write_text(json.dumps(tips))


def report_status(D: dict, lead_min: int = 60, espn_like: bool = False) -> dict:
    """{row: official injury-report status} from the report published `lead_min` before
    the game's tip, floored to the hour, falling back 1-2 hours (edge.nba_injury_report,
    cache only: scripts fetch the archive). None = not listed; rows of a game with no
    report are absent. Names join on edge.names.norm over the night's report.
    `espn_like` keeps only what ESPN's Out feed + the app used through cycle 6: Out for
    an injury (not "G League ..."), Doubtful / Questionable / Probable ignored."""
    from datetime import datetime as _dt, timedelta as _td
    from edge import nba_injury_report as IR
    from edge.names import norm
    names: dict = {}
    for f in sorted(CACHE.glob("*.json")):
        try:
            for g_ in parse(json.loads(f.read_text())):
                for p_ in g_["players"]:
                    names[str(p_["id"])] = p_["name"]
        except (ValueError, KeyError):
            continue
    tips = json.loads((GT / "schedule_tips.json").read_text())
    parsed: dict = {}
    out = {}
    for g in D["games"]:
        t = tips.get(g["gid"])
        if not t:
            continue
        at = _dt.fromisoformat(t.replace("Z", "")) - _td(minutes=lead_min)
        got = None
        for back in (0, 1, 2):
            got = IR.report(at.date().isoformat(), at.hour - back, 0, fetch=False)
            if got:
                break
        if not got:
            continue
        if got[0] not in parsed:
            parsed[got[0]] = {norm(IR.full_name(a, b)): (st if not (espn_like and st == "Out" and why.startswith("G League"))
                                                          else None) if not espn_like or st == "Out" else None
                              for a, b, st, why in IR.parse(got[1])["rows"]}
        st_by = parsed[got[0]]
        for i in g["idx"]:
            out[i] = st_by.get(norm(names.get(D["rows"][i]["pid"], "")))
    return out


def report_preds(D: dict, prm: dict, season: str, mode: str, status: dict, cut: int = 15) -> dict:
    """The live state with the official report as the Out list. Out -> not dressing;
    the cut keeps `cut` (starters first, then role). A kept player is UNCERTAIN if he is
    Doubtful / Questionable / Probable (Available counts as Probable), or not listed but
    did not dress in his team's latest game this season ("stale"); announced starters
    are certain. For any P(dress) per (category, role >= 10) the expected minutes are
      m_i = A_i + sum over uncertain k != i of (1 - p_k) (B_k,i - A_i),  x p_i if i is uncertain
    with A = everyone kept dresses, B_k = k sits (cut re-run). Stored per row: A, the
    (k's category, B_k - A) terms, own category, dressed, actual (0 if not dressed)."""
    R = roles(D, prm)
    rows = D["rows"]
    prev = _prev_inactive(D)
    out = defaultdict(list)
    for g in D["games"]:
        if g["season"] != season or not all(i in status for i in g["idx"]):
            continue
        truth = {i: rows[i]["inactive"] for i in g["idx"]}
        cand = [i for i in g["idx"] if status[i] != "Out"]
        rank = lambda c: set(sorted(c, key=lambda i: (not rows[i]["starter"], -float(R["base"][i])))[:cut])  # noqa: E731
        keep = rank(cand)

        def cat(i):
            if mode == "confirmed" and rows[i]["starter"]:
                return None
            st = status[i]
            if st in ("Doubtful", "Questionable"):
                return st
            if st in ("Probable", "Available"):
                return "Probable"
            if prev[i] is True and rows[i]["team_game_no"] > 1:
                return "stale"
            return None
        unc = {i: (cat(i), float(R["base"][i]) >= 10) for i in keep if cat(i)}

        def proj(kept):
            for i in g["idx"]:
                rows[i]["inactive"] = i not in kept
            try:
                return project_game(D, g, R, prm, mode)
            finally:
                for i in g["idx"]:
                    rows[i]["inactive"] = truth[i]
        A = proj(keep)
        B = {k: proj(rank([i for i in cand if i != k])) for k in unc}
        members = set(keep) | {i for b in B.values() for i in b} | {i for i in g["idx"] if not truth[i]}
        for i in g["idx"]:
            if i not in members or (not truth[i] and rows[i]["actual"] is None):
                continue
            a = A.get(i, 0.0)
            out["a"].append(a)
            out["terms"].append([(unc[k], B[k].get(i, 0.0) - a) for k in unc if k != i])
            out["own"].append(unc.get(i))
            out["dressed"].append(not truth[i])
            out["y"].append(float(rows[i]["actual"]) if not truth[i] else 0.0)
            out["status"].append(status[i])
    return dict(out)


def report_score(P: dict, p: dict | None) -> dict:
    """Board / active RMSE for P(dress) p = {(category, big): prob}; None = no hedge
    (every kept player dresses: the report's Out list alone)."""
    pr = (lambda c: 1.0) if p is None else (lambda c: p[c])  # noqa: E731
    m = np.array([a + sum((1 - pr(c)) * d for c, d in t) for a, t in zip(P["a"], P["terms"])])
    own = np.array([pr(c) if c else 1.0 for c in P["own"]])
    mean = own * m
    y, dr = np.array(P["y"]), np.array(P["dressed"])
    eb, ea = mean - y, (m - y)[dr]
    # "fixed": the board's squared error over the ACTIVE player-games -- comparable across
    # Out lists that keep different numbers of phantoms (a player no board keeps costs 0)
    return {"board": float(np.sqrt((eb ** 2).mean())), "board_bias": float(eb.mean()), "n_board": len(y),
            "active": float(np.sqrt((ea ** 2).mean())), "n_active": int(dr.sum()), "phantoms": int((~dr).sum()),
            "fixed": float(np.sqrt((eb ** 2).sum() / max(int(dr.sum()), 1)))}


def report_fit(P: dict) -> dict:
    """P(dress) per (category, role >= 10): the share of such kept players who dressed (train)."""
    acc = defaultdict(lambda: [0, 0])
    for c, d in zip(P["own"], P["dressed"]):
        if c:
            acc[c][0] += 1
            acc[c][1] += d
    return {c: (acc[c][1] / acc[c][0] if acc[c][0] else 1.0) for c in
            [(k, b) for k in REPORT_CATS for b in (False, True)]}, dict(acc)

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["diagnose", "ablate", "fit", "salary", "report", "cycle1", "cycle2", "live",
                                    "cycles", "cycle4", "cycle5", "cycle6", "cycle7", "cycle10", "refit-gap"])
    ap.add_argument("--mode", default="confirmed", choices=["confirmed", "inactives"])
    ap.add_argument("--write", action="store_true", help="cycle1 / cycle5: write edge/nba_minutes_gap.py")
    a = ap.parse_args()
    D = load()
    if a.cmd == "diagnose":
        for mode in ("confirmed", "inactives"):
            print(table(evaluate(D, SHIPPED, mode), f"shipped model, {mode}"))
        return 0
    head = ["| model | mode | season | RMSE all | " + " | ".join(KEY) + " |", "|---|---|---|---|" + "---|" * len(KEY)]
    if a.cmd == "cycle1":
        F = final_params()
        coef = fit_gap(D, F)
        P = {**F, "alloc": "rank_gap", "gap_coef": coef}
        print(report(D, P, legacy=False))
        print("\n".join(salary_test(D, P)))
        if a.write:
            write_gap_module(D, coef, F)
        return 0
    if a.cmd == "cycle2":
        F = final_params()
        best = None
        for lam in (0.1, 0.15, 0.2, 0.25, 0.3):
            for q in (0.2, 0.3, 0.4, 0.5):
                P = {**F, "recency_lam": lam, "drop_q": q}
                P["gap_coef"] = fit_gap(D, P)
                o = obj(run(D, P, seasons=["2024-25"]), "2024-25")
                print(f"  lambda {lam} q {q}: 2024-25 RMSE (both modes) {o:.4f}", file=sys.stderr, flush=True)
                if best is None or o < best[0]:
                    best = (o, lam, q, P)
        o, lam, q, P = best
        print(f"  chosen on 2024-25: lambda {lam}, q {q}", file=sys.stderr)
        for guard in (False,):
            Pg = {**P, "recency_guard": guard}
            Pg["gap_coef"] = fit_gap(D, Pg)
            print(f"  guards off, 2024-25 RMSE (both modes): {obj(run(D, Pg, seasons=['2024-25']), '2024-25'):.4f} "
                  f"vs on {o:.4f}", file=sys.stderr)
        print(report(D, P, legacy=False))
        if a.write:
            write_gap_module(D, P["gap_coef"], P, cycle=2, extra=f"recency_lam {lam}, drop_q {q}")
        return 0
    if a.cmd == "cycle10":
        # target substitutions: P(start) knowing who is out, read by the rank-gap step as a
        # per-cell term on u (inactives state). Everything fitted on 2024-25.
        F = final_params()
        P = {**F, "pstart_w": fit_pstart(D, F)}
        P["gap_coef"] = {**F["gap_coef"], "inactives": fit_gap(D, P)["inactives"]}
        Fr = {**F, "gap_coef": {**F["gap_coef"], "inactives": fit_gap(D, F)["inactives"]}}
        print(report(D, F, legacy=False))
        print("\n(shipped roles, gap refit on 2024-25 -- isolates the refit)")
        print(report(D, Fr, legacy=False))
        print("\n(P(start) term)")
        print(report(D, P, legacy=False))
        status = report_status(D)
        for m in ("confirmed", "inactives"):
            tr = report_preds(D, F, "2024-25", m, status)
            p, _ = report_fit(tr)
            for s_ in ("2025-26",):
                h = report_score(report_preds(D, F, s_, m, status), p)
                z = report_score(report_preds(D, P, s_, m, status), p)
                print(f"official report, {m}, {s_}: board {h['fixed']:.3f} -> {z['fixed']:.3f}, active "
                      f"{h['active']:.3f} -> {z['active']:.3f} (n {z['n_active']:,})", flush=True)
        print(f"PSTART_W = {P['pstart_w']}")
        print(f"GAP_INACTIVES = {P['gap_coef']['inactives']}")
        return 0
    if a.cmd == "cycle7":
        # the official injury report as the Out list, P(dress) by status fitted on 2024-25.
        # Needs the archive: scripts fetch it into data/nba_injury_reports/ (see the log).
        if not (GT / "schedule_tips.json").exists():
            fetch_tips()
        F = final_params()
        status, espn = report_status(D), report_status(D, espn_like=True)
        print(f"  report status for {len(status):,} rows", file=sys.stderr, flush=True)
        for m in ("confirmed", "inactives"):
            tr = report_preds(D, F, "2024-25", m, status)
            p, n = report_fit(tr)
            lo, hi = NM.AVAIL_P[m]
            p6 = {(c, b): 1.0 for c in REPORT_CATS for b in (False, True)}
            p6.update({("stale", False): lo, ("stale", True): hi})
            print(f"\n{m}: P(dress) fitted on 2024-25 (n kept): " + ", ".join(
                f"{c} {'10+' if b else '<10'} {p[(c, b)]:.2f} ({n.get((c, b), [0])[0]})" for c, b in p))
            print("| season | ESPN-like Out + cycle-6 stale hedge (shipped) | official Out only | official + P(dress) "
                  "by status | active RMSE: shipped -> official + P |\n|---|---|---|---|---|")
            for s_ in ("2024-25", "2025-26"):
                P = tr if s_ == "2024-25" else report_preds(D, F, s_, m, status)
                E = report_preds(D, F, s_, m, espn)
                e, h, z = report_score(E, p6), report_score(P, None), report_score(P, p)
                print(f"| {s_} | {e['fixed']:.3f} ({e['phantoms']:,} phantoms) | {h['fixed']:.3f} | **{z['fixed']:.3f}** "
                      f"({z['phantoms']:,} phantoms) | {e['active']:.3f} -> {z['active']:.3f} |", flush=True)
        return 0
    if a.cmd == "cycle6":
        # stale players the Out list may have missed: fit w, p on 2024-25 at each miss rate,
        # then choose ONE (w, p) by minimax regret (board metric) over the miss rates
        F = final_params()
        misses = (0.0, 0.25, 0.5)
        P = {(s_, m, mi): avail_preds(D, F, s_, m, mi) for s_ in ("2024-25", "2025-26")
             for m in ("confirmed", "inactives") for mi in misses}
        print("  built", file=sys.stderr, flush=True)
        for m in ("confirmed", "inactives"):
            fits = {mi: avail_fit(P[("2024-25", m, mi)]) for mi in misses}
            for mi, (w, p) in fits.items():
                x = P[("2024-25", m, mi)]
                print(f"  {m} miss {mi:.2f}: 2024-25 fit w {w:.3f}, p {p[0]:.3f} / {p[1]:.3f}; stale {avail_score(x, None, None)['stale']:,}, "
                      f"dressed {avail_score(x, None, None)['stale_dressed']:.3f}", file=sys.stderr, flush=True)
            cands = [(w_, (p0, p1)) for w_ in np.round(np.arange(0, 1.01, 0.1), 2) for p0 in (0.15, 0.2, 0.25, 0.3, 0.4)
                     for p1 in np.round(np.arange(0.3, 1.01, 0.1), 2)]
            best = {mi: min(avail_score(P[("2024-25", m, mi)], *c)["board"] for c in cands) for mi in misses}
            reg = [max(avail_score(P[("2024-25", m, mi)], *c)["board"] - best[mi] for mi in misses) for c in cands]
            w, p = cands[int(np.argmin(reg))]
            print(f"\n{m}: chosen on 2024-25 (minimax regret {min(reg):.4f}): w {w:.3f}, p {p[0]:.3f} (role < 10) / {p[1]:.3f} (10+)")
            print("| Out list misses | season | active n | active RMSE: shipped -> soft (bias) | active, DFS mean | board n (phantoms) | "
                  "board RMSE: shipped -> soft (bias) |\n|---|---|---|---|---|---|---|")
            for mi in misses:
                for s_ in ("2024-25", "2025-26"):
                    x = P[(s_, m, mi)]
                    h, z = avail_score(x, None, None), avail_score(x, w, p)
                    print(f"| {mi:.0%} | {s_} | {h['n_active']:,} | {h['active']:.3f} ({h['active_bias']:+.2f}) -> **{z['active']:.3f}** "
                          f"({z['active_bias']:+.2f}) | {z['active_mean']:.3f} | {h['n_board']:,} ({h['phantoms']:,}) | {h['board']:.3f} ({h['board_bias']:+.2f}) -> "
                          f"**{z['board']:.3f}** ({z['board_bias']:+.2f}) |")
        return 0
    if a.cmd == "cycle5":
        # the starting role's own memory: grid on 2024-25 ONLY, rank-gap coefficients
        # refitted for every cell; objective = 2024-25 RMSE averaged over both modes
        F = final_params()
        best = None
        for d_ in (0.8, 0.85, 0.9, 0.93, 0.95, 0.97):
            for k_ in (1.0, 2.0, 3.0, 5.0, 8.0):
                P = {**F, "ms_decay": d_, "ms_kappa": k_}
                P["gap_coef"] = fit_gap(D, P)
                res = run(D, P, seasons=["2024-25"])
                o = obj(res, "2024-25")
                print(f"  ms_decay {d_} ms_kappa {k_}: 2024-25 RMSE confirmed "
                      f"{res['confirmed']['2024-25']['all']['rmse']:.4f}, both modes {o:.4f}", file=sys.stderr, flush=True)
                if best is None or o < best[0]:
                    best = (o, d_, k_, P)
        o, d_, k_, P = best
        print(f"  chosen on 2024-25: ms_decay {d_}, ms_kappa {k_}", file=sys.stderr)
        print(report(D, P, legacy=False))
        for live in (None, ("role10", 15)):
            for name, q in (("before", F), ("after", P)):
                E = expected_preds(D, q, seasons=("2025-26",), live=live)["2025-26"]
                x = expected_score(E, NM.EXPECTED_W, 0.25)
                print(f"Expected state, 25% wrong, {'live' if live else 'lab'}, {name}: "
                      f"RMSE {x['rmse']:.3f} MAE {x['mae']:.3f} ({x['bias']:+.2f})")
        for m in ("confirmed", "inactives"):
            print(f"live state (injury-list Out, cut 15), {m}, 2025-26 RMSE: before "
                  f"{live_replay(D, D, F, '2025-26', 'role10', 15, mode=m)['rmse']:.3f} "
                  f"after {live_replay(D, D, P, '2025-26', 'role10', 15, mode=m)['rmse']:.3f}")
        if a.write:
            write_gap_module(D, P["gap_coef"], P, cycle=5,
                             extra=f"recency_lam {P['recency_lam']}, drop_q {P['drop_q']}, ms_decay {d_}, ms_kappa {k_}")
        return 0
    if a.cmd == "refit-gap":
        # Refit the rank-gap cells on 2024-25 against TODAY's roles (Musk "simplify" review,
        # 2026-10-28: the cells were last fitted at cycle 5 and cycles 6-13 changed the
        # roles under them). Run after any change to the roles; ship only if 2025-26 holds.
        F = final_params()
        P = {**F, "gap_coef": fit_gap(D, F)}
        for name, prm in (("shipped cells", F), ("refitted cells", P)):
            r = run(D, prm)
            for m in r:
                print(name, m, {sn: (round(c["all"]["rmse"], 4), round(c["all"]["mae"], 4)) for sn, c in r[m].items()})
        for m in ("confirmed", "inactives"):
            print(f"live state (injury-list Out, cut 15), {m}, 2025-26 RMSE: shipped "
                  f"{live_replay(D, D, F, '2025-26', 'role10', 15, mode=m)['rmse']:.3f} "
                  f"refit {live_replay(D, D, P, '2025-26', 'role10', 15, mode=m)['rmse']:.3f}")
        if a.write:
            write_gap_module(D, P["gap_coef"], P, cycle=14, cmd="refit-gap",
                             extra="all shipped roles through cycle 13 (refit, no new term)")
        return 0
    if a.cmd == "cycle4":
        # the Expected five: blend w x project(Expected five) + (1 - w) x project(no five).
        # w is chosen on 2024-25 ONLY, by minimax regret over the plausible error rates:
        # 2026-27 measured 7 of 28 (25%, 95% CI ~11-45%) but on opening nights, when
        # rotations are least settled, so 10-30% is the range a season should live in.
        F = final_params()
        rhos = (0.10, 0.15, 0.20, 0.25, 0.30)
        out = []
        for name, live in (("lab protocol (full inactive list)", None),
                           ("live state (injury-list Out, cut 15)", ("role10", 15))):
            P = expected_preds(D, F, live=live)
            tr = P["2024-25"]
            grid = [round(x, 2) for x in np.arange(0.5, 1.001, 0.05)]
            best = {r: min(expected_score(tr, w, r)["rmse"] for w in grid) for r in rhos}
            regret = {w: max(expected_score(tr, w, r)["rmse"] - best[r] for r in rhos) for w in grid}
            w = min(grid, key=lambda x: (regret[x], -x))
            print(f"{name}: w chosen on 2024-25 = {w} (max regret {regret[w]:.4f}); "
                  f"closed-form w* at 0.10 / 0.25: {expected_w(tr, 0.10):.3f} / {expected_w(tr, 0.25):.3f}",
                  file=sys.stderr, flush=True)
            out += [f"\n{name}, 2025-26 held out (w = {w} chosen on 2024-25), RMSE / MAE (bias):\n",
                    "| Expected five wrong in | n | as confirmed (shipped) | no five (inactives mode) | blend w | "
                    "Expected right: shipped / blend RMSE | Expected wrong: shipped / blend RMSE | "
                    "promoted starters, Expected right: shipped / blend MAE (bias) |",
                    "|---|---|---|---|---|---|---|---|"]
            te = P["2025-26"]
            for r in (0.0, 0.10, 0.15, 0.20, 0.25, 0.30):
                h, z, b = expected_score(te, 1.0, r), expected_score(te, 0.0, r), expected_score(te, w, r)
                cell = lambda x: f"{x['rmse']:.3f} / {x['mae']:.3f} ({x['bias']:+.2f})"  # noqa: E731
                out.append(f"| {r:.0%} | {h['n']:,} | {cell(h)} | {cell(z)} | **{cell(b)}** | {h['right']:.3f} / {b['right']:.3f} | "
                           f"{h['wrong']:.3f} / {b['wrong']:.3f} | {h['promoted'][0]:.2f} ({h['promoted'][1]:+.1f}) / "
                           f"{b['promoted'][0]:.2f} ({b['promoted'][1]:+.1f}), n {h['promoted'][2]:,} |")
        print("\n".join(out))
        return 0
    if a.cmd == "cycles":
        # every version of the critique loop, scored the same way (the final report)
        F = final_params()
        v2 = {**F, "alloc": "power", "gap_coef": None, "recency_lam": None, "drop_q": None}
        c1 = {**v2, "alloc": "rank_gap"}
        c1["gap_coef"] = fit_gap(D, c1)
        versions = [("v1 (10/15)", SHIPPED), ("cycle 0: v2", v2), ("cycle 1: + rank-gap", c1),
                    ("cycles 2-3: + recency / dropped starter", F)]
        head = ["| version | mode | RMSE | MAE (bias) | opening | rookies | lottery | new team | 1st game back | "
                "scratch teammates | promoted | high role 28+ | 2026-27 RMSE / MAE (n) |", "|---" * 13 + "|"]
        rows_ = []
        for name, p in versions:
            res = run(D, p)
            for m in ("confirmed", "inactives"):
                c, z = res[m]["2025-26"], res[m].get("2026-27", {})
                cell = lambda k: f"{c[k]['mae']:.2f} ({c[k]['bias']:+.1f})" if k in c else "-"  # noqa: E731
                rows_.append(f"| {name} | {m} | {c['all']['rmse']:.3f} | {cell('all')} | {cell('opening (team games 1-5)')} | "
                             f"{cell('rookie / no NBA history')} | {cell('lottery rookie')} | {cell('new team (first 5)')} | "
                             f"{cell('back from 5+ missed')} | {cell('teammate of a scratch')} | {cell('promoted starter')} | "
                             f"{cell('high role (base 28+)')} | "
                             + (f"{z['all']['rmse']:.2f} / {z['all']['mae']:.2f} ({z['all']['n']})" if z else "-") + " |")
                print(rows_[-1], file=sys.stderr, flush=True)
        Dp = load(verbose=False, phantom=True)
        live = ["\n| version | live state on 2025-26 | mode | RMSE | MAE | bias |", "|---|---|---|---|---|---|"]
        for name, p in versions:
            for m in ("confirmed", "inactives"):
                r_ = live_replay(D, Dp, p, "2025-26", "stale10", 13, mode=m)
                live.append(f"| {name} | broken feed: phantom history, DK-like Out, cut 13 | {m} | {r_['rmse']:.3f} | "
                            f"{r_['mae']:.3f} | {r_['bias']:+.2f} |")
        for m in ("confirmed", "inactives"):
            r_ = live_replay(D, D, F, "2025-26", "role10", 15, mode=m)
            live.append(f"| cycle 3: availability fixed | box-score history, injury-list Out, cut 15 | {m} | "
                        f"{r_['rmse']:.3f} | {r_['mae']:.3f} | {r_['bias']:+.2f} |")
        print("\n".join(head + rows_ + live))
        return 0
    if a.cmd == "live":
        Dp = load(verbose=False, phantom=True)
        F = final_params()
        out = ["| history | tonight's Out list | 13-man cut | season | n | RMSE | MAE | bias |", "|---|---|---|---|---|---|---|---|"]
        for season in ("2024-25", "2025-26"):
            for hist, Dh in (("clean", D), ("phantom (2026-27 feed)", Dp)):
                for known, cut in (("all", None), ("all", 13), ("role10", 13), ("stale10", 13)):
                    m = live_replay(D, Dh, F, season, known, cut)
                    out.append(f"| {hist} | {known} | {'yes' if cut else 'no'} | {season} | {m['n']:,} | "
                               f"{m['rmse']:.3f} | {m['mae']:.3f} | {m['bias']:+.2f} |")
                    print(out[-1], file=sys.stderr, flush=True)
        print("\n".join(out))
        return 0
    if a.cmd == "report":
        print(report(D, final_params()))
        return 0
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
