#!/usr/bin/env python3
"""Sweep project_ownership's gamma/pitcher_gamma against real held-out contest
ownership, out-of-sample (fit intuition on one date, confirm on another --
never just fit-and-declare, the same discipline as every other banked
ownership fix in this project).

Built after an external review found the hitter ownership softmax was "too
hot" (systematic over-prediction at the top of the predicted-ownership
range). Confirmed on 7/3 and 7/7: hitter gamma 3.5->1.5 dropped MAE with rank
correlation unchanged; pitcher_gamma 6.0->8.0 similarly (the ORIGINAL 6.0 was
still under-concentrated, not over -- these needed to be tuned independently).

Reconstructs each date's pool from data/dfs_proj_log.csv (proj/salary/team,
slot parsed back out of the "conf" column) rather than re-running a live
build, so this is free and fast to re-run as more slates accumulate.

Usage: python3 scripts/dfs_ownership_gamma_sweep.py
"""
import csv
import copy
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs  # noqa: E402
from edge.dfs import norm  # noqa: E402
from edge.dfs_validate import pearson, spearman  # noqa: E402


def load_pools_and_actuals():
    by_date_log = defaultdict(list)
    for r in csv.DictReader(open(ROOT / "data/dfs_proj_log.csv")):
        by_date_log[r["date"]].append(r)
    cal = json.loads((ROOT / "data/dfs_calibration.json").read_text())
    actual_own = {(r["date"], norm(r["player"]), r["is_pitcher"]): r["actual_own"] for r in cal}
    return by_date_log, actual_own


def build_pool(rows, is_pitcher):
    pool = []
    for r in rows:
        if ("P" in r["pos"].split("/")) != is_pitcher:
            continue
        if not r["salary"] or not r["proj"]:
            continue
        m = re.match(r"H-slot(\d)", r["conf"])
        pool.append({"proj": float(r["proj"]), "salary": float(r["salary"]),
                    "pos": {"P"} if is_pitcher else set(r["pos"].split("/")),
                    "team": r["team"], "slot": int(m.group(1)) if m else None, "name": r["player"]})
    return pool


def team_proj_for(pool):
    tp = defaultdict(float)
    for p in pool:
        tp[p["team"]] += p["proj"]
    return dict(tp)


def eval_gamma(pool, date, is_pitcher, actual_own, **gamma_kwargs):
    pool2 = copy.deepcopy(pool)
    tp = None if is_pitcher else team_proj_for(pool2)
    dfs.project_ownership(pool2, tp, **gamma_kwargs)
    pairs = [(p["own"], actual_own[(date, norm(p["name"]), is_pitcher)]) for p in pool2
             if (date, norm(p["name"]), is_pitcher) in actual_own]
    if not pairs:
        return None
    preds, acts = zip(*pairs)
    mae = sum(abs(x - y) for x, y in pairs) / len(pairs)
    return {"mae": round(mae, 2), "pearson": round(pearson(list(preds), list(acts)), 3),
           "spearman": round(spearman(list(preds), list(acts)), 3), "n": len(pairs)}


def main():
    by_date_log, actual_own = load_pools_and_actuals()
    own_dates = sorted({d for (d, _, _) in actual_own})
    if len(own_dates) < 2:
        sys.exit(f"only {len(own_dates)} date(s) with real ownership data -- need >=2 for an "
                 f"out-of-sample sweep. Have: {own_dates}")

    print(f"dates with real ownership: {own_dates}\n")
    print("=== hitter gamma sweep (pitcher_gamma fixed at current default) ===")
    for g in [1.0, 1.25, 1.5, 1.75, 2.0, 2.5, 3.0, 3.5]:
        line = f"  gamma={g:4.2f}: "
        for d in own_dates:
            pool = build_pool(by_date_log[d], is_pitcher=False)
            r = eval_gamma(pool, d, False, actual_own, gamma=g)
            if r:
                line += f"{d} MAE={r['mae']:.2f} sp={r['spearman']:+.3f}  |  "
        print(line)

    print("\n=== pitcher_gamma sweep (hitter gamma fixed at current default) ===")
    for pg in [4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 12.0]:
        line = f"  pitcher_gamma={pg:4.1f}: "
        for d in own_dates:
            pool = build_pool(by_date_log[d], is_pitcher=True)
            r = eval_gamma(pool, d, True, actual_own, pitcher_gamma=pg)
            if r:
                line += f"{d} MAE={r['mae']:.2f} sp={r['spearman']:+.3f}  |  "
        print(line)

    print("\ncurrent defaults live in edge/dfs.py::project_ownership -- if this sweep's minimum")
    print("moves as more slates accumulate, update the defaults there, not just here.")


if __name__ == "__main__":
    main()
