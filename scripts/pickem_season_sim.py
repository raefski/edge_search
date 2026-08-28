#!/usr/bin/env python3
"""Simulate a full season in Adam's actual pool: 20 players, real money.

Replaces the old 1,000-coin-flipper thought experiment (scripts/pickem_vs_random.py)
with the real thing: 19 opponents, 1,000 simulated seasons, and the actual prize
structure -- so the output is DOLLARS OF PROFIT after the $150 buy-in, not a win rate.

THE POOL BALANCES EXACTLY AT 20 PLAYERS, which is a useful check that the
structure below is right:

    20 x $150 buy-in            = $3,000
    season ladder 1200+450+300+150 = $2,100
    weekly 18 weeks x $50          =   $900
                                   -------
                                     $3,000

Dev seasons only (2014-2022). The 2023-24 holdout was spent by
scripts/pickem_backtest.py and is not reused here.

Two things this answers that a win rate cannot:
  1. How often does the model actually cash, and for how much?
  2. Is the round-4/5 "tournament strategy" (shadowing the field on coin flips)
     worth anything against THIS field? Against coin flippers the answer is no,
     for a reason worth understanding -- see the note at the bottom of the output.

Run: python3 scripts/pickem_season_sim.py [n_sims] [seed]
"""
from __future__ import annotations

import csv
import statistics
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.pickem import ats_result, make_pick  # noqa: E402

ODDS = ROOT / "data" / "pickem_odds_history.csv"
HOLDOUT = {2023, 2024}
BUY_IN = 150.0
LADDER = [1200.0, 450.0, 300.0, 150.0]     # 1st..4th
WEEKLY_POT = 50.0
N_OPP = 19                                  # 19 opponents + Adam = 20 players
DEV_SD_MOVE = 1.8152                        # SD of open->close on dev


def load(half_point: bool = True, seed: int = 11) -> dict:
    """Dev seasons, grouped by season, via the shared loader.

    CBS's pool never posts an integer line -- 16/16 real Week 1 lines ended in
    .5 -- while the historical proxy is an integer 52.5% of the time, so
    half_point=True is the convention Adam actually plays under. That option
    now lives in edge/pickem_data so every reader of this CSV can apply it;
    this script having it privately was the drift that hid a push bug.
    """
    from edge.pickem_data import Split, by_season
    from edge.pickem_data import load as load_games

    grouped = by_season(load_games(Split.DEV, half_point=half_point,
                                   half_point_seed=seed))
    return {s: [dict(week=g.week, away=g.away, home=g.home, op=g.pool_line,
                     cl=g.live_line, margin=g.margin,
                     to=g.total_open, tc=g.total_close) for g in games]
            for s, games in grouped.items()}


def payout(my_score: float, opp_scores: np.ndarray) -> float:
    """Season ladder with ties splitting the payouts they span."""
    better = int((opp_scores > my_score).sum())
    tied = int((opp_scores == my_score).sum())
    if better >= len(LADDER):
        return 0.0
    span = LADDER[better:better + tied + 1]
    return sum(span) / (tied + 1) if span else 0.0


def simulate(by_season, n_sims, seed, w, policy, field, rng=None,
             randomise_flat=False):
    """w=1.0 is the backtest's assumption; lower w means CBS freezes after more
    of the move has happened (PICKEM_MODEL.md 5j round 6).

    field: 'coinflip' = opponents flip a fair coin per game (Adam's question)
           'chalk'    = opponents lean toward the frozen favourite
    """
    rng = rng or np.random.default_rng(seed)
    seasons = sorted(by_season)
    results = []
    for _ in range(n_sims):
        games = by_season[seasons[rng.integers(len(seasons))]]
        weeks: dict[int, list] = {}
        my_ok, fav_ok = [], []
        for g in games:
            M = g["cl"] - g["op"]
            if w >= 1.0:
                e2 = M
            elif w <= 0.0:
                e2 = 0.0
            else:
                e2 = w * M + np.sqrt(w * (1 - w)) * DEV_SD_MOVE * rng.standard_normal()
            cbs = round((g["cl"] - e2) * 2) / 2          # the line Adam is graded on
            to2 = tc2 = None
            if g["to"] is not None:
                to2, tc2 = g["to"] + (1 - w) * (g["tc"] - g["to"]), g["tc"]
            pk = make_pick(g["away"], g["home"], cbs, g["cl"], to2, tc2)
            side = pk.side
            if policy == "shadow" and pk.tier == "COIN FLIP":
                side = "away" if cbs > 0 else "home"     # go with the crowd
            elif policy == "diverge" and pk.tier == "COIN FLIP":
                side = "home" if cbs > 0 else "away"
            r = ats_result(g["margin"], cbs, side)
            fav = "away" if cbs > 0 else "home"
            rf = ats_result(g["margin"], cbs, fav)
            if randomise_flat and pk.tier == "COIN FLIP":
                # Remove the confound identified in 5j round 4: real flat-zone
                # outcomes credit each policy with its dev-era luck. Randomising
                # them gives every policy identical expected WINS, leaving
                # correlation with the field as the only difference.
                truth_fav = rng.random() < 0.5
                r = "W" if (side == fav) == truth_fav else "L"
                rf = "W" if truth_fav else "L"
            weeks.setdefault(g["week"], []).append(len(my_ok))
            my_ok.append(1 if r == "W" else 0 if r == "L" else 0.5)
            fav_ok.append(1 if rf == "W" else 0 if rf == "L" else 0.5)
        my_ok = np.array(my_ok, dtype=float)
        fav_ok = np.array(fav_ok, dtype=float)
        n = len(my_ok)

        if field == "coinflip":
            # A push is a push for EVERYONE. An earlier version gave Adam 0.5 on
            # a push while opponents got a clean 0/1, which quietly penalised
            # him. Only bites when the grading line is an integer -- which
            # CBS's pool never posts (every pool line ends in .5), so this is
            # moot under `--half-point` but wrong without it.
            opp_ok = np.where(fav_ok[None, :] == 0.5, 0.5,
                              (rng.random((N_OPP, n)) < 0.5).astype(float))
        else:
            lean = rng.uniform(0.60, 0.90, size=(N_OPP, 1))
            takes_fav = rng.random((N_OPP, n)) < lean
            opp_ok = np.where(takes_fav, fav_ok[None, :], 1.0 - fav_ok[None, :])

        weekly = 0.0
        for _, idx in sorted(weeks.items()):
            idx = np.array(idx)
            mine = my_ok[idx].sum()
            theirs = opp_ok[:, idx].sum(axis=1)
            top = max(mine, theirs.max())
            if mine == top:
                weekly += WEEKLY_POT / (1 + int((theirs == top).sum()))
        season = payout(my_ok.sum(), opp_ok.sum(axis=1))
        rank = 1 + int((opp_ok.sum(axis=1) > my_ok.sum()).sum())
        results.append((rank, season, weekly, my_ok.mean()))
    return results


