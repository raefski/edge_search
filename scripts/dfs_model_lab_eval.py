#!/usr/bin/env python3
"""Evaluate hitter-model variants over data/model_lab_rows.json (no network).

Discipline (matches DFS_METHODOLOGY §4): hyperparameters are tuned on the
TRAIN window (dates < 2025-06-01) and every reported number comes from the
TEST window (2025-06-01..2025-07-31). A variant ships only if it improves
MAE without hurting correlation (or vice versa) on TEST.

Variants over the production-proxy baseline:
  EB    empirical-Bayes shrunk skill (raw 23+24+25-to-date pooled, +K league PA)
        instead of the min-120-PA hard cutoff + ad-hoc w25 blend
  HA    home/away-aware expected-PA table (empirical, train window)
  ERA   opposing starter run-prevention blended into the matchup factor
  PLAT  platoon (2024 vs-hand split), higher PA floor + shrunk weight

Usage: python3 scripts/dfs_model_lab_eval.py
"""
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.dfs import SLOT_PA, LG_K9  # noqa: E402
from edge.dfs_validate import cross_slate_summary, incremental_baseline_test  # noqa: E402

ROWS = json.loads((ROOT / "data/model_lab_rows.json").read_text())
TRAIN_END = "2025-06-01"
LG_ERA = 4.10

# league skill rate from the pooled 23+24 raw data in the rows themselves
_tp = sum(r["pts23"] + r["pts24"] for r in ROWS)
_ta = sum(r["pa23"] + r["pa24"] for r in ROWS)
LG_RATE = _tp / _ta


def clamp(x, lo, hi):
    return min(hi, max(lo, x))


def skill_base(r):
    pa_prior = r["pa23"] + r["pa24"]
    old = (r["pts23"] + r["pts24"]) / pa_prior if pa_prior >= 120 else LG_RATE
    if r["pa25"] >= 20:
        w = r["pa25"] / (r["pa25"] + 300)
        return (1 - w) * old + w * (r["pts25"] / r["pa25"])
    return old


def skill_eb(r, K, w23=1.0):
    pts = w23 * r["pts23"] + r["pts24"] + r["pts25"]
    pa = w23 * r["pa23"] + r["pa24"] + r["pa25"]
    return (pts + LG_RATE * K) / (pa + K)


def k9_blend(r):
    k9_old = r["sp_k9_24"] if (r["sp_k9_24"] and r["sp_outs24"] >= 90) else LG_K9
    ip25 = r["sp_outs25"]  # gamelog_upto returns innings for pitching, despite the name
    if ip25 >= 15:
        w = ip25 * 3 / (ip25 * 3 + 180)
        k9_new = (1 - w) * k9_old + w * (9 * r["sp_k25"] / ip25)
    else:
        k9_new = k9_old
    return 0.6 * k9_new + 0.4 * (r["bp_k9"] or LG_K9)


def era_blend(r):
    ip24 = r["sp_outs24"] / 3 if r["sp_outs24"] else 0
    era24 = r["sp_era24"] if (r["sp_era24"] is not None and ip24 >= 30) else LG_ERA
    ip25 = r["sp_outs25"]
    if ip25 >= 15:
        era25 = 9 * r["sp_er25"] / ip25
        w = ip25 / (ip25 + 60)
        return (1 - w) * era24 + w * era25
    return era24


def proj(r, skill, pa, w_match=0.3, era_w=0.0):
    of = clamp(1 - w_match * (k9_blend(r) / LG_K9 - 1), 0.82, 1.18)
    rf = clamp(1 + era_w * (era_blend(r) / LG_ERA - 1), 0.85, 1.15) if era_w else 1.0
    return skill * pa * r["park"] * of * rf


# empirical PA table from TRAIN window only
_pa_emp = defaultdict(list)
for r in ROWS:
    if r["date"] < TRAIN_END:
        _pa_emp[(r["home"], r["slot"])].append(r["pa_game"])
PA_EMP = {k: statistics.mean(v) for k, v in _pa_emp.items()}


def pa_for(r, ha=False):
    if ha:
        return PA_EMP.get((r["home"], r["slot"]), SLOT_PA.get(r["slot"], 4.2))
    return SLOT_PA.get(r["slot"], 4.2)


