#!/usr/bin/env python3
"""Backtest field-aware MLB cash against the shipped projection-max cash.

    python3 scripts/dfs_cash_field_mlb.py

For every MLB double-up export tagged cash in data/contest_meta.json whose date
is confirmed by real scores (scripts/dfs_calibration.infer_date_by_ground_truth
>= 0.9): rebuild the logged pool (scripts/dfs_construction_replay.build_pool),
restricted to the teams on that contest's board; fit the cash-field sharpening
on the OTHER contests (leave one out); build both cash lineups; score them on
real DK points (data/actuals_cache) against the real cash line.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs_opt, dfs_sim  # noqa: E402
from edge.dfs_contest import parse_contest_file  # noqa: E402
from edge.names import norm, rekey_bare  # noqa: E402
from scripts.dfs_calibration import infer_date_by_ground_truth, load_proj_log  # noqa: E402
from scripts.dfs_construction_replay import build_pool  # noqa: E402

KS = [1.0, 1.5, 2.0, 2.5, 3.0, 4.0]
N_SIMS, FIELD = 2000, 300


def season_rates(pool):
    try:
        raw = json.loads((ROOT / "data/dfs_season_hitting.json").read_text())
    except OSError:
        return {}
    by = {norm(k.split("|", 1)[1]): v for k, v in raw.items() if isinstance(v, dict) and "|" in k}
    return {p["name"]: by[norm(p["name"])] for p in pool
            if "P" not in p["pos"] and norm(p["name"]) in by}


def entries(cid):
    return [float(l.split(",")[4]) for l in (ROOT / f"data/contest-standings-{cid}.csv").open()
            if l.split(",")[0].isdigit()]


def own_err(pool, actual, k):
    rows = [dict(p) for p in pool]
    dfs_sim.cash_ownership(rows, k)
    return statistics.fmean((r["own_cash"] - actual.get(norm(r["name"]), 0.0)) ** 2 for r in rows)


def main():
    meta = json.loads((ROOT / "data/contest_meta.json").read_text())
    by_date = load_proj_log()
    dates = sorted(by_date)
    slates = []
    for cid, m in sorted(meta.items()):
        if m.get("type") != "cash" or m.get("sport") not in (None, "MLB"):
            continue
        f = ROOT / f"data/contest-standings-{cid}.csv"
        if not f.exists():
            continue
        con = parse_contest_file(str(f))
        date, frac, _ = infer_date_by_ground_truth(con, dates)
        if frac < 0.9:
            continue
        pool = build_pool(date, by_date[date])
        teams = {p["team"] for p in pool if norm(p["name"]) in con}
        pool = [p for p in pool if p["team"] in teams]
        actp = ROOT / f"data/actuals_cache/{date}.json"
        if len(pool) < 20 or not actp.exists():
            continue
        scores_real = sorted(entries(cid), reverse=True)
        paid = m.get("places_paid") or int(len(scores_real) * dfs_sim.CASH_PAY_FRAC)
        slates.append({"cid": cid, "date": date, "frac": frac, "pool": pool, "teams": teams,
                       "act": {norm(k): v for k, v in rekey_bare(json.loads(actp.read_text())).items()},
                       "own": {k: v["pct_drafted"] for k, v in con.items()},
                       "real": scores_real, "line": scores_real[paid - 1]})
    print(f"{len(slates)} MLB cash contests with a confirmed date and a logged pool")
    k_all = min(KS, key=lambda k: sum(own_err(s["pool"], s["own"], k) for s in slates))
    print(f"cash sharpening fitted on all: k={k_all}  (RMSE "
          f"{statistics.fmean(own_err(s['pool'], s['own'], k_all) for s in slates) ** .5:.2f} vs "
          f"k=1 {statistics.fmean(own_err(s['pool'], s['own'], 1.0) for s in slates) ** .5:.2f})\n")

    print(f"{'date':10} {'cid':10} {'teams':>5} {'line':>6} | {'proj':>6} {'pct':>4} {'cash':4} | "
          f"{'field':>6} {'pct':>4} {'cash':4} | model P(clear) proj/field  k")
    tally = {"proj": [], "field": []}
    for s in slates:
        k = min(KS, key=lambda kk: sum(own_err(o["pool"], o["own"], kk) for o in slates if o is not s))
        pool = [dict(p) for p in s["pool"]]
        dfs_sim.cash_ownership(pool, k)
        base = dfs_opt.optimize(pool, mode="cash", iters=800)
        stk = None
        try:
            team_proj = {}
            for p in pool:
                if "P" not in p["pos"]:
                    team_proj[p["team"]] = team_proj.get(p["team"], 0) + p["proj"]
            stk = dfs_opt.optimize(pool, mode="cash", stack_team=max(team_proj, key=team_proj.get),
                                   stack_n=3, iters=800)
        except Exception:                                    # noqa: BLE001
            pass
        scores, _ = dfs_sim.simulate_slate(pool, n_sims=N_SIMS, seed=1, season_rates=season_rates(pool))
        field = dfs_sim.generate_field(pool, FIELD, rng=np.random.default_rng(11), own_key="own_cash")
        line = dfs_sim.cash_line(scores, field)
        ix = {p["name"]: i for i, p in enumerate(pool)}
        starts = [[ix[p["name"]] for p, _ in r["lineup"]] for r in (base, stk) if r]
        best, p_field = dfs_sim.optimize_cash_vs_field(pool, scores, line, starts, dfs_opt._valid)
        p_proj = float((scores[:, starts[0]].sum(1) >= line).mean())
        row = f"{s['date']:10} {s['cid']:10} {len(s['teams']):5} {s['line']:6.1f} |"
        for label, names in (("proj", [p["name"] for p, _ in base["lineup"]]),
                             ("field", [pool[i]["name"] for i in best])):
            pts = sum(s["act"].get(norm(n), 0.0) for n in names)
            pct = sum(x < pts for x in s["real"]) / len(s["real"])
            tally[label].append((pts >= s["line"], pct, pts))
            row += f" {pts:6.1f} {pct:4.0%} {'YES' if pts >= s['line'] else 'no':4} |"
        print(row + f" {p_proj:6.0%} / {p_field:.0%}  {k}")
    print()
    for label, rows in tally.items():
        print(f"{label:6} cashed {sum(r[0] for r in rows)}/{len(rows)}  mean percentile "
              f"{statistics.fmean(r[1] for r in rows):.0%}  mean points "
              f"{statistics.fmean(r[2] for r in rows):.1f}")
    wins = sum(f[1] > p[1] for p, f in zip(tally["proj"], tally["field"]))
    ties = sum(f[1] == p[1] for p, f in zip(tally["proj"], tally["field"]))
    print(f"field-aware finished higher on {wins} of {len(slates)} ({ties} identical)")


if __name__ == "__main__":
    main()
