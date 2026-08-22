#!/usr/bin/env python3
"""Feature experiments for the pick'em model -- DEV SPLIT ONLY.

This script is the reproducible record behind PICKEM_MODEL.md's
"what we tried" tables. Re-run it and the numbers in that document should
come back identical.

HARD RULE, enforced in code: this file refuses to look at HOLDOUT seasons
(2023-2024). Every experiment, threshold, and go/no-go decision happens on
2014-2022 -- train 2014-2019, validate 2020-2022. The holdout is spent by
scripts/pickem_backtest.py only, and only on a model that already earned it
here. Nothing in this file can quietly tune against the honest number.

Run: python3 scripts/pickem_feature_lab.py
"""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.pickem import ats_result, make_pick  # noqa: E402
from edge.pickem_features import (  # noqa: E402
    build_coach_timeline, build_ratings_timeline, canon, coach_ats_rate,
    load_games, load_team_games, net_rating,
)

HOLDOUT = {2023, 2024}
TRAIN = range(2014, 2020)
VAL = range(2020, 2023)
ODDS = ROOT / "data" / "pickem_odds_history.csv"


def load_rows() -> list[dict]:
    out = []
    with ODDS.open() as f:
        for r in csv.DictReader(f):
            s = int(r["season"])
            if s in HOLDOUT:
                continue          # guard rail, not a filter to relax
            out.append({
                "season": s, "week": int(r["week"]),
                "home": canon(r["home_team"]), "away": canon(r["away_team"]),
                "home_raw": r["home_team"], "away_raw": r["away_team"],
                "pool_line": float(r["home_line_open"]),
                "live_line": float(r["home_line_close"]),
                "margin": int(r["home_score"]) - int(r["away_score"]),
            })
    return out


def _z(w, l):
    n = w + l
    return 0.0 if n == 0 else ((w / n) - 0.5) / math.sqrt(0.25 / n)


def ev(rows, seasons, side_fn, label, only=None):
    w = l = 0
    for r in rows:
        if r["season"] not in seasons or (only and not only(r)):
            continue
        side = side_fn(r)
        if side is None:
            continue
        res = ats_result(r["margin"], r["pool_line"], side)
        w += res == "W"
        l += res == "L"
    pct = w / (w + l) if (w + l) else 0.0
    flag = " *" if abs(_z(w, l)) >= 1.96 else ""
    print(f"  {label:<48} {w:4d}-{l:<4d} {pct:6.1%}  z={_z(w,l):+5.2f}{flag}  (n={w+l})")
    return w, l


def corr(a, b):
    n = len(a)
    ma, mb = sum(a) / n, sum(b) / n
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db) if da and db else 0.0


