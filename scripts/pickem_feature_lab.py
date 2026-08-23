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
import random
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
            row = {
                "season": s, "week": int(r["week"]),
                "home": canon(r["home_team"]), "away": canon(r["away_team"]),
                "home_raw": r["home_team"], "away_raw": r["away_team"],
                "pool_line": float(r["home_line_open"]),
                "live_line": float(r["home_line_close"]),
                "margin": int(r["home_score"]) - int(r["away_score"]),
            }
            # Moneyline drift, de-vigged. The spread only moves in 0.5-point
            # steps, so a market that shifts less than half a point shows NO
            # spread movement at all -- but the moneyline, being continuous,
            # still records it. That makes this the natural place to look for
            # signal inside "coin flip" games.
            try:
                ho, ao = 1 / float(r["home_ml_open"]), 1 / float(r["away_ml_open"])
                hc, ac = 1 / float(r["home_ml_close"]), 1 / float(r["away_ml_close"])
                row["ml_open"] = ho / (ho + ao)
                row["ml_close"] = hc / (hc + ac)
                row["ml_delta"] = row["ml_close"] - row["ml_open"]
            except (ValueError, ZeroDivisionError):
                row["ml_open"] = row["ml_close"] = row["ml_delta"] = None
            try:
                row["total_open"] = float(r["total_open"])
                row["total_close"] = float(r["total_close"])
                row["total_delta"] = row["total_close"] - row["total_open"]
            except ValueError:
                row["total_open"] = row["total_close"] = row["total_delta"] = None
            out.append(row)
    return out


KEY_CORE = (3.0, 7.0)                    # the two that actually dominate NFL margins
KEY_EXT = (3.0, 4.0, 6.0, 7.0, 10.0)     # the wider set Adam asked to test


def crosses(pool, live, keys) -> bool:
    """True if any key number lies strictly between the two lines (either sign)."""
    lo, hi = min(pool, live), max(pool, live)
    return any(lo < k < hi for k in (*keys, *(-k for k in keys)))


def key_distance(line, keys) -> float:
    """How far this line sits from the nearest key number."""
    return min(abs(abs(line) - k) for k in keys)


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



