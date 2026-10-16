#!/usr/bin/env python3
"""Jeff Mans's (One MANS Opinion) fantasy claims, tested on our data. Adam, 2026-10-14:
"is there a philosophy or methodology we can distill?" -- see MANS_METHODOLOGY.md.

Rules written before the numbers were looked at:

  C1 position stability (ep 322: "running backs are a lot more stable ... wide receivers
     are a greyhound race every single week ... quarterbacks erratic early, then level out")
     Weeks 1-3 DK points/game -> weeks 4-18 DK points/game, rank correlation, per position,
     per season; share of the early top-N still top-N; single week -> next week correlation.
     Players need >= 2 early and >= 6 late games (QBs: games with >= 15 attempts).
  C2 opportunity over production (ep 321: "being on the field is the only way to score")
     Does early workload predict weeks 4-18 beyond early points? Workload = attempts+carries
     (QB), carries + 2*targets (RB), target share (WR/TE). Fit on 2020-21, score on 2023-24.
  C3 the crowd chases the box score (ep 321: "everybody they're talking up, take a second
     look ... the consensus down, they are wrong 95% of the time")
     2026 main-slate single-entry GPPs: rank corr, within slate, between our ownership miss
     log(own+.5)-log(pred+.5) and (last week's DK points - this week's projection).
     Positive = the field chases last week beyond what our model expects.

Data: data/nfl_ground_truth/player_week_<season>.json (scripts/nfl_ground_truth_collect.py)
and nflverse's stats_player_week_2026.csv (downloaded on first run, gitignored).
"""
from __future__ import annotations

import csv
import json
import math
import sqlite3
import statistics as st
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from edge.names import norm                     # noqa: E402
from edge.nfl import actual_offense_points      # noqa: E402

GT = ROOT / "data" / "nfl_ground_truth"
WEEK_2026 = GT / "stats_player_week_2026.csv"
WEEK_2026_URL = ("https://github.com/nflverse/nflverse-data/releases/download/"
                 "stats_player/stats_player_week_2026.csv")
SEASONS = (2020, 2021, 2023, 2024)
TOP = {"QB": 12, "RB": 24, "WR": 24, "TE": 12}
# (contest_id, draft_group, NFL week, pre-lock dfs_nfl scan or None): the 2026 main-slate
# single-entry GPPs. Week 1 has no prior week. Week 4 (10/4) was built on the phone, so
# nothing was logged; it is rebuilt with today's code from the last full pre-lock scan
# (4700, 12:44 ET) and DK's salaries -- its own MAE 2.56 vs the logged slates' 2.59-2.72.
GPP_SLATES = ((195661382, 153428, 2, None), (195921991, 153769, 3, None),
              (196169741, 154078, 4, 4700), (196420207, 154468, 5, None))
REBUILD_CACHE = ROOT / "data" / "cache" / "mans_rebuilt_pool_{gid}.json"


def _f(v) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def rank_corr(a, b) -> float:
    a, b = np.asarray(a), np.asarray(b)
    return float(np.corrcoef(np.argsort(np.argsort(a)), np.argsort(np.argsort(b)))[0, 1])


def _workload(pos: str, r: dict) -> float:
    if pos == "QB":
        return _f(r["attempts"]) + _f(r["carries"])
    if pos == "RB":
        return _f(r["carries"]) + 2 * _f(r["targets"])
    return _f(r["target_share"])


def season_split(season: int) -> dict[str, dict]:
    """Per position: arrays of early PPG, late PPG, early workload; plus week-to-week."""
    rows = json.load(open(GT / f"player_week_{season}.json"))
    by = defaultdict(list)
    for r in rows:
        if r.get("season_type") != "REG" or r["position"] not in TOP:
            continue
        if r["position"] == "QB" and _f(r["attempts"]) < 15:
            continue
        r["dk"], r["wk"] = actual_offense_points(r), int(r["week"])
        by[(r["player_id"], r["position"])].append(r)
    out = {}
    for pos in TOP:
        E, L, O, a, b = [], [], [], [], []
        for (_, p), g in by.items():
            if p != pos:
                continue
            pts = {r["wk"]: r["dk"] for r in g}
            a += [pts[w] for w in pts if w + 1 in pts]
            b += [pts[w + 1] for w in pts if w + 1 in pts]
            early = [r for r in g if r["wk"] <= 3]
            late = [r for r in g if r["wk"] >= 4]
            if len(early) < 2 or len(late) < 6:
                continue
            E.append(st.mean(r["dk"] for r in early))
            L.append(st.mean(r["dk"] for r in late))
            O.append(st.mean(_workload(pos, r) for r in early))
        E, L, O = map(np.array, (E, L, O))
        n = TOP[pos]
        stay = len(set(np.argsort(-E)[:n]) & set(np.argsort(-L)[:n])) / n
        out[pos] = dict(E=E, L=L, O=O, rho=rank_corr(E, L), opp_rho=rank_corr(O, L),
                        stay=stay, w2w=float(np.corrcoef(a, b)[0, 1]))
    return out


