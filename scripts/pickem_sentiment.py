#!/usr/bin/env python3
"""Public-sentiment model: does the line lean toward what the public saw last
week, and can an objective call attack it? (Adam, 2026-10-23)

THE IDEA. Books want money on both sides, so (the theory goes) the posted
number leans toward how the public FEELS about a team -- shaped by what it
saw: a big win last week, a cover streak, a winning record, and transitive
logic (A beat B, B beat C, so A beats C). If the line carries that lean, the
side the public dislikes covers more than half the time.

THE PUBLIC VIEW, per team, from games strictly before the week:
  last_margin   last game's margin (the highlight the public remembers)
  last_ats      last game's margin against its closing spread
  ats3          covers in the last 3 games, minus non-covers
  win_pct       season win% (0.5 before any game)
  public_gap    a WIN/LOSS-only rating (transitive by construction -- a ridge
                solve over "A beat B" with no margins, half-life 2 weeks: the
                public's memory) minus the objective margin rating of
                scripts/pickem_handicap.py, both in points. Positive = the
                public rates the team above what its scores say.
Each is HOME minus AWAY.

THE TEST. Target = did home cover the POOL line (margin + pool > 0). Each
feature's sign is chosen on train 2014-19; a logistic model of all five is
fitted on train. Scored on test 2020-22 and live 2026 (real CBS line), with
the shipped market-move model beside it, and on the market model's COIN
FLIPS -- the only games where a new signal can change a pick. 2023-24 never
graded (spent holdout). Live, the public view is also checked against CBS's
community pick % (comm_pct_home): if it cannot predict what the public
picks, it is not a public-sentiment model.

Run: python3 scripts/pickem_sentiment.py
"""
from __future__ import annotations

import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import pickem_handicap as ph  # noqa: E402
from edge.pickem import ats_result, make_pick  # noqa: E402
from edge.pickem_data import Split  # noqa: E402

FEATURES = ["last_margin", "last_ats", "ats3", "win_pct", "public_gap"]


