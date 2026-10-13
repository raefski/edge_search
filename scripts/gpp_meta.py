#!/usr/bin/env python3
"""What finishes in the money in DK GPPs -- chalk or contrarian -- per sport,
and the field-construction numbers the contest simulator should use.
Reads data/contests.db (build it with scripts/contest_db.py).

    python3 scripts/gpp_meta.py                 # everything
    python3 scripts/gpp_meta.py --sport MLB     # one sport
    python3 scripts/gpp_meta.py --no-sharp      # skip the (slower) sharp-share fit
    python3 scripts/gpp_meta.py --boot 2000     # more bootstrap draws
    python3 scripts/gpp_meta.py --section sim   # just the simulator parameters (with CIs)
    python3 scripts/gpp_meta.py --full-cov      # robustness: fully-logged lineups only

THE TRAP, AND HOW THIS AVOIDS IT
"Winners were contrarian" is selected on the OUTCOME: in any slate the chalk
that busted is what made the winners look contrarian. Section 1 shows that
descriptive picture and labels it as such. The ex-ante question is section 2:
across EVERY entry (not just winners), holding our PRE-LOCK projection fixed,
does lower ownership raise the expected cash rate / top-1% rate / ROI, and does
that depend on field size? Every entry in a contest shares one realisation of
the slate, so the unit of independent evidence is the SLATE, not the entry: all
intervals resample whole slates (cluster bootstrap), each contest gets equal
weight, and every claim prints n slates / n contests / n entries.

DEFINITIONS (all within one contest)
  own_sum      summed %Drafted of the lineup's players (CPT slot: CPT share)
  own_lp       log-product ownership, sum(log(own/100)) -- "lineup ownership"
  proj         our pre-lock projected total (log matched by draft group; a board
               player the log never carried is filled from a salary fit, flagged
               by proj_cov) -- never hindsight
  z(.)         standardised within the contest
  cash / top1 / win / roi   from the real payout table (DK's own where cached,
               else the template / flat / scaled reconstruction; see contest_db)
"""
from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.contest_stats import cluster_bootstrap, ols  # noqa: E402

DB = ROOT / "data" / "contests.db"
BANDS = [(0, 600, "<=600"), (601, 1500, "601-1.5k"), (1501, 10**9, ">1.5k")]
CAP = 50000


def band(n: int) -> str:
    return next(lbl for lo, hi, lbl in BANDS if lo <= n <= hi)


def load(conn) -> pd.DataFrame:
    df = pd.read_sql_query("""
        SELECT f.*, c.sport, c.type, c.final, c.live_entries AS field, c.entry_fee AS fee,
               c.draft_group, c.places_paid, c.name, c.max_entries, c.max_entries_seen, c.slate_date,
               COALESCE(m.ok_points, 0) AS ok_points, e.parse_ok, e.entrant
        FROM entry_feature f JOIN contest c USING(contest_id)
        JOIN entry e ON e.contest_id=f.contest_id AND e.entry_id=f.entry_id
        LEFT JOIN contest_match m USING(contest_id)
        WHERE f.live=1""", conn)
    df["band"] = df["field"].map(band)
    df["roi"] = np.where(df["fee"] > 0, df["payout"] / df["fee"].where(df["fee"] > 0) - 1, np.nan)
    g = df.groupby("contest_id")
    df["pts_pct"] = g["points"].rank(pct=True)
    df["own_pct"] = g["own_sum"].rank(pct=True)
    df["lp_pct"] = g["own_logprod"].rank(pct=True)
    df["proj_pct"] = g["proj_imp_sum"].rank(pct=True)
    for col, z in (("own_sum", "z_own"), ("own_logprod", "z_lp"), ("proj_imp_sum", "z_proj")):
        mu, sd = g[col].transform("mean"), g[col].transform("std")
        df[z] = (df[col] - mu) / sd.replace(0, np.nan)
    df["w"] = 1.0 / g["entry_id"].transform("size")       # every contest weighs the same
    return df


def wmean(x, w):
    x, w = np.asarray(x, float), np.asarray(w, float)
    ok = np.isfinite(x)
    return float((x[ok] * w[ok]).sum() / w[ok].sum()) if ok.any() else float("nan")