def c1_c2() -> None:
    S = {s: season_split(s) for s in SEASONS}
    print("C1  wk1-3 PPG -> wk4-18 PPG rank corr | early top-N still top-N | week -> next week")
    for pos in TOP:
        print(f"  {pos}  " + "  ".join(
            f"{s}: {S[s][pos]['rho']:.2f} {S[s][pos]['stay']:.0%} {S[s][pos]['w2w']:.2f} "
            f"(n={len(S[s][pos]['E'])})" for s in SEASONS))
    print("\nC2  early workload alone vs early points alone -> wk4-18 (rank corr)")
    for pos in TOP:
        print(f"  {pos}  " + "  ".join(
            f"{s}: opp {S[s][pos]['opp_rho']:.2f} pts {S[s][pos]['rho']:.2f}" for s in SEASONS))
    print("\nC2b wk4-18 PPG ~ early pts (+ early workload): fit 2020-21, score 2023-24")
    rng = np.random.default_rng(0)
    for pos in TOP:
        def stack(seasons, key):
            return np.concatenate([S[s][pos][key] for s in seasons])
        ytr, yte = stack((2020, 2021), "L"), stack((2023, 2024), "L")
        preds = []
        for cols in (("E",), ("E", "O")):
            Xtr = np.column_stack([np.ones_like(ytr)] + [stack((2020, 2021), c) for c in cols])
            Xte = np.column_stack([np.ones_like(yte)] + [stack((2023, 2024), c) for c in cols])
            preds.append(Xte @ np.linalg.lstsq(Xtr, ytr, rcond=None)[0])
        d = np.abs(preds[0] - yte) - np.abs(preds[1] - yte)
        bs = [d[rng.integers(0, len(d), len(d))].mean() for _ in range(2000)]
        print(f"  {pos}: MAE pts-only {np.mean(np.abs(preds[0]-yte)):.2f} -> +workload "
              f"{np.mean(np.abs(preds[1]-yte)):.2f}  gain {d.mean():+.2f} "
              f"[{np.percentile(bs, 2.5):+.2f}, {np.percentile(bs, 97.5):+.2f}]  n={len(yte)} players")


def c3() -> None:
    if not WEEK_2026.exists():
        urllib.request.urlretrieve(WEEK_2026_URL, WEEK_2026)
    wk = defaultdict(dict)
    for r in csv.DictReader(open(WEEK_2026)):
        if r["season_type"] == "REG" and r["position"] in TOP:
            wk[int(r["week"])][norm(r["player_display_name"])] = actual_offense_points(r)
    con = sqlite3.connect(f"file:{ROOT / 'data' / 'contests.db'}?mode=ro", uri=True)
    print("\nC3  2026 GPP ownership miss vs (last week DK pts - this week proj)")
    data = {}
    for cid, dg, w, scan in GPP_SLATES:
        own = dict(con.execute(
            "select player_norm, pct_drafted from player_result where contest_id=?", (cid,)))
        if scan is None:
            proj = {n: (p, pos, op) for n, p, pos, op in con.execute(
                "select player_norm, proj, pos, own_pred from projection "
                "where draft_group=? and own_pred is not null order by logged_at", (dg,))}
        else:
            proj = {norm(p["name"]): (p["proj"], p["pos"].split("/")[0], p["own"])
                    for p in rebuilt_pool(dg, scan)}
        res, surprise, rows = [], [], []
        for n, (p, pos, op) in proj.items():
            if pos not in TOP or p is None or n not in own:
                continue
            last = wk[w - 1].get(n)
            rows.append((own[n], op, 0.0 if last is None else max(-15, min(15, last - p)) / 10))
            if p < 5 or last is None:
                continue
            res.append(math.log(own[n] + .5) - math.log(op + .5))
            surprise.append(last - p)
        data[w] = np.array(rows)
        print(f"  week {w} (contest {cid}{', rebuilt' if scan else ''}): rank corr "
              f"{rank_corr(res, surprise):+.3f}  n={len(res)} players")

    # The candidate fix: own * exp(beta * surprise/10), renormalised to the slate's total,
    # beta chosen on the other slates and scored on the held-out one.
    def mae(d, beta):
        adj = d[:, 1] * np.exp(beta * d[:, 2])
        adj *= d[:, 1].sum() / adj.sum()
        return float(np.mean(np.abs(adj - d[:, 0])))
    grid = np.linspace(0, 1.5, 31)
    print("  leave-one-slate-out recency term (ownership MAE, pct pts):")
    for w in data:
        b = grid[np.argmin([sum(mae(data[k], g) for k in data if k != w) for g in grid])]
        print(f"    held-out week {w}: beta {b:.2f}  {mae(data[w], 0):.2f} -> "
              f"{mae(data[w], b):.2f}  (n={len(data[w])})")


def rebuilt_pool(gid: int, scan: int) -> list[dict]:
    """A past NFL slate's pool rebuilt from the odds store, cached (gitignored)."""
    path = Path(str(REBUILD_CACHE).format(gid=gid))
    if path.exists():
        return json.loads(path.read_text())
    from edge import dfs, dfs_run_nfl as R
    from edge.odds.source import ScrapedOddsClient
    from edge.odds.store import OddsStore
    from scripts.multi_entry_backtest import salaries_for
    sal = salaries_for(gid, "")
    dfs.fetch_draftables = lambda *_a, **_k: sal      # this run only: no live refetch
    groups = [{"DraftGroupId": gid, "GameTypeId": R.CLASSIC_GAME_TYPE,
               "ContestStartTimeSuffix": "", "GameCount": 0}]
    out = R.build_slate(ScrapedOddsClient(OddsStore(), "dfs_nfl", scan_id=scan),
                        draft_group=gid, groups=groups, persist=False)
    pool = [{"name": p["name"], "pos": p["dk_pos"], "proj": p["proj"], "own": p["own"]}
            for p in out["pool"]]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pool))
    return pool


if __name__ == "__main__":
    c1_c2()
    c3()