def winloss_ratings(rows: list[dict], half_life: float = 2.0,
                    ridge: float = 2.0) -> dict:
    """{(season, week, home): (home WL rating - away WL rating)} in 'points'
    (a win counts as +7, a loss -7, a tie 0 -- outcome only, no margin)."""
    teams = sorted({r["home"] for r in rows} | {r["away"] for r in rows})
    ix = {t: i for i, t in enumerate(teams)}
    played = [r for r in rows if r["margin"] is not None]
    out = {}
    for t in sorted({r["t"] for r in rows if r["season"] >= 2014}):
        past = [r for r in played if r["t"] < t and r["season"] == t // 100]
        X = np.zeros((len(past), len(teams)))
        y = np.empty(len(past))
        w = np.empty(len(past))
        for k, r in enumerate(past):
            X[k, ix[r["home"]]] = 1
            X[k, ix[r["away"]]] = -1
            y[k] = 7 * np.sign(r["margin"]) - (0 if r["neutral"] else 2.0)
            w[k] = 0.5 ** ((t - r["t"]) / half_life)
        rating = (np.linalg.solve(X.T @ (X * w[:, None]) + ridge * np.eye(len(teams)),
                                  X.T @ (w * y)) if len(past) else np.zeros(len(teams)))
        for r in rows:
            if r["t"] == t:
                out[(r["season"], r["week"], r["home"])] = \
                    rating[ix[r["home"]]] - rating[ix[r["away"]]]
    return out


def team_history(rows):
    """{team: [(t, season, margin_for, ats_for), ...]} in time order."""
    h = defaultdict(list)
    for r in sorted(rows, key=lambda r: r["t"]):
        if r["margin"] is None:
            continue
        ats = (r["margin"] + r["close"]) if r["close"] is not None else None
        h[r["home"]].append((r["t"], r["season"], r["margin"], ats))
        h[r["away"]].append((r["t"], r["season"], -r["margin"],
                             -ats if ats is not None else None))
    return h


def team_features(hist, team, t):
    past = [x for x in hist.get(team, []) if x[0] < t]
    season = [x for x in past if x[1] == t // 100]
    last = season[-1] if season else None
    ats_recent = [x[3] for x in season[-3:] if x[3] is not None]
    return dict(
        last_margin=last[2] if last else 0.0,
        last_ats=(last[3] or 0.0) if last else 0.0,
        ats3=sum((a > 0) - (a < 0) for a in ats_recent),
        win_pct=(sum(x[2] > 0 for x in season) + 0.5 * sum(x[2] == 0 for x in season))
        / len(season) if season else 0.5)


def attach(games, rows, hist, wl, obj):
    by = {(r["season"], r["week"], r["home"]): r for r in rows}
    alias = {}
    try:
        import pickem_grade as pg
        alias = pg.CBS_TO_NFLVERSE
    except Exception:                                   # noqa: BLE001
        pass
    out = []
    for g in games:
        key = (g["season"], g["week"], alias.get(g["home"], g["home"]))
        r = by.get(key) or by.get((g["season"], g["week"], g["home"]))
        if r is None or key not in wl:
            continue
        hf = team_features(hist, r["home"], r["t"])
        af = team_features(hist, r["away"], r["t"])
        f = {k: hf[k] - af[k] for k in FEATURES[:4]}
        # WL rating says home by -wl less than... both as home-minus-away
        # strength in points; objective strength = -(our home spread) - hfa.
        f["public_gap"] = wl[key] - (-obj[key] - (0 if r["neutral"] else ph.PARAMS["hfa"]))
        out.append(dict(g, **f))
    return out


def _cover(g):
    return g["margin"] + g["pool"]          # >0 home covered the pool line


def fit_logit(X, y, l2=1.0, iters=200):
    w = np.zeros(X.shape[1])
    for _ in range(iters):
        p = 1 / (1 + np.exp(-X @ w))
        H = X.T @ (X * (p * (1 - p))[:, None]) + l2 * np.eye(len(w))
        w += np.linalg.solve(H, X.T @ (y - p) - l2 * w)
    return w


def wl(games, side_fn):
    w = l = 0
    weeks = defaultdict(lambda: [0, 0])
    for g in games:
        r = ats_result(g["margin"], g["pool"], side_fn(g))
        if r == "P":
            continue
        weeks[(g["season"], g["week"])][r == "L"] += 1
    w = sum(v[0] for v in weeks.values())
    l = sum(v[1] for v in weeks.values())
    d = [v[0] - (v[0] + v[1]) / 2 for v in weeks.values()]
    se = statistics.stdev(d) / math.sqrt(len(d)) if len(d) > 1 else float("nan")
    return f"{w}-{l} ({w / (w + l):.1%}) {statistics.mean(d):+.2f}/wk se {se:.2f}" if w + l else "-"


def main() -> None:
    rows = ph.games_table()
    obj = ph.predict_all(rows, **ph.PARAMS)
    wlr = winloss_ratings(rows)
    hist = team_history(rows)
    sets = {
        "train 2014-19": attach(ph.history_games(Split.TRAIN, rows, obj), rows, hist, wlr, obj),
        "test 2020-22": attach(ph.history_games(Split.VALIDATE, rows, obj), rows, hist, wlr, obj),
        "live 2026": attach(ph.live_games(rows, obj), rows, hist, wlr, obj),
    }
    train = sets["train 2014-19"]

    # Sign of each feature on train: does home cover more when it is HIGHER?
    print("Per-feature: fade-the-public side chosen on train, then scored")
    print("| feature | train corr w/ cover | " + " | ".join(sets) + " |")
    print("|---|---|" + "---|" * len(sets))
    signs = {}
    for f in FEATURES:
        x = np.array([g[f] for g in train], float)
        y = np.array([_cover(g) for g in train], float)
        c = float(np.corrcoef(x, y)[0, 1])
        signs[f] = 1 if c > 0 else -1

        def side(g, f=f):
            v = signs[f] * g[f]
            return "home" if v > 0 else "away" if v < 0 else "home"
        cells = [wl([g for g in gs if g[f] != 0], side) for gs in sets.values()]
        print(f"| {f} | {c:+.3f} | " + " | ".join(cells) + " |")

    # All five together.
    def mat(gs):
        X = np.array([[g[f] for f in FEATURES] for g in gs], float)
        return X
    Xtr = mat(train)
    mu, sd = Xtr.mean(0), Xtr.std(0)
    ytr = np.array([_cover(g) > 0 for g in train if _cover(g) != 0], float)
    Xn = np.c_[np.ones(len(train)), (Xtr - mu) / sd][[_cover(g) != 0 for g in train]]
    coef = fit_logit(Xn, ytr)
    print("\nlogit coef (std units):", dict(zip(["const"] + FEATURES, np.round(coef, 3))))

    def side_model(g):
        z = coef[0] + ((np.array([g[f] for f in FEATURES]) - mu) / sd) @ coef[1:]
        return "home" if z > 0 else "away"

    def side_market(g):
        return make_pick(g["away"], g["home"], g["pool"], g["live"],
                         g["total_open"], g["total_close"]).side

    def coin(g):
        return make_pick(g["away"], g["home"], g["pool"], g["live"],
                         g["total_open"], g["total_close"]).tier == "COIN FLIP"

    def side_combo(g):
        return side_model(g) if coin(g) else side_market(g)

    print("\n| split | market (shipped) | sentiment model alone | sentiment on coin flips: market W-L / sentiment W-L | market + sentiment on coin flips |")
    print("|---|---|---|---|---|")
    for name, gs in sets.items():
        cf = [g for g in gs if coin(g)]
        print(f"| {name} | {wl(gs, side_market)} | {wl(gs, side_model)} | "
              f"{wl(cf, side_market)} / {wl(cf, side_model)} | {wl(gs, side_combo)} |")

    # Is it a model of the PUBLIC at all? Compare with CBS community picks.
    import pickem_grade as pg  # noqa: F401
    from edge.pickem_log import load as load_log
    comm = {}
    for r in load_log():
        if r.get("comm_pct_home") and r["snapshot"] in ("post", "midweek", "lock-sun"):
            comm[(int(r["season"]), int(r["week"]), r["home_team"])] = float(r["comm_pct_home"])
    live = [g for g in sets["live 2026"] if (g["season"], g["week"], g["home"]) in comm]
    if live:
        y = np.array([comm[(g["season"], g["week"], g["home"])] for g in live])
        print(f"\nLive: does the public view predict CBS's community pick %? (n={len(live)} games)")
        for f in FEATURES + ["pool"]:
            x = np.array([g[f] for g in live], float)
            print(f"  corr(comm_pct_home, {f}) = {np.corrcoef(x, y)[0, 1]:+.3f}")
        # Beyond the line: partial correlation after the pool line.
        P = np.c_[np.ones(len(live)), [g["pool"] for g in live]]
        res_y = y - P @ np.linalg.lstsq(P, y, rcond=None)[0]
        for f in FEATURES:
            x = np.array([g[f] for g in live], float)
            res_x = x - P @ np.linalg.lstsq(P, x, rcond=None)[0]
            print(f"  ... after the line: partial corr {f} = {np.corrcoef(res_x, res_y)[0, 1]:+.3f}")
        fav = sum((y[i] > 50) == (live[i]["pool"] < 0) for i in range(len(live)))
        print(f"  public took the CBS favourite in {fav}/{len(live)} games")
        pub = [g for g in live]
        fade = lambda g: "away" if comm[(g["season"], g["week"], g["home"])] > 50 else "home"  # noqa: E731
        print(f"  fade the CBS public majority: {wl(pub, fade)}")


if __name__ == "__main__":
    main()
