#!/usr/bin/env python3
"""Critique cycle 23 (target: CourtIQ): CourtIQ's slot history as a P(start) feature.

    python3 scripts/nba_courtiq_pstart_test.py

Critic: with no announced five, teammates of a scratch who end up STARTING run -1.3 min
and the bench +0.7 (2025-26, inactives state); in the confirmed state +0.2 / -0.3 -- the
penalty is not knowing who takes the start. Cycle 10's P(start) logistic (pstart_feats)
knows position groups but not what THIS team did. New feature per active Y:
    ciq = sum over tonight's scratched starters X (start rate >= 0.5) of
          n_wo / (n_wo + K) x (Y's start rate in this team's earlier games X was inactive
                               - Y's start rate in its earlier games X dressed for)
(only games before tonight; a day enters history after the whole day). P(start) weights,
K (grid) and the inactives rank-gap coefficients (with cycle 10's u term) fitted on 2024-25;
2025-26 / 2026-27 held out; plus the official-report replay (production's state).
"""
from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import nba_minutes_lab as L   # noqa: E402

CFG = {"ciq": False, "K": 5.0}
PAST: dict = {}          # team -> [{pid: started bool or None (inactive)}] in date order
AT: dict = {}            # id(game) -> how many of its team's games precede it


def index(D: dict) -> None:
    rows = D["rows"]
    order = sorted(D["games"], key=lambda g: g["date"])
    k = 0
    while k < len(order):
        d = order[k]["date"]
        day = []
        while k < len(order) and order[k]["date"] == d:
            day.append(order[k])
            k += 1
        for g in day:
            AT[id(g)] = len(PAST.setdefault(g["team"], []))
        for g in day:
            PAST[g["team"]].append({rows[i]["pid"]: (None if rows[i]["inactive"] else bool(rows[i]["starter"]))
                                    for i in g["idx"]})


orig_feats = L.pstart_feats


def feats(D, g, R):
    out = orig_feats(D, g, R)
    if not CFG["ciq"]:
        return out
    rows = D["rows"]
    hist = PAST[g["team"]][: AT[id(g)]]
    xs = [rows[j]["pid"] for j in g["idx"] if rows[j]["inactive"] and float(R["srate"][j]) >= 0.5]
    for i, f in out.items():
        y = rows[i]["pid"]
        v = 0.0
        for x in xs:
            sw = nw = so = no = 0
            for m in hist:
                if x not in m or m.get(y) is None:
                    continue
                if m[x] is None:
                    so += m[y]; no += 1
                else:
                    sw += m[y]; nw += 1
            if no and nw:
                v += no / (no + CFG["K"]) * (so / no - sw / nw)
        out[i] = f + [v]
    return out


L.pstart_feats = feats


def bias_split(D, prm, season):
    R = L.roles(D, prm)
    e = defaultdict(list)
    for g in D["games"]:
        if g["season"] != season:
            continue
        rows = D["rows"]
        if not any(rows[i]["inactive"] and R["base"][i] >= L.NM.REGULAR_MIN for i in g["idx"]):
            continue
        p = L.project_game(D, g, R, prm, "inactives")
        for i in g["idx"]:
            r = rows[i]
            if not r["inactive"] and r["actual"] is not None:
                e["starter" if r["starter"] else "bench"].append(p.get(i, 0.0) - r["actual"])
    return " / ".join(f"{k} {np.mean(v):+.2f}" for k, v in sorted(e.items(), reverse=True))


def line(D, name, prm):
    r = L.evaluate(D, prm, "inactives", seasons=("2024-25", "2025-26", "2026-27"))
    sl = "teammate of a scratch"
    cells = " | ".join(f"{r[s]['all']['rmse']:.4f} / {r[s][sl]['rmse']:.4f} MAE {r[s][sl]['mae']:.3f}"
                       for s in ("2024-25", "2025-26", "2026-27"))
    print(f"| {name} | {cells} | {bias_split(D, prm, '2025-26')} |", flush=True)
    return r["2024-25"]["all"]["rmse"]


def main() -> int:
    D = L.load(verbose=False)
    index(D)
    F = L.final_params()
    print("| inactives state | 2024-25 all / slice | 2025-26 held out | 2026-27 | 2025-26 starter / bench bias |\n|---|---|---|---|---|")
    line(D, "shipped", F)
    CFG["ciq"] = False
    P10 = {**F, "pstart_w": L.fit_pstart(D, F)}
    P10["gap_coef"] = {**F["gap_coef"], "inactives": L.fit_gap(D, P10)["inactives"]}
    line(D, "cycle 10 P(start), no CourtIQ", P10)
    best = None
    CFG["ciq"] = True
    for K in (3.0, 5.0, 10.0, 20.0):
        CFG["K"] = K
        P = {**F, "pstart_w": L.fit_pstart(D, F)}
        P["gap_coef"] = {**F["gap_coef"], "inactives": L.fit_gap(D, P)["inactives"]}
        o = line(D, f"+ CourtIQ start feature, K {K:g}", P)
        if best is None or o < best[0]:
            best = (o, K, P)
    o, K, P = best
    CFG["K"] = K
    print(f"\nchosen on 2024-25: K {K:g}, P(start) w {np.round(P['pstart_w'], 3).tolist()}", flush=True)
    status = L.report_status(D)
    tr = L.report_preds(D, F, "2024-25", "inactives", status)
    p, _ = L.report_fit(tr)
    for s_ in ("2025-26", "2026-27"):
        h = L.report_score(L.report_preds(D, F, s_, "inactives", status), p)
        CFG["ciq"] = False
        c10 = L.report_score(L.report_preds(D, P10, s_, "inactives", status), p)
        CFG["ciq"] = True
        z = L.report_score(L.report_preds(D, P, s_, "inactives", status), p)
        print(f"official report, inactives, {s_}: board {h['fixed']:.3f} / cycle10 {c10['fixed']:.3f} / CourtIQ "
              f"{z['fixed']:.3f}; active {h['active']:.3f} / {c10['active']:.3f} / {z['active']:.3f} "
              f"(n {z['n_active']:,})", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
