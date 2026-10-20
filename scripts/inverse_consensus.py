#!/usr/bin/env python3
"""Inverse-consensus / reverse-sentiment research for DK GPPs: is there a
crowd ("noise") signal you can fade, and does any rule built on it beat
random field lineups by more than the noise? Reads data/contests.db.

    python3 scripts/inverse_consensus.py                    # all three sections
    python3 scripts/inverse_consensus.py --section noise    # one section
    python3 scripts/inverse_consensus.py --sport NHL --boot 2000

WHY THIS IS NOT gpp_meta.py AGAIN
gpp_meta.py (GPP_META.md section A2) already asked the lineup-level question
"at a fixed projection, does lower ownership pay?" and found no ROI effect in
MLB or NHL. The investing version of contrarianism is narrower than "be
different": it says the crowd OVERREACTS (prices move past fundamentals), or
that a identifiable cohort of "dumb money" is persistently wrong and its
positions are the thing to fade. Three tests, one per section:

  sentiment  PLAYER level. "Excess sentiment" = ownership above what salary
             and our pre-lock projection imply (within-contest residual of
             log ownership). If the crowd overreacts, excess sentiment
             predicts points BELOW projection (negative slope) and is a fade
             signal. A positive slope means the crowd knows something our
             projection doesn't -- follow it, don't fade it.
  cohort     ENTRANT level. Score each repeat entrant's skill on OTHER slates
             (mean within-contest points percentile), never the held-out one.
             (a) Is skill persistent at all (is there "dumb money")? (b) On
             the held-out slate, do the bottom quintile's lineups still lose,
             and how are they built differently? (c) Does the players the
             skilled cohort owns more than the unskilled one (log ratio)
             score above projection? (c) uses the cohorts' ACTUAL lineups on
             the held-out slate -- hindsight you can't have before lock -- so
             it is an upper bound: if it is null, the cohort route is dead.
  noise      LINEUP level, a simulation against noise. A "strategy" ranks the
             real field's lineups by a pre-lock score and takes the top K
             (1% of the field, at least 5). The null ("noise") is K random
             lineups from the same field: its expected ROI is the field
             mean, its spread comes from 2,000 draws. A strategy has signal
             only if its excess ROI over the field clears the slate-to-slate
             noise, held out by slate. Selecting from the field needs no
             lineup builder, every pick is a real, legal, already-scored
             lineup, and duplication costs are already in its payout.

THE UNIT
Entries in a contest share one realisation, so the independent unit is the
slate: contests on one draft group are averaged first, every interval
resamples whole slates, and every row carries n slates. Under ~5 slates the
row says "not enough data".

PRE-LOCK vs HINDSIGHT
proj and own_pred come from the projection log matched by draft group
(contest_match), so they are pre-lock. %Drafted and the cohorts' lineups are
only known after lock; any score using them is marked (hindsight).
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.contest_stats import cluster_bootstrap, demean_by, ols  # noqa: E402

DB = ROOT / "data" / "contests.db"
MIN_SLATES = 5          # CLAUDE.md: below ~5 slates say "not enough data"
OWN_FLOOR = 0.5         # %: floor before logs, so 0%-owned players don't explode
TOP_FRAC, TOP_MIN = 0.01, 5
N_NULL = 2000
LAMBDAS = (-1.0, -0.5, 0.0, 0.25, 0.5, 1.0, 2.0)   # proj_z - lam * own_z grid (fit on other slates)


def ci(est, lo, hi, nd=2, pct=False) -> str:
    f = (lambda x: f"{100 * x:+.{nd - 1}f} pp") if pct else (lambda x: f"{x:+.{nd}f}")
    star = " **" if np.isfinite(lo) and np.isfinite(hi) and (lo > 0 or hi < 0) else ""
    return f"{f(est)} [{f(lo)}, {f(hi)}]{star}"


def enough(n: int) -> str:
    return "" if n >= MIN_SLATES else " (not enough data)"


# --------------------------------------------------------------------------
# loading
# --------------------------------------------------------------------------
def contests(conn, sport: str | None) -> pd.DataFrame:
    q = """SELECT c.contest_id, c.sport, c.type, c.draft_group, c.slate_date, c.entry_fee AS fee,
                  c.live_entries AS field, m.method, m.proj_draft_group, m.proj_date, m.proj_variant,
                  COALESCE(m.ok_points, 0) ok_points, COALESCE(m.ok_own, 0) ok_own
           FROM contest c LEFT JOIN contest_match m USING(contest_id)
           WHERE c.final = 1 AND c.type = 'gpp'"""
    df = pd.read_sql_query(q, conn)
    return df[df.sport == sport] if sport else df


def projection_for(conn, row) -> pd.DataFrame:
    """The pre-lock log contest_match chose for this contest (draft group, or
    MLB's date-keyed main/recovered logs)."""
    if not row.ok_points:
        return pd.DataFrame(columns=["player_norm", "proj", "own_pred", "salary"])
    if row.method == "draft_group":
        where, args = "draft_group = ? AND variant = ?", (int(row.proj_draft_group), row.proj_variant)
    else:
        where, args = "date = ? AND variant = ?", (row.proj_date, row.proj_variant)
    return pd.read_sql_query(
        f"""SELECT player_norm, AVG(proj) proj, AVG(own_pred) own_pred, AVG(salary) salary
            FROM projection WHERE source = 'ours' AND sport = ? AND {where}
            GROUP BY player_norm""", conn, params=(row.sport, *args))


def players(conn, cons: pd.DataFrame) -> pd.DataFrame:
    """One row per (contest, player): actual %Drafted + fpts, pre-lock proj /
    own_pred / salary (slate_player salary where the log has none)."""
    out = []
    for r in cons.itertuples():
        p = pd.read_sql_query("SELECT player_norm, pct_drafted own, fpts FROM player_result WHERE contest_id = ?",
                              conn, params=(int(r.contest_id),))
        p = p.merge(projection_for(conn, r), on="player_norm", how="left")
        sal = pd.read_sql_query("SELECT player_norm, salary sal2 FROM slate_player WHERE draft_group = ?",
                                conn, params=(int(r.draft_group),))
        p = p.merge(sal, on="player_norm", how="left")
        p["salary"] = pd.to_numeric(p["salary"], errors="coerce").fillna(pd.to_numeric(p.pop("sal2"), errors="coerce"))
        for c in ("contest_id", "sport", "draft_group", "slate_date", "field"):
            p[c] = getattr(r, c)
        if len(p):
            out.append(p)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame()


def entries(conn, cons: pd.DataFrame) -> pd.DataFrame:
    ids = ",".join(str(int(i)) for i in cons.contest_id)
    e = pd.read_sql_query(f"""
        SELECT f.contest_id, f.entry_id, f.points, f.payout, f.own_logprod, f.own_sum, f.proj_imp_sum,
               f.salary, f.dup_count, f.stack1, f.n_teams, e.entrant
        FROM entry_feature f JOIN entry e USING(contest_id, entry_id)
        WHERE f.live = 1 AND f.contest_id IN ({ids})""", conn)
    e = e.merge(cons[["contest_id", "sport", "draft_group", "slate_date", "fee", "field"]], on="contest_id")
    g = e.groupby("contest_id")
    e["pts_pct"] = g["points"].rank(pct=True)
    e["roi"] = np.where(e.fee > 0, e.payout.fillna(0) / e.fee.where(e.fee > 0) - 1, np.nan)
    return e


def lineup_players(conn, cids) -> pd.DataFrame:
    ids = ",".join(str(int(i)) for i in cids)
    return pd.read_sql_query(f"SELECT contest_id, entry_id, player_norm FROM entry_player "
                             f"WHERE contest_id IN ({ids})", conn)


def fe_slope(df: pd.DataFrame, y: str, x: str, n_boot: int, seed: int = 0):
    """Contest-FE slope of y on standardised x, contest-equal weights,
    slate-cluster bootstrap. Returns (est, lo, hi)."""
    d = df.dropna(subset=[y, x])
    d = d.assign(w=1.0 / d.groupby("contest_id")[y].transform("size"))
    cid, slates = d.contest_id.to_numpy(), d.draft_group.to_numpy()
    yv, xv = demean_by(cid, d[y].to_numpy(float), d[x].to_numpy(float))
    w = d.w.to_numpy(float)

    def fit(idx):
        return ols(xv[idx, None], yv[idx], w[idx])[0]
    est, lo, hi, _ = cluster_bootstrap(slates, fit, n_boot=n_boot, seed=seed)
    return float(est), float(lo), float(hi)


def within_resid(df: pd.DataFrame, y: str, xs: list[str]) -> pd.Series:
    """Residual of y on xs (+ intercept) fitted separately in each contest."""
    out = pd.Series(np.nan, index=df.index)
    for _, g in df.dropna(subset=[y, *xs]).groupby("contest_id"):
        if len(g) < len(xs) + 5:
            continue
        X = np.column_stack([np.ones(len(g))] + [g[c].to_numpy(float) for c in xs])
        b = ols(X, g[y].to_numpy(float))
        out.loc[g.index] = g[y].to_numpy(float) - X @ b
    return out


def z_within(df: pd.DataFrame, col: str) -> pd.Series:
    g = df.groupby("contest_id")[col]
    return (df[col] - g.transform("mean")) / g.transform("std").replace(0, np.nan)


# --------------------------------------------------------------------------
# 1. sentiment: does the crowd overreact at the player level?
# --------------------------------------------------------------------------
def sentiment(conn, cons: pd.DataFrame, n_boot: int) -> list[str]:
    out = ["## 1. Sentiment: does over-ownership predict under-performance? (player level)\n",
           "Excess sentiment = within-contest residual of log(%Drafted) after log(salary) and "
           "log(projection). Slope = DK points vs. projection per +1 SD of excess sentiment; "
           "**negative = the crowd overreacts (fade)**, positive = the crowd knows something the "
           "projection doesn't (follow). One contest per draft group (the largest), slate bootstrap. "
           "The salary-only rows have no projection, so their excess includes real information the "
           "crowd priced in; read them as an upper bound on 'follow', not as overreaction.\n",
           "| sport | control | slates / players | slope (pts per SD) | top-quintile minus bottom-quintile excess, pts vs expectation |",
           "|---|---|---|---|---|"]
    one = cons.sort_values("field").groupby("draft_group").tail(1)
    p = players(conn, one)
    if p.empty:
        return out
    p = p[p.salary > 0].copy()
    p["l_own"] = np.log(p.own.clip(lower=OWN_FLOOR))
    p["l_sal"] = np.log(p.salary)
    for sport, d in p.groupby("sport"):
        rows = []
        withp = d[d.proj > 0].copy()
        if withp.draft_group.nunique() >= 2:
            withp["l_proj"] = np.log(withp.proj)
            withp["surprise"] = withp.fpts - withp.proj
            rows.append(("salary + our projection", withp))
            # Scratched / benched players are ~0% owned AND score 0: the crowd's
            # late lineup news alone makes the slope positive. Among players the
            # crowd actually rostered (>= 2%) that channel is mostly gone (MLB's
            # +0.54 fell to +0.01 on 14 slates, 2026-10-19); what is left is
            # over- or under-reaction proper.
            rows.append(("... owned >= 2% only", withp[withp.own >= 2].copy()))
        d = d.copy()
        d["excess"] = within_resid(d, "l_own", ["l_sal"])
        d["surprise"] = within_resid(d, "fpts", ["l_sal"])
        rows.append(("salary only", d))
        for label, x in rows:
            if "excess" not in x:
                x["excess"] = within_resid(x, "l_own", ["l_sal", "l_proj"])
            x = x.dropna(subset=["excess", "surprise"]).copy()
            if x.draft_group.nunique() < 2:
                continue
            x["z_ex"] = z_within(x, "excess")
            est, lo, hi = fe_slope(x, "surprise", "z_ex", n_boot)
            x["q"] = x.groupby("contest_id")["excess"].transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False))
            per = x.groupby(["draft_group", "q"]).surprise.mean().unstack()
            diff = (per[4] - per[0]).dropna()
            k = int((diff > 0).sum())
            n = x.draft_group.nunique()
            out.append(f"| {sport} | {label} | {n} / {len(x):,}{enough(n)} | {ci(est, lo, hi)} | "
                       f"{diff.mean():+.2f} ({k}+/{len(diff) - k}-) |")
    return out


