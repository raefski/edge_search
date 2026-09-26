#!/usr/bin/env python3
"""Fit every measured constant the DK MMA build ships, and grade it end to end.

    python3 scripts/mma_fit.py                    # everything
    python3 scripts/mma_fit.py --what scoring market
    python3 scripts/mma_fit.py --what backtest --test-from 2022-01-01

  scoring   DK's scoring table, checked against DraftKings' OWN published
            "Fantasy Points per Fight" (draftStatAttributes 635) for every
            fighter on every saved MMA board: recompute the career average from
            UFCStats under each reading of the rules, keep the one that
            reproduces DK's number.
  market    moneyline and method-market calibration: devig method, the
            favourite slope, the decision under-pricing, all out of sample.
  timing    the finish-hazard shape by round and the first-minute factor, by
            maximum likelihood on priced fights, with the market setting each
            fight's own overall finish rate.
  stats     burst + slope by outcome, style-index shrinkage and exponents,
            the noise shape, and how much of it the two corners share.
  backtest  the whole projection on held-out fights against two baselines a
            DFS player already has for free: DK's own FPPF-style career average,
            and the market with an average fighter's stats. Plus a calibration
            check of the simulated DISTRIBUTION, which is what the percentile
            objectives actually read.

Data: UFCStats via the Greco1899 mirror (edge/mma.py) and ufc-master.csv
prices (edge/mma.py::priced_fights). Everything is walk-forward: a fighter's
style index for a fight uses only fights before it.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import json
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np                                   # noqa: E402

from edge import mma, mma_market as mk, mma_sim as sim   # noqa: E402
from edge.names import norm                          # noqa: E402


def _lg(p):
    p = min(max(p, 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _fit_logistic(X, y, iters=60, l2=1e-6):
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    w = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ w))
        g = X.T @ (p - y) + l2 * w
        H = (X * (p * (1 - p))[:, None]).T @ X + l2 * np.eye(len(w))
        w -= np.linalg.solve(H, g)
    return w


def _ll(p, y):
    p = np.clip(p, 1e-9, 1 - 1e-9)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------
def fit_scoring() -> None:
    boards = {}
    files = sorted(glob.glob(str(ROOT / "data/mma_raw/draftables_*.json")))
    for path in files:
        for p in json.load(open(path)).get("draftables", []):
            st = {a["id"]: a["value"] for a in p.get("draftStatAttributes") or []}
            try:
                fppf = float(st.get(mma.FPPF_STAT_ID))
            except (TypeError, ValueError):
                continue
            boards[norm(p["displayName"])] = (p["displayName"], fppf)
    hist = mma.fighter_history()
    rows = []
    for k, (name, fppf) in boards.items():
        h = hist.get(k) or hist.get(k + "@L") or hist.get(k + "@H")
        if h:
            rows.append((name, fppf, h))
    print(f"scoring: {len(rows)} fighters with a DK FPPF and UFC history "
          f"({len(files)} saved boards)")

    def variant(a, which):
        me, won = a["me"], a["me"]["won"]
        b = mma.win_bonus(won, a["method"], a["rnd"], a["sec_in_round"])
        quick = (won and a["method"] != "DEC" and a["rnd"] == 1
                 and a["sec_in_round"] <= mma.QUICK_WIN_SECONDS)
        full = mma.stat_points(me["strikes"], me["sig"], me["ctrl"], me["td"],
                               me["rev"], me["kd"]) + b
        return {
            "shipped (sig 0.4, other 0.2)": full,
            "sig counted once (0.2)": full - 0.2 * me["sig"],
            "no quick-win bonus": full - (mma.QUICK_WIN_BONUS if quick else 0),
            "no reversals": full - 5 * me["rev"],
            "no control time": full - 0.03 * me["ctrl"],
        }[which]

    for which in ("shipped (sig 0.4, other 0.2)", "sig counted once (0.2)",
                  "no quick-win bonus", "no reversals", "no control time"):
        for nc in (True, False):
            errs, exact = [], 0
            for _name, fppf, h in rows:
                hh = [a for a in h if nc or not a["nc"]]
                if not hh:
                    continue
                m = statistics.fmean(variant(a, which) for a in hh)
                errs.append(m - fppf)
                exact += abs(round(m, 1) - fppf) < 0.051
            print(f"  {which:30s} no-contests {'in ' if nc else 'out'}: "
                  f"MAE {statistics.fmean(abs(e) for e in errs):6.3f}  "
                  f"bias {statistics.fmean(errs):+6.3f}  exact to 0.1: "
                  f"{exact}/{len(errs)}")


# ---------------------------------------------------------------------------
# market
# ---------------------------------------------------------------------------
def fit_market(priced, split=dt.date(2021, 1, 1)) -> None:
    ml, dec, six = [], [], []
    for f, o in priced:
        if f["nc"] or f["draw"]:
            continue
        a_won = f["f"][0]["won"]
        if all(o["ml"]):
            q = mk.devig_power(o["ml"])
            ml.append((f["date"], _lg(q[0]), float(a_won)))
        if all(o["method"]):
            q = mk.devig_power(o["method"])
            dec.append((f["date"], _lg(q[2] + q[5]), float(f["method"] == "DEC")))
            y = (0 if a_won else 3) + {"KO": 0, "SUB": 1, "DEC": 2}[f["method"]]
            six.append((f, o, y))
    print(f"market: {len(ml)} moneylines, {len(dec)} full method markets")

    def split_rows(rows):
        tr = np.array([r[1:] for r in rows if r[0] < split])
        te = np.array([r[1:] for r in rows if r[0] >= split])
        return tr, te

    tr, te = split_rows(ml)
    w = _fit_logistic(tr[:, :1], tr[:, 1])
    p0 = 1 / (1 + np.exp(-te[:, 0]))
    p1 = 1 / (1 + np.exp(-w[0] * te[:, 0]))
    allw = _fit_logistic(np.array([r[1:2] for r in ml]), np.array([r[2] for r in ml]))
    print(f"  ML slope fitted < {split}: {w[0]:.3f}; test n={len(te)} log loss "
          f"{_ll(p0, te[:, 1]):.5f} -> {_ll(p1, te[:, 1]):.5f}.  All data: "
          f"{allw[0]:.3f}  (shipped {mk.ML_SLOPE})")

    tr, te = split_rows(dec)
    w = _fit_logistic(np.c_[np.ones(len(tr)), tr[:, 0]], tr[:, 1])
    p0 = 1 / (1 + np.exp(-te[:, 0]))
    p1 = 1 / (1 + np.exp(-(w[0] + w[1] * te[:, 0])))
    alld = np.array([r[1:] for r in dec])
    wa = _fit_logistic(np.c_[np.ones(len(alld)), alld[:, 0]], alld[:, 1])
    print(f"  DEC fitted < {split}: a={w[0]:+.3f} b={w[1]:.3f}; test n={len(te)} "
          f"log loss {_ll(p0, te[:, 1]):.5f} -> {_ll(p1, te[:, 1]):.5f}; mean "
          f"{p0.mean():.3f} -> {p1.mean():.3f} vs actual {te[:, 1].mean():.3f}")
    print(f"  DEC all data: a={wa[0]:+.3f} b={wa[1]:.3f}  (shipped "
          f"{mk.DEC_A:+.3f} {mk.DEC_B:.3f})")
    for lo, hi in ((2013, 2016), (2017, 2019), (2020, 2022), (2023, 2026)):
        s = np.array([r[1:] for r in dec if lo <= r[0].year <= hi])
        print(f"    {lo}-{hi}: n={len(s)} market P(dec) "
              f"{np.mean(1 / (1 + np.exp(-s[:, 0]))):.3f}  actual {s[:, 1].mean():.3f}")

    # The whole shipped table, out of sample, against the raw power devig.
    te6 = [(f, o, y) for f, o, y in six if f["date"] >= split]
    raw = [-math.log(max(1e-9, mk.devig_power(o["method"])[y])) for f, o, y in te6]
    shipped = []
    for f, o, y in te6:
        t = mk.outcome_table(o["ml"] if all(o["ml"]) else None, o["method"])
        cells = [t["cells"][c] for c in mk.CELLS]
        shipped.append(-math.log(max(1e-9, cells[y] / sum(cells))))
    print(f"  six-way table, test n={len(te6)}: log loss raw power "
          f"{statistics.fmean(raw):.5f} -> shipped {statistics.fmean(shipped):.5f}")

    # Fallback split for a fight with no method market.
    by = collections.defaultdict(lambda: collections.Counter())
    for f in mma.fights():
        if f["date"] and f["date"].year >= 2013 and not f["nc"] and not f["draw"]:
            by[(f["lbs"], f["women"])][f["method"]] += 1
            by[0][f["method"]] += 1
    print("  fallback split (KO, SUB, DEC) by division, 2013+:")
    for k in sorted(by, key=str):
        c = by[k]
        n = sum(c.values())
        if n >= 150:
            print(f"    {k}: n={n} ({c['KO'] / n:.3f}, {c['SUB'] / n:.3f}, "
                  f"{c['DEC'] / n:.3f})")


# ---------------------------------------------------------------------------
# timing
# ---------------------------------------------------------------------------
def _timing_rows(priced):
    rows = {3: [], 5: []}
    for f, o in priced:
        if f["nc"] or f["draw"] or not all(o["method"]):
            continue
        if f["sched_rounds"] not in rows:
            continue
        t = mk.outcome_table(o["ml"] if all(o["ml"]) else None, o["method"],
                             f["lbs"], f["women"], f["sched_rounds"])
        a_won = f["f"][0]["won"]
        if f["method"] == "DEC":
            cell, k = "DEC", None
        else:
            cell = ("A_" if a_won else "B_") + f["method"]
            k = ((f["rnd"] - 1) * sim.BINS_PER_ROUND
                 + min(sim.BINS_PER_ROUND - 1, f["sec_in_round"] // sim.BIN_SEC))
        rows[f["sched_rounds"]].append((f, t["cells"], cell, k))
    return rows


def _timing_ll(rows, shape, first) -> float:
    tot, n = 0.0, 0
    for sched, rs in rows.items():
        if not rs:
            continue
        out = sim.timing_batch([r[1] for r in rs], sched, shape=shape, first=first)
        for i, (_f, _c, cell, k) in enumerate(rs):
            if cell == "DEC":
                p = out["DEC_TOTAL"][i]
            else:
                p = out[cell][i, k] if k < out[cell].shape[1] else 1e-9
            tot += math.log(max(1e-12, p))
            n += 1
    return tot / n if n else float("nan")


def fit_timing(priced, test_from=dt.date(2022, 1, 1),
               train_from=dt.date(2000, 1, 1)) -> tuple[dict, dict]:
    """Fit on [train_from, test_from), grade on [test_from, ...).

    Shipped constants come from `--train-from 2019-01-01 --test-from 2100-01-01`:
    the first-minute finish rate has DRIFTED (4.6% of fights 2012-16, 4.0%
    2017-21, 3.2% 2022-26, while the overall finish rate has not), so an
    all-history fit over-prices the +25 quick-win bonus on a current card.
    """
    rows = _timing_rows(priced)
    train = {s: [r for r in rs if train_from <= r[0]["date"] < test_from]
             for s, rs in rows.items()}
    test = {s: [r for r in rs if r[0]["date"] >= test_from] for s, rs in rows.items()}
    shape = {k: list(v) for k, v in sim.HAZARD_SHAPE.items()}
    first = dict(sim.FIRST_MINUTE)
    flat = ({"KO": [1.0] * 5, "SUB": [1.0] * 5}, {"KO": 1.0, "SUB": 1.0})
    print(f"timing: train {sum(map(len, train.values()))} fights, test "
          f"{sum(map(len, test.values()))}")
    print(f"  flat hazard   train ll {_timing_ll(train, *flat):.5f}  "
          f"test ll {_timing_ll(test, *flat):.5f}")
    best = _timing_ll(train, shape, first)
    # Coordinate ascent: coarse multiplicative steps, then fine ones. Round 1
    # is the reference (1.0); rounds 4 and 5 are tied because only ~500
    # five-round fights carry method prices.
    for sweep in range(12):
        steps = (0.8, 0.9, 1.1, 1.25) if sweep < 4 else (0.95, 0.98, 1.02, 1.05)
        improved = False
        for m in ("KO", "SUB"):
            for r in (1, 2, 3):
                for step in steps:
                    trial = {k: list(v) for k, v in shape.items()}
                    trial[m][r] *= step
                    if r == 3:
                        trial[m][4] = trial[m][3]
                    ll = _timing_ll(train, trial, first)
                    if ll > best + 1e-7:
                        best, shape, improved = ll, trial, True
            for step in steps:
                trial = dict(first)
                trial[m] *= step
                ll = _timing_ll(train, shape, trial)
                if ll > best + 1e-7:
                    best, first, improved = ll, trial, True
        if not improved and sweep >= 4:
            break
    print(f"  fitted        train ll {best:.5f}  test ll "
          f"{_timing_ll(test, shape, first):.5f}")
    print("  HAZARD_SHAPE =", {k: tuple(round(x, 3) for x in v) for k, v in shape.items()})
    print("  FIRST_MINUTE =", {k: round(v, 3) for k, v in first.items()})

    # The two things DK pays on: the round, and the first minute (+25).
    for label, part in (("train", train), ("held-out", test)):
        for sched in (3, 5):
            rs = part[sched]
            if not rs:
                continue
            out = sim.timing_batch([r[1] for r in rs], sched, shape=shape, first=first)
            pred_r = np.zeros(sched)
            act_r = np.zeros(sched)
            pred_q = act_q = 0.0
            for i, (f, _c, cell, _k) in enumerate(rs):
                fin = sum(out[c][i] for c in ("A_KO", "A_SUB", "B_KO", "B_SUB"))
                for r in range(sched):
                    pred_r[r] += fin[r * sim.BINS_PER_ROUND:(r + 1) * sim.BINS_PER_ROUND].sum()
                pred_q += fin[:2].sum()
                if cell != "DEC":
                    act_r[f["rnd"] - 1] += 1
                    act_q += f["rnd"] == 1 and f["sec_in_round"] <= 60
            n = len(rs)
            print(f"  {label} {sched}-round, n={n}: finish by round predicted "
                  f"{np.round(pred_r / n, 3).tolist()} actual "
                  f"{np.round(act_r / n, 3).tolist()}; inside 60s predicted "
                  f"{pred_q / n:.4f} actual {act_q / n:.4f}")
    return shape, first


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------
def fit_baseline(fights, lo: int, hi: int) -> dict:
    by = collections.defaultdict(list)
    for f in fights:
        if f["nc"] or not f["date"] or not (lo <= f["date"].year <= hi):
            continue
        for me in f["f"]:
            by[sim.outcome_key(me["won"], f["draw"], f["method"])].append(
                (f["elapsed"] / 60.0, me["dk_stats"]))
    base = {}
    for k, v in by.items():
        D = np.array([d for d, _ in v])
        y = np.array([s for _, s in v])
        if k.endswith("DEC"):
            # Only the 15- and 25-minute points exist; the line through them.
            m15 = y[np.abs(D - 15) < 0.1].mean()
            m25 = y[np.abs(D - 25) < 0.1].mean() if (np.abs(D - 25) < 0.1).sum() >= 30 else None
            slope = (m25 - m15) / 10.0 if m25 is not None else m15 / 15.0
            base[k] = (round(max(0.0, m15 - slope * 15), 2), round(slope, 3))
        else:
            c, *_ = np.linalg.lstsq(np.c_[np.ones(len(D)), D], y, rcond=None)
            base[k] = (round(max(0.0, c[0]), 2), round(c[1], 3))
    return base


def walk_forward(fights, start: dt.date):
    """Yield (fight, [(off,def) A, (off,def) B], [n prior A, n prior B]) for
    fights on or after `start`, with style indices built ONLY from earlier
    events. Accumulates event by event, so a same-night fight never leaks."""
    acc = collections.defaultdict(lambda: [0.0, 0.0, 0.0, 0.0, 0])
    by_date = collections.defaultdict(list)
    for f in fights:
        if f["date"] is not None:
            by_date[f["date"]].append(f)

    def idx(k):
        a = acc.get(k)
        if a is None or a[4] == 0:
            return (sim.DEBUT_OFF, sim.DEBUT_DEF), 0
        return ((a[0] + sim.K_OFF * sim.E_TYPICAL) / (a[1] + sim.K_OFF * sim.E_TYPICAL),
                (a[2] + sim.K_DEF * sim.E_TYPICAL) / (a[3] + sim.K_DEF * sim.E_TYPICAL)), a[4]

    for day in sorted(by_date):
        todays = by_date[day]
        if day >= start:
            for f in todays:
                ia, na = idx(f["f"][0]["key"])
                ib, nb = idx(f["f"][1]["key"])
                yield f, [ia, ib], [na, nb]
        for f in todays:
            if f["nc"]:
                continue
            mins = f["elapsed"] / 60.0
            for i, me in enumerate(f["f"]):
                opp = f["f"][1 - i]
                e = float(sim.expected_stats(
                    sim.outcome_key(me["won"], f["draw"], f["method"]), mins))
                acc[me["key"]][0] += me["dk_stats"]
                acc[me["key"]][1] += e
                acc[me["key"]][4] += 1
                acc[opp["key"]][2] += me["dk_stats"]
                acc[opp["key"]][3] += e


def fit_stats(fights, lo=2019, hi=2026) -> None:
    base = fit_baseline(fights, lo, hi)
    print(f"stats: BASELINE fitted on {lo}-{hi} =")
    for k in sorted(base):
        print(f"    {k!r}: {base[k]},")
    sim.BASELINE.update(base)

    # Era drift, against the shipped baseline: is a pooled window safe?
    drift = collections.defaultdict(lambda: [0.0, 0.0])
    for f in fights:
        if f["nc"] or not f["date"] or f["date"].year < 2010:
            continue
        for me in f["f"]:
            e = float(sim.expected_stats(sim.outcome_key(me["won"], f["draw"], f["method"]),
                                         f["elapsed"] / 60.0))
            drift[f["date"].year][0] += me["dk_stats"]
            drift[f["date"].year][1] += e
    print("  actual / baseline stat points by year:",
          {y: round(a / e, 3) for y, (a, e) in sorted(drift.items())})

    recs = list(walk_forward(fights, dt.date(2016, 1, 1)))
    tr = [r for r in recs if r[0]["date"].year < 2022]
    te = [r for r in recs if r[0]["date"].year >= 2022]

    def evaluate(rows, a, b):
        err = []
        for f, idx, _n in rows:
            if f["nc"]:
                continue
            mins = f["elapsed"] / 60.0
            for i, me in enumerate(f["f"]):
                off = idx[i][0]
                dfn = idx[1 - i][1]
                e = float(sim.expected_stats(
                    sim.outcome_key(me["won"], f["draw"], f["method"]), mins))
                pred = e * off ** a * dfn ** b
                err.append((pred, me["dk_stats"]))
        p = np.array([x for x, _ in err])
        y = np.array([x for _, x in err])
        return float(np.mean(np.abs(p - y))), float(np.mean(p - y)), float(np.corrcoef(p, y)[0, 1])

    print("  outcome+duration known, style exponents (a, b) -> MAE, bias, corr")
    for a, b in ((0, 0), (0.6, 0.6), (0.8, 0.8), (1.0, 0.8), (1.0, 1.0)):
        print(f"    a={a} b={b}: train {tuple(round(x, 3) for x in evaluate(tr, a, b))}"
              f"   test {tuple(round(x, 3) for x in evaluate(te, a, b))}")

    # Noise: ratio actual / predicted, and how much the two corners share.
    ratios, pairs = [], []
    for f, idx, _n in tr:
        if f["nc"]:
            continue
        mins = f["elapsed"] / 60.0
        rr = []
        for i, me in enumerate(f["f"]):
            e = float(sim.expected_stats(sim.outcome_key(me["won"], f["draw"], f["method"]),
                                         mins, idx[i][0], idx[1 - i][1]))
            if e > 4:
                ratios.append(me["dk_stats"] / e)
                rr.append(me["dk_stats"] / e)
        if len(rr) == 2:
            pairs.append(rr)
    r = np.array(ratios)
    pr = np.array(pairs)
    cv2 = (r.std() / r.mean()) ** 2
    rho = float(np.corrcoef(pr[:, 0], pr[:, 1])[0, 1])
    print(f"  noise: ratio mean {r.mean():.3f} sd {r.std():.3f} -> gamma shape "
          f"{1 / cv2:.2f}; corner-to-corner correlation of the ratio {rho:+.3f}")

    # Debuts: how does a first UFC fight compare to the average fighter?
    deb = [[], []]
    for f, idx, n in te + tr:
        if f["nc"]:
            continue
        mins = f["elapsed"] / 60.0
        for i, me in enumerate(f["f"]):
            if n[i] == 0:
                e = float(sim.expected_stats(sim.outcome_key(me["won"], f["draw"], f["method"]),
                                             mins, 1.0, idx[1 - i][1]))
                deb[0].append(me["dk_stats"])
                deb[1].append(e)
    print(f"  debut fighters n={len(deb[0])}: actual/expected stat points "
          f"{sum(deb[0]) / sum(deb[1]):.3f} (DEBUT_OFF)")


# ---------------------------------------------------------------------------
# backtest
# ---------------------------------------------------------------------------
def backtest(fights, priced, test_from: dt.date, n_dist: int = 400,
             strict: bool = False) -> None:
    """Grade the whole projection on fights from `test_from` on.

    strict=True refits BOTH things the shipped constants were fitted on
    through 2026 -- the stats baseline and the timing hazard -- on the three
    years before `test_from` only, so no number reported here has seen a test
    fight. Style indices are walk-forward either way.
    """
    if strict:
        sim.BASELINE.update(fit_baseline(fights, test_from.year - 3, test_from.year - 1))
        shape, first = fit_timing(priced, test_from,
                                  dt.date(test_from.year - 3, 1, 1))
        sim.HAZARD_SHAPE.update({k: tuple(v) for k, v in shape.items()})
        sim.FIRST_MINUTE.update(first)
        print("  (strict: baseline and timing refitted on "
              f"{test_from.year - 3}-{test_from.year - 1} only)")
    by_url = {f["url"]: o for f, o in priced}
    career = collections.defaultdict(list)
    rows = []
    wf = {f["url"]: (idx, n) for f, idx, n in walk_forward(fights, test_from)}
    league_mean = statistics.fmean(me["dk"] for f in fights if f["date"]
                                   and f["date"] < test_from and not f["nc"]
                                   for me in f["f"])
    for f in fights:
        if f["date"] is None:
            continue
        if f["date"] >= test_from and f["url"] in by_url and not f["nc"]:
            o = by_url[f["url"]]
            if all(o["method"]) and all(o["ml"]):
                idx, n = wf[f["url"]]
                rows.append((f, o, idx, n,
                             [statistics.fmean(career[me["key"]]) if career[me["key"]]
                              else league_mean for me in f["f"]]))
        for me in f["f"]:
            career[me["key"]].append(me["dk"])
    print(f"backtest: {len(rows)} priced fights from {test_from} "
          f"({2 * len(rows)} fighter-fights)")

    preds = {"career mean (DK FPPF analog)": [], "market + average fighter": [],
             "market + style (shipped)": []}
    actual = []
    for f, o, idx, n, cm in rows:
        t = mk.outcome_table(o["ml"], o["method"], f["lbs"], f["women"],
                             f["sched_rounds"])
        tim = sim.timing(t["cells"], f["sched_rounds"])
        ea0, eb0 = sim.expected_points(t["cells"], t["p_draw"], f["sched_rounds"],
                                       tim, (1.0, 1.0), (1.0, 1.0))
        ea, eb = sim.expected_points(t["cells"], t["p_draw"], f["sched_rounds"],
                                     tim, idx[0], idx[1])
        preds["career mean (DK FPPF analog)"] += cm
        preds["market + average fighter"] += [ea0, eb0]
        preds["market + style (shipped)"] += [ea, eb]
        actual += [f["f"][0]["dk"], f["f"][1]["dk"]]
    y = np.array(actual)
    for name, p in preds.items():
        p = np.array(p)
        print(f"  {name:32s} MAE {np.mean(np.abs(p - y)):6.2f}  RMSE "
              f"{np.sqrt(np.mean((p - y) ** 2)):6.2f}  bias {np.mean(p - y):+6.2f}  "
              f"corr {np.corrcoef(p, y)[0, 1]:.3f}")
    p = np.array(preds["market + style (shipped)"])
    order = np.argsort(p)
    print("  calibration by projected decile (shipped): projected -> actual")
    for d, chunk in enumerate(np.array_split(order, 10)):
        print(f"    decile {d + 1}: {p[chunk].mean():6.1f} -> {y[chunk].mean():6.1f}")

    # The DISTRIBUTION, which is what the percentile objectives read: where does
    # each real score fall among that fighter's own simulated scores?
    pit = []
    for f, o, idx, _n, _cm in rows[:: max(1, len(rows) // n_dist)]:
        t = mk.outcome_table(o["ml"], o["method"], f["lbs"], f["women"],
                             f["sched_rounds"])
        s = sim.simulate([{"cells": t["cells"], "p_draw": t["p_draw"],
                           "sched": f["sched_rounds"], "idx": idx}],
                         n_sims=2000, seed=len(pit))["points"]
        for i in (0, 1):
            pit.append(float((s[:, i] < f["f"][i]["dk"]).mean()))
    pit = np.array(pit)
    print(f"  simulated-distribution calibration, n={len(pit)} fighter-fights: "
          "share of actual scores below the sim's own percentile")
    for q in (10, 25, 50, 75, 90):
        print(f"    p{q}: {100 * (pit < q / 100).mean():5.1f}%")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--what", nargs="+",
                    default=["scoring", "market", "timing", "stats", "backtest"])
    ap.add_argument("--test-from", default="2022-01-01")
    ap.add_argument("--train-from", default="2000-01-01",
                    help="timing fit: earliest fight used for training")
    ap.add_argument("--strict", action="store_true",
                    help="backtest: refit baseline + timing before --test-from only")
    ap.add_argument("--refresh", action="store_true",
                    help="re-download the UFCStats mirror and the odds file")
    args = ap.parse_args()
    test_from = dt.date.fromisoformat(args.test_from)
    if args.refresh:
        print("mirror:", mma.refresh_mirror(force=True))
    fights = mma.fights()
    priced = mma.priced_fights(refresh=args.refresh)
    print(f"{len(fights)} UFC fights, {len(priced)} with prices\n")
    if "scoring" in args.what:
        fit_scoring()
        print()
    if "market" in args.what:
        fit_market(priced)
        print()
    if "timing" in args.what:
        shape, first = fit_timing(priced, test_from,
                                  dt.date.fromisoformat(args.train_from))
        sim.HAZARD_SHAPE.update({k: tuple(v) for k, v in shape.items()})
        sim.FIRST_MINUTE.update(first)
        print()
    if "stats" in args.what:
        fit_stats(fights)
        print()
    if "backtest" in args.what:
        backtest(fights, priced, test_from, strict=args.strict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
