#!/usr/bin/env python3
"""Is NHL field ownership a market signal our projection should use?

    python3 scripts/nhl_ownership_signal.py            # player-level, leave one DAY out
    python3 scripts/nhl_ownership_signal.py --boot 2000

WHERE THIS COMES FROM
scripts/inverse_consensus.py (GPP_META.md section G) found that NHL players the GPP
crowd owned above what salary + our projection imply went on to beat our
projection: +0.45 DK points per SD of "excess ownership" among players owned
>= 2% (15 slates). That used ACTUAL %Drafted, which only exists after lock.
This asks whether any of it is usable BEFORE lock:

  M0  calibrated projection         fpts ~ a + b * proj
  M1  + our predicted ownership     ... + excess of the LOGGED own_pred over
                                    salary + proj (what the model already says
                                    the crowd will do -- pre-lock)
  M2  + actual ownership            ... + excess of real %Drafted (HINDSIGHT:
                                    the ceiling of what the crowd knew)
  M3  + what the crowd looks at     ... + this season's DK points per game to
                                    date, last season's, PP1 / top-line flags
                                    (pre-lock, from cached box scores)

(A fitted guess of the crowd's surprise from M3's features is a linear mix
of those same features, so it adds nothing to M3 -- tried, identical.)

Every fit is on the other days, scored on the held-out day (an early slate's
games are also on that day's main slate, so a DAY is the fold, as in
NHL_STATUS.md 6f). Skaters only: goalie points beat no predictor held out
(NHL_STATUS.md 6e). One GPP per draft group (the largest field) supplies %Drafted.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.names import norm  # noqa: E402
from edge.nhl import skater_points  # noqa: E402
from scripts.contest_stats import cluster_bootstrap, ols, spearman  # noqa: E402
from scripts.inverse_consensus import OWN_FLOOR, contests, players, within_resid  # noqa: E402

BOX = ROOT / "data" / "nhl_cache" / "box"


def box_key(team: str, name: str) -> tuple:
    """(team, first initial, norm(last)) -- the box score writes 'C. McDavid'."""
    if ". " in name:
        ini, _, last = name.partition(". ")
    else:
        first, *rest = name.split()
        ini, last = first[:1], " ".join(rest)
    return (team, ini[:1].upper(), norm(last))


def skater_games() -> dict:
    """{box_key: [(date, dk_points), ...]} over every cached box score.
    Keys that two different player ids share are dropped, never guessed."""
    games, ids = defaultdict(list), defaultdict(set)
    for f in BOX.glob("*.json"):
        g = json.loads(f.read_text())
        for s in g.get("skaters", []):
            if not s.get("toi") or s["toi"] in ("00:00", "0:00"):
                continue
            k = box_key(s["team"], s["name"])
            ids[k].add(s["id"])
            games[k].append((g["date"], skater_points(s["goals"], s["assists"], s["shots"], s["blocks"])))
    return {k: sorted(v) for k, v in games.items() if len(ids[k]) == 1}


def form(games: dict, team: str, name: str, date: str, season_start: str) -> tuple:
    """(this season's DK pts/game before `date`, games, last season's pts/game).
    A traded player's history sits under his old team: missing, not guessed."""
    g = games.get(box_key(team, name), [])
    cur = [p for d, p in g if season_start <= d < date]
    last = [p for d, p in g if d < season_start]
    return (np.mean(cur) if cur else np.nan, len(cur), np.mean(last) if last else np.nan)