# --------------------------------------------------------------------------
# 2. cohort: is there persistent "dumb money", and is its book fadeable?
# --------------------------------------------------------------------------
def cohort(conn, cons: pd.DataFrame, e: pd.DataFrame, n_boot: int, min_other: int = 3) -> list[str]:
    out = ["## 2. Dumb money: are some entrants persistently bad, and is what they own fadeable?\n",
           f"Skill = an entrant's mean within-contest points percentile on OTHER slates (at least "
           f"{min_other}); quintiles Q1 (worst) to Q5 (best) are cut on that, then everything is "
           "measured on the held-out slate. Persistence = Spearman between an entrant's odd- and "
           "even-numbered slates (entrants with >=4 slates).\n"]
    rows_a = ["| sport | entrants (>=4 slates) | persistence rho | held-out slates | Q5 - Q1 points pctile | Q5 - Q1 ROI |",
              "|---|---|---|---|---|---|"]
    rows_b = ["| sport | held-out slates | Q1 vs field: own_sum pctile | lineup own (log-prod) pctile | dup>=2 | salary left > $500 | top stack size |",
              "|---|---|---|---|---|---|---|"]
    rows_c = ["| sport | held-out slates / players | sharp-minus-dumb ownership slope, pts vs expectation (hindsight) |",
              "|---|---|---|"]
    e = e.dropna(subset=["entrant"])
    for sport, d in e.groupby("sport"):
        per = d.groupby(["entrant", "draft_group", "slate_date"]).agg(pct=("pts_pct", "mean"),
                                                                       roi=("roi", "mean")).reset_index()
        # (a) persistence
        cnt = per.groupby("entrant").draft_group.transform("nunique")
        pp = per[cnt >= 4].sort_values("slate_date")
        pp["k"] = pp.groupby("entrant").cumcount() % 2
        halves = pp.groupby(["entrant", "k"]).pct.mean().unstack().dropna()
        rho = halves.rank().corr().iloc[0, 1] if len(halves) > 20 else np.nan
        # leave-one-slate-out quintiles
        held = []
        slates = per.draft_group.unique()
        tot = per.groupby("entrant").agg(s=("pct", "sum"), n=("pct", "size"))
        for s in slates:
            here = per[per.draft_group == s].set_index("entrant")
            other = tot.loc[here.index]
            other = other.assign(s=other.s - here.pct, n=other.n - 1)
            other = other[other.n >= min_other]
            if len(other) < 50:
                continue
            skill = other.s / other.n
            q = pd.qcut(skill.rank(method="first"), 5, labels=False)
            held.append(pd.DataFrame({"entrant": q.index, "q": q.values, "draft_group": s}))
        if not held:
            continue
        H = pd.concat(held)
        dq = d.merge(H, on=["entrant", "draft_group"])
        ns = dq.draft_group.nunique()

        def gap(col):
            m = dq.groupby(["draft_group", "q"])[col].mean().unstack()
            m = m.dropna(subset=[0, 4])
            arr = (m[4] - m[0]).to_numpy()
            est, lo, hi, _ = cluster_bootstrap(np.arange(len(arr)), lambda i: arr[i].mean(), n_boot=n_boot, seed=2)
            return ci(float(est), float(lo), float(hi), pct=(col == "pts_pct"))
        rows_a.append(f"| {sport} | {len(halves):,} | {rho:+.2f} | {ns}{enough(ns)} | {gap('pts_pct')} | {gap('roi')} |")

        # (b) how Q1 builds, as within-contest percentiles vs the whole field
        f = d.copy()
        g = f.groupby("contest_id")
        f["own_pct"] = g.own_sum.rank(pct=True)
        f["lp_pct"] = g.own_logprod.rank(pct=True)
        f["dup2"] = f.dup_count >= 2
        f["left500"] = (f.salary > 0) & (f.salary < 49_500)
        f = f.merge(H, on=["entrant", "draft_group"], how="left")
        q1 = f[f.q == 0]
        rows_b.append(f"| {sport} | {ns} | {q1.own_pct.mean():.2f} | {q1.lp_pct.mean():.2f} | "
                      f"{q1.dup2.mean():.0%} vs {f.dup2.mean():.0%} | {q1.left500.mean():.0%} vs {f.left500.mean():.0%} | "
                      f"{q1.stack1.mean():.1f} vs {f.stack1.mean():.1f} |")

        # (c) players the skilled cohort owns more than the unskilled one
        cs = cons[(cons.sport == sport) & cons.draft_group.isin(dq.draft_group.unique())]
        lp = lineup_players(conn, cs.contest_id).merge(dq[["contest_id", "entry_id", "q"]], on=["contest_id", "entry_id"])
        if lp.empty:
            continue
        n_q = lp.drop_duplicates(["contest_id", "entry_id"]).groupby(["contest_id", "q"]).size()
        sh = lp.groupby(["contest_id", "player_norm", "q"]).size().div(n_q).unstack(fill_value=0) * 100
        sh = sh.reindex(columns=range(5), fill_value=0)
        sh["signal"] = np.log((sh[4] + OWN_FLOOR) / (sh[0] + OWN_FLOOR))
        pl = players(conn, cs).merge(sh["signal"].reset_index(), on=["contest_id", "player_norm"])
        pl = pl[pl.salary > 0].copy()
        pl["l_sal"] = np.log(pl.salary)
        has = pl.proj > 0
        pl["surprise"] = np.where(has, pl.fpts - pl.proj, np.nan)
        pl.loc[~has, "surprise"] = within_resid(pl[~has], "fpts", ["l_sal"])
        pl["z_sig"] = z_within(pl, "signal")
        if pl.draft_group.nunique() >= 2:
            est, lo, hi = fe_slope(pl, "surprise", "z_sig", n_boot)
            n = pl.draft_group.nunique()
            rows_c.append(f"| {sport} | {n} / {len(pl):,}{enough(n)} | {ci(est, lo, hi)} |")
    out += ["### 2a. Persistence and held-out quintile gap\n", *rows_a,
            "\n### 2b. What the worst quintile's lineups look like (held-out slates; pctile 0.50 = field)\n", *rows_b,
            "\n### 2c. Fade the dumb cohort's book? (player level; uses held-out lineups = hindsight upper bound)\n", *rows_c]
    return out


