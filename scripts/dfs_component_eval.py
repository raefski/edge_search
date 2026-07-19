#!/usr/bin/env python3
"""Evaluate candidate hitter-model upgrades over model_lab_rows_v2.json.

Discipline (DFS_METHODOLOGY §4/§18): every hyperparameter is chosen on the
TRAIN window (dates < 2025-06-01); every reported number comes from the TEST
window (2025-06-01..2025-07-31). A candidate ships only if TEST corr improves
without MAE getting worse (or both improve), with a per-date consistency check.

Candidates over the §18 production baseline (EB K=60 + home/away PA + ERA 0.2):
  MARCEL     decay prior seasons (w23, w24) instead of flat PA-pooling
  HOME       home/away per-PA quality multiplier (beyond the PA-table effect)
  PLATCELL   league-average platoon by (batter side x SP hand) cell -- no noisy
             per-player splits, the fix for the twice-killed per-player version
  KINT       odds-ratio K matchup: hitter K% x SP K% replaces the uniform
             one-size-fits-all K9 factor (zero new free parameters)
  PAOPP      expected-PA adjusted by opposing-starter quality
  COMP       full component decomposition (1B/2B/3B/HR/BB/HBP/SB/R/RBI rates,
             park routed to the components it physically affects, HR/BB matchup
             vs the SP's own HR/BB-allowed rates)

Usage: python3 scripts/dfs_component_eval.py
"""
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.dfs import LG_K9  # noqa: E402
from edge.dfs_validate import cross_slate_summary, incremental_baseline_test  # noqa: E402

ROWS = json.loads((ROOT / "data/model_lab_rows_v2.json").read_text())
TRAIN_END = "2025-06-01"
LG_ERA = 4.10

# c-vector indices: [pa, s1, s2, s3, hr, bb, hbp, sb, so, r, rbi]
PA, S1, S2, S3, HR, BB, HBP, SB, SO, R, RBI = range(11)
PTS_W = {S1: 3, S2: 5, S3: 8, HR: 10, BB: 2, HBP: 2, SB: 5, R: 2, RBI: 2}

_tp = sum(r["pts23"] + r["pts24"] for r in ROWS)
_ta = sum(r["pa23"] + r["pa24"] for r in ROWS)
LG_RATE = _tp / _ta

# league component rates from the full 23+24 seasons of this row population
_lgc = [0.0] * 11
for r in ROWS:
    for i in range(11):
        _lgc[i] += r["c23"][i] + r["c24"][i]
LG_C = [x / _lgc[PA] for x in _lgc]  # per-PA rates (index PA becomes 1.0)

# league SP rates (per batter faced) from full-2024 SP components
_sp = [0.0] * 8
for r in ROWS:
    if r["sp24c"]:
        for i in range(8):
            _sp[i] += r["sp24c"][i]
SP_OUTS, SP_BF, SP_H, SP_HR, SP_BB, SP_SO, SP_GO, SP_AO = range(8)
LG_SP_HR_BF = _sp[SP_HR] / _sp[SP_BF]
LG_SP_BB_BF = _sp[SP_BB] / _sp[SP_BF]
LG_K_PA = LG_C[SO]  # league hitter K per PA


def clamp(x, lo, hi):
    return min(hi, max(lo, x))


def skill_eb(r, K=60, w23=1.0, w24=1.0):
    pts = w23 * r["pts23"] + w24 * r["pts24"] + r["pts25"]
    pa = w23 * r["pa23"] + w24 * r["pa24"] + r["pa25"]
    return (pts + LG_RATE * K) / (pa + K)


def k9_blend(r):
    k9_old = r["sp_k9_24"] if (r["sp_k9_24"] and r["sp_outs24"] >= 90) else LG_K9
    ip25 = r["sp_outs25"]
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


# empirical home/away PA table from TRAIN only (same as §18 production shape)
_pa_emp = defaultdict(list)
for r in ROWS:
    if r["date"] < TRAIN_END:
        _pa_emp[(r["home"], r["slot"])].append(r["pa_game"])
PA_EMP = {k: statistics.mean(v) for k, v in _pa_emp.items()}


def pa_for(r):
    return PA_EMP.get((r["home"], r["slot"]), 4.1)


def comp_rates(r, K_hit=60, K_hr=170, K_bb=120, K_sb=250, K_run=200, w23=1.0, w24=1.0):
    """Per-PA EB-shrunk component rates pooled over 23/24/25-to-date."""
    pa = w23 * r["c23"][PA] + w24 * r["c24"][PA] + r["c25"][PA]
    Ks = {S1: K_hit, S2: K_hit, S3: K_sb, HR: K_hr, BB: K_bb, HBP: K_bb,
          SB: K_sb, SO: K_bb, R: K_run, RBI: K_run}
    out = {}
    for i, K in Ks.items():
        n = w23 * r["c23"][i] + w24 * r["c24"][i] + r["c25"][i]
        out[i] = (n + LG_C[i] * K) / (pa + K)
    return out