def main() -> None:
    rows = load_rows()
    assert not ({r["season"] for r in rows} & HOLDOUT), "holdout leaked into dev"
    print(f"DEV: {len(rows)} games, 2014-2022 | train 2014-2019, validate 2020-2022")
    print(f"HOLDOUT {sorted(HOLDOUT)} excluded from this file entirely")
    print("(* marks results that clear 95% significance vs a coin flip)\n")

    tg = load_team_games()
    ratings = build_ratings_timeline(tg)
    ratings_gt = build_ratings_timeline(tg, garbage_time=True)
    games = load_games()

    lines = {(r["season"], r["week"], r["home_raw"], r["away_raw"]): r["pool_line"] for r in rows}
    gidx = {(int(g["season"]), int(g["week"]), g["home_team"]): g for g in games}
    coaches = build_coach_timeline(games, lambda g: lines.get(
        (int(g["season"]), int(g["week"]), g["home_team"], g["away_team"])))

    # --- points-per-EPA scale, fitted on TRAIN only ------------------------
    xs, ys = [], []
    for r in rows:
        if r["season"] not in TRAIN:
            continue
        nr = net_rating(ratings.get((r["season"], r["week"]), {}), r["home"], r["away"])
        if nr is not None:
            xs.append(nr)
            ys.append(r["margin"])
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sum((x - mx) ** 2 for x in xs)
    hfa = my - slope * mx
    print(f"scale fitted on TRAIN only: {slope:.1f} pts per EPA/play, home edge {hfa:+.2f}\n")

    def disagree(r, tl=ratings):
        nr = net_rating(tl.get((r["season"], r["week"]), {}), r["home"], r["away"])
        return None if nr is None else (slope * nr + hfa) + r["pool_line"]

    def shipped(r):
        return make_pick(r["away"], r["home"], r["pool_line"], r["live_line"]).side

    def coinflip(r):
        return make_pick(r["away"], r["home"], r["pool_line"], r["live_line"]).tier == "COIN FLIP"

    def ratings_side(r, tl=ratings):
        d = disagree(r, tl)
        return None if d is None else ("home" if d > 0 else "away")

    def coach_of(r, home=True):
        g = gidx.get((r["season"], r["week"], r["home_raw"]))
        return g.get("home_coach" if home else "away_coach") if g else None

    totals: dict[str, list[int]] = {}

    def run(label, fn, seas, only=None):
        w, l = ev(rows, seas, fn, label, only=only)
        t = totals.setdefault(label, [0, 0])
        t[0] += w
        t[1] += l

    for name, seas in (("TRAIN", TRAIN), ("VALIDATE", VAL)):
        print(f"=== {name} ===")
        run("SHIPPED model (market movement)", shipped, seas)
        run("  ...signal games only", shipped, seas, only=lambda r: not coinflip(r))
        run("  ...COIN FLIP games only", shipped, seas, only=coinflip)

        run("ratings vs pool line (all games)", ratings_side, seas)
        run("  ratings, COIN FLIP games only", ratings_side, seas, only=coinflip)
        for thr in (2.0, 3.0, 4.0):
            run(f"  ratings, |disagreement| >= {thr}",
                lambda r, t=thr: (None if disagree(r) is None or abs(disagree(r)) < t
                                  else ("home" if disagree(r) > 0 else "away")), seas)
        run("ratings (garbage-time filtered)", lambda r: ratings_side(r, ratings_gt), seas)
        for lo, hi, lab in ((1, 5, "wk1-4"), (5, 10, "wk5-9"), (10, 19, "wk10+")):
            run(f"  ratings, {lab}", ratings_side, seas,
                only=lambda r, lo=lo, hi=hi: lo <= r["week"] < hi)

        def coach_edge(r):
            tl = coaches.get((r["season"], r["week"]), {})
            hc = coach_ats_rate(tl.get(coach_of(r, True)))
            ac = coach_ats_rate(tl.get(coach_of(r, False)))
            return None if abs(hc - ac) < 0.01 else ("home" if hc > ac else "away")
        run("coach career ATS edge (shrunk)", coach_edge, seas)

        def first_year(r):
            tl = coaches.get((r["season"], r["week"]), {})
            hc, ac = tl.get(coach_of(r, True)), tl.get(coach_of(r, False))
            h_new = bool(hc) and hc["g"] < 17
            a_new = bool(ac) and ac["g"] < 17
            return None if h_new == a_new else ("away" if h_new else "home")
        run("fade first-year head coach", first_year, seas)

        def experience(r):
            tl = coaches.get((r["season"], r["week"]), {})
            hg = (tl.get(coach_of(r, True)) or {}).get("g", 0)
            ag = (tl.get(coach_of(r, False)) or {}).get("g", 0)
            return None if abs(hg - ag) < 64 else ("home" if hg > ag else "away")
        run("back far more experienced coach", experience, seas)

        def dog_coach(r):
            tl = coaches.get((r["season"], r["week"]), {})
            h_dog = r["pool_line"] > 0
            ent = tl.get(coach_of(r, h_dog))
            return None if coach_ats_rate(ent, "dog") <= 0.02 else ("home" if h_dog else "away")
        run("back proven underdog-covering coach", dog_coach, seas)
        print()

    print("=== COMBINED DEV (train+validate) ===")
    for label, (w, l) in totals.items():
        if label.startswith("  "):
            continue
        pct = w / (w + l) if (w + l) else 0
        z = _z(w, l)
        verdict = "SIGNIFICANT" if abs(z) >= 1.96 else "indistinguishable from a coin flip"
        print(f"  {label:<40} {w:4d}-{l:<4d} {pct:6.1%}  z={z:+5.2f}  {verdict}")

    # --- validity check: is the rating broken, or just redundant? ----------
    nr, mg, ln = [], [], []
    for r in rows:
        v = net_rating(ratings.get((r["season"], r["week"]), {}), r["home"], r["away"])
        if v is None:
            continue
        nr.append(v)
        mg.append(r["margin"])
        ln.append(-r["pool_line"])
    su = sum(1 for v, m in zip(nr, mg) if (v > 0) == (m > 0) and m != 0)
    lsu = sum(1 for v, m in zip(ln, mg) if (v > 0) == (m > 0) and m != 0)
    tot = sum(1 for m in mg if m != 0)
    mnr, mln = sum(nr) / len(nr), sum(ln) / len(ln)
    b = sum((x - mln) * (y - mnr) for x, y in zip(ln, nr)) / sum((x - mln) ** 2 for x in ln)
    resid = [y - (mnr + b * (x - mln)) for x, y in zip(ln, nr)]
    mres = [m - l for m, l in zip(mg, ln)]

    print("\n=== VALIDITY: is the rating broken, or merely redundant? ===")
    print(f"  corr(rating, margin)              {corr(nr, mg):+.3f}   rating DOES predict games")
    print(f"  corr(opening line, margin)        {corr(ln, mg):+.3f}   bookmaker predicts them better")
    print(f"  corr(rating, opening line)        {corr(nr, ln):+.3f}   they already agree this much")
    print(f"  straight-up winners: rating {su/tot:.1%} vs line {lsu/tot:.1%}")
    print(f"  corr(rating BEYOND line, margin BEYOND line) = {corr(resid, mres):+.3f}")
    print("    ^ the decisive number: the rating's unique information vs. what the")
    print("      line actually gets wrong. ~0 means there is nothing left to exploit.")


if __name__ == "__main__":
    main()
