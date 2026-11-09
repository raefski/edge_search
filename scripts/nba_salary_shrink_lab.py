#!/usr/bin/env python3
"""NBA: does DK's salary know something our projection doesn't? (2026-11-08)

    python3 scripts/nba_salary_shrink_lab.py report          # player error, held out
    python3 scripts/nba_salary_shrink_lab.py lineups         # optimizer replay vs the cash line
    python3 scripts/nba_salary_shrink_lab.py fit             # constants for edge/dfs_run_nba.py

Why it exists. Adam's NBA entries 10/20-11/7 (57 entries in exported contests, 26 slates)
lost -57%. By our own projection his lineup sat at the field's 96th (cash) / 99th (GPP)
percentile every night, then scored ~35 points under that projection, while the field's
lineups scored +0.7 / -4.9 against the same projection. Player level: across the slate our
projection was unbiased (miss +0.00 on 1,825 players nobody of ours used), but the top
decile of projected value (pts per $1k) missed -5.2 a player, and 57% of our picks came
from it. The optimizer selects our errors: when we disagree with the salary most, we are
mostly wrong (the winner's curse).

The fix tested here: blend each projection with what its salary implies,
    proj' = A + B * proj + C * salary_k   (+ D * proj for promoted players: news the salary
                                           has not priced, Adam's promoted-bench chalk)
fitted on earlier slates, scored on later ones.

Data: every NBA draft group with a logged pre-lock board (contests.db `projection`, source
'ours', the last row per player = what the board showed at lock). Truth: DK points from the
NBA box score (data/nba_ground_truth/player_gamelog_2026-27.json, scored as DK does); a
boarded player with no box row did not play = 0. Clusters: the slate date (several draft
groups share games; a night is one realisation). Slates on one date never straddle the split.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.names import norm  # noqa: E402

DB = ROOT / "data" / "contests.db"
BOX = ROOT / "data" / "nba_ground_truth" / "player_gamelog_2026-27.json"
SPLIT = "2026-10-31"        # train: slates before; test: on/after (13 / 13 dates as of 11/8)
SLOTS = ("PG", "SG", "SF", "PF", "C", "G", "F", "UTIL")
FITS = {"G": {"PG", "SG"}, "F": {"SF", "PF"}, "UTIL": {"PG", "SG", "SF", "PF", "C"}}
CAP = 50000


def dk(r: dict) -> float:
    s = [r["PTS"], r["REB"], r["AST"], r["STL"], r["BLK"]]
    dd = sum(1 for x in s if (x or 0) >= 10)
    return ((r["PTS"] or 0) + 0.5 * (r["FG3M"] or 0) + 1.25 * (r["REB"] or 0) + 1.5 * (r["AST"] or 0)
            + 2 * (r["STL"] or 0) + 2 * (r["BLK"] or 0) - 0.5 * (r["TOV"] or 0)
            + (1.5 if dd >= 2 else 0) + (3 if dd >= 3 else 0))


def load() -> list[dict]:
    box = {}
    for r in json.loads(BOX.read_text()):
        box[(r["GAME_DATE"], norm(r["PLAYER_NAME"]))] = dk(r)
    played = {d for d, _ in box}
    c = sqlite3.connect(DB)
    out = []
    q = """select draft_group, date, player_norm, name, team, opp, pos, salary, proj, extra, max(logged_at)
           from projection where sport='NBA' and source='ours' and proj > 0 and salary > 0
           group by draft_group, player_norm"""
    for dg, d, pn, name, team, opp, pos, sal, proj, extra, _ in c.execute(q):
        if d not in played:
            continue                         # box not collected yet
        ex = json.loads(extra or "{}")
        out.append({"dg": dg, "date": d, "player": name, "pn": pn, "team": team, "opp": opp,
                    "pos": pos or "", "salary": float(sal), "proj": float(proj),
                    "promoted": ex.get("promoted") in ("1", "True", True),
                    "actual": box.get((d, pn), 0.0)})
    return out


def design(rows: list[dict], variant: str) -> np.ndarray:
    P = np.array([r["proj"] for r in rows]); S = np.array([r["salary"] / 1000 for r in rows])
    if variant == "salary":
        return np.c_[np.ones(len(rows)), P, S]
    if variant == "salary+promoted":
        pr = np.array([r["promoted"] for r in rows], float)
        return np.c_[np.ones(len(rows)), P, S, pr * P]
    raise ValueError(variant)


def fit(rows: list[dict], variant: str) -> np.ndarray:
    return np.linalg.lstsq(design(rows, variant), np.array([r["actual"] for r in rows]), rcond=None)[0]


def predict(rows: list[dict], variant: str, b: np.ndarray | None) -> np.ndarray:
    if variant == "raw":
        return np.array([r["proj"] for r in rows])
    return np.maximum(design(rows, variant) @ b, 0.0)


def _boot(cl, d, n=2000, seed=0):
    ids, inv = np.unique(cl, return_inverse=True)
    s = np.bincount(inv, weights=d); c = np.bincount(inv).astype(float)
    k = np.random.default_rng(seed).integers(0, len(ids), (n, len(ids)))
    b = s[k].sum(1) / c[k].sum(1)
    return d.mean(), np.percentile(b, 5), np.percentile(b, 95)


def report() -> int:
    rows = load()
    tr = [r for r in rows if r["date"] < SPLIT]; te = [r for r in rows if r["date"] >= SPLIT]
    print(f"# NBA salary shrink, held out by date (train < {SPLIT} <= test)\n")
    print(f"train {len(tr)} player-slates / {len({r['dg'] for r in tr})} draft groups / "
          f"{len({r['date'] for r in tr})} dates; test {len(te)} / {len({r['dg'] for r in te})} / "
          f"{len({r['date'] for r in te})}\n")
    A = np.array([r["actual"] for r in te]); cl = np.array([r["date"] for r in te])
    P0 = predict(te, "raw", None)
    v = np.array([r["proj"] / (r["salary"] / 1000) for r in te])
    qs = np.quantile(np.array([r["proj"] / (r["salary"] / 1000) for r in tr]), [.5, .75, .9])
    bands = [("bottom half", v < qs[0]), ("50-75%", (v >= qs[0]) & (v < qs[1])),
             ("75-90%", (v >= qs[1]) & (v < qs[2])), ("top 10%", v >= qs[2])]
    print("| model | coefficients (fit on train) | test MAE | test RMSE | abs err diff vs raw [90%, by date] | "
          + " | ".join(f"miss {n}" for n, _ in bands) + " |")
    print("|---|---|---|---|---|" + "---|" * len(bands))
    for var in ("raw", "salary", "salary+promoted"):
        b = None if var == "raw" else fit(tr, var)
        P = predict(te, var, b)
        d, lo, hi = _boot(cl, np.abs(A - P) - np.abs(A - P0))
        print(f"| {var} | {'-' if b is None else ', '.join(f'{x:+.3f}' for x in b)} | {np.abs(A-P).mean():.3f} | "
              f"{np.sqrt(((A-P)**2).mean()):.3f} | {d:+.3f} [{lo:+.3f}, {hi:+.3f}] | "
              + " | ".join(f"{(A-P)[m].mean():+.2f} (n {m.sum()})" for _, m in bands) + " |")
    return 0


def optimize(pool: list[dict], score: np.ndarray) -> list[int] | None:
    """Max-score DK NBA Classic lineup (8 slots, $50k, >= 2 games) by MILP."""
    from scipy.optimize import Bounds, LinearConstraint, milp
    n = len(pool)
    elig = [[set(p["pos"].split("/")) & (FITS.get(s, {s})) for s in SLOTS] for p in pool]
    var = [(i, j) for i in range(n) for j in range(len(SLOTS)) if elig[i][j]]
    m = len(var)
    c = -np.array([score[i] for i, _ in var])
    A, lb, ub = [], [], []
    for j in range(len(SLOTS)):                                  # each slot once
        A.append([1.0 if jj == j else 0.0 for _, jj in var]); lb.append(1); ub.append(1)
    for i in range(n):                                           # each player at most once
        A.append([1.0 if ii == i else 0.0 for ii, _ in var]); lb.append(0); ub.append(1)
    A.append([pool[i]["salary"] for i, _ in var]); lb.append(0); ub.append(CAP)
    games = sorted({tuple(sorted((p["team"], p["opp"]))) for p in pool})
    if len(games) >= 2:                                          # DK: players from >= 2 games
        for g in games:
            A.append([1.0 if tuple(sorted((pool[i]["team"], pool[i]["opp"]))) == g else 0.0 for i, _ in var])
            lb.append(0); ub.append(7)
    res = milp(c, constraints=LinearConstraint(np.array(A), lb, ub), integrality=np.ones(m),
               bounds=Bounds(0, 1))
    if not res.success:
        return None
    return [var[k][0] for k in range(m) if res.x[k] > 0.5]


def cash_lines() -> dict:
    """{draft group: [(type, cash line points, field)]} from the exported contests."""
    c = sqlite3.connect(DB)
    out = defaultdict(list)
    for cid, dg, typ, paid, field in c.execute(
            "select contest_id, draft_group, type, places_paid, field from contest where sport='NBA' "
            "and slate_date >= '2026-10-01' and places_paid > 0"):
        pts = sorted((p for (p,) in c.execute("select points from entry where contest_id=? and points is not null",
                                               (cid,))), reverse=True)
        if pts:
            out[dg].append((typ, pts[min(paid, len(pts)) - 1], field))
    return out


def lineups() -> int:
    rows = load()
    tr = [r for r in rows if r["date"] < SPLIT]
    lines = cash_lines()
    by_dg = defaultdict(list)
    for r in rows:
        by_dg[r["dg"]].append(r)
    fits = {v: fit(tr, v) for v in ("salary", "salary+promoted")}
    print(f"# Optimizer replay: the max-projection Classic lineup per draft group, scored with box DK points\n"
          f"(constants fitted on dates < {SPLIT}; train-period rows are in-sample for the shrink)\n")
    res = defaultdict(list)
    for dg, ps in sorted(by_dg.items(), key=lambda kv: kv[1][0]["date"]):
        if len({tuple(sorted((p["team"], p["opp"]))) for p in ps}) < 2 or len(ps) < 16:
            continue                                             # Showdown-size / one-game pools
        row = {"dg": dg, "date": ps[0]["date"], "test": ps[0]["date"] >= SPLIT}
        for v in ("raw", "salary", "salary+promoted"):
            sc = predict(ps, v, fits.get(v))
            pick = optimize(ps, sc)
            if pick is None:
                break
            row[v] = (sum(ps[i]["actual"] for i in pick), sum(ps[i]["proj"] for i in pick))
        else:
            for typ, line, field in lines.get(dg, []):
                row.setdefault("lines", []).append((typ, line))
            for k in ("test", "all"):
                if k == "all" or row["test"]:
                    res[k].append(row)
    for k in ("test", "all"):
        rs = res[k]
        print(f"## {'test dates only' if k == 'test' else 'every date'}: {len(rs)} draft groups, "
              f"{len({r['date'] for r in rs})} dates\n")
        print("| lineup from | mean actual | mean raw projection of it | actual - raw proj | "
              "beats cash line (cash contests) | beats cash line (GPP) |")
        print("|---|---|---|---|---|---|")
        for v in ("raw", "salary", "salary+promoted"):
            act = np.array([r[v][0] for r in rs]); pj = np.array([r[v][1] for r in rs])
            hits = {"cash": [], "gpp": []}
            for r in rs:
                for typ, line in r.get("lines", []):
                    hits[typ].append(r[v][0] >= line)
            print(f"| {v} | {act.mean():.1f} | {pj.mean():.1f} | {(act - pj).mean():+.1f} | "
                  + " | ".join(f"{np.mean(h):.0%} of {len(h)}" if h else "-" for h in hits.values()) + " |")
        d = np.array([r["salary"][0] - r["raw"][0] for r in rs]); cl = np.array([r["date"] for r in rs])
        m, lo, hi = _boot(cl, d)
        print(f"\nsalary - raw, actual points per lineup: {m:+.1f} [90% {lo:+.1f}, {hi:+.1f}] by date\n")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["report", "lineups", "fit"])
    a = ap.parse_args()
    if a.cmd == "report":
        return report()
    if a.cmd == "lineups":
        return lineups()
    rows = load()
    for v in ("salary", "salary+promoted"):
        print(v, "all dates:", ", ".join(f"{x:+.4f}" for x in fit(rows, v)), f"(n {len(rows)}, "
              f"{len({r['date'] for r in rows})} dates)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
