#!/usr/bin/env python3
"""NBA GPPs: does chalk or contrarian pay at a FIXED projection? (GPP_META.md A2, for NBA)

    python3 scripts/nba_gpp_exante.py                 # every NBA GPP night
    python3 scripts/nba_gpp_exante.py --night 2026-10-20   # also that night in detail

gpp_meta.py's ex-ante test could not run on NBA: no NBA night had a logged
projection. scripts/nba_ownership_fit.rebuild now rebuilds each 2025-26 GPP night
as it stood, so every entry gets our pre-lock projection (sum of its 8 players)
beside its real lineup ownership (log-product of %Drafted), duplicates and payout.
Within each night (contest fixed effects, nights weighted equally), per +1 SD:
cash / top-1% / ROI on projection and on ownership together; then the
ownership quintile at fixed projection (Q1 contrarian - Q5 chalk). Intervals
resample nights. Entries with any player off our board are dropped.

A night with a LIVE pre-lock log matched by draft group (2026-10-20 on) uses the
logged projection the app actually had, not a rebuild.
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
        live = c.execute("SELECT proj_draft_group, proj_date FROM contest_match WHERE contest_id=? AND ok_points=1 "
                         "AND method='draft_group' AND proj_variant='live'", (cid,)).fetchone()
        if live:
            proj = dict(c.execute("SELECT player_norm, AVG(proj) FROM projection WHERE source='ours' AND sport='NBA' "
                                  "AND draft_group=? AND variant='live' GROUP BY player_norm", (live[0],)))
        else:
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
        e["date"], e["cid"], e["gid"], e["logged"] = d, cid, gid, bool(live)
        e["is_adam"] = e.entry_id.map(dict(c.execute("SELECT entry_id, is_adam FROM entry WHERE contest_id=?", (cid,))))
        rows.append(e)
    return pd.concat(rows, ignore_index=True)


def night_detail(E: pd.DataFrame, night: str) -> list[str]:
    """One night: chalk vs contrarian at fixed projection, your entry, the app's lineups."""
    import csv
    c = sqlite3.connect(ROOT / "data" / "contests.db")
    g = E[E.date == night].copy()
    if g.empty:
        return [f"\nno GPP for {night}"]
    b = np.polyfit(g.proj, g.own_logprod, 1)
    g["own_res"] = g.own_logprod - np.polyval(b, g.proj)
    g["q"] = pd.qcut(g.own_res.rank(method="first"), 5, labels=["Q1 contrarian", "Q2", "Q3", "Q4", "Q5 chalk"])
    cid = int(g.cid.iloc[0])
    fee, = c.execute("SELECT entry_fee FROM contest WHERE contest_id=?", (cid,)).fetchone()
    out = [f"\n## {night}: contest {cid}, {len(g):,} entries, projection {'LOGGED pre-lock' if g.logged.iloc[0] else 'rebuilt'}\n",
           "| ownership for its projection | entries | mean projection | mean DK points | cash rate | ROI | dup |",
           "|---|---|---|---|---|---|---|"]
    for q, x in g.groupby("q", observed=True):
        out.append(f"| {q} | {len(x)} | {x.proj.mean():.1f} | {x.points.mean():.1f} | {x.cash.mean():.0%} | "
                   f"{x.roi.mean():+.2f} | {x.dup_count.mean():.1f} |")
    me = g[g.is_adam == 1]
    pts = np.sort(g.points.to_numpy())
    line = g[g.cash == 1].points.min()
    for r in me.itertuples():
        out.append(f"\nYour entry: {r.points:.2f} pts, {(pts < r.points).mean():.0%} of the field beaten, "
                   f"projection {r.proj:.1f} ({(g.proj < r.proj).mean():.0%} pctile), ownership "
                   f"{(g.own_logprod < r.own_logprod).mean():.0%} pctile, payout ${r.payout or 0:.2f}")
    fp = dict(c.execute("SELECT player_norm, fpts FROM player_result WHERE contest_id=?", (cid,)))
    own = dict(c.execute("SELECT player_norm, pct_drafted FROM player_result WHERE contest_id=?", (cid,)))
    path = ROOT / "data" / f"dfs_lineups_nba_{night}.csv"
    if path.exists():
        lus: dict = {}
        for r in csv.DictReader(path.open()):
            if str(r["gid"]) == str(int(g.gid.iloc[0])):
                lus.setdefault(r["mode"], []).append(r["player"])
        for mode, names in lus.items():
            p = sum(fp.get(norm(n), 0.0) for n in names)
            lp = sum(np.log(max(own.get(norm(n), 0.1), 0.1) / 100) for n in names)
            out.append(f"App {mode.upper()} lineup: {p:.2f} pts, beats {(pts < p).mean():.0%} of the field "
                       f"(cash line {line:.2f}: {'CASH' if p >= line else 'no cash'}), ownership "
                       f"{(g.own_logprod < lp).mean():.0%} pctile -- {', '.join(names)}")
    return out


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--night", help="YYYY-MM-DD: also that night in detail")
    a = ap.parse_args()
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
    if a.night:
        out += night_detail(E, a.night)
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