def sp_hr_bb_rate(r, K_bf=300):
    """Opp SP's HR-allowed and BB-allowed per BF, EB-shrunk, 2024 + 2025-to-date."""
    c24, c25 = r.get("sp24c"), r.get("sp25c")
    bf = hr = bb = 0.0
    for c in (c24, c25):
        if c:
            bf += c[SP_BF]; hr += c[SP_HR]; bb += c[SP_BB]
    hr_r = (hr + LG_SP_HR_BF * K_bf) / (bf + K_bf)
    bb_r = (bb + LG_SP_BB_BF * K_bf) / (bf + K_bf)
    return hr_r / LG_SP_HR_BF, bb_r / LG_SP_BB_BF


def hitter_k_rate(r, K=120):
    pa = r["c23"][PA] + r["c24"][PA] + r["c25"][PA]
    so = r["c23"][SO] + r["c24"][SO] + r["c25"][SO]
    return (so + LG_K_PA * K) / (pa + K)


# ---------------------------------------------------------------- multipliers
def train_cell_mults(train, projfn):
    """(bhand, sphand) -> actual/proj ratio on train."""
    agg = defaultdict(lambda: [0.0, 0.0])
    for r in train:
        cell = (r["bhand"], r["sp_hand"])
        a = agg[cell]
        a[0] += r["actual"]; a[1] += projfn(r)
    return {c: (v[0] / v[1] if v[1] > 50 else 1.0) for c, v in agg.items()}


def train_home_mult(train, projfn):
    h = [0.0, 0.0]; aw = [0.0, 0.0]
    for r in train:
        t = h if r["home"] else aw
        t[0] += r["actual"]; t[1] += projfn(r)
    return h[0] / h[1], aw[0] / aw[1]


def kint_factor(r, gamma=1.0):
    """Odds-ratio K matchup: hitter-specific K suppression instead of uniform.
    Matchup K% = hK * pK / lgK (log5); balls-in-play scale accordingly."""
    pk_scale = k9_blend(r) / LG_K9          # SP+pen K rate vs league (approx per-PA scale)
    hk = hitter_k_rate(r)
    k_match = clamp(hk * pk_scale, 0.02, 0.60)
    bip_ratio = (1 - k_match) / (1 - hk) if hk < 1 else 1.0
    return clamp(bip_ratio ** gamma, 0.75, 1.25)


def train_paopp(train):
    """gamma for pa = pa_emp * (1 + g*(era_blend/LG-1)): least squares on train."""
    num = den = 0.0
    for r in train:
        x = pa_for(r) * (era_blend(r) / LG_ERA - 1)
        y = r["pa_game"] - pa_for(r)
        num += x * y; den += x * x
    return num / den if den else 0.0


# ---------------------------------------------------------------- projections
def proj_scalar(r, cfg, mults=None):
    """Production §18 shape + optional scalar-level candidate multipliers."""
    se = skill_eb(r, K=cfg.get("K", 60), w23=cfg.get("w23", 1.0), w24=cfg.get("w24", 1.0))
    pa = pa_for(r)
    if cfg.get("paopp_g"):
        pa *= clamp(1 + cfg["paopp_g"] * (era_blend(r) / LG_ERA - 1) / pa_for(r) * pa_for(r), 0.9, 1.1) \
            if False else 1.0  # replaced below; kept for clarity
    if cfg.get("paopp_gamma") is not None:
        pa = pa + cfg["paopp_gamma"] * pa_for(r) * (era_blend(r) / LG_ERA - 1)
    if cfg.get("kint_gamma"):
        of = kint_factor(r, cfg["kint_gamma"])
    else:
        of = clamp(1 - 0.3 * (k9_blend(r) / LG_K9 - 1), 0.82, 1.18)
    rf = clamp(1 + 0.2 * (era_blend(r) / LG_ERA - 1), 0.85, 1.15)
    p = se * pa * r["park"] * of * rf
    if mults:
        if "cell" in mults:
            lam = cfg.get("cell_lam", 1.0)
            m = mults["cell"].get((r["bhand"], r["sp_hand"]), 1.0)
            p *= 1 + lam * (m - 1)
        if "home" in mults:
            mh, ma = mults["home"]
            p *= mh if r["home"] else ma
    return p


