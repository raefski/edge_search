#!/usr/bin/env python3
"""NBA GPPs: does chalk or contrarian pay at a FIXED projection? (GPP_META.md A2, for NBA)

    python3 scripts/nba_gpp_exante.py

gpp_meta.py's ex-ante test could not run on NBA: no NBA night had a logged
projection. scripts/nba_ownership_fit.rebuild now rebuilds each 2025-26 GPP night
as it stood, so every entry gets our pre-lock projection (sum of its 8 players)
beside its real lineup ownership (log-product of %Drafted), duplicates and payout.
Within each night (contest fixed effects, nights weighted equally), per +1 SD:
cash / top-1% / ROI on projection and on ownership together; then the
ownership quintile at fixed projection (Q1 contrarian - Q5 chalk). Intervals
resample nights. Entries with any player off our board are dropped.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.names import norm  # noqa: E402
from scripts.contest_stats import cluster_bootstrap  # noqa: E402
from scripts.nba_ownership_fit import rebuild  # noqa: E402


def load() -> pd.DataFrame:
    c = sqlite3.connect(ROOT / "data" / "contests.db")
    rows = []
    for cid, gid, d in c.execute("SELECT contest_id, draft_group, slate_date FROM contest WHERE sport='NBA' "
                                 "AND type='gpp' AND final=1 ORDER BY slate_date").fetchall():
        proj = {norm(p["name"]): p["proj"] for p in rebuild(int(gid), d)}
        e = pd.read_sql("SELECT f.entry_id, f.points, f.payout, f.own_logprod, f.dup_count, c.entry_fee fee "
                        "FROM entry_feature f JOIN contest c USING(contest_id) WHERE f.contest_id=? AND f.live=1",
                        c, params=(cid,))
        ep = pd.read_sql("SELECT entry_id, player_norm FROM entry_player WHERE contest_id=?", c, params=(cid,))
        ep["proj"] = ep.player_norm.map(proj)
        g = ep.groupby("entry_id").agg(proj=("proj", "sum"), cov=("proj", lambda s: s.notna().mean()))
        e = e.merge(g, on="entry_id")
        e = e[e["cov"] >= 0.99].copy()
        e["roi"] = e.payout.fillna(0) / e.fee - 1
        e["cash"] = (e.payout.fillna(0) > 0).astype(float)
        k = max(1, int(len(e) * 0.01))
        e["top1"] = (e.points.rank(ascending=False, method="min") <= k).astype(float)
        for col in ("proj", "own_logprod"):
            e["z_" + col] = (e[col] - e[col].mean()) / e[col].std()
        e["date"] = d
        rows.append(e)
    return pd.concat(rows, ignore_index=True)


def main() -> int:
    E = load()
    nights = E.date.unique()

    def fit(idx, y):
        X, ys, W = [], [], []
        for _, g in E[E.date.isin(nights[idx])].groupby("date"):
            ys.append(g[y] - g[y].mean())
            X.append(np.c_[g.z_proj - g.z_proj.mean(), g.z_own_logprod - g.z_own_logprod.mean()])
            W.append(np.full(len(g), 1 / len(g)))
        ys, X, sw = np.concatenate(ys), np.vstack(X), np.sqrt(np.concatenate(W))
        return np.linalg.lstsq(X * sw[:, None], ys * sw, rcond=None)[0]

    out = [f"# NBA GPPs, ex ante: {len(E):,} entries, {len(nights)} nights\n",
           "| outcome | projection, per SD | ownership at fixed projection, per SD |", "|---|---|---|"]
    for y in ("cash", "top1", "roi"):
        est, lo, hi, _ = cluster_bootstrap(np.arange(len(nights)), lambda i: fit(i, y), n_boot=1000, seed=1)
        out.append(f"| {y} | {est[0]:+.4f} [{lo[0]:+.4f}, {hi[0]:+.4f}] | {est[1]:+.4f} [{lo[1]:+.4f}, {hi[1]:+.4f}] |")
    res = []
    for _, g in E.groupby("date"):
        b = np.polyfit(g.proj, g.own_logprod, 1)
        res.append(g.own_logprod - np.polyval(b, g.proj))
    E["own_res"] = pd.concat(res)
    E["q"] = E.groupby("date").own_res.transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False))
    t = E.groupby(["date", "q"])[["roi", "proj", "dup_count"]].mean().unstack()
    d = (t["roi"][0] - t["roi"][4]).to_numpy()
    est, lo, hi, _ = cluster_bootstrap(np.arange(len(d)), lambda i: d[i].mean(), n_boot=2000, seed=2)
    out.append(f"\nQ1 (contrarian for its projection) minus Q5 (chalk) ROI: {est:+.3f} [{lo:+.3f}, {hi:+.3f}] "
               f"({(d > 0).sum()}+/{(d < 0).sum()}-); projection Q1 {t['proj'][0].mean():.1f} vs Q5 "
               f"{t['proj'][4].mean():.1f}; duplicates Q1 {t['dup_count'][0].mean():.1f} vs Q5 {t['dup_count'][4].mean():.1f}")
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