def load(conn) -> pd.DataFrame:
    cons = contests(conn, "NHL")
    cons = cons[cons.ok_points == 1]
    one = cons.sort_values("field").groupby("draft_group").tail(1)
    p = players(conn, one)
    ext = pd.read_sql_query(
        "SELECT draft_group, player_norm, name, team, pos, extra FROM projection "
        "WHERE sport='NHL' AND source='ours'", conn).drop_duplicates(["draft_group", "player_norm"])
    p = p.merge(ext, on=["draft_group", "player_norm"], how="left")
    p = p[(p.pos != "G") & (p.proj > 0) & (p.salary > 0) & p.own_pred.notna()].copy()
    ex = p.extra.map(lambda s: json.loads(s) if isinstance(s, str) and s else {})
    p["pp1"] = (ex.map(lambda e: e.get("pp")) == "PP1").astype(float)
    p["top6"] = ex.map(lambda e: e.get("line")).isin(["F1", "F2", "D1"]).astype(float)
    games = skater_games()
    season_start = "2026-09-01"
    f = [form(games, t, n, d, season_start) for t, n, d in zip(p.team, p.name, p.slate_date)]
    p["form_now"], p["form_n"], p["form_last"] = zip(*f)
    p["day"] = p.slate_date
    p["l_own"] = np.log(p.own.clip(lower=OWN_FLOOR))
    p["l_pred"] = np.log(p.own_pred.clip(lower=OWN_FLOOR))
    p["l_sal"], p["l_proj"] = np.log(p.salary), np.log(p.proj)
    p["ex_pred"] = within_resid(p, "l_pred", ["l_sal", "l_proj"])
    p["ex_act"] = within_resid(p, "l_own", ["l_sal", "l_proj"])
    p["surprise_own"] = p.ex_act - p.ex_pred
    # form relative to tonight's projection; shrink the current-season mean toward
    # last season by games played (k=5: about a fortnight of hockey) and fill a
    # missing side from the other, then from the projection itself.
    last = p.form_last.fillna(p.proj)
    now = p.form_now.fillna(last)
    w = p.form_n / (p.form_n + 5)
    p["form_gap"] = w * now + (1 - w) * last - p.proj
    p["last_gap"] = last - p.proj
    return p


FEATS = {
    "M0 calibrated projection": ["proj"],
    "M1 + predicted-ownership excess": ["proj", "ex_pred"],
    "M2 + ACTUAL-ownership excess (hindsight)": ["proj", "ex_act"],
    "M3 + form / role (what the crowd sees)": ["proj", "form_gap", "last_gap", "pp1", "top6"],
}


def held_out(p: pd.DataFrame) -> pd.DataFrame:
    """Leave-one-day-out predictions for every model, plus the coefficients
    each fold fitted (to see what the signal is, and whether it is stable)."""
    out = p[["day", "draft_group", "fpts", "proj", "own"]].copy()
    coefs = defaultdict(list)
    for day in sorted(p.day.unique()):
        tr, te = p[p.day != day], p.day == day
        te_d = p[te]
        for name, cols in FEATS.items():
            X = np.column_stack([np.ones(len(tr))] + [tr[c] for c in cols])
            bb = ols(X, tr.fpts.to_numpy())
            coefs[name].append(bb[1:])
            out.loc[te, name] = np.column_stack([np.ones(te.sum())] + [te_d[c] for c in cols]) @ bb
    return out, {k: np.array(v) for k, v in coefs.items()}


def score(out: pd.DataFrame, n_boot: int) -> list[str]:
    days = sorted(out.day.unique())
    base = list(FEATS)[0]
    rows = ["| model | held-out MAE | vs M0, per day [95% CI] | days better | within-slate rank corr | rank vs M0 |",
            "|---|---|---|---|---|---|"]
    per = {}
    for name in ["raw projection", *FEATS]:
        col = "proj" if name == "raw projection" else name
        g = out.groupby("day")
        mae = g.apply(lambda d: (d[col] - d.fpts).abs().mean())
        rho = out.groupby("draft_group").apply(lambda d: spearman(d[col], d.fpts)).groupby(
            out.groupby("draft_group").day.first()).mean()
        per[name] = (mae, rho)
    m0, r0 = per[base]
    for name, (mae, rho) in per.items():
        d = (mae - m0).reindex(days).to_numpy()
        dr = (rho - r0).reindex(days).to_numpy()
        est, lo, hi, _ = cluster_bootstrap(np.arange(len(d)), lambda i: d[i].mean(), n_boot=n_boot, seed=4)
        better = int((d < 0).sum())
        star = " **" if hi < 0 or lo > 0 else ""
        rows.append(f"| {name} | {mae.mean():.3f} | {est:+.3f} [{lo:+.3f}, {hi:+.3f}]{star} | "
                    f"{better}/{len(days)} | {rho.mean():+.3f} | {np.nanmean(dr):+.3f} |")
    return rows