def report(label, res):
    ranks = [r[0] for r in res]
    profit = [r[1] + r[2] - BUY_IN for r in res]
    top4 = sum(1 for r in ranks if r <= 4)
    print(f"  {label:<34} "
          f"1st {sum(1 for r in ranks if r == 1)/len(res):>5.1%}  "
          f"top4 {top4/len(res):>5.1%}  "
          f"profit mean ${statistics.mean(profit):>7.0f}  "
          f"median ${statistics.median(profit):>6.0f}  "
          f"P(+) {sum(1 for p in profit if p > 0)/len(res):>5.1%}")
    return top4 / len(res), statistics.mean(profit)


def main() -> None:
    n_sims = int(sys.argv[1]) if len(sys.argv) > 1 else 1000
    seed = int(sys.argv[2]) if len(sys.argv) > 2 else 20260823
    by_season = load()

    print("=" * 96)
    print(f"SEASON SIMULATION -- {N_OPP} opponents + you = {N_OPP+1} players, "
          f"{n_sims} seasons, dev 2014-2022")
    print("=" * 96)
    print("lines: CBS's half-point convention applied (no pushes possible) -- "
          "the pool never posts an integer")
    print(f"buy-in ${BUY_IN:.0f} x {N_OPP+1} = ${BUY_IN*(N_OPP+1):,.0f}   "
          f"= season ${sum(LADDER):,.0f} + weekly ${WEEKLY_POT*18:,.0f}   "
          "(the pool balances exactly at 20)")

    print(f"\nVS 19 COIN FLIPPERS -- your question")
    print(f"  {'':34} {'1st':>9} {'top4':>10} {'profit mean':>19} {'median':>13} {'P(+)':>10}")
    base = {}
    for w, wl in ((1.0, "full backtested edge"), (0.5, "half the move lost (w=0.5)"),
                  (0.3, "most of the move lost (w=0.3)")):
        r = simulate(by_season, n_sims, seed, w, "current", "coinflip")
        base[w] = report(f"model, {wl}", r)

    print(f"\n  Tournament strategy (shadow the field on coin flips) vs the SAME field:")
    for w in (1.0, 0.5):
        r = simulate(by_season, n_sims, seed, w, "shadow", "coinflip")
        t4, pr = report(f"  shadow, w={w}", r)
        print(f"  {'':34} vs current: top4 {100*(t4-base[w][0]):+.1f}pp, "
              f"profit ${pr-base[w][1]:+.0f}")

    print(f"\nVS 19 CHALK-LEANING OPPONENTS -- what your pool probably actually is")
    print(f"  {'':34} {'1st':>9} {'top4':>10} {'profit mean':>19} {'median':>13} {'P(+)':>10}")
    cbase = {}
    for w in (1.0, 0.5):
        r = simulate(by_season, n_sims, seed, w, "current", "chalk")
        cbase[w] = report(f"model, w={w}", r)
    for w in (1.0, 0.5):
        r = simulate(by_season, n_sims, seed, w, "shadow", "chalk")
        t4, pr = report(f"  shadow, w={w}", r)
        print(f"  {'':34} vs current: top4 {100*(t4-cbase[w][0]):+.1f}pp, "
              f"profit ${pr-cbase[w][1]:+.0f}")

    print("\n" + "-" * 96)
    print("WHY SHADOWING DOES NOTHING AGAINST COIN FLIPPERS: the strategy works by")
    print("CORRELATING your score with the field's, so that coin-flip noise cancels out")
    print("between you and them and your rank is decided by the games where you have an")
    print("edge. Coin flippers have no shared lean -- there is no crowd to join. The")
    print("strategy needs a field that leans somewhere predictable, which real pool")
    print("players do (they take favourites) and random simulated ones do not.")


if __name__ == "__main__":
    main()