def proj_comp(r, cfg, mults=None):
    """Component decomposition with park/matchup routed per component."""
    rates = comp_rates(r, K_hit=cfg.get("K_hit", 60), K_hr=cfg.get("K_hr", 170),
                       K_bb=cfg.get("K_bb", 120), K_sb=cfg.get("K_sb", 250),
                       K_run=cfg.get("K_run", 200),
                       w23=cfg.get("w23", 1.0), w24=cfg.get("w24", 1.0))
    pa = pa_for(r)
    if cfg.get("paopp_gamma") is not None:
        pa = pa + cfg["paopp_gamma"] * pa_for(r) * (era_blend(r) / LG_ERA - 1)
    if cfg.get("kint_gamma"):
        of = kint_factor(r, cfg["kint_gamma"])
    else:
        of = clamp(1 - 0.3 * (k9_blend(r) / LG_K9 - 1), 0.82, 1.18)
    rf = clamp(1 + 0.2 * (era_blend(r) / LG_ERA - 1), 0.85, 1.15)
    park = r["park"]
    b1, bH, bR = cfg.get("b1", 0.5), cfg.get("bH", 1.5), cfg.get("bR", 1.0)
    hr_m, bb_m = 1.0, 1.0
    if cfg.get("sp_w"):
        hr_ratio, bb_ratio = sp_hr_bb_rate(r)
        hr_m = clamp(1 + cfg["sp_w"] * (hr_ratio - 1), 0.7, 1.3)
        bb_m = clamp(1 + cfg.get("sp_wb", cfg["sp_w"]) * (bb_ratio - 1), 0.7, 1.3)
    hits_part = (3 * rates[S1] + 5 * rates[S2] + 8 * rates[S3]) * (park ** b1) * of
    hr_part = 10 * rates[HR] * (park ** bH) * of * hr_m
    bb_part = 2 * (rates[BB] + rates[HBP]) * bb_m
    sb_part = 5 * rates[SB]
    run_part = 2 * (rates[R] + rates[RBI]) * (park ** bR) * rf * of
    p = pa * (hits_part + hr_part + bb_part + sb_part + run_part)
    if mults:
        if "cell" in mults:
            lam = cfg.get("cell_lam", 1.0)
            m = mults["cell"].get((r["bhand"], r["sp_hand"]), 1.0)
            p *= 1 + lam * (m - 1)
        if "home" in mults:
            mh, ma = mults["home"]
            p *= mh if r["home"] else ma
    return p


# ---------------------------------------------------------------- evaluation
def metrics(rows, projfn):
    ps, as_, ds = [], [], []
    for r in rows:
        ps.append(projfn(r)); as_.append(r["actual"]); ds.append(r["date"])
    mae = statistics.mean(abs(p - a) for p, a in zip(ps, as_))
    rws = [{"d": d, "p": p, "a": a} for p, a, d in zip(ps, as_, ds)]
    cs = cross_slate_summary(rws, "d", "p", "a")
    return {"mae": mae, "corr": cs["pooled_corr"], "sm": cs.get("cross_slate_mean"),
            "se": cs.get("cross_slate_se"), "ps": ps}


def show(name, m, base=None):
    d = ""
    if base:
        d = f"   (dMAE {m['mae']-base['mae']:+.3f}, dcorr {m['corr']-base['corr']:+.4f})"
    print(f"  {name:24} MAE {m['mae']:.3f}  corr {m['corr']:.4f}  "
          f"slate {m['sm']}+/-{m['se']}{d}", flush=True)