def what_crowd_knows(p: pd.DataFrame) -> list[str]:
    """Correlates of the crowd's surprise (actual minus predicted ownership
    excess), within slate: what moves the field that our ownership model
    misses."""
    rows = ["| feature | rank corr with crowd surprise (mean over slates) | slates same sign |",
            "|---|---|---|"]
    for c in ["form_gap", "last_gap", "pp1", "top6", "proj", "salary"]:
        r = p.groupby("draft_group").apply(lambda d: spearman(d[c], d.surprise_own)).dropna()
        s = np.sign(r.mean())
        rows.append(f"| {c} | {r.mean():+.3f} | {int((np.sign(r) == s).sum())}/{len(r)} |")
    return rows


def biases(p: pd.DataFrame, n_boot: int) -> list[str]:
    """Pre-lock cuts of (DK points - projection), per day then averaged, with
    a day bootstrap. No parameters are fitted, so these are out of sample
    except for the choice of cuts (k=5 shrink, F1/F2/D1 = top six)."""
    p = p.assign(res=p.fpts - p.proj,
                 role=np.where(p.pp1 == 1, "PP1", np.where(p.top6 == 1, "top-6 / D1, not PP1", "depth")))
    p["fq"] = p.groupby("draft_group").form_gap.transform(lambda s: pd.qcut(s.rank(method="first"), 5, labels=False))
    rows = ["| cut | skaters | mean owned | points minus projection, per day [95% CI] | days above 0 |",
            "|---|---|---|---|---|"]

    def line(label, mask_or_diff, n="", own=""):
        a = mask_or_diff.dropna().to_numpy()
        est, lo, hi, _ = cluster_bootstrap(np.arange(len(a)), lambda i: a[i].mean(), n_boot=n_boot, seed=5)
        star = " **" if hi < 0 or lo > 0 else ""
        rows.append(f"| {label} | {n} | {own} | {est:+.2f} [{lo:+.2f}, {hi:+.2f}]{star} | {(a > 0).sum()}/{len(a)} |")
    for r, g in p.groupby("role"):
        line(r, g.groupby("day").res.mean(), f"{len(g):,}", f"{g.own.mean():.1f}%")
    t = p.groupby(["day", "fq"]).res.mean().unstack()
    for q in (0, 4):
        g = p[p.fq == q]
        line(f"form quintile {q + 1} ({'cold' if q == 0 else 'hot'})", t[q], f"{len(g):,}", f"{g.own.mean():.1f}%")
    line("form Q5 minus Q1", t[4] - t[0])
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--boot", type=int, default=2000)
    a = ap.parse_args()
    p = load(sqlite3.connect(ROOT / "data" / "contests.db"))
    cov = p.form_now.notna().mean()
    out, coefs = held_out(p)
    lines = [f"# NHL ownership as a projection signal: {p.draft_group.nunique()} slates, "
             f"{p.day.nunique()} days, {len(p):,} skaters (form found for {cov:.0%})\n",
             "## Held out by day (skaters; MAE in DK points)\n", *score(out, a.boot),
             "\n## Fitted coefficients across folds (mean, min..max)\n"]
    for name, b in coefs.items():
        cols = FEATS[name][-b.shape[1]:]
        lines.append(f"- {name}: " + ", ".join(
            f"{c} {b[:, j].mean():+.3f} ({b[:, j].min():+.3f}..{b[:, j].max():+.3f})" for j, c in enumerate(cols)))
    lines += ["\n## Pre-lock projection biases (no ownership needed)\n", *biases(p, a.boot)]
    lines += ["\n## What moves the crowd that our ownership model misses\n", *what_crowd_knows(p)]
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