# --------------------------------------------------------------------------
# 1. descriptive (survivorship-prone): who finishes in the money
# --------------------------------------------------------------------------
def descriptive(df: pd.DataFrame, sport: str) -> list[str]:
    d = df[(df.sport == sport) & (df.type == "gpp") & (df.final == 1) & df.payout.notna()]
    if d.empty:
        return []
    out = [f"\n### {sport}: who finished where ({d.contest_id.nunique()} final GPPs, "
           f"{d.draft_group.nunique()} slates, {len(d):,} entries) -- DESCRIPTIVE, selected on outcome\n",
           "| field band | group | n entries | own_sum (median) | own_sum pctile | log-prod pctile | "
           "proj pctile | dup>=2 |",
           "|---|---|---|---|---|---|---|---|"]
    for lbl in [b[2] for b in BANDS] + ["all"]:
        dd = d if lbl == "all" else d[d.band == lbl]
        if dd.empty:
            continue
        groups = [("field", dd), ("cashed", dd[dd.cash == 1]), ("top 1%", dd[dd.top1 == 1]),
                  ("winner", dd[dd.win == 1])]
        for name, g in groups:
            if g.empty:
                continue
            pp = wmean(g.proj_pct, g.w) if g.ok_points.any() else float("nan")
            out.append(f"| {lbl} ({dd.contest_id.nunique()}c) | {name} | {len(g):,} | "
                       f"{g.own_sum.median():.0f}% | {wmean(g.own_pct, g.w):.2f} | {wmean(g.lp_pct, g.w):.2f} | "
                       f"{'-' if math.isnan(pp) else f'{pp:.2f}'} | {wmean(g.dup_count >= 2, g.w):.1%} |")
    return out


# --------------------------------------------------------------------------
# 2. ex-ante: holding projection fixed, does lower ownership pay?
# --------------------------------------------------------------------------
OUTCOMES = [("cash", "cash rate"), ("top1", "top-1% rate"), ("roi", "ROI"), ("pts_pct", "points pctile")]


def fe_design(d: pd.DataFrame, own_col: str, with_field: bool):
    """Contest-demeaned X (z_proj, z_own[, z_own*Lc, z_proj*Lc]) and the
    contest-equal weights. Lc = log2(field) centred on this sample, so an
    interaction is the change in the slope per DOUBLING of the field."""
    L = np.log2(d["field"].to_numpy(float))
    Lc = L - np.average(L, weights=d["w"])
    cols = [d["z_proj"].to_numpy(float), d[own_col].to_numpy(float)]
    names = ["z_proj", own_col]
    if with_field:
        cols += [cols[1] * Lc, cols[0] * Lc]
        names += [f"{own_col} x log2(field)", "z_proj x log2(field)"]
    X = np.column_stack(cols)
    cid = d["contest_id"].to_numpy()
    _, inv = np.unique(cid, return_inverse=True)
    cnt = np.bincount(inv)
    Xd = X - np.column_stack([np.bincount(inv, weights=X[:, j]) / cnt for j in range(X.shape[1])])[inv]
    return Xd, names, inv, cnt


def regress(d: pd.DataFrame, own_col: str, n_boot: int) -> list[dict]:
    """One row per (outcome, coefficient): estimate and 95% slate-bootstrap CI."""
    d = d.dropna(subset=["z_proj", own_col])
    with_field = d["field"].nunique() > 1 and np.log2(d["field"]).std() > 0.15
    Xd, names, inv, cnt = fe_design(d, own_col, with_field)
    w = d["w"].to_numpy(float)
    slates = d["draft_group"].to_numpy()
    rows = []
    for y, label in OUTCOMES:
        yv = d[y].to_numpy(float)
        ok = np.isfinite(yv)
        if ok.sum() < 50:
            continue
        ym = np.where(ok, yv, 0.0)
        yd = ym - (np.bincount(inv, weights=ym) / cnt)[inv]

        def fit(idx, yd=yd, ok=ok):
            idx = idx[ok[idx]]
            return ols(Xd[idx], yd[idx], w[idx])
        est, lo, hi, _ = cluster_bootstrap(slates, fit, n_boot=n_boot, seed=1)
        base = wmean(yv[ok], w[ok])
        for j, nm in enumerate(names):
            rows.append({"outcome": label, "term": nm, "est": est[j], "lo": lo[j], "hi": hi[j],
                         "base": base})
    return rows