def market_experiments(rows) -> None:
    """Round 2: market-only features (no team/coach modelling).

    Everything here uses data already in data/pickem_odds_history.csv --
    spread open/close plus the previously unused moneyline and totals
    open/close. Nothing needs a new feed, so all of it is reproducible.
    """
    print("\n" + "=" * 78)
    print("ROUND 2 -- MARKET-BASED FEATURES")
    print("=" * 78)

    def edge(r):
        return r["live_line"] - r["pool_line"]

    def moved_side(r):
        return "home" if edge(r) < 0 else "away"

    def fav_side(r):
        return "home" if r["live_line"] < 0 else "away"

    def coinflip(r):
        return abs(edge(r)) < 0.5

    # ---------------------------------------------------------------- 1
    print("\n--- 1. KEY NUMBER CROSSING: does crossing 3/7 beat an equal-size move? ---")
    for name, seas in (("TRAIN", TRAIN), ("VALIDATE", VAL)):
        print(f"  [{name}]")
        for lo, hi in ((0.5, 1.5), (1.5, 3.0), (3.0, 99.0)):
            for keys, klab in ((KEY_CORE, "3/7"), (KEY_EXT, "3/4/6/7/10")):
                for crossed in (True, False):
                    lab = (f"    move {lo}-{hi}, {'CROSSES' if crossed else 'misses '} "
                           f"{klab:<10}")
                    ev(rows, seas, moved_side, lab,
                       only=lambda r, lo=lo, hi=hi, c=crossed, k=keys:
                           lo <= abs(edge(r)) < hi
                           and crosses(r["pool_line"], r["live_line"], k) == c)
        print()

    # Adam's specific question: 0.5pt crossing a key vs 1.0pt not crossing
    print("  [Adam's direct question] 0.5pt crossing 3/7  vs  1.0pt+ not crossing:")
    for name, seas in (("TRAIN", TRAIN), ("VALIDATE", VAL)):
        print(f"    [{name}]")
        ev(rows, seas, moved_side, "      0.5pt move that CROSSES 3 or 7",
           only=lambda r: abs(edge(r)) == 0.5
           and crosses(r["pool_line"], r["live_line"], KEY_CORE))
        ev(rows, seas, moved_side, "      1.0pt+ move that crosses nothing",
           only=lambda r: abs(edge(r)) >= 1.0
           and not crosses(r["pool_line"], r["live_line"], KEY_CORE))

    # ---------------------------------------------------------------- 2
    print("\n--- 2. COIN FLIPS (|edge| < 0.5): can anything beat 'take the favorite'? ---")
    for name, seas in (("TRAIN", TRAIN), ("VALIDATE", VAL)):
        print(f"  [{name}]  (baseline = current shipped behaviour)")
        ev(rows, seas, fav_side, "    BASELINE: take the market favorite", only=coinflip)

        # (a) key-number proximity
        def key_prox(r):
            d_pool = key_distance(r["pool_line"], KEY_CORE)
            if d_pool > 0.75:
                return None
            # pool line sits ON/near a key number: back the side getting it
            return "home" if r["pool_line"] > 0 else "away"
        ev(rows, seas, key_prox, "    (a) side receiving a key number", only=coinflip)

        # (b) SUBSTITUTE for sharp-book agreement (single-book history):
        #     continuous moneyline drift, which records sub-half-point moves
        #     the quantised spread cannot show.
        for thr in (0.005, 0.010, 0.020):
            def ml_side(r, t=thr):
                d = r["ml_delta"]
                if d is None or abs(d) < t:
                    return None
                return "home" if d > 0 else "away"
            ev(rows, seas, ml_side,
               f"    (b) moneyline drift >= {thr:.3f} prob", only=coinflip)

        # (c) SUBSTITUTE for public-pick fading (no historical pick data):
        #     totals drift as a proxy for changed game-environment expectations
        for thr in (0.5, 1.5):
            def tot_side(r, t=thr):
                d = r["total_delta"]
                if d is None or abs(d) < t:
                    return None
                # falling total -> lower-scoring game -> favours the underdog
                # getting points; rising total favours the favorite
                return fav_side(r) if d > 0 else ("away" if fav_side(r) == "home" else "home")
            ev(rows, seas, tot_side, f"    (c) totals drift >= {thr} pts", only=coinflip)
        print()

    # ---------------------------------------------------------------- 3
    print("--- 3. MONEYLINE DRIFT AS A GENERAL SIGNAL (all games) ---")
    for name, seas in (("TRAIN", TRAIN), ("VALIDATE", VAL)):
        print(f"  [{name}]")
        for thr in (0.010, 0.025, 0.050):
            def ml_all(r, t=thr):
                d = r["ml_delta"]
                if d is None or abs(d) < t:
                    return None
                return "home" if d > 0 else "away"
            ev(rows, seas, ml_all, f"    moneyline drift >= {thr:.3f} prob (all games)")
        # does ML agree/disagree with the spread move?
        def ml_confirms(r):
            d = r["ml_delta"]
            if d is None or coinflip(r):
                return None
            ml_s = "home" if d > 0 else "away"
            return moved_side(r) if ml_s == moved_side(r) else None
        ev(rows, seas, ml_confirms, "    spread move CONFIRMED by moneyline")

        def ml_conflicts(r):
            d = r["ml_delta"]
            if d is None or coinflip(r):
                return None
            ml_s = "home" if d > 0 else "away"
            return moved_side(r) if ml_s != moved_side(r) else None
        ev(rows, seas, ml_conflicts, "    spread move CONTRADICTED by moneyline")
        print()

    # ---------------------------------------------------------------- 4
    print("--- 4. WIN-PROBABILITY CALIBRATION (is Phi(edge/13.45) honest?) ---")
    from edge.pickem import win_prob
    for name, seas in (("TRAIN", TRAIN), ("VALIDATE", VAL)):
        print(f"  [{name}]   predicted -> actual")
        for lo, hi in ((0.5, 1.0), (1.0, 2.0), (2.0, 3.0), (3.0, 99.0)):
            sub = [r for r in rows if r["season"] in seas and lo <= abs(edge(r)) < hi]
            if not sub:
                continue
            pred = sum(win_prob(edge(r)) for r in sub) / len(sub)
            w = l = 0
            for r in sub:
                res = ats_result(r["margin"], r["pool_line"], moved_side(r))
                w += res == "W"
                l += res == "L"
            act = w / (w + l) if (w + l) else 0
            gap = act - pred
            print(f"    edge {lo:>4}-{hi:<5} n={w+l:<5} predicted {pred:.1%}  "
                  f"actual {act:.1%}  gap {gap:+.1%}")
        print()


