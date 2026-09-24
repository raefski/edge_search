#!/usr/bin/env python3
"""Fit -- and then grade -- the two constants the ladder estimator cannot price.

    python3 scripts/ncaaf_ladder_fit.py                  # fit + grade, 2024+2025
    python3 scripts/ncaaf_ladder_fit.py --seasons 2023 2024 2025
    python3 scripts/ncaaf_ladder_fit.py --grade-only     # grade what is shipped

WHAT IS BEING FITTED, AND WHY IT IS NOT A DISTRIBUTION FAMILY
edge/dfs_ladder.py computes E[X] from the exact identity E[X] = integral of
P(X >= t) dt. Between the lowest and highest posted rung every term is a real
devigged price and nothing is assumed. Two pieces are not posted:

    HEAD   integral of S(t) from 0 to t_min
    TAIL   integral of S(t) from t_max to infinity

The obvious move is to fit a parametric family to the rungs and integrate it
over those ranges. That was tried first and it does not work well enough:

    end-to-end bias, estimator fed TRUE survival probabilities (no market,
    no vig), against real cfbfastR player-game distributions, 2024+2025
                     lognormal      Weibull
        pass yds        + 5.5%       + 0.8%
        rush yds        +15.8%       + 8.9%
        rec yds         +20.0%       +12.4%

Both families overstate the head for rushing and receiving, because those
distributions have a large mass at and near zero -- a receiver who was targeted
once for four yards, or not at all -- and a smooth right-skewed density does
not reproduce that. Swapping families moved the bias without removing it, and
choosing between two wrong shapes on feel is exactly the kind of decision this
repo tries not to make.

So the shape is dropped and the two quantities are measured directly instead,
each as ONE bounded, interpretable number per market:

    HEAD_FILL   head = t_min * [ S(t_min) + c * (1 - S(t_min)) ]
                c = 0 is the rectangle t_min*S(t_min), which is the strict
                LOWER bound; c = 1 is the full box t_min, the strict UPPER
                bound. Monotonicity guarantees the truth is between them, so c
                is not an extrapolation -- it is a position inside a bracket
                that the posted prices themselves establish.

    TAIL_EXCESS tail = S(t_max) * (m * gap), gap = mean spacing of the rungs
                m = E[X - t_max | X >= t_max] / gap.
                Measured against t_max instead, m ranges over 0.017-0.067
                across the three markets with a wide spread; against the RUNG
                GAP it is 0.40-0.50 with a tight one. That is the empirical
                reason for the parametrisation, and it has a plain reading:
                the spacing a book chooses reflects the local scale of the
                distribution where it stopped posting.

HOW THE TRUTH IS COMPUTED, WHICH IS THE PART THAT MAKES THIS A REAL FIT
For one player's own sample of game outcomes, the head integral is exactly
mean(min(x, t_min)) and the tail integral is exactly mean(max(0, x - t_max)).
No estimate of either is involved -- those identities hold for any sample. So
each player-season with enough games yields one exact observation of c and one
of m, and the constant is the MEDIAN over thousands of them.

Median, not mean: a player with two games above the top rung produces a wild
ratio and the mean follows it.

GRADING IS PART OF THE SCRIPT ON PURPOSE. A fit that is never scored against
the thing it was supposed to fix is how a plausible constant survives being
wrong. The --grade pass re-runs the whole estimator with the fitted values and
reports the same end-to-end bias table as above.
"""
from __future__ import annotations

import argparse
import collections
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import dfs_ladder as L, ncaaf    # noqa: E402

#: The rung patterns DraftKings actually posts, read off the live NCAAF board
#: on 2026-09-24. These matter: fitting the head against thresholds DK does not
#: use would fit the wrong t_min, and t_min is the whole exposure.
DK_RUNG_PATTERN = {
    "pass_yds": list(range(180, 420, 10)),
    "rush_yds": list(range(25, 205, 10)),
    "rec_yds": list(range(15, 215, 10)),
    "rec": list(range(2, 12)),
    "pass_td": list(range(1, 7)),
}
MARKET = {"pass_yds": "player_pass_yds", "rush_yds": "player_rush_yds",
          "rec_yds": "player_reception_yds", "rec": "player_receptions",
          "pass_td": "player_pass_tds"}
COUNT = {"rec", "pass_td"}

#: A player needs enough games for his own empirical survival curve to mean
#: anything. Eight is most of a college season and still admits thousands of
#: players; at four the c estimates are dominated by sampling noise.
MIN_GAMES = 8


def player_games(seasons: list[int]) -> dict:
    """{player: {stat: [per-game values]}} over every season asked for."""
    per: dict = collections.defaultdict(lambda: collections.defaultdict(list))
    for season in seasons:
        print(f"  loading {season}…", flush=True)
        box = ncaaf.boxscores(ncaaf.fetch_season(season))
        for v in box.values():
            name = ncaaf.norm(v["player"])
            for stat in DK_RUNG_PATTERN:
                per[name][stat].append(float(v.get(stat, 0.0) or 0.0))
    return per


def ladder_for(values: list[float], thresholds: list[int]):
    """The TRUE survival curve at DK's own thresholds, as priced rungs.

    Zero vig on purpose. This isolates the ESTIMATOR: any bias that shows up
    here is the estimator's, because the market has been removed entirely.
    """
    rungs = []
    for t in thresholds:
        p = sum(1 for v in values if v >= t) / len(values)
        if 0.02 < p < 0.98:
            rungs.append((float(t), 1.0 / p))
    return rungs


