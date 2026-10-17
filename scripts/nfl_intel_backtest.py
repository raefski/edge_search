#!/usr/bin/env python3
"""Do our Fantasy-Guru-style tools (edge/nfl_intel.py) improve the NFL model?

    python3 scripts/nfl_intel_backtest.py

Adam, 2026-10-16, after looking at fantasyguru.com's NFL tool list: build our
own versions of the tools that help projections and ownership. edge/nfl_intel
measures; this decides. Two questions, each answered held out BY SLATE (the
slate is the unit -- every player on it shares one Sunday):

  1. PROJECTION. The shipped projection is the props (edge/dfs_project). A
     book that has priced a soft matchup, a fast-paced game or a teammate's
     injury has already put it in the prop. Does any tool still explain what
     the props missed (actual DK points - projection)? Fitted on the other
     slates, scored on the held-out one, against nflverse actuals for players
     who played.

  2. OWNERSHIP. The shipped field model is a softmax over value (+ YouTube
     buzz when collected). The field reads Fantasy Guru-type content too: does
     a matchup rating, last week's score, vacated injury volume, a
     Questionable tag, or the team's Vegas total move real ownership beyond
     value and buzz? Each candidate is a further softmax term, fitted on the
     other slates, scored by mean absolute ownership error on the held-out
     one, against the shipped model's error on the same players.

Slates: every Main-slate GPP with a logged (or rebuilt) pool and real
ownership in data/contests.db. Cash is never pooled with GPP ownership.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import nfl_intel as ni  # noqa: E402
from edge import dfs_nfl_theory as theory  # noqa: E402
from edge.names import norm  # noqa: E402

DB = ROOT / "data" / "contests.db"
REBUILT = ROOT / "data" / "cache" / "mans_rebuilt_pool_{gid}.json"   # scripts/mans_claims.py
OWN_FEATURES = ("implied", "dk_last", "matchup", "vacated", "questionable", "snap_avg")
SHIPPED = {"value + buzz": theory.INTEL_BUZZ, "value only": theory.INTEL_NO_BUZZ}
PROJ_FEATURES = ("matchup", "team_plays", "team_proe", "vacated", "questionable",
                 "dk_avg_gap", "snap_avg", "rz_share")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def implied_totals(season: int, week: int) -> dict:
    """{team: implied points} from nflverse's closing total and spread."""
    out = {}
    with (ROOT / "data" / "nflverse_games.csv").open(newline="") as fh:
        for r in csv.DictReader(fh):
            if int(r["season"]) != season or int(r["week"]) != week or not r["total_line"]:
                continue
            total, home_margin = float(r["total_line"]), float(r["spread_line"] or 0)
            out[ni.dk_team(r["home_team"])] = total / 2 + home_margin / 2
            out[ni.dk_team(r["away_team"])] = total / 2 - home_margin / 2
    return out


