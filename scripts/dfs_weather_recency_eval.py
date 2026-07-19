#!/usr/bin/env python3
"""Test WEATHER (temp, wind out/in) and RECENT-FORM on top of the §18 baseline
+ the validated scalar combo (marcel+home+platcell), same train/test discipline.

Weather: per-game temp/wind from data/bt_weather.json (statsapi boxscore info).
  temp factor: 1 + wT*(temp-70)/10, wind factor: 1 +/- wW*mph/10 (out/in, CF
  strongest), tuned on train. Dome/None wind = neutral; temp missing = 70.
Recency: blend last-N-days DK rate into season skill at weight wR (the classic
  "hot hand" claim, tested rather than assumed).

Usage: python3 scripts/dfs_weather_recency_eval.py
"""
import json
import statistics
import sys
from bisect import bisect_left
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.dfs import LG_K9  # noqa: E402
from edge.dfs_validate import cross_slate_summary, incremental_baseline_test  # noqa: E402
from scripts.dfs_hitter_backtest import load_cache  # noqa: E402
from scripts.dfs_component_eval import (  # noqa: E402
    ROWS, TRAIN_END, pa_for, skill_eb, k9_blend, era_blend, clamp, LG_ERA,
    train_cell_mults, train_home_mult, metrics,
)

# ---- map (date,pid) -> gamePk, and pk -> weather
BOX = ROOT / "data" / "bt_boxscores"
WX = json.loads((ROOT / "data" / "bt_weather.json").read_text())
pkmap = {}
for f in BOX.glob("*.json"):
    g = json.loads(f.read_text())
    for side in ("home", "away"):
        for pid, _n, _s in g[side]["lineup"]:
            pkmap[(g["date"], pid)] = str(g["pk"])

OUT_DIRS = {"Out To CF": 1.0, "Out To RF": 0.8, "Out To LF": 0.8}
IN_DIRS = {"In From CF": 1.0, "In From RF": 0.8, "In From LF": 0.8}

# per-date mean temp (for slate-relative centering -- computable live from
# forecasts the same way, so no train/production mismatch)
DATE_MEAN_TEMP = {}


def _build_date_means():
    by_date = defaultdict(set)
    box = ROOT / "data" / "bt_boxscores"
    for f in box.glob("*.json"):
        g = json.loads(f.read_text())
        by_date[g["date"]].add(str(g["pk"]))
    for d, pks in by_date.items():
        ts = [WX[pk]["temp"] for pk in pks if WX.get(pk) and WX[pk].get("temp") is not None]
        if ts:
            DATE_MEAN_TEMP[d] = statistics.mean(ts)


_build_date_means()


def wx_for(r):
    w = WX.get(pkmap.get((r["date"], r["pid"]), ""), None)
    if not w:
        return 70, 0.0
    temp = w.get("temp") or 70
    mph = w.get("wind_mph") or 0
    d = w.get("wind_dir") or "None"
    sign = OUT_DIRS.get(d, 0.0) - IN_DIRS.get(d, 0.0)
    return temp, sign * mph


# ---- recency: last-N-days rate from cached game logs (prefix sums)
cache = load_cache()
from scripts.dfs_component_collect import prefix_sums, hit_components  # noqa: E402
HPRE = {}
for pid in {r["pid"] for r in ROWS}:
    HPRE[pid] = prefix_sums(cache.get(f"hlog:{pid}:2025", []),
                            lambda st: [st.get("plateAppearances", 0) or 0,
                                        __import__("edge.dfs", fromlist=["d"]).actual_hitter_points(st)])


def recent_rate(r, days=15):
    import datetime as dt
    dates, cums = HPRE[r["pid"]]
    if not dates:
        return None, 0
    d1 = bisect_left(dates, r["date"])
    d0 = bisect_left(dates, (dt.date.fromisoformat(r["date"]) - dt.timedelta(days=days)).isoformat())
    if d1 <= d0:
        return None, 0
    pa = cums[d1 - 1][0] - (cums[d0 - 1][0] if d0 > 0 else 0)
    pts = cums[d1 - 1][1] - (cums[d0 - 1][1] if d0 > 0 else 0)
    return (pts / pa if pa >= 20 else None), pa