def main():
    train = [r for r in ROWS if r["date"] < TRAIN_END]
    test = [r for r in ROWS if r["date"] >= TRAIN_END]
    print(f"rows: train {len(train)} test {len(test)}  lg_rate {LG_RATE:.3f}")
    print(f"lg HR/PA {LG_C[HR]:.4f}  BB/PA {LG_C[BB]:.4f}  K/PA {LG_C[SO]:.4f}  "
          f"SP HR/BF {LG_SP_HR_BF:.4f} BB/BF {LG_SP_BB_BF:.4f}")

    basefn = lambda r: proj_scalar(r, {})
    print("\n== TRAIN sweeps ==")
    mb_train = metrics(train, basefn)
    show("PROD baseline", mb_train)

    # MARCEL season decay
    best_marcel = None
    for w23, w24 in ((1.0, 1.0), (0.6, 0.8), (0.4, 0.7), (0.2, 0.6), (0.5, 1.0), (0.8, 0.9)):
        m = metrics(train, lambda r: proj_scalar(r, {"w23": w23, "w24": w24}))
        show(f"MARCEL w23={w23} w24={w24}", m, mb_train)
        if best_marcel is None or m["corr"] > best_marcel[1]["corr"]:
            best_marcel = ((w23, w24), m)

    # HOME quality multiplier (estimated on train, vs train baseline)
    home_m = train_home_mult(train, basefn)
    print(f"  home/away quality mults: {home_m[0]:.4f}/{home_m[1]:.4f}")
    m = metrics(train, lambda r: proj_scalar(r, {}, {"home": home_m}))
    show("HOME", m, mb_train)

    # PLATCELL
    cells = train_cell_mults(train, basefn)
    print("  platoon cells:", {k: round(v, 3) for k, v in sorted(cells.items(), key=str)})
    best_lam = None
    for lam in (0.5, 0.75, 1.0):
        m = metrics(train, lambda r: proj_scalar(r, {"cell_lam": lam}, {"cell": cells}))
        show(f"PLATCELL lam={lam}", m, mb_train)
        if best_lam is None or m["corr"] > best_lam[1]["corr"]:
            best_lam = (lam, m)

    # KINT odds-ratio K matchup
    for g in (0.5, 1.0, 1.5):
        m = metrics(train, lambda r: proj_scalar(r, {"kint_gamma": g}))
        show(f"KINT gamma={g}", m, mb_train)

    # PAOPP
    g = train_paopp(train)
    print(f"  paopp gamma (train fit): {g:.3f}")
    m = metrics(train, lambda r: proj_scalar(r, {"paopp_gamma": g}))
    show("PAOPP", m, mb_train)

    # COMP: park routing + K grid (coarse), then SP HR/BB weights
    best_comp = None
    for b1, bH, bR in ((0.0, 1.0, 1.0), (0.5, 1.5, 1.0), (0.5, 2.0, 1.0), (1.0, 1.0, 1.0),
                       (0.5, 1.5, 1.5), (0.0, 2.0, 1.5)):
        cfg = {"b1": b1, "bH": bH, "bR": bR}
        m = metrics(train, lambda r: proj_comp(r, cfg))
        show(f"COMP b1={b1} bH={bH} bR={bR}", m, mb_train)
        if best_comp is None or m["corr"] > best_comp[1]["corr"]:
            best_comp = (dict(cfg), m)
    ccfg = best_comp[0]
    for K_hr in (120, 170, 300):
        cfg = dict(ccfg, K_hr=K_hr)
        m = metrics(train, lambda r: proj_comp(r, cfg))
        show(f"COMP K_hr={K_hr}", m, mb_train)
        if m["corr"] > best_comp[1]["corr"]:
            best_comp = (dict(cfg), m)
    ccfg = best_comp[0]
    for sp_w in (0.0, 0.3, 0.5):
        cfg = dict(ccfg, sp_w=sp_w)
        m = metrics(train, lambda r: proj_comp(r, cfg))
        show(f"COMP sp_w={sp_w}", m, mb_train)
        if m["corr"] > best_comp[1]["corr"]:
            best_comp = (dict(cfg), m)

    # ------------------------------------------------------------- TEST
    print("\n== TEST (all hyperparameters frozen from train) ==")
    mb = metrics(test, basefn)
    show("PROD baseline", mb)
    (w23, w24), _ = best_marcel
    m_marcel = metrics(test, lambda r: proj_scalar(r, {"w23": w23, "w24": w24}))
    show(f"MARCEL w23={w23} w24={w24}", m_marcel, mb)
    m_home = metrics(test, lambda r: proj_scalar(r, {}, {"home": home_m}))
    show("HOME", m_home, mb)
    lam = best_lam[0]
    m_cell = metrics(test, lambda r: proj_scalar(r, {"cell_lam": lam}, {"cell": cells}))
    show(f"PLATCELL lam={lam}", m_cell, mb)
    m_kint = metrics(test, lambda r: proj_scalar(r, {"kint_gamma": 1.0}))
    show("KINT gamma=1.0", m_kint, mb)
    m_pa = metrics(test, lambda r: proj_scalar(r, {"paopp_gamma": g}))
    show("PAOPP", m_pa, mb)
    m_comp = metrics(test, lambda r: proj_comp(r, best_comp[0]))
    show(f"COMP {best_comp[0]}", m_comp, mb)

    # combined: best-performing single additions layered
    combo_cfg = {"w23": w23, "w24": w24, "cell_lam": lam}
    m_combo = metrics(test, lambda r: proj_scalar(r, combo_cfg, {"cell": cells, "home": home_m}))
    show("SCALAR COMBO (marcel+home+cell)", m_combo, mb)
    ccfg2 = dict(best_comp[0], w23=w23, w24=w24, cell_lam=lam)
    m_ccombo = metrics(test, lambda r: proj_comp(r, ccfg2, {"cell": cells, "home": home_m}))
    show("COMP COMBO", m_ccombo, mb)

    # incremental value over baseline for the top candidates
    ys = [r["actual"] for r in test]
    for nm, mm in (("COMP", m_comp), ("COMBO", m_combo), ("CCOMBO", m_ccombo)):
        t = incremental_baseline_test(ys, mb["ps"], mm["ps"])
        print(f"  incremental {nm} over baseline: {t}")


if __name__ == "__main__":
    main()