def load_slates() -> list[dict]:
    con = sqlite3.connect(DB)
    contests = con.execute(
        "SELECT contest_id, draft_group, slate_date, field FROM contest WHERE sport='NFL' "
        "AND type='gpp' AND name NOT LIKE '%Primetime%' ORDER BY slate_date").fetchall()
    buzz = defaultdict(dict)
    with (ROOT / "data" / "buzz_nfl.csv").open(newline="") as fh:
        for r in csv.DictReader(fh):
            buzz[r["date"]][norm(r["player"])] = float(r["mentions"] or 0)
    slates = []
    for cid, gid, date, field in contests:
        actual = {k: (own, fpts) for k, own, fpts in con.execute(
            "SELECT player_norm, pct_drafted, fpts FROM player_result WHERE contest_id=?", (cid,))}
        pool = [dict(norm=k, name=n, team=t, opp_team=o, dk_pos=p, salary=s, proj=pr, own_log=ow)
                for k, n, t, o, p, s, pr, ow in con.execute(
                    "SELECT player_norm, name, team, opp, pos, salary, proj, own_pred FROM projection "
                    "WHERE sport='NFL' AND variant='live' AND draft_group=?", (gid,))]
        source = "log"
        if not pool and Path(str(REBUILT).format(gid=gid)).exists():
            board = {k: (n, t, o, p, s) for k, n, t, o, p, s in con.execute(
                "SELECT player_norm, name, team, opp, position, salary FROM slate_player "
                "WHERE draft_group=?", (gid,))}
            for r in json.loads(Path(str(REBUILT).format(gid=gid)).read_text()):
                k = norm(r["name"])
                if k in board:
                    n, t, o, p, s = board[k]
                    pool.append(dict(norm=k, name=n, team=t, opp_team=o, dk_pos=p, salary=s,
                                     proj=r["proj"], own_log=r.get("own")))
            source = "rebuilt"
        if not pool:
            continue
        season, week = ni.season_week(date)
        info = ni.attach(pool, date, research=True)   # past week -> nflverse's Friday report
        imp = implied_totals(season, week)
        for p in pool:
            own, fpts = actual.get(p["norm"], (0.0, None))
            p["actual_own"] = own or 0.0
            p["implied"] = imp.get(p["team"])
            p["questionable"] = 1.0 if p["inj_status"].lower() == "questionable" else 0.0
            if date in buzz:
                p["buzz"] = buzz[date].get(p["norm"], 0.0)
        # nflverse actual DK points for players who played that week
        played = {r["norm"]: r["dk"] for r in ni.player_games(season) if r["week"] == week}
        for p in pool:
            p["actual_pts"] = played.get(p["norm"])
        slates.append({"date": date, "gid": gid, "cid": cid, "field": field, "week": week,
                       "pool": pool, "source": source, "has_buzz": date in buzz,
                       "injury_source": info.get("injury_source")})
    return slates


# ---------------------------------------------------------------------------
# 1. Projection residuals
# ---------------------------------------------------------------------------
def _feature(p: dict, f: str) -> float | None:
    if f == "dk_avg_gap":
        return None if p.get("dk_avg") is None else p["dk_avg"] - p["proj"]
    v = p.get(f)
    return None if v is None else float(v)


def _ols(X, y, ridge=1.0):
    """Ridge least squares (intercept unpenalised) -- numpy is available."""
    import numpy as np
    X = np.asarray(X, float)
    y = np.asarray(y, float)
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1
    Z = (X - mu) / sd
    A = np.c_[np.ones(len(Z)), Z]
    pen = ridge * np.eye(A.shape[1])
    pen[0, 0] = 0
    beta = np.linalg.solve(A.T @ A + pen, A.T @ y)
    return lambda Xn: np.c_[np.ones(len(Xn)), (np.asarray(Xn, float) - mu) / sd] @ beta, beta