def proj(r, cfg, mults):
    se = skill_eb(r, K=60, w23=cfg.get("w23", 1.0), w24=cfg.get("w24", 1.0))
    if cfg.get("rec_w"):
        rr, rpa = recent_rate(r, cfg.get("rec_days", 15))
        if rr is not None:
            w = cfg["rec_w"] * rpa / (rpa + 60)
            se = (1 - w) * se + w * rr
    of = clamp(1 - 0.3 * (k9_blend(r) / LG_K9 - 1), 0.82, 1.18)
    rf = clamp(1 + 0.2 * (era_blend(r) / LG_ERA - 1), 0.85, 1.15)
    p = se * pa_for(r) * r["park"] * of * rf
    if "cell" in mults:
        m = mults["cell"].get((r["bhand"], r["sp_hand"]), 1.0)
        p *= 1 + cfg.get("cell_lam", 0.5) * (m - 1)
    if "home" in mults:
        mh, ma = mults["home"]
        p *= mh if r["home"] else ma
    if cfg.get("wT") or cfg.get("wW"):
        temp, wind = wx_for(r)
        t0 = DATE_MEAN_TEMP.get(r["date"], 76) if cfg.get("t0") == "date" else cfg.get("t0", 76)
        p *= clamp(1 + (cfg.get("wT", 0)) * (temp - t0) / 10.0, 0.80, 1.25)
        p *= clamp(1 + (cfg.get("wW", 0)) * wind / 10.0, 0.80, 1.25)
    return p


def show(name, m, base=None):
    d = ""
    if base:
        d = f"   (dMAE {m['mae']-base['mae']:+.3f}, dcorr {m['corr']-base['corr']:+.4f}, dslate {m['sm']-base['sm']:+.4f})"
    print(f"  {name:26} MAE {m['mae']:.3f}  corr {m['corr']:.4f}  slate {m['sm']}{d}", flush=True)


def main():
    train = [r for r in ROWS if r["date"] < TRAIN_END]
    test = [r for r in ROWS if r["date"] >= TRAIN_END]
    basefn0 = lambda r: proj(r, {}, {})
    cells = train_cell_mults(train, basefn0)
    home_m = train_home_mult(train, basefn0)
    CFG = {"w23": 0.5, "w24": 1.0, "cell_lam": 0.5}
    mults = {"cell": cells, "home": home_m}
    combofn = lambda r: proj(r, CFG, mults)

    nwx = sum(1 for r in ROWS if pkmap.get((r["date"], r["pid"])) in WX)
    print(f"rows with weather: {nwx}/{len(ROWS)}")

    t_mean = statistics.mean(wx_for(r)[0] for r in train)
    print(f"train mean temp: {t_mean:.1f}")

    print("\n== TRAIN sweeps (on top of scalar combo) ==")
    mb = metrics(train, combofn)
    show("COMBO baseline", mb)
    best_w = (None, mb)
    for wT in (0.0, 0.01, 0.02, 0.03, 0.05):
        for wW in (0.0, 0.01, 0.02, 0.03):
            if wT == 0 and wW == 0:
                continue
            cfg = dict(CFG, wT=wT, wW=wW, t0="date")
            m = metrics(train, lambda r: proj(r, cfg, mults))
            show(f"WX wT={wT} wW={wW}", m, mb)
            if m["corr"] > best_w[1]["corr"] or (m["corr"] == best_w[1]["corr"] and m["mae"] < best_w[1]["mae"]):
                best_w = (dict(cfg), m)
    best_r = (None, mb)
    for rw in (0.15, 0.3, 0.5):
        for rd in (15, 30):
            cfg = dict(CFG, rec_w=rw, rec_days=rd)
            m = metrics(train, lambda r: proj(r, cfg, mults))
            show(f"REC w={rw} days={rd}", m, mb)
            if m["corr"] > best_r[1]["corr"]:
                best_r = (dict(cfg), m)

    print("\n== TEST (frozen from train) ==")
    mt = metrics(test, combofn)
    show("COMBO baseline", mt)
    if best_w[0]:
        m = metrics(test, lambda r: proj(r, best_w[0], mults))
        show(f"WX {dict((k, v) for k, v in best_w[0].items() if k in ('wT', 'wW'))}", m, mt)
        t = incremental_baseline_test([r["actual"] for r in test], mt["ps"], m["ps"])
        print(f"  incremental WX over combo: {t}")
    if best_r[0]:
        m = metrics(test, lambda r: proj(r, best_r[0], mults))
        show(f"REC {dict((k, v) for k, v in best_r[0].items() if k in ('rec_w', 'rec_days'))}", m, mt)
        t = incremental_baseline_test([r["actual"] for r in test], mt["ps"], m["ps"])
        print(f"  incremental REC over combo: {t}")


if __name__ == "__main__":
    main()