# --------------------------------------------------------------------------
# 3. noise: rules that pick from the real field vs random picks
# --------------------------------------------------------------------------
def pred_own_lp(conn, cons: pd.DataFrame) -> pd.Series:
    """Pre-lock lineup ownership: sum(log(own_pred/100)) over each entry's
    players, own_pred from the matched projection log. Players the log never
    carried get OWN_FLOOR (they were off the model's radar = low-owned)."""
    parts = []
    for r in cons[cons.ok_own == 1].itertuples():
        pj = projection_for(conn, r)[["player_norm", "own_pred"]]
        lp = lineup_players(conn, [r.contest_id]).merge(pj, on="player_norm", how="left")
        lp["l"] = np.log(lp.own_pred.fillna(OWN_FLOOR).clip(lower=OWN_FLOOR) / 100)
        parts.append(lp.groupby(["contest_id", "entry_id"]).l.sum())
    return pd.concat(parts).rename("own_pred_lp") if parts else pd.Series(dtype=float, name="own_pred_lp")


def pick_excess(g: pd.DataFrame, score: np.ndarray, rng) -> tuple[float, float]:
    """(ROI of the top-K lineups by score minus the field-mean ROI, sd of that
    excess under the random-K null). Ties are broken at random."""
    roi = g.roi.to_numpy(float)
    k = max(TOP_MIN, int(round(TOP_FRAC * len(g))))
    order = np.lexsort((rng.random(len(g)), -score))
    ex = roi[order[:k]].mean() - roi.mean()
    null = np.array([roi[rng.choice(len(g), k, replace=False)].mean() for _ in range(N_NULL)]) - roi.mean()
    return ex, null.std()