def quintiles(d: pd.DataFrame, n_boot: int) -> tuple[list[dict], dict]:
    """Within each contest: residual ownership after projection (z_lp on z_proj,
    contest-specific slope), cut into quintiles. Q1 = most contrarian FOR ITS
    PROJECTION, Q5 = chalkiest. Contest-equal means; CI on Q1 - Q5 by slate."""
    d = d.dropna(subset=["z_proj", "z_lp"]).copy()
    res = np.empty(len(d))
    for cid, idx in d.groupby("contest_id").indices.items():
        x, y = d["z_proj"].to_numpy()[idx], d["z_lp"].to_numpy()[idx]
        b = np.polyfit(x, y, 1) if len(idx) > 5 and x.std() > 0 else (0.0, 0.0)
        res[idx] = y - (b[0] * x + b[1])
    d["own_resid"] = res
    d["q"] = d.groupby("contest_id")["own_resid"].transform(
        lambda s: pd.qcut(s.rank(method="first"), 5, labels=False)) + 1
    table = []
    for q in range(1, 6):
        g = d[d.q == q]
        table.append({"q": q, "n": len(g), "own_sum": wmean(g.own_sum, g.w), "proj_pct": wmean(g.proj_pct, g.w),
                      **{y: wmean(g[y], g.w) for y, _ in OUTCOMES}})
    q = d["q"].to_numpy()
    w = d["w"].to_numpy(float)
    slates = d["draft_group"].to_numpy()
    diffs = {}
    for y, _ in OUTCOMES:
        yv = d[y].to_numpy(float)

        def diff(idx, yv=yv):
            a, b = idx[(q[idx] == 1) & np.isfinite(yv[idx])], idx[(q[idx] == 5) & np.isfinite(yv[idx])]
            return wmean(yv[a], w[a]) - wmean(yv[b], w[b])
        est, lo, hi, _ = cluster_bootstrap(slates, diff, n_boot=n_boot, seed=2)
        per = [diff(np.flatnonzero(slates == sl)) for sl in np.unique(slates)]
        per = [x for x in per if np.isfinite(x)]
        diffs[y] = (float(est), float(lo), float(hi), sum(x > 0 for x in per), sum(x < 0 for x in per))
    return table, diffs


