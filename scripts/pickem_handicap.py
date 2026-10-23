#!/usr/bin/env python3
"""Our own NFL handicap -- a power rating built only from final scores -- and
how it fares against the pool line, next to the shipped market-move model.

WHY (Adam, 2026-10-23): the shipped model (edge.pickem.make_pick) never forms
an opinion of the game; it only reads which way the market moved off CBS's
frozen number. This asks the other question: if we set our own line, does
beating CBS with it win picks?

THE RATING
  margin(home - away) = hfa*[not neutral] + r_home - r_away + noise, solved by
  weighted ridge least squares over every earlier game (regular season and
  playoffs, nflverse). Weight = 0.5 ** (weeks_ago / half_life), with games
  from previous seasons further multiplied by `carry` (rosters turn over).
  Walk-forward: a week's games are predicted from games strictly before it.
  Our home spread = -(predicted margin), the same sign as the pool line.

FITTING: half_life, carry, ridge and hfa are chosen on 2014-2019 by MARGIN
  error (MSE), never by pick record -- fitting to ATS wins on ~1,500 games
  would just fit noise. Scored on train 2014-19, test 2020-22, live 2026
  (real CBS lines). 2023-24 is the spent pick'em holdout: its scores feed the
  2025+ ratings as inputs, but no pick is ever graded on it.

THE PICK vs the pool line: take the side our line says is undervalued. Edge
  = pool_line - our_line (positive = home is getting more than it should).

Run:  python3 scripts/pickem_handicap.py [--fit] [--week N]
"""
from __future__ import annotations

import argparse
import csv
import itertools
import math
import statistics
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from edge.pickem import ats_result, make_pick  # noqa: E402
from edge.pickem_data import Split, load  # noqa: E402

GAMES_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
CACHE = ROOT / "data" / "cache" / "nflverse_games.csv"

#: Chosen by --fit on 2014-2019 margin MSE, 2026-10-23 (see the log printed
#: by --fit; grid 4x3x3x4, best train margin RMSE 13.32).
PARAMS = dict(half_life=10.0, carry=0.5, ridge=4.0, hfa=2.0)


def games_table(refresh: bool = False) -> list[dict]:
    if refresh or not CACHE.exists():
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(GAMES_URL, timeout=120) as r:
            CACHE.write_bytes(r.read())
    rows = []
    for r in csv.DictReader(CACHE.open()):
        s, w = int(r["season"]), int(r["week"])
        rows.append(dict(
            season=s, week=w, t=s * 100 + w, type=r["game_type"],
            away=r["away_team"], home=r["home_team"],
            neutral=r["location"] == "Neutral",
            margin=(int(r["home_score"]) - int(r["away_score"])
                    if r["home_score"] not in ("", "NA") else None),
            close=(-float(r["spread_line"])
                   if r["spread_line"] not in ("", "NA") else None)))
    return rows


def _weeks_between(a: int, b: int) -> float:
    """Weeks from game-time a to b (season*100+week); off-season = 0 weeks,
    the season change is handled by `carry`."""
    sa, wa, sb, wb = a // 100, a % 100, b // 100, b % 100
    return (wb - wa) if sa == sb else wb + max(0, 18 - wa)