def noise(conn, cons: pd.DataFrame, e: pd.DataFrame, n_boot: int) -> list[str]:
    out = ["## 3. Strategies vs noise (pick the top 1% of the real field by a rule; held out by slate)\n",
           "Excess = ROI of the rule's top-K field lineups minus the field's mean ROI (K = 1% of the "
           f"field, min {TOP_MIN}), averaged per slate. Noise floor = 2 x the null's standard error "
           "over these slates: an excess inside it is what random picks produce. z = mean per-slate "
           "excess / null sd. `proj-lev` picks lambda in proj_z - lambda x own_z on the OTHER slates "
           f"(grid {LAMBDAS}) and applies it to the held-out one.\n",
           "| sport | rule | slates | mean excess ROI [95% CI] | noise floor (+/-) | mean z | slates above field |",
           "|---|---|---|---|---|---|---|"]
    rng = np.random.default_rng(0)
    e = e.dropna(subset=["roi"]).copy()
    e = e.merge(pred_own_lp(conn, cons).reset_index(), on=["contest_id", "entry_id"], how="left")
    e["z_proj"] = z_within(e, "proj_imp_sum")
    e["z_ownp"] = z_within(e, "own_pred_lp")
    e["z_owna"] = z_within(e, "own_logprod")
    rules = {
        "consensus (max pred own)": ("pre", lambda g: g.z_ownp.to_numpy()),
        "inverse consensus (min pred own)": ("pre", lambda g: -g.z_ownp.to_numpy()),
        "max projection": ("pre", lambda g: g.z_proj.to_numpy()),
        "inverse consensus (min actual own)": ("hindsight", lambda g: -g.z_owna.to_numpy()),
        "consensus (max actual own)": ("hindsight", lambda g: g.z_owna.to_numpy()),
    }
    for sport, d in e.groupby("sport"):
        res = {}
        for name, (_, fn) in rules.items():
            rows = []
            for cid, g in d.groupby("contest_id"):
                s = fn(g)
                if not np.isfinite(s).any():
                    continue
                ex, sd = pick_excess(g, np.nan_to_num(s, nan=-9), rng)
                rows.append((g.draft_group.iat[0], ex, sd))
            if rows:
                res[name] = pd.DataFrame(rows, columns=["dg", "ex", "sd"]).groupby("dg").mean()
        # proj-lev, lambda fitted leave-one-slate-out
        dd = d.dropna(subset=["z_proj", "z_ownp"])
        if dd.draft_group.nunique() >= 3:
            grid = {}
            for lam in LAMBDAS:
                rows = []
                for cid, g in dd.groupby("contest_id"):
                    ex, sd = pick_excess(g, (g.z_proj - lam * g.z_ownp).to_numpy(), rng)
                    rows.append((g.draft_group.iat[0], ex, sd))
                grid[lam] = pd.DataFrame(rows, columns=["dg", "ex", "sd"]).groupby("dg").mean()
            M = pd.DataFrame({lam: t.ex for lam, t in grid.items()})
            held = []
            for dg in M.index:
                best = M.drop(index=dg).mean().idxmax()
                held.append((dg, grid[best].ex[dg], grid[best].sd[dg], best))
            hv = pd.DataFrame(held, columns=["dg", "ex", "sd", "lam"]).set_index("dg")
            res[f"proj-lev (lambda chosen out of sample; picked {hv.lam.mode().iat[0]:+.2f} most)"] = hv[["ex", "sd"]]
        for name, t in res.items():
            n = len(t)
            if n < 2:
                continue
            arr = t.ex.to_numpy()
            est, lo, hi, _ = cluster_bootstrap(np.arange(n), lambda i: arr[i].mean(), n_boot=n_boot, seed=3)
            floor = 2 * np.sqrt((t.sd ** 2).mean() / n)
            z = (t.ex / t.sd.replace(0, np.nan)).mean()
            tag = " (hindsight)" if rules.get(name, ("pre",))[0] == "hindsight" else ""
            out.append(f"| {sport} | {name}{tag} | {n}{enough(n)} | {ci(float(est), float(lo), float(hi))} | "
                       f"{floor:.2f} | {z:+.2f} | {(arr > 0).sum()}/{n} |")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--section", choices=["sentiment", "cohort", "noise"], action="append")
    ap.add_argument("--sport")
    ap.add_argument("--boot", type=int, default=1000)
    a = ap.parse_args()
    secs = a.section or ["sentiment", "cohort", "noise"]
    conn = sqlite3.connect(DB)
    cons = contests(conn, a.sport.upper() if a.sport else None)
    e = entries(conn, cons) if {"cohort", "noise"} & set(secs) else None
    out = [f"# Inverse consensus: {len(cons)} final GPPs, {cons.draft_group.nunique()} slates\n"]
    if "sentiment" in secs:
        out += sentiment(conn, cons, a.boot) + [""]
    if "cohort" in secs:
        out += cohort(conn, cons, e, a.boot) + [""]
    if "noise" in secs:
        out += noise(conn, cons, e, a.boot)
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