def spread_regime_experiments(rows) -> None:
    """Round 3: does the model's accuracy depend on WHERE on the spread
    spectrum a game sits -- and if so, should the RULE change per regime?

    Distinct from every other section: this doesn't add a feature, it asks
    whether the one-size-fits-all rule should be several rules. That CAN add
    wins (it changes which side we take), unlike confidence-only findings.

    Includes a selection-bias control, which is the reusable part: fitting a
    per-bucket rule choice is a best-of-N search, and best-of-N looks good on
    pure noise. The control runs the identical procedure on coin-flip
    outcomes so any real gain has a null distribution to beat.
    """
    print("\n" + "=" * 78)
    print("ROUND 3 -- SPREAD REGIMES (is one model enough?)")
    print("=" * 78)

    BUCKETS = ((0.0, 3.0), (3.0, 6.0), (6.0, 9.0), (9.0, 13.0), (13.0, 99.0))

    def abs_line(r):
        return abs(r["pool_line"])

    def bucket_of(r):
        for lo, hi in BUCKETS:
            if lo <= abs_line(r) < hi:
                return (lo, hi)
        return None

    def fav_prob(r):
        """De-vigged implied win prob of whichever side the CLOSING line favours."""
        if r["ml_close"] is None:
            return None
        return r["ml_close"] if r["live_line"] < 0 else 1 - r["ml_close"]

    def shipped(r):
        return make_pick(r["away"], r["home"], r["pool_line"], r["live_line"],
                         r["total_open"], r["total_close"]).side

    def dog(r):
        return "home" if r["pool_line"] > 0 else "away"

    def fav(r):
        return "away" if r["pool_line"] > 0 else "home"

    def dog_on_coinflips(r):
        pk = make_pick(r["away"], r["home"], r["pool_line"], r["live_line"],
                       r["total_open"], r["total_close"])
        return dog(r) if pk.tier == "COIN FLIP" else pk.side

    RULES = {
        "shipped": shipped,
        "shipped-inverted": lambda r: "away" if shipped(r) == "home" else "home",
        "always-dog": dog,
        "always-fav": fav,
        "always-home": lambda r: "home",
        "dog-on-coinflips": dog_on_coinflips,
    }

    # ---------------------------------------------------------------- 1
    print("\n--- 1. EXPOSURE: how many games could a big-spread rule even touch? ---")
    n_weeks = len({(r["season"], r["week"]) for r in rows})
    for lo, hi in BUCKETS:
        n = sum(1 for r in rows if bucket_of(r) == (lo, hi))
        print(f"  spread {lo:>4.1f}-{hi:<5.1f} {n:5d} games  {n/len(rows):5.1%} of slate  "
              f"~{16*n/len(rows):.1f} per 16-game week")
    print(f"  ({n_weeks} dev weeks)")

    # ---------------------------------------------------------------- 2
    print("\n--- 2. SHIPPED MODEL'S ACCURACY BY SPREAD SIZE ---")
    for name, seas in (("TRAIN", TRAIN), ("VALIDATE", VAL)):
        print(f"  [{name}]")
        for lo, hi in BUCKETS:
            ev(rows, seas, shipped, f"    |line| {lo:.1f}-{hi:.1f}",
               only=lambda r, lo=lo, hi=hi: bucket_of(r) == (lo, hi))

    # ---------------------------------------------------------------- 3
    print("\n--- 3. SHIPPED MODEL'S ACCURACY BY MONEYLINE (de-vigged fav prob) ---")
    ML = ((0.50, 0.55), (0.55, 0.60), (0.60, 0.65), (0.65, 0.70), (0.70, 1.01))
    for name, seas in (("TRAIN", TRAIN), ("VALIDATE", VAL)):
        print(f"  [{name}]")
        for lo, hi in ML:
            ev(rows, seas, shipped, f"    fav prob {lo:.2f}-{hi:.2f}",
               only=lambda r, lo=lo, hi=hi: (fav_prob(r) is not None
                                             and lo <= fav_prob(r) < hi))

    # ---------------------------------------------------------------- 4
    print("\n--- 4. IS THE BOOK LESS ACCURATE ON BIG SPREADS? (the premise itself) ---")
    print("    sigma = std dev of (actual margin - frozen line); model assumes 13.45 flat")
    for lo, hi in BUCKETS:
        errs = [r["margin"] + r["pool_line"] for r in rows if bucket_of(r) == (lo, hi)]
        n = len(errs)
        mean = sum(errs) / n
        sd = math.sqrt(sum((e - mean) ** 2 for e in errs) / (n - 1))
        print(f"  spread {lo:>4.1f}-{hi:<5.1f} n={n:5d}   sigma {sd:5.2f}")

    # ---------------------------------------------------------------- 5
    print("\n--- 5. 'TAKE THE POINTS ON BIG DOGS' at rising thresholds ---")
    for thr in (7, 9, 11, 13, 14):
        for name, seas in (("TRAIN   ", TRAIN), ("VALIDATE", VAL)):
            ev(rows, seas, dog, f"    dog, |line| >= {thr:<2} [{name}]",
               only=lambda r, t=thr: abs_line(r) >= t)

    # ---------------------------------------------------------------- 6
    print("\n--- 6. WHY THE ANECDOTE FEELS TRUE: shape of big-spread outcomes ---")
    for lab, sel in (("|line| >= 13", lambda r: abs_line(r) >= 13),
                     ("|line| 3-9  ", lambda r: 3 <= abs_line(r) < 9)):
        # dog_err = points by which the UNDERDOG beat the frozen number
        de = [(r["margin"] + r["pool_line"]) * (1 if r["pool_line"] > 0 else -1)
              for r in rows if sel(r)]
        de = [d for d in de if d != 0]
        de.sort()
        cov = [d for d in de if d > 0]
        mis = [d for d in de if d < 0]
        med = de[len(de) // 2] if len(de) % 2 else (de[len(de)//2 - 1] + de[len(de)//2]) / 2
        print(f"  {lab}  n={len(de):4d}  dog covered {len(cov)/len(de):5.1%}   "
              f"mean {sum(de)/len(de):+5.2f}  median {med:+5.2f}   "
              f"when covering {sum(cov)/len(cov):+6.2f}, when not {sum(mis)/len(mis):+6.2f}")

    # ---------------------------------------------------------------- 7
    print("\n--- 7. EVERY RULE x EVERY BUCKET (a regime needs to win in BOTH eras) ---")
    for lo, hi in BUCKETS:
        print(f"  [spread {lo:.1f}-{hi:.1f}]")
        base = {}
        for name, seas in (("TRAIN", TRAIN), ("VALIDATE", VAL)):
            w, l = ev(rows, seas, shipped, f"    shipped [{name}]",
                      only=lambda r, lo=lo, hi=hi: bucket_of(r) == (lo, hi))
            base[name] = w / (w + l) if (w + l) else 0
        for rname, fn in RULES.items():
            if rname == "shipped":
                continue
            deltas = []
            for name, seas in (("TRAIN", TRAIN), ("VALIDATE", VAL)):
                w = l = 0
                for r in rows:
                    if r["season"] not in seas or bucket_of(r) != (lo, hi):
                        continue
                    res = ats_result(r["margin"], r["pool_line"], fn(r))
                    w += res == "W"
                    l += res == "L"
                deltas.append((w / (w + l) if (w + l) else 0) - base[name])
            flag = "  <== beats shipped in BOTH" if all(d > 0 for d in deltas) else (
                "  (sign flip)" if any(d > 0 for d in deltas) else "")
            print(f"    {rname:<20} vs shipped: train {deltas[0]*100:+5.1f}pp  "
                  f"validate {deltas[1]*100:+5.1f}pp{flag}")

    # ---------------------------------------------------------------- 8
    print("\n--- 8. SELECTION-BIAS CONTROL (the reusable part) ---")
    print("    Picking the best of 6 rules in each of 5 buckets is a best-of-N search.")
    print("    Run the IDENTICAL procedure on pure coin-flip outcomes to see what it")
    print("    manufactures from nothing. A real gain has to beat that null.")

    sides = {n: [f(r) for r in rows] for n, f in RULES.items()}
    bkt = [bucket_of(r) for r in rows]
    covers0 = [None if (r["margin"] + r["pool_line"]) == 0
               else (r["margin"] + r["pool_line"]) > 0 for r in rows]
    i_tr = [i for i, r in enumerate(rows) if r["season"] in TRAIN]
    i_va = [i for i, r in enumerate(rows) if r["season"] in VAL]

    def procedure(covers):
        best = {}
        for b in BUCKETS:
            bn, bp = "shipped", -1.0
            for n in RULES:
                w = l = 0
                for i in i_tr:
                    if bkt[i] != b or covers[i] is None:
                        continue
                    if (sides[n][i] == "home") == covers[i]:
                        w += 1
                    else:
                        l += 1
                p = w / (w + l) if (w + l) else 0.0
                if p > bp:
                    bn, bp = n, p
            best[b] = bn
        w = l = 0
        for i in i_va:
            if covers[i] is None:
                continue
            if (sides[best.get(bkt[i], "shipped")][i] == "home") == covers[i]:
                w += 1
            else:
                l += 1
        return w / (w + l), best

    def val_rate(covers, rule):
        w = l = 0
        for i in i_va:
            if covers[i] is None:
                continue
            if (sides[rule][i] == "home") == covers[i]:
                w += 1
            else:
                l += 1
        return w / (w + l)

    real, chosen = procedure(covers0)
    base_real = val_rate(covers0, "shipped")
    print(f"  REAL: best-per-bucket fitted on train -> validate {real:.1%}")
    print(f"        shipped one-size-fits-all       -> validate {base_real:.1%}")
    print(f"        gain {100*(real-base_real):+.1f}pp")
    print("        chosen: " + ", ".join(f"{b[0]:.0f}-{b[1]:.0f}={n}" for b, n in chosen.items()))

    rng = random.Random(20260823)
    gains = []
    for _ in range(400):
        fake = [None if c is None else (rng.random() < 0.5) for c in covers0]
        r, _ = procedure(fake)
        gains.append(r - val_rate(fake, "shipped"))
    gains.sort()
    beat = sum(1 for g in gains if g >= (real - base_real))
    print(f"  NULL (400 sims, no signal anywhere): mean {100*sum(gains)/len(gains):+.1f}pp, "
          f"95th pct {100*gains[int(.95*len(gains))]:+.1f}pp, max {100*gains[-1]:+.1f}pp")
    print(f"  -> the real gain was matched by chance in {beat}/400 sims "
          f"(empirical p = {beat/400:.3f})")
    print("  NOTE: the 95th percentile is the bar. A '+3pp regime improvement' here")
    print("        would have been an ordinary draw from pure noise.")


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

    market_experiments(rows)
    spread_regime_experiments(rows)

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