def variants(r, cfg):
    """-> {name: proj} for one row."""
    out = {}
    sb = skill_base(r)
    out["BASE"] = proj(r, sb, pa_for(r))
    K = cfg.get("K", 200)
    se = skill_eb(r, K)
    out["EB"] = proj(r, se, pa_for(r))
    out["EB+HA"] = proj(r, se, pa_for(r, ha=True))
    out["EB+HA+ERA"] = proj(r, se, pa_for(r, ha=True), era_w=cfg.get("era_w", 0.2))
    fl, pw = cfg.get("plat_floor", 100), cfg.get("plat_w", 0.25)
    if r["plat_rate"] is not None and r["plat_pa"] >= fl:
        wsh = pw * r["plat_pa"] / (r["plat_pa"] + 200)
        sp = (1 - wsh) * se + wsh * r["plat_rate"]
    else:
        sp = se
    out["EB+HA+PLAT"] = proj(r, sp, pa_for(r, ha=True))
    out["ALL"] = proj(r, sp, pa_for(r, ha=True), era_w=cfg.get("era_w", 0.2))
    return out


def evaluate(rows, cfg):
    names = None
    per = defaultdict(list)
    for r in rows:
        v = variants(r, cfg)
        names = names or list(v)
        for k, p in v.items():
            per[k].append((p, r["actual"], r["date"]))
    res = {}
    for k in names:
        ps = [x[0] for x in per[k]]
        as_ = [x[1] for x in per[k]]
        mae = statistics.mean(abs(p - a) for p, a in zip(ps, as_))
        rws = [{"d": d, "p": p, "a": a} for p, a, d in per[k]]
        cs = cross_slate_summary(rws, "d", "p", "a")
        res[k] = {"mae": mae, "corr": cs["pooled_corr"],
                  "slate_mean": cs.get("cross_slate_mean"), "slate_se": cs.get("cross_slate_se")}
    return res


def show(title, res):
    print(f"\n{title}")
    print(f"  {'variant':14} {'MAE':>7} {'corr':>7} {'slate-mean':>11} {'SE':>6}")
    for k, v in res.items():
        print(f"  {k:14} {v['mae']:7.3f} {v['corr']:7.3f} {v['slate_mean']!s:>11} {v['slate_se']!s:>6}")


def main():
    train = [r for r in ROWS if r["date"] < TRAIN_END]
    test = [r for r in ROWS if r["date"] >= TRAIN_END]
    print(f"rows: train {len(train)}  test {len(test)}  (league rate {LG_RATE:.3f})")

    # --- tune on TRAIN ---
    print("\n== TRAIN sweeps (tuning only; ignore absolute values) ==")
    for K in (60, 120, 200, 300, 500):
        res = evaluate(train, {"K": K})
        print(f"  K={K:<4} EB: MAE {res['EB']['mae']:.3f} corr {res['EB']['corr']:.3f}   "
              f"(BASE {res['BASE']['mae']:.3f}/{res['BASE']['corr']:.3f})")
    for ew in (0.1, 0.2, 0.3):
        res = evaluate(train, {"K": 200, "era_w": ew})
        print(f"  era_w={ew:<4} EB+HA+ERA: MAE {res['EB+HA+ERA']['mae']:.3f} corr {res['EB+HA+ERA']['corr']:.3f}  "
              f"(EB+HA {res['EB+HA']['mae']:.3f}/{res['EB+HA']['corr']:.3f})")
    for fl, pw in ((40, 0.25), (100, 0.25), (100, 0.5), (150, 0.25)):
        res = evaluate(train, {"K": 200, "plat_floor": fl, "plat_w": pw})
        print(f"  plat floor={fl:<4} w={pw:<5} EB+HA+PLAT: MAE {res['EB+HA+PLAT']['mae']:.3f} "
              f"corr {res['EB+HA+PLAT']['corr']:.3f}  (EB+HA {res['EB+HA']['mae']:.3f}/{res['EB+HA']['corr']:.3f})")

    # --- final config, chosen from train, evaluated on TEST ---
    import argparse  # noqa -- keep simple: edit cfg after reading train output
    cfg = json.loads(Path(sys.argv[1]).read_text()) if len(sys.argv) > 1 else {"K": 200, "era_w": 0.2,
                                                                               "plat_floor": 100, "plat_w": 0.25}
    print(f"\n== TEST (cfg {cfg}) ==")
    res = evaluate(test, cfg)
    show("TEST window 2025-06-01..2025-07-31:", res)

    # incremental value of the best variant over BASE (does it add signal, not
    # just recalibrate?)
    base = []
    best = []
    ys = []
    for r in test:
        v = variants(r, cfg)
        base.append(v["BASE"]); best.append(v["ALL"]); ys.append(r["actual"])
    t = incremental_baseline_test(ys, base, best)
    print("\nincremental test: actual ~ BASE + ALL:", t)


if __name__ == "__main__":
    main()