def exante(df: pd.DataFrame, sport: str, n_boot: int) -> list[str]:
    d = df[(df.sport == sport) & (df.type == "gpp") & (df.final == 1) & (df.ok_points == 1)
           & df.payout.notna() & df.z_proj.notna()]
    if d.empty:
        return [f"\n### {sport}: no final GPP with a matched pre-lock projection -- nothing ex-ante to say"]
    n_c, n_s = d.contest_id.nunique(), d.draft_group.nunique()
    fields = sorted(d.groupby("contest_id")["field"].first())
    out = [f"\n### {sport}: ex-ante, holding our projection fixed ({n_c} contests, {n_s} slates, "
           f"{len(d):,} entries; fields {fields[0]:,}-{fields[-1]:,}; mean proj coverage "
           f"{wmean(d.proj_cov, d.w):.0%} of lineup slots logged, rest salary-imputed)\n"]
    if n_s < 5:
        out.append(f"**{n_s} slate(s): too few to conclude anything -- every number below is one or two "
                   f"realisations of the slate, shown for completeness.**\n")
    show_ci = n_s >= 5
    out += ["Contest-FE linear probability / OLS, contest-equal weights, "
            + ("95% CI from a slate bootstrap. " if show_ci else "POINT ESTIMATES ONLY (a bootstrap over "
               f"{n_s} slate(s) is not an interval). ")
            + "Coefficient = change in the outcome per +1 within-contest SD. The interaction is the change "
            "in the ownership slope per DOUBLING of the field, centred on this sample's average field.\n",
            "| own measure | outcome | base | z_proj | z_own | z_own x log2(field) |",
            "|---|---|---|---|---|---|"]
    for own_col, label in (("z_lp", "log-prod"), ("z_own", "summed")):
        rows = regress(d, own_col, n_boot)
        by = defaultdict(dict)
        for r in rows:
            by[r["outcome"]][r["term"]] = r
        for oc, terms in by.items():
            def c(term):
                r = terms.get(term)
                if not r:
                    return "-"
                sc = 100 if oc in ("cash rate", "top-1% rate", "points pctile") else 1
                u = "pp" if sc == 100 else ""
                if not show_ci:
                    return f"{r['est'] * sc:+.2f}{u}"
                star = "**" if r["lo"] * r["hi"] > 0 else ""
                return f"{star}{r['est'] * sc:+.2f}{u} [{r['lo'] * sc:+.2f}, {r['hi'] * sc:+.2f}]{star}"
            base = next(iter(terms.values()))["base"]
            b = f"{base:.1%}" if oc in ("cash rate", "top-1% rate") else f"{base:+.3f}"
            out.append(f"| {label} | {oc} | {b} | {c('z_proj')} | {c(own_col)} | "
                       f"{c(f'{own_col} x log2(field)')} |")
    table, diffs = quintiles(d, n_boot)
    out += ["\nOwnership quintile AFTER projection (Q1 = lowest log-prod ownership for its projection):\n",
            "| quintile | n | own_sum | proj pctile | cash rate | top-1% | ROI | pts pctile |",
            "|---|---|---|---|---|---|---|---|"]
    for t in table:
        out.append(f"| Q{t['q']} | {t['n']:,} | {t['own_sum']:.0f}% | {t['proj_pct']:.2f} | {t['cash']:.1%} | "
                   f"{t['top1']:.2%} | {t['roi']:+.3f} | {t['pts_pct']:.3f} |")
    def cell(y, t, ci):
        sc, u = (1, "") if y == "roi" else (100, "pp")
        txt = f"{t[0] * sc:+.2f}{u}"
        if ci:
            star = "**" if t[1] * t[2] > 0 else ""
            txt = f"{star}{txt} [{t[1] * sc:+.2f}, {t[2] * sc:+.2f}]{star}"
        return f"{txt} ({t[3]}+/{t[4]}-)"
    out.append("| Q1 - Q5 | | | | " + " | ".join(cell(y, diffs[y], show_ci)
                                                for y in ("cash", "top1", "roi", "pts_pct")) + " |")
    out.append("\n(k+/m-) = slates where Q1 beat / trailed Q5. ** = 95% interval excludes 0.")
    # by field band, Q1 - Q5 only (where a band has >= 2 slates)
    bands = []
    for lo, hi, lbl in BANDS:
        dd = d[d.band == lbl]
        if dd.draft_group.nunique() >= 2:
            _, df_ = quintiles(dd, max(200, n_boot // 2))
            ci_ok = dd.draft_group.nunique() >= 5
            bands.append(f"| {lbl} | {dd.contest_id.nunique()} | {dd.draft_group.nunique()} | {len(dd):,} | "
                         + " | ".join(cell(y, df_[y], ci_ok) for y in ("cash", "top1", "roi")) + " |")
    if bands:
        out += ["\nQ1 - Q5 by field band (pp for rates; intervals only where the band has >= 5 slates):\n",
                "| field | contests | slates | entries | cash | top-1% | ROI |", "|---|---|---|---|---|---|---|"] + bands
    adam = d[d.entrant.str.lower() == "gorillabiscuit"]
    if len(adam):
        out.append(f"\nAdam's own {len(adam)} entries here: own_sum pctile {adam.own_pct.mean():.2f}, "
                   f"log-prod pctile {adam.lp_pct.mean():.2f}, proj pctile {adam.proj_pct.mean():.2f}, "
                   f"cash {adam.cash.mean():.0%}, ROI {adam.roi.mean():+.2f} (field: 0.50 / 0.50 / 0.50)")
    return out


def projection_only(df: pd.DataFrame, sport: str, n_boot: int) -> list[str]:
    """Our projection ALONE (no ownership term): does it separate GPP lineups?
    Read beside section 2's joint fit -- if ownership absorbs the projection's
    slope there, the field's consensus knows what our projection knows."""
    d = df[(df.sport == sport) & (df.type == "gpp") & (df.final == 1) & (df.ok_points == 1)
           & df.payout.notna() & df.z_proj.notna()]
    n_s = d.draft_group.nunique()
    if n_s < 2:
        return []
    w = d["w"].to_numpy(float)
    slates = d["draft_group"].to_numpy()
    zp = d["z_proj"].to_numpy(float)
    _, inv = np.unique(d["contest_id"].to_numpy(), return_inverse=True)
    cnt = np.bincount(inv)
    xd = zp - (np.bincount(inv, weights=zp) / cnt)[inv]
    cells = []
    for y, label in OUTCOMES:
        yv = d[y].to_numpy(float)
        ok = np.isfinite(yv)
        ym = np.where(ok, yv, 0)
        yd = ym - (np.bincount(inv, weights=ym) / cnt)[inv]

        def fit(idx, yd=yd, ok=ok):
            idx = idx[ok[idx]]
            return ols(xd[idx, None], yd[idx], w[idx])[0]
        est, lo, hi, _ = cluster_bootstrap(slates, fit, n_boot=n_boot, seed=4)
        sc, u = (1, "") if y == "roi" else (100, "pp")
        cells.append(f"{label} {est * sc:+.2f}{u}" + (f" [{lo * sc:+.2f}, {hi * sc:+.2f}]" if n_s >= 5 else ""))
    return [f"\nOur projection ALONE (no ownership term), per +1 SD, {n_s} slates: " + "; ".join(cells)]


def ownership_only(df: pd.DataFrame, sport: str, n_boot: int) -> list[str]:
    """No projection control (every final GPP, incl. slates with no log): the
    raw ownership gradient. Biased toward 'chalk is good' when chalk is chalk
    because it projects well -- read beside section 2, never instead of it."""
    d = df[(df.sport == sport) & (df.type == "gpp") & (df.final == 1) & df.payout.notna() & df.z_lp.notna()]
    if d.draft_group.nunique() < 2:
        return []
    w = d["w"].to_numpy(float)
    slates = d["draft_group"].to_numpy()
    zl = d["z_lp"].to_numpy(float)
    out = [f"\nOwnership only, NO projection control ({d.contest_id.nunique()} contests, "
           f"{d.draft_group.nunique()} slates, {len(d):,} entries): slope per +1 SD log-prod ownership\n",
           "| outcome | slope [95% CI] |", "|---|---|"]
    cid = d["contest_id"].to_numpy()
    _, inv = np.unique(cid, return_inverse=True)
    cnt = np.bincount(inv)
    xd = zl - (np.bincount(inv, weights=zl) / cnt)[inv]
    for y, label in OUTCOMES:
        yv = d[y].to_numpy(float)
        ok = np.isfinite(yv)
        ym = np.where(ok, yv, 0)
        yd = ym - (np.bincount(inv, weights=ym) / cnt)[inv]

        def fit(idx, yd=yd, ok=ok):
            idx = idx[ok[idx]]
            return ols(xd[idx, None], yd[idx], w[idx])[0]
        est, lo, hi, _ = cluster_bootstrap(slates, fit, n_boot=n_boot, seed=3)
        sc, u = (1, "") if y == "roi" else (100, "pp")
        out.append(f"| {label} | {est * sc:+.2f}{u} [{lo * sc:+.2f}, {hi * sc:+.2f}] |")
    return out


# --------------------------------------------------------------------------
# 3. field construction: stacks, dupes, ownership, salary
# --------------------------------------------------------------------------
STACKS = {
    "NFL": [("QB+>=1 WR/TE", "qb_mates", 1), ("QB+>=2", "qb_mates", 2), ("bring-back>=1", "bringback", 1)],
    "NCAAF": [("QB+>=1 WR", "qb_mates", 1), ("QB+>=2", "qb_mates", 2), ("bring-back>=1", "bringback", 1)],
    "MLB": [("team hitters>=3", "stack1", 3), (">=4", "stack1", 4), ("=5", "stack1", 5),
            ("2nd stack>=3", "stack2", 3), ("hitter vs own P", "vs_own_anchor", 1)],
    "NHL": [("team skaters>=3", "stack1", 3), (">=4", "stack1", 4), ("F-line>=2", "line_max", 2),
            ("F-line>=3", "line_max", 3), ("PP unit>=3", "pp_max", 3), ("skater vs own G", "vs_own_anchor", 1)],
    "MMA": [("both fighters of a bout", "same_bout", 1)],
}


def slate_games(conn) -> dict:
    """{draft_group: games on the slate} from DK draftables (else teams / 2)."""
    out = {}
    for gid, games, teams, src in conn.execute(
            "SELECT draft_group, COUNT(DISTINCT CASE WHEN source='draftables' THEN game END), "
            "COUNT(DISTINCT team), MAX(source='draftables') FROM slate_player GROUP BY draft_group"):
        out[gid] = games if src and games else (teams // 2 if teams else None)
    return out


def _rate(g: pd.DataFrame, col: str, k: int) -> float:
    x = g[col]
    ok = x.notna()
    return wmean((x[ok] >= k).astype(float), g.w[ok]) if ok.any() else float("nan")


def field_params(df: pd.DataFrame, sport: str, games: dict | None = None) -> list[str]:
    """Ownership, dupes, salary and stack rates of real fields. Stack rates use
    only contests where >= 95% of lineups have every player's team known (the
    July MLB slates predate DK draftables snapshots, so their teams come from
    our log alone and would bias the rates toward the players we logged)."""
    d = df[(df.sport == sport) & (df.parse_ok == 1)].copy()
    if d.empty:
        return []
    games = games or {}
    full = d.groupby("contest_id")["team_cov"].apply(lambda x: (x.fillna(0) >= 0.99).mean() >= 0.95)
    d["teams_ok"] = d.contest_id.map(full)
    stacks = STACKS.get(sport, [])
    out = [f"\n### {sport}: field construction (all exports, lineups that parsed; contest-equal means)\n",
           "Stack columns use only contests with every lineup's teams known (n in the last column).\n"]
    hdr = ["type", "field", "contests", "entries", "own_sum field / cashers (median)", "dup>=2",
           "winner dup'd", "salary left median / share >=$1k"]
    out.append("| " + " | ".join(hdr + [s[0] for s in stacks] + ["contests w/ teams"]) + " |")
    out.append("|" + "---|" * (len(hdr) + len(stacks) + 1))
    for ctype in ("gpp", "cash"):
        for lo, hi, lbl in BANDS + [(0, 10**9, "all")]:
            g = d[(d.type == ctype) & (d.field >= lo) & (d.field <= hi)]
            if g.empty:
                continue
            fin = g[(g.final == 1) & g.cash.notna()]
            cashers = fin[fin.cash == 1]
            win = fin[fin.win == 1].groupby("contest_id")["dup_count"].max()
            sal = (CAP - g.salary.where(g.salary_cov == 1)).dropna()
            cells = [ctype, lbl, str(g.contest_id.nunique()), f"{len(g):,}",
                     f"{g.own_sum.median():.0f}% / {cashers.own_sum.median():.0f}%" if len(cashers) else
                     f"{g.own_sum.median():.0f}% / -",
                     f"{wmean(g.dup_count >= 2, g.w):.1%}",
                     f"{(win >= 2).sum()} of {len(win)}" if len(win) else "-",
                     f"${sal.median():,.0f} / {(sal >= 1000).mean():.0%}" if len(sal) else "-"]
            gt = g[g.teams_ok]
            for _, col, k in stacks:
                v = _rate(gt, col, k) if len(gt) else float("nan")
                cells.append("-" if math.isnan(v) else f"{v:.0%}")
            cells.append(str(gt.contest_id.nunique()))
            out.append("| " + " | ".join(cells) + " |")
    if stacks and sport != "MLB":
        out += [f"\nPer GPP contest ({sport}):\n",
                "| contest | date | field | games | own_sum median | dup>=2 | " + " | ".join(s[0] for s in stacks) + " |",
                "|" + "---|" * (6 + len(stacks))]
        for cid, g in d[d.type == "gpp"].groupby("contest_id"):
            r = [str(cid), str(g.slate_date.iloc[0]), f"{int(g.field.iloc[0]):,}",
                 str(games.get(int(g.draft_group.iloc[0])) or "?"), f"{g.own_sum.median():.0f}%",
                 f"{(g.dup_count >= 2).mean():.1%}"]
            r += [f"{_rate(g, col, k):.0%}" if g.teams_ok.all() else "-" for _, col, k in stacks]
            out.append("| " + " | ".join(r) + " |")
    return out


def mlb_shapes(df: pd.DataFrame) -> list[str]:
    d = df[(df.sport == "MLB") & (df.type == "gpp") & (df.parse_ok == 1)]
    full = d.groupby("contest_id")["team_cov"].apply(lambda x: (x.fillna(0) >= 0.99).mean() >= 0.95)
    d = d[d.contest_id.map(full)]
    if d.empty:
        return []
    shape = d.stack1.astype(int).astype(str) + "-" + d.stack2.astype(int).astype(str)
    out = [f"\nMLB GPP primary-secondary stack shapes ({d.contest_id.nunique()} contests with every team "
           f"known, {len(d):,} lineups; contest-equal):\n", "| shape | share |", "|---|---|"]
    w = d.w / d.w.sum()
    s = w.groupby(shape).sum().sort_values(ascending=False)
    for k, v in s.head(10).items():
        out.append(f"| {k} | {v:.1%} |")
    return out


def sim_params(df: pd.DataFrame, sport: str, games: dict, n_boot: int = 2000) -> list[str]:
    """The numbers an in-app GPP field simulator should draw with: per-contest
    rates averaged over contests, 95% CI from resampling contests, split by
    slate size where the sport has enough contests. GPP only, teams known."""
    d = df[(df.sport == sport) & (df.type == "gpp") & (df.parse_ok == 1)].copy()
    if d.empty:
        return []
    full = d.groupby("contest_id")["team_cov"].apply(lambda x: (x.fillna(0) >= 0.99).mean() >= 0.95)
    d["g"] = d.draft_group.map(lambda x: games.get(int(x)))
    metrics = [(lbl, col, k) for lbl, col, k in STACKS.get(sport, [])]
    per = []
    for cid, g in d.groupby("contest_id"):
        row = {"cid": cid, "games": g.g.iloc[0], "field": int(g.field.iloc[0]),
               "dup": float((g.dup_count >= 2).mean()), "own_med": float(g.own_sum.median()),
               "teams": bool(full.get(cid, False))}
        for lbl, col, k in metrics:
            x = g[col].dropna()
            row[lbl] = float((x >= k).mean()) if row["teams"] and len(x) else float("nan")
        per.append(row)
    P = pd.DataFrame(per)
    rng = np.random.default_rng(7)

    def mci(v):
        v = np.asarray([x for x in v if np.isfinite(x)], float)
        if len(v) == 0:
            return "-"
        if len(v) < 3:
            return f"{v.mean():.0%} (n={len(v)}, no CI)"
        bs = [rng.choice(v, len(v)).mean() for _ in range(n_boot)]
        return f"{v.mean():.0%} [{np.percentile(bs, 2.5):.0%}, {np.percentile(bs, 97.5):.0%}] (n={len(v)})"
    groups = [("all slates", P)]
    if P.games.notna().sum() >= 6:
        groups += [("<=4 games", P[P.games <= 4]), (">=5 games", P[P.games >= 5])]
    out = [f"\n### {sport}: simulator parameters (GPP; mean over contests, 95% CI resampling contests)\n",
           "| " + " | ".join(["slates", "contests", "dup>=2", "lineup own_sum median"] + [m[0] for m in metrics]) + " |",
           "|" + "---|" * (4 + len(metrics))]
    for lbl, g in groups:
        if g.empty:
            continue
        out.append("| " + " | ".join([lbl, str(len(g)), mci(g.dup), f"{g.own_med.median():.0f}%"]
                                     + [mci(g[m[0]]) for m in metrics]) + " |")
    return out


# --------------------------------------------------------------------------
# 4. sharp share: how far above an ownership-only field does the real one sit?
# --------------------------------------------------------------------------
QS = (50, 75, 90, 95, 99)


def contest_pool(conn, cid: int):
    """Slot-specific ownership, salary and our (imputed) projection per player."""
    from scripts.contest_db import _imputer
    m = conn.execute("SELECT proj_draft_group, proj_date, sport, draft_group, ok_points FROM contest_match "
                     "WHERE contest_id=?", (cid,)).fetchone()
    if not m or not m[4]:
        return None
    pg, pdate, sport, gid, _ = m
    q = ("SELECT player_norm, proj, pos, salary FROM projection WHERE source='ours' AND sport=? AND date=? AND "
         + ("draft_group=?" if pg is not None else "draft_group IS NULL"))
    prow = conn.execute(q, (sport, pdate, pg) if pg is not None else (sport, pdate)).fetchall()
    proj = {k: p for k, p, _, _ in prow if p is not None}
    imp = _imputer([(pos, sal, p) for _, p, pos, sal in prow])
    sp = {k: (pos, sal) for k, pos, sal in conn.execute(
        "SELECT player_norm, position, salary FROM slate_player WHERE draft_group=?", (gid,))}
    slots = defaultdict(list)
    for k, slot, pct in conn.execute("SELECT player_norm, roster_position, pct FROM player_slot WHERE contest_id=?",
                                     (cid,)):
        slots[slot].append((k, pct))
    lus = Counter()
    for (s,) in conn.execute("SELECT slot FROM entry_player WHERE contest_id=? AND entry_id=("
                             "SELECT MIN(entry_id) FROM entry WHERE contest_id=? AND parse_ok=1 AND n_players>0)",
                             (cid, cid)):
        lus[s] += 1
    players = sorted({k for v in slots.values() for k, _ in v})
    ix = {k: i for i, k in enumerate(players)}
    pj = np.array([proj.get(k, imp((sp.get(k) or (None, None))[0], (sp.get(k) or (None, None))[1]))
                   for k in players])
    sal = np.array([(sp.get(k) or (None, None))[1] or np.nan for k in players], float)
    if np.isfinite(sal).mean() < 0.5:        # no salaries (recovered July MLB slates): no spend rule
        return None
    return {"slots": {s: ([ix[k] for k, _ in v], np.array([p for _, p in v])) for s, v in slots.items()},
            "count": lus, "proj": pj, "sal": sal, "sport": sport}


MIN_SPEND = 48_000      # edge/dfs_field.MIN_SPEND: real fields leave >= $1k unspent only 1-4% of the time


def ownership_field(pool, n: int, rng) -> np.ndarray:
    """Lineups drawn slot by slot from each slot's own %Drafted; duplicate
    players, over-cap and under-$48k lineups rejected (a player with no known
    salary counts at the pool median). Returns (<= n, k) player indices."""
    sal_all = pool["sal"]
    sal_fill = np.where(np.isfinite(sal_all), sal_all, np.nanmedian(sal_all))
    out, have = [], 0
    for _ in range(25):
        cols = []
        for s, c in pool["count"].items():
            idx, p = pool["slots"][s]
            for _ in range(c):
                cols.append(rng.choice(idx, size=n * 2, p=p / p.sum()))
        L = np.column_stack(cols)
        srt = np.sort(L, axis=1)
        ok = (np.diff(srt, axis=1) != 0).all(1)
        tot = sal_fill[L].sum(1)
        ok &= (tot <= CAP) & (tot >= MIN_SPEND)
        out.append(L[ok])
        have += ok.sum()
        if have >= n:
            break
    return np.concatenate(out)[:n]


def sharp_fit(conn, df: pd.DataFrame, sport: str, seed: int = 0) -> list[str]:
    d = df[(df.sport == sport) & (df.type == "gpp") & (df.ok_points == 1) & (df.parse_ok == 1)]
    cids = sorted(d.contest_id.unique())
    rows = []
    rng = np.random.default_rng(seed)
    for cid in cids:
        pool = contest_pool(conn, int(cid))
        if pool is None or "CPT" in pool["count"]:
            continue
        L = ownership_field(pool, 40000, rng)
        if len(L) < 5000:
            continue
        mult = np.ones(L.shape[1])
        raw = (pool["proj"][L] * mult).sum(1)
        real = d[d.contest_id == cid].proj_imp_sum.dropna().to_numpy()
        sd = raw.std()
        best = None
        for s in np.arange(0.0, 0.81, 0.05):
            for b in (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0):
                t = np.exp(b * (raw - raw.max()) / sd)
                wts = (1 - s) / len(raw) + s * t / t.sum()
                order = np.argsort(raw)
                cw = np.cumsum(wts[order])
                qv = np.array([raw[order][np.searchsorted(cw, q / 100 * cw[-1])] for q in QS])
                err = ((qv - np.percentile(real, QS)) ** 2).sum()
                if best is None or err < best[0]:
                    best = (err, s, b, qv)
        rows.append({"cid": cid, "field": int(d[d.contest_id == cid].field.iloc[0]),
                     "raw_q": np.percentile(raw, QS), "real_q": np.percentile(real, QS),
                     "s": best[1], "beta_sd": best[2], "beta_pt": best[2] / sd, "fit_q": best[3],
                     "sse": best[0]})
    if not rows:
        return []
    out = [f"\n### {sport}: sharp share -- real field vs an ownership-only field, scored with OUR projection "
           f"({len(rows)} contests)\n",
           "Ownership-only lineups are drawn slot by slot from each slot's real %Drafted ($48k-$50k spend, "
           "no stacking). A share s of the field is then re-weighted by exp(beta * proj); (s, beta) fitted to "
           "the real field's p50-p99. beta is per SD of the ownership-only field's projection (and per point).\n",
           "| contest | field | own-only p50 / p99 | real p50 / p99 | gap p50 / p99 | s | beta/SD | beta/pt | fit sse |",
           "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        out.append(f"| {r['cid']} | {r['field']:,} | {r['raw_q'][0]:.1f} / {r['raw_q'][-1]:.1f} | "
                   f"{r['real_q'][0]:.1f} / {r['real_q'][-1]:.1f} | {r['real_q'][0] - r['raw_q'][0]:+.1f} / "
                   f"{r['real_q'][-1] - r['raw_q'][-1]:+.1f} | {r['s']:.2f} | {r['beta_sd']:.2f} | "
                   f"{r['beta_pt']:.3f} | {r['sse']:.1f} |")
    s = np.array([r["s"] for r in rows])
    bpt = np.array([r["beta_pt"] for r in rows])
    out.append(f"| **median** | | | | | **{np.median(s):.2f}** (IQR {np.percentile(s, 25):.2f}-"
               f"{np.percentile(s, 75):.2f}) | | **{np.median(bpt):.3f}** | |")
    return out


# --------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sport")
    ap.add_argument("--boot", type=int, default=1000)
    ap.add_argument("--no-sharp", action="store_true")
    ap.add_argument("--section", choices=("descriptive", "exante", "field", "sim", "sharp"))
    ap.add_argument("--full-cov", action="store_true",
                    help="robustness: only lineups whose every player was in the pre-lock log")
    args = ap.parse_args()
    conn = sqlite3.connect(str(DB))
    df = load(conn)
    if args.full_cov:
        df = df[(df.proj_cov == 1) | df.proj_cov.isna()].copy()
        g = df.groupby("contest_id")
        df["w"] = 1.0 / g["entry_id"].transform("size")
    sports = [args.sport] if args.sport else ["MLB", "NFL", "NHL", "NCAAF", "NASCAR", "MMA", "NBA"]
    lines = []
    if args.section in (None, "descriptive"):
        lines.append("\n## 1. Who finished in the money (descriptive -- selected on outcome)")
        for s in sports:
            lines += descriptive(df, s)
    if args.section in (None, "exante"):
        lines.append("\n## 2. Ex-ante: holding projection fixed, does lower ownership pay?")
        for s in sports:
            lines += exante(df, s, args.boot)
            lines += projection_only(df, s, args.boot)
            lines += ownership_only(df, s, args.boot)
    if args.section in (None, "field"):
        lines.append("\n## 3. Field construction")
        games = slate_games(conn)
        for s in sports:
            lines += field_params(df, s, games)
            if s == "MLB":
                lines += mlb_shapes(df)
    if args.section in (None, "sim"):
        lines.append("\n## 3b. Simulator parameters")
        games = slate_games(conn)
        for s in sports:
            lines += sim_params(df, s, games)
    if args.section in (None, "sharp") and not args.no_sharp:
        lines.append("\n## 4. Sharp share")
        for s in sports:
            lines += sharp_fit(conn, df, s)
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