def projection_test(slates):
    import numpy as np
    rows = defaultdict(list)
    for s in slates:
        for p in s["pool"]:
            pos = (p["dk_pos"] or "").split("/")[0]
            if pos not in ni.POSITIONS or p["actual_pts"] is None or not p["proj"]:
                continue
            feats = [_feature(p, f) for f in PROJ_FEATURES]
            feats = [0.0 if v is None else v for v in feats]   # missing -> neutral after centring
            rows[s["date"]].append((pos, feats, p["actual_pts"] - p["proj"], p["proj"], p["actual_pts"]))
    n_total = sum(len(v) for v in rows.values())
    print(f"\n1. PROJECTION -- do the tools explain what the props missed?  "
          f"{len(rows)} slates, {n_total} player-weeks (QB/RB/WR/TE who played)")
    print(f"   mean residual (actual - proj) by slate: " +
          "  ".join(f"{d[5:]} {np.mean([r[2] for r in v]):+.2f}" for d, v in sorted(rows.items())))

    print(f"\n   per feature: correlation with the residual on each slate (sign must hold)")
    print(f"   {'feature':12} " + " ".join(f"{d[5:]:>7}" for d in sorted(rows)) + "   pooled")
    for j, f in enumerate(PROJ_FEATURES):
        cs = []
        for d in sorted(rows):
            x = np.array([r[1][j] for r in rows[d]])
            y = np.array([r[2] for r in rows[d]])
            cs.append(np.corrcoef(x, y)[0, 1] if x.std() else float("nan"))
        allx = np.array([r[1][j] for v in rows.values() for r in v])
        ally = np.array([r[2] for v in rows.values() for r in v])
        pooled = np.corrcoef(allx, ally)[0, 1] if allx.std() else float("nan")
        se = 1 / math.sqrt(len(allx))
        print(f"   {f:12} " + " ".join(f"{c:+7.3f}" for c in cs) + f"   {pooled:+.3f} (±{2*se:.3f})")

    print(f"\n   held out: MAE of the props projection vs props + every tool (ridge, fitted on the other slates)")
    print(f"   (+const = props shifted by the other slates' median miss, so a feature is not")
    print(f"   blamed or credited for the props' overall bias; the residual is right-skewed)")
    print(f"   {'slate':8} {'n':>4} {'props':>7} {'+const':>7} {'+tools':>7} {'tools vs const':>14}")
    tot = [0.0, 0.0, 0, 0.0]
    for held in sorted(rows):
        train = [r for d, v in rows.items() if d != held for r in v]
        fn, _ = _ols([r[1] for r in train], [r[2] for r in train], ridge=float(len(train)) * 0.05)
        test = rows[held]
        adj = fn([r[1] for r in test])
        # the ridge fits a mean; re-centre it on the median so it is judged on MAE's terms
        med = float(np.median([r[2] for r in train]))
        adj = adj - float(np.median(fn([r[1] for r in train]))) + med
        base = np.mean([abs(r[2]) for r in test])
        const = np.mean([abs(r[2] - med) for r in test])
        new = np.mean([abs(r[2] - a) for r, a in zip(test, adj)])
        tot[0] += base * len(test)
        tot[1] += new * len(test)
        tot[2] += len(test)
        tot[3] += const * len(test)
        print(f"   {held[5:]:8} {len(test):4} {base:7.3f} {const:7.3f} {new:7.3f} {new - const:+14.3f}")
    n = tot[2]
    print(f"   {'all':8} {n:4} {tot[0]/n:7.3f} {tot[3]/n:7.3f} {tot[1]/n:7.3f} {(tot[1]-tot[3])/n:+14.3f}")


# ---------------------------------------------------------------------------
# 2. Ownership
# ---------------------------------------------------------------------------
def predict(pool, gamma, beta, thetas: dict, cap):
    """The SHIPPED field model (theory.add_ownership) at these constants; the
    nfl_intel terms are its `intel` argument, so what is scored is what runs."""
    theory.add_ownership(pool, gamma=gamma, cap=cap, buzz_beta=beta, intel=thetas)
    return {id(p): p["own"] for p in pool}


def own_mae(slate, gamma, beta, thetas):
    pred = predict(slate["pool"], gamma, beta, thetas, slate["cap"])
    return sum(abs(p["actual_own"] - pred[id(p)]) for p in slate["pool"]) / len(slate["pool"])


def fit_thetas(train, gamma, beta, feats, grid=(-0.6, -0.4, -0.2, -0.1, 0, 0.1, 0.2, 0.4, 0.6)):
    """Coordinate descent over a grid, gamma and beta re-searched alongside."""
    th = {f: 0.0 for f in feats}
    best = sum(own_mae(s, gamma, beta, th) for s in train)
    for _ in range(3):
        improved = False
        for f in feats:
            for v in grid:
                trial = dict(th, **{f: v})
                e = sum(own_mae(s, gamma, beta, trial) for s in train)
                if e < best - 1e-9:
                    best, th, improved = e, trial, True
        for g in (gamma - 0.2, gamma - 0.1, gamma + 0.1, gamma + 0.2):
            if g <= 0:
                continue
            e = sum(own_mae(s, g, beta, th) for s in train)
            if e < best - 1e-9:
                best, gamma, improved = e, g, True
        if not improved:
            break
    return gamma, th


