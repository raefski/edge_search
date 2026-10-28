#!/usr/bin/env python3
"""Star-out usage test (Adam, 2026-10-28; NBA_MINUTES_CRITIQUE_LOG.md cycle 19).

When a star sits, the minutes model hands his minutes to teammates at THEIR own DK
points per minute. Adam's question: does a superstar (Doncic, Wembanyama) leave
production that those minutes at teammates' rates do not make up? The pooled
with/without test (scripts/nba_onoff_backtest.py, 10/16) said no on average, but it
pooled every 20+ minute teammate; a true high-usage star may differ.

One pooled parameter, not per-pair splits:
    excess  E = sum over FRESH outs s (played the team's previous game) whose last-10
            mean DK points >= T of  m_s x max(0, rate_s - rbar)
            (m_s, rate_s his last-10 minutes and DK pts/min; rbar = tonight's
            minute-weighted rate of the projected actives)
    bump    pts_j += alpha x E x share_j,   share_j = base_j / sum(base) over the
            players it goes to (all actives, or projected starters only)
alpha (least squares) and T / recipients (by MAE) are chosen on 2024-25 only;
2025-26 is held out, the DATE the bootstrap unit. Leak-free games and the base model
come from scripts/nba_projection_backtest.py, as in nba_onoff_backtest.py.

    python3 scripts/critic/nba_star_out.py
"""
from __future__ import annotations

import random
import statistics as st
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from edge import nba_minutes as NM, nba_project as NP   # noqa: E402
import nba_projection_backtest as BT                     # noqa: E402

TS = (0.0, 25.0, 30.0, 35.0, 40.0, 45.0)
RECIP = ("all", "starters")


def rows_for(games):
    """[(date, actual, base, {(T, recip): E x share}, starter)] per active player-game."""
    out = []
    for t in games:
        team = [{"id": r["id"], "position": r["position"], "inactive": r["inactive"],
                 "starter": r["starter"], "history": [(m, s) for m, s, _ in r["history"]]}
                for r in t["rows"]]
        mins = NM.project(team)
        of = NP.opponent_factor(t["opp_pm"], t["league_pm"])
        act = [r for r in t["rows"] if not r["inactive"] and len(r["history"]) >= 5]
        base = {r["id"]: mins[r["id"]]["minutes"] * NP.rate(r["history"], r["position"]) * of for r in act}
        mtot = sum(mins[r["id"]]["minutes"] for r in act)
        rbar = sum(base.values()) / mtot if mtot else 0.0
        outs = []
        for r in t["rows"]:
            last = [(m, p) for m, _, p in r["history"][-10:] if m > 0]
            if not (r["new_out"] and len(last) >= 5):
                continue
            m_s = st.mean(m for m, _ in last)
            ppg = st.mean(p for _, p in last)
            outs.append((ppg, m_s * max(0.0, ppg / m_s - rbar) if m_s else 0.0))
        feats = {}
        for T in TS:
            E = sum(e for ppg, e in outs if ppg >= T and T > 0) if T > 0 else sum(e for _, e in outs)
            for rc in RECIP:
                to = [r for r in act if rc == "all" or r["starter"]]
                tot = sum(base[r["id"]] for r in to) or 1.0
                for r in act:
                    feats.setdefault(r["id"], {})[(T, rc)] = (E * base[r["id"]] / tot) if r in to else 0.0
        for r in act:
            out.append((t["date"], r["pts"], base[r["id"]], feats[r["id"]], r["starter"]))
    return out


def fit_alpha(rows, key):
    x = np.array([f[key] for _, _, _, f, _ in rows])
    y = np.array([a - b for _, a, b, _, _ in rows])
    return float(x @ y / (x @ x)) if x @ x > 0 else 0.0


def preds(rows, key, alpha):
    return np.array([b + alpha * f[key] for _, _, b, f, _ in rows])


def mae(rows, p):
    return float(np.mean(np.abs(p - np.array([a for _, a, _, _, _ in rows]))))


def boot(rows, p, n=1000, seed=0):
    """95% CI of the per-row MAE change, resampling dates."""
    by = defaultdict(list)
    for (d, a, b, _, _), q in zip(rows, p):
        by[d].append(abs(q - a) - abs(b - a))
    dates, rng, s = list(by), random.Random(seed), []
    for _ in range(n):
        xs = [x for d in (rng.choice(dates) for _ in dates) for x in by[d]]
        s.append(sum(xs) / len(xs))
    s.sort()
    return s[int(.025 * n)], s[int(.975 * n)]


def main() -> int:
    tr, te = rows_for(BT.build("2024-25")), rows_for(BT.build("2025-26"))
    base_tr = np.array([b for _, _, b, _, _ in tr])
    print(f"train 2024-25 n={len(tr)}  model alone MAE {mae(tr, base_tr):.3f}")
    best = None
    for T in TS:
        for rc in RECIP:
            a = fit_alpha(tr, (T, rc))
            m = mae(tr, preds(tr, (T, rc), a))
            hit = sum(1 for r in tr if r[3][(T, rc)] > 0)
            print(f"  T={T:>4} {rc:8} alpha {a:+.3f}  train MAE {m:.4f}  rows touched {hit}")
            if best is None or m < best[0]:
                best = (m, T, rc, a)
    _, T, rc, a = best
    key = (T, rc)
    p = preds(te, key, a)
    base_te = np.array([b for _, _, b, _, _ in te])
    hit = [i for i, r in enumerate(te) if r[3][key] > 0]
    hr = [te[i] for i in hit]
    print(f"\nchosen on 2024-25: T={T} recipients={rc} alpha={a:+.3f}")
    print(f"2025-26 held out, n={len(te)} over {len({r[0] for r in te})} dates")
    lo, hi = boot(te, p)
    print(f"  all rows      MAE {mae(te, base_te):.4f} -> {mae(te, p):.4f}  diff CI [{lo:+.4f}, {hi:+.4f}]")
    lo, hi = boot(hr, p[hit])
    print(f"  star-out rows n={len(hr)}  MAE {mae(hr, base_te[hit]):.4f} -> {mae(hr, p[hit]):.4f}  "
          f"diff CI [{lo:+.4f}, {hi:+.4f}]  bias {np.mean(base_te[hit] - [r[1] for r in hr]):+.2f} -> "
          f"{np.mean(p[hit] - [r[1] for r in hr]):+.2f}")
    # the same question asked of the held-out season alone (not a ship criterion)
    for T2 in (35.0, 40.0, 45.0):
        a2 = fit_alpha(te, (T2, rc))
        print(f"  (diagnostic) 2025-26's own best alpha at T={T2}: {a2:+.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
