#!/usr/bin/env python3
"""How much of the backtested edge actually transfers to live play?

THE QUESTION THIS ANSWERS (PICKEM_MODEL.md 5j rounds 3 and 6)
-------------------------------------------------------------
The backtest measures a sportsbook OPENER against the CLOSE. Adam is graded
against CBS's frozen Tuesday number and picks at each day's deadline, so he
only gets whatever movement happens AFTER the freeze. Round 3 found that line
movement is front-loaded -- essentially independent of how many days remain --
which raised the possibility that CBS freezes after the valuable correction has
already happened, making the 55.9% headline unreachable.

Round 6 turned that fear into one parameter:

    w = share of the total move VARIANCE that lands after CBS freezes

and measured the damage curve for it on dev data. The key results: the curve is
CONCAVE (losing half the movement costs only about a quarter of the edge), and
its floor is chalk, not a coin flip.

THE POINT OF THIS SCRIPT: w needs NO GAME OUTCOMES to measure. Because the pool
grades against CBS's number, the live edge is exactly `market_at_lock -
cbs_line` whatever CBS anchors to, so only the MAGNITUDE of that gap matters:

    w  =  E[(market_at_lock - cbs_line)^2] / E[(open - close)^2]
       =  E[gap^2] / 3.3296

w is defined as a VARIANCE share, so it must be estimated with second moments.
An earlier version of this script used the ratio of MEAN ABSOLUTE gaps instead,
which overestimated w by ~30% in synthetic tests (true 0.50 -> recovered 0.66).
That form is only valid when the live gap has the same distributional SHAPE as
the historical move, and it does not: 19.7% of historical games do not move at
all, a spike that drags E|M| down relative to SD(M). Second moments are exact
regardless of shape. Four weeks of captures (~64 games)
gives a usable standard error. That converts the single largest open question
in the project from "wait a season and grade results" into "log two numbers a
week and read them off a curve."

Run: python3 scripts/pickem_transferability.py
Reads data/pickem_line_log.csv (written by scripts/pickem_capture.py).
Costs nothing and needs no API key.
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.pickem_log import LINE_LOG, load  # noqa: E402

# Dev-era mean |open - close|, from data/pickem_odds_history.csv (2014-2022).
# This is the denominator: a live gap this big means w = 1, i.e. the whole
# backtested move is still available after the freeze.
DEV_MEAN_ABS_MOVE = 1.2347       # E|open - close|, reported for context only
DEV_MEAN_SQ_MOVE = 3.3296        # E[(open - close)^2] -- the correct denominator

# Round 6's damage curve: margin over chalk (percentage points) by w.
# Chalk is the right comparator because ~17 opponents mostly play chalk.
# Reproduce with the round-6 block in scripts/pickem_idea_rounds.py.
CURVE = [
    # w,   train margin, validate margin
    (1.0, 6.72, 7.70),
    (0.8, 5.85, 6.89),
    (0.6, 5.37, 6.07),
    (0.5, 5.06, 5.48),
    (0.4, 4.62, 5.14),
    (0.3, 4.23, 4.74),
    (0.2, 3.84, 4.06),
    (0.1, 3.05, 3.65),
    (0.0, -0.13, 0.00),
]
TARGET_GAMES = 64          # ~4 weeks; enough for a usable SE on a mean


def interp(w: float) -> tuple[float, float]:
    """Margin over chalk (train, validate) at this w, linearly interpolated."""
    pts = sorted(CURVE)
    if w <= pts[0][0]:
        return pts[0][1], pts[0][2]
    if w >= pts[-1][0]:
        return pts[-1][1], pts[-1][2]
    for (w0, t0, v0), (w1, t1, v1) in zip(pts, pts[1:]):
        if w0 <= w <= w1:
            f = 0.0 if w1 == w0 else (w - w0) / (w1 - w0)
            return t0 + f * (t1 - t0), v0 + f * (v1 - v0)
    return pts[-1][1], pts[-1][2]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def collect(path=LINE_LOG) -> list[dict]:
    """One row per game: CBS's frozen line, the market at post, and at the
    LAST lock snapshot before kickoff."""
    rows = load(path)
    by_game: dict[tuple, dict] = defaultdict(dict)
    for r in rows:
        key = (r["season"], r["week"], r["home_team"])
        snap = r["snapshot"]
        cbs, mkt = _f(r.get("cbs_line_home")), _f(r.get("market_line_home"))
        g = by_game[key]
        g.setdefault("away", r["away_team"])
        if cbs is not None:
            g["cbs"] = cbs
        if mkt is None:
            continue
        if snap == "post":
            g["post"] = mkt
        elif snap == "lock":
            g["lock"] = mkt                       # later locks overwrite earlier
        else:
            g.setdefault("mid", {})[r.get("captured_at", "")] = mkt
    out = []
    for (season, week, home), g in by_game.items():
        if "cbs" not in g:
            continue
        out.append(dict(season=season, week=week, home=home, away=g.get("away", ""),
                        cbs=g["cbs"], post=g.get("post"), lock=g.get("lock"),
                        mid=g.get("mid", {})))
    return out


def main(path=LINE_LOG) -> None:
    games = collect(path)
    usable = [g for g in games if g["lock"] is not None]

    print("=" * 74)
    print("TRANSFERABILITY: how much of the backtested edge survives live?")
    print("=" * 74)
    print(f"log: {LINE_LOG}")
    print(f"games with a CBS line logged:        {len(games)}")
    print(f"games with a LOCK market reading:    {len(usable)}   "
          f"(need ~{TARGET_GAMES} for a usable estimate)")

    if not usable:
        print("\nNothing to measure yet. What this needs, per week:")
        print("  1. Transcribe the CBS screenshot into data/pickem_current_week.csv")
        print("  2. python3 scripts/pickem_capture.py --snapshot post --week N --confirm")
        print("     (run it WITHIN MINUTES of step 1 -- 'the market when CBS posted'")
        print("      is only that if it is contemporaneous)")
        print("  3. python3 scripts/pickem_capture.py --snapshot midweek --week N --confirm")
        print("     (Thursday or Friday -- see the note at the bottom)")
        print("  4. python3 scripts/pickem_capture.py --snapshot lock --week N --confirm")
        print("     (before the first game of each day)")
        print(f"\nAfter ~4 weeks this script prints the answer. A missed week is a")
        print("permanently missing row -- the historical file cannot substitute.")
        return

    signed = [g["lock"] - g["cbs"] for g in usable]
    gaps = [abs(x) for x in signed]
    sq = [x * x for x in signed]
    n = len(gaps)
    mean_gap = sum(gaps) / n
    mean_sq = sum(sq) / n
    # SE of the mean of gap^2, then propagate to w (a simple linear scaling)
    sd_sq = (math.sqrt(sum((x - mean_sq) ** 2 for x in sq) / (n - 1)) if n > 1 else 0.0)
    se_sq = sd_sq / math.sqrt(n) if n else 0.0

    w = mean_sq / DEV_MEAN_SQ_MOVE
    lo = max(0.0, (mean_sq - 1.96 * se_sq) / DEV_MEAN_SQ_MOVE)
    hi = min(1.0, (mean_sq + 1.96 * se_sq) / DEV_MEAN_SQ_MOVE)
    tr, va = interp(w)
    tr_lo, va_lo = interp(lo)
    tr_hi, va_hi = interp(hi)

    print(f"\nlive gap (market_at_lock - cbs_line), n={n}:")
    print(f"  mean |gap| {mean_gap:.3f} pts      (dev benchmark E|move| {DEV_MEAN_ABS_MOVE})")
    print(f"  mean gap^2 {mean_sq:.3f}           (dev benchmark E[move^2] {DEV_MEAN_SQ_MOVE})")
    print(f"\nimplied w = E[gap^2] / {DEV_MEAN_SQ_MOVE} = {w:.2f}   "
          f"95% CI [{lo:.2f}, {hi:.2f}]")
    print(f"\nexpected margin OVER CHALK (what the 17 opponents mostly play):")
    print(f"  using train-era curve:    {tr:+.2f}pp   CI [{tr_lo:+.2f}, {tr_hi:+.2f}]")
    print(f"  using validate-era curve: {va:+.2f}pp   CI [{va_lo:+.2f}, {va_hi:+.2f}]")
    print(f"  (backtest assumed w=1.0 -> +6.72 / +7.70pp)")
    retained = (tr / 6.72) if 6.72 else 0.0
    print(f"\n  -> roughly {retained:.0%} of the backtested margin transfers.")

    if n < TARGET_GAMES:
        print(f"\n  PRELIMINARY: {n}/{TARGET_GAMES} games. Treat the CI, not the "
              "point estimate, as the answer.")

    # Is CBS anchored to the opener or to the Tuesday market?
    with_post = [g for g in usable if g["post"] is not None]
    if with_post:
        off = [g["post"] - g["cbs"] for g in with_post]
        m = sum(off) / len(off)
        print(f"\ncbs_offset = market_at_post - cbs_line   (n={len(with_post)})")
        print(f"  mean {m:+.3f}   mean |offset| {sum(map(abs, off))/len(off):.3f}")
        print("  Near zero means CBS posts the contemporaneous market, so essentially")
        print("  all of the live edge is post-Tuesday drift. Large and persistent means")
        print("  CBS's own anchoring is handing you value before anything moves.")
        print("  NOTE: do NOT subtract this from the model's edge -- you are graded")
        print("  against CBS's number, so offset is worth as much as drift (5j r3c).")

    # Time profile, if the mid-week snapshot habit is being kept.
    with_mid = [g for g in usable if g["mid"] and g["post"] is not None]
    if with_mid:
        early, late = [], []
        for g in with_mid:
            mid = g["mid"][sorted(g["mid"])[-1]]
            early.append(abs(mid - g["cbs"]))
            late.append(abs(g["lock"] - mid))
        print(f"\ntime profile of post-freeze drift (n={len(with_mid)}):")
        print(f"  freeze -> midweek: mean |move| {sum(early)/len(early):.3f}")
        print(f"  midweek -> lock:   mean |move| {sum(late)/len(late):.3f}")
        print("  This is the one thing the 2014-2024 archive can NEVER supply --")
        print("  it has only two snapshots per game. Keep the mid-week capture.")
    else:
        print("\nNo mid-week snapshots logged yet. Adding one (Thursday or Friday,")
        print("`--snapshot midweek`) costs 2 credits and is the ONLY way to learn the")
        print("TIME PROFILE of post-freeze drift -- the archive cannot ever provide it.")


if __name__ == "__main__":
    main()