def ownership_test(slates):
    print(f"\n2. OWNERSHIP -- does a tool move the field beyond value + buzz?  (GPP main slates)")
    for s in slates:
        s["cap"] = theory.slate_cap(len({p["team"] for p in s["pool"]}) // 2)
    # how far the logged (pre-intel) ownership is from a rebuild with intel off
    for s in slates:
        if s["source"] == "log" and s["has_buzz"]:
            pred = predict(s["pool"], theory.BUZZ_GAMMA, theory.BUZZ_BETA, {}, s["cap"])
            drift = sum(abs((p["own_log"] or 0) - pred[id(p)]) for p in s["pool"]) / len(s["pool"])
            print(f"   check {s['date']}: logged own vs rebuilt (intel off), mean |diff| {drift:.2f}")

    for label, subset, gamma, beta in (
            ("value + buzz (shipped when buzz exists)", [s for s in slates if s["has_buzz"]],
             theory.BUZZ_GAMMA, theory.BUZZ_BETA),
            ("value only (every slate)", slates, theory.OWNERSHIP_GAMMA, 0.0)):
        if len(subset) < 3:
            continue
        print(f"\n   base = {label}; {len(subset)} slates, "
              f"{sum(len(s['pool']) for s in subset)} player-slates")
        print(f"   single features, each fitted on the other slates (theta, then held-out MAE change):")
        print(f"   {'feature':13} " + " ".join(f"{s['date'][5:]:>13}" for s in subset) + "     mean")
        for f in OWN_FEATURES:
            cells, gains = [], []
            for held in subset:
                train = [s for s in subset if s is not held]
                g, th = fit_thetas(train, gamma, beta, [f])
                base = own_mae(held, gamma, beta, {})
                new = own_mae(held, g, beta, th)
                gains.append(new - base)
                cells.append(f"{th[f]:+.1f} {new - base:+.3f}")
            print(f"   {f:13} " + " ".join(f"{c:>13}" for c in cells) + f"  {sum(gains)/len(gains):+.3f}")

        print(f"\n   all features together, held out:")
        print(f"   {'slate':8} {'shipped':>8} {'refit':>8} {'change':>7}  thetas")
        tb, tn = [], []
        for held in subset:
            train = [s for s in subset if s is not held]
            g, th = fit_thetas(train, gamma, beta, list(OWN_FEATURES))
            base = own_mae(held, gamma, beta, {})
            new = own_mae(held, g, beta, th)
            tb.append(base)
            tn.append(new)
            print(f"   {held['date'][5:]:8} {base:8.3f} {new:8.3f} {new - base:+7.3f}  "
                  f"g={g:.1f} " + " ".join(f"{k}={v:+.1f}" for k, v in th.items() if v))
        print(f"   {'mean':8} {sum(tb)/len(tb):8.3f} {sum(tn)/len(tn):8.3f} "
              f"{(sum(tn)-sum(tb))/len(tb):+7.3f}")
        th = SHIPPED[label.split(" (")[0]]
        ins = sum(own_mae(s, gamma, beta, th) for s in subset) / len(subset)
        print(f"   shipped INTEL constants (fitted on all of these -- in-sample): {ins:.3f}")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--only", choices=("proj", "own"))
    args = ap.parse_args()
    slates = load_slates()
    print("slates:")
    for s in slates:
        n_inj = sum(1 for p in s["pool"] if p["inj_status"])
        n_vac = sum(1 for p in s["pool"] if p["vacated"])
        print(f"  {s['date']} wk{s['week']} gid {s['gid']} ({s['source']}, "
              f"{'buzz' if s['has_buzz'] else 'no buzz'}): {len(s['pool'])} players, "
              f"{s['field']} entries, injury tag on {n_inj}, vacated volume on {n_vac}")
    if args.only != "own":
        projection_test(slates)
    if args.only != "proj":
        ownership_test(slates)


if __name__ == "__main__":
    main()