def predict_all(rows: list[dict], half_life: float, carry: float,
                ridge: float, hfa: float, from_season: int = 2014,
                to_season: int = 9999) -> dict:
    """{(season, week, home): our home spread}, walk-forward by week."""
    teams = sorted({r["home"] for r in rows} | {r["away"] for r in rows})
    ix = {t: i for i, t in enumerate(teams)}
    played = [r for r in rows if r["margin"] is not None]
    out = {}
    for t in sorted({r["t"] for r in rows
                    if from_season <= r["season"] <= to_season}):
        past = [r for r in played if r["t"] < t and r["season"] >= t // 100 - 2]
        if not past:
            continue
        X = np.zeros((len(past), len(teams)))
        y = np.empty(len(past))
        wt = np.empty(len(past))
        for k, r in enumerate(past):
            X[k, ix[r["home"]]] = 1
            X[k, ix[r["away"]]] = -1
            y[k] = r["margin"] - (0 if r["neutral"] else hfa)
            wt[k] = 0.5 ** (_weeks_between(r["t"], t) / half_life) \
                * carry ** (t // 100 - r["season"])
        A = X.T @ (X * wt[:, None]) + ridge * np.eye(len(teams))
        rating = np.linalg.solve(A, X.T @ (wt * y))
        for r in rows:
            if r["t"] == t:
                m = (0 if r["neutral"] else hfa) + rating[ix[r["home"]]] \
                    - rating[ix[r["away"]]]
                out[(r["season"], r["week"], r["home"])] = -m
    return out


def fit(rows: list[dict]) -> None:
    train = [r for r in rows if 2014 <= r["season"] <= 2019
             and r["margin"] is not None]
    best = None
    for hl, c, rg, h in itertools.product((5, 10, 20, 40), (0.25, 0.5, 0.75),
                                          (1, 4, 16), (1.0, 1.5, 2.0, 2.5)):
        pred = predict_all(rows, hl, c, rg, h, 2014, 2019)
        pred = {k: v for k, v in pred.items() if k[0] <= 2019}
        err = [(pred[(r["season"], r["week"], r["home"])] + r["margin"]) ** 2
               for r in train if (r["season"], r["week"], r["home"]) in pred]
        mse = statistics.mean(err)
        print(f"hl={hl} carry={c} ridge={rg} hfa={h}  rmse={math.sqrt(mse):.3f}")
        if best is None or mse < best[0]:
            best = (mse, hl, c, rg, h)
    print("best", best)


# ---- joining our line onto the pick'em games --------------------------------

def _hist_key(rows):
    """odds-history spells some teams differently; join on scores instead."""
    by = {}
    for r in rows:
        if r["margin"] is not None and r["type"] == "REG":
            by.setdefault((r["season"], r["week"], r["margin"]), []).append(r)
    return by


def history_games(split: Split, rows, pred) -> list[dict]:
    by = _hist_key(rows)
    out = []
    for g in load(split):
        cands = by.get((g.season, g.week, g.margin), [])
        if len(cands) != 1:
            continue                     # ambiguous same-score twin: skip
        r = cands[0]
        ours = pred.get((r["season"], r["week"], r["home"]))
        if ours is None:
            continue
        out.append(dict(season=g.season, week=g.week, away=g.away, home=g.home,
                        pool=g.pool_line, live=g.live_line, margin=g.margin,
                        total_open=g.total_open, total_close=g.total_close,
                        ours=ours))
    return out


def live_games(rows, pred, graded_only=True) -> list[dict]:
    import pickem_grade as pg
    out = []
    by = {(r["season"], r["week"], r["home"]): r for r in rows}
    for g in pg.readings():
        if g["cbs"] is None or g["lock"] is None:
            continue
        key = (g["season"], g["week"],
               pg.CBS_TO_NFLVERSE.get(g["home"], g["home"]))
        r = by.get(key)
        if r is None or key not in pred:
            continue
        if graded_only and r["margin"] is None:
            continue
        out.append(dict(season=g["season"], week=g["week"], away=g["away"],
                        home=g["home"], pool=g["cbs"], live=g["lock"],
                        margin=r["margin"], total_open=g["total_open"],
                        total_close=g["total_close"], ours=pred[key]))
    return out


# ---- the pickers -------------------------------------------------------------

def side_ours(g):
    return "home" if g["pool"] - g["ours"] > 0 else "away"


def side_market(g):
    return make_pick(g["away"], g["home"], g["pool"], g["live"],
                     g["total_open"], g["total_close"]).side


def side_blend(g):
    """Average of our line and the live market line, read against the pool.
    The market moved line is the best single estimate we have; ours is an
    independent one built only from scores."""
    return "home" if g["pool"] - (g["ours"] + g["live"]) / 2 > 0 else "away"


PICKERS = {"ours": side_ours, "market (shipped)": side_market,
           "blend 50/50": side_blend}


def score(games, picker) -> dict:
    weeks = defaultdict(lambda: [0, 0])
    for g in games:
        r = ats_result(g["margin"], g["pool"], picker(g))
        if r != "P":
            weeks[(g["season"], g["week"])][r == "L"] += 1
    w = sum(v[0] for v in weeks.values())
    l = sum(v[1] for v in weeks.values())
    d = [v[0] - (v[0] + v[1]) / 2 for v in weeks.values()]
    return dict(w=w, l=l, pct=w / (w + l), wk=len(d),
                vs=statistics.mean(d),
                se=statistics.stdev(d) / math.sqrt(len(d)) if len(d) > 1 else float("nan"))


def report(rows, pred) -> None:
    splits = [("train 2014-19", history_games(Split.TRAIN, rows, pred)),
              ("test 2020-22", history_games(Split.VALIDATE, rows, pred)),
              ("live 2026 (CBS)", live_games(rows, pred))]
    print("| split | picker | W-L | win% | pts/wk vs random (se) |")
    print("|---|---|---|---|---|")
    for name, gs in splits:
        for pn, p in PICKERS.items():
            s = score(gs, p)
            print(f"| {name} ({s['wk']} wk) | {pn} | {s['w']}-{s['l']} | "
                  f"{s['pct']:.1%} | {s['vs']:+.2f} ({s['se']:.2f}) |")
    print()
    print("| split | our line vs pool-line MAE to margin | agree w/ market: W-L | disagree: ours W-L |")
    print("|---|---|---|---|")
    for name, gs in splits:
        mae_o = statistics.mean(abs(g["ours"] + g["margin"]) for g in gs)
        mae_p = statistics.mean(abs(g["pool"] + g["margin"]) for g in gs)
        mae_c = statistics.mean(abs(g["live"] + g["margin"]) for g in gs)
        agree = [g for g in gs if side_ours(g) == side_market(g)]
        dis = [g for g in gs if side_ours(g) != side_market(g)]
        a, d = score(agree, side_ours), score(dis, side_ours)
        print(f"| {name} | ours {mae_o:.2f} / pool {mae_p:.2f} / live {mae_c:.2f} | "
              f"{a['w']}-{a['l']} ({a['pct']:.1%}) | {d['w']}-{d['l']} ({d['pct']:.1%}) |")


def week_card(rows, pred, season, week) -> None:
    import pickem_grade as pg
    gs = [g for g in live_games(rows, pred, graded_only=False)
          if g["season"] == season and g["week"] == week]
    print(f"\nWeek {week} {season}: home spread (negative = home favoured)")
    print("| game | CBS | market | ours | ours picks | market picks | blend picks | result |")
    print("|---|---|---|---|---|---|---|---|")
    for g in gs:
        def nm(s):
            return g["home"] if s == "home" else g["away"]
        res = "" if g["margin"] is None else ats_result(
            g["margin"], g["pool"], side_blend(g))
        print(f"| {g['away']} @ {g['home']} | {g['pool']:+.1f} | {g['live']:+.2f} | "
              f"{g['ours']:+.1f} | {nm(side_ours(g))} | {nm(side_market(g))} | "
              f"**{nm(side_blend(g))}** | {res} |")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fit", action="store_true")
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--week", type=int)
    a = ap.parse_args()
    rows = games_table(a.refresh)
    if a.fit:
        fit(rows)
        return
    pred = predict_all(rows, **PARAMS)
    report(rows, pred)
    if a.week:
        week_card(rows, pred, 2026, a.week)


if __name__ == "__main__":
    main()