def fit(per: dict) -> dict:
    """Measured HEAD_FILL and TAIL_EXCESS per stat, with their spreads."""
    out = {}
    for stat, pattern in DK_RUNG_PATTERN.items():
        fills, excesses = [], []
        for values in (d.get(stat) for d in per.values()):
            if not values or len(values) < MIN_GAMES or statistics.fmean(values) <= 0:
                continue
            rungs = ladder_for(values, pattern)
            if len(rungs) < 3:
                continue
            t_min, p_min = rungs[0][0], 1.0 / rungs[0][1]
            t_max, p_max = rungs[-1][0], 1.0 / rungs[-1][1]

            # head: exactly mean(min(x, t_min)) for this player's own sample
            if (1.0 - p_min) > 1e-6:
                head = statistics.fmean(min(v, t_min) for v in values)
                fills.append(max(0.0, min(1.0, (head / t_min - p_min) / (1.0 - p_min))))

            # tail: exactly mean(max(0, x - t_max)), expressed per rung gap
            over = [v for v in values if v >= t_max]
            if len(over) >= 2:
                gap = (t_max - t_min) / max(1, len(rungs) - 1)
                if gap > 0:
                    excesses.append(statistics.fmean(v - t_max for v in over) / gap)

        out[stat] = {
            "n_head": len(fills), "n_tail": len(excesses),
            "head_fill": statistics.median(fills) if fills else None,
            "head_iqr": _iqr(fills), "tail_excess": statistics.median(excesses)
            if excesses else None, "tail_iqr": _iqr(excesses),
        }
    return out


def _iqr(xs: list[float]):
    if len(xs) < 4:
        return None
    s = sorted(xs)
    return (s[len(s) // 4], s[(3 * len(s)) // 4])


def grade(per: dict) -> dict:
    """End-to-end bias of the SHIPPED estimator against known truth."""
    out = {}
    for stat, pattern in DK_RUNG_PATTERN.items():
        truth, est = [], []
        for values in (d.get(stat) for d in per.values()):
            if not values or len(values) < MIN_GAMES:
                continue
            actual = statistics.fmean(values)
            if actual <= 0:
                continue
            rungs = ladder_for(values, pattern)
            if len(rungs) < 3:
                continue
            got = L.project_market(rungs, MARKET[stat], overround=0.0)["mean"]
            if got is None:
                continue
            truth.append(actual)
            est.append(got)
        if len(truth) < 20:
            out[stat] = None
            continue
        mt, me = statistics.fmean(truth), statistics.fmean(est)
        out[stat] = {"n": len(truth), "true": mt, "est": me,
                     "bias": me - mt, "bias_pct": 100.0 * (me - mt) / mt}
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", type=int, nargs="+", default=[2024, 2025])
    ap.add_argument("--grade-only", action="store_true",
                    help="skip the fit; only score the shipped constants")
    args = ap.parse_args()

    print(f"cfbfastR seasons {args.seasons}")
    per = player_games(args.seasons)
    print(f"  {len(per)} players with at least one game\n")

    if not args.grade_only:
        res = fit(per)
        print("FITTED (median over players with "
              f"{MIN_GAMES}+ games; IQR in brackets)")
        print(f"{'stat':>9}{'n':>7}{'HEAD_FILL':>12}{'IQR':>18}"
              f"{'n':>7}{'TAIL_EXCESS':>14}{'IQR':>18}")
        for stat, r in res.items():
            hf = f"{r['head_fill']:.3f}" if r["head_fill"] is not None else "-"
            te = f"{r['tail_excess']:.3f}" if r["tail_excess"] is not None else "-"
            hi = (f"[{r['head_iqr'][0]:.2f}, {r['head_iqr'][1]:.2f}]"
                  if r["head_iqr"] else "-")
            ti = (f"[{r['tail_iqr'][0]:.2f}, {r['tail_iqr'][1]:.2f}]"
                  if r["tail_iqr"] else "-")
            print(f"{stat:>9}{r['n_head']:>7}{hf:>12}{hi:>18}"
                  f"{r['n_tail']:>7}{te:>14}{ti:>18}")
        print("\nPaste into edge/dfs_ladder.py:\n")
        print("HEAD_FILL = {")
        for stat, r in res.items():
            if r["head_fill"] is not None:
                print(f'    "{MARKET[stat]}": {r["head_fill"]:.3f},'
                      f'    # n={r["n_head"]}')
        print("}")
        print("TAIL_EXCESS = {")
        for stat, r in res.items():
            if r["tail_excess"] is not None:
                print(f'    "{MARKET[stat]}": {r["tail_excess"]:.3f},'
                      f'    # n={r["n_tail"]}')
        print("}")
        print()

    print("GRADE -- shipped estimator vs known truth, no market, no vig")
    print(f"{'stat':>9}{'n':>7}{'true':>10}{'est':>10}{'bias':>9}{'bias %':>9}")
    for stat, r in grade(per).items():
        if r is None:
            print(f"{stat:>9}{'-':>7}   too few players")
            continue
        print(f"{stat:>9}{r['n']:>7}{r['true']:>10.2f}{r['est']:>10.2f}"
              f"{r['bias']:>9.2f}{r['bias_pct']:>8.1f}%")
    print("\nA bias under a couple of percent is the target. This measures the "
          "ESTIMATOR\nonly -- it says nothing about whether DraftKings' prices "
          "are right, which is what\nscripts/ncaaf_overround_fit.py and the "
          "forward-test log are for.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
