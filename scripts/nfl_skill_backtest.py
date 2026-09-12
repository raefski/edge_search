#!/usr/bin/env python3
"""Leak-free NFL skill-model backtest, scored through the SHIPPED assembler.

WHAT THIS IS FOR
DFS_STATUS.md's "immediate next step" was a props-vs-skill head-to-head on
paid historical props. Those props are gone -- `data/nfl_historical_props_*`
was a one-shot pull on a key that expired 2026-07-24 and is not on this
machine any more (see data/HISTORICAL_COLLECTION_README.md, which said to back
it up). So the backward half of that test cannot be run at any price.

What survives is the FREE half: data/nfl_ground_truth/player_week_{2023,2024}
from nflverse. That is enough to build the arm the repo does not have. The
shipped NFL projection (edge/dfs_project.project) is purely props-driven;
there is no skill model to compare it against. This builds one, measures it
honestly on held-out weeks, and leaves a baseline that the forward grader can
hold the props model up against as real weeks land.

DISCIPLINE (DFS_METHODOLOGY §4/§18, the same rules dfs_component_eval.py uses)
  * Every prediction for week w uses ONLY rows before week w: all of 2023 plus
    2024 weeks 1..w-1. Nothing else is in scope at prediction time, including
    the position priors.
  * The one hyperparameter (the shrinkage constant K) is chosen on the TRAIN
    window, 2024 weeks 1-9.
  * Every reported number comes from the TEST window, 2024 weeks 10-18, which
    the fit never saw.

THE COMPARISON IS THROUGH ONE ASSEMBLER, DELIBERATELY
The per-category means go through edge.dfs_project.points_from_means -- the
exact function the props path uses once it has means. Same imputation, same
sigmas, same distribution-scored bonuses, same rounding. If the two projections
went through two assemblers, a difference between them could be the means or
the assembly and there would be no way to tell which.

WHAT IT ASSUMES, STATED RATHER THAN HIDDEN
Only players who actually have a row that week are predicted, i.e. the model is
told who played. That is a real look-ahead, and it is the same one the live
path makes (a player with a posted prop is expected to play) and the same one
MLB's backtest makes with confirmed lineups. It means these numbers describe
projection accuracy, NOT the separate problem of predicting availability.

    python3 scripts/nfl_skill_backtest.py
    python3 scripts/nfl_skill_backtest.py --train-weeks 1-9 --test-weeks 10-18
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.dfs_project import points_from_means          # noqa: E402
from edge.dfs_sport import NFL                          # noqa: E402
from edge.nfl import actual_offense_points              # noqa: E402

GT = ROOT / "data" / "nfl_ground_truth"

#: nflverse column -> the market key edge/dfs_sport.NFL prices it under. The
#: keys on the right are the Odds API's own spellings, which is what makes a
#: skill mean and a prop mean interchangeable at the assembler.
CATEGORIES = {
    "passing_yards": "player_pass_yds",
    "passing_tds": "player_pass_tds",
    "passing_interceptions": "player_pass_interceptions",
    "rushing_yards": "player_rush_yds",
    "rushing_tds": "player_rush_tds",
    "receiving_yards": "player_reception_yds",
    "receptions": "player_receptions",
    "receiving_tds": "player_reception_tds",
}

#: DST has no player rows and is projected from the market's implied team total
#: (dfs_project.project_dst), so it is not a skill-model question at all.
POSITIONS = ("QB", "RB", "WR", "TE")


def _f(v) -> float:
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def load_rows() -> list[dict]:
    """Regular-season player-weeks for the skill positions, both seasons."""
    out = []
    for season in (2023, 2024):
        path = GT / f"player_week_{season}.json"
        if not path.exists():
            raise SystemExit(
                f"missing {path}. It is free and regenerable: "
                f"`python3 scripts/nfl_ground_truth_collect.py`.")
        raw = json.loads(path.read_text())
        rows = raw if isinstance(raw, list) else (raw.get("rows") or list(raw.values())[0])
        for r in rows:
            if r.get("season_type") != "REG" or r.get("position") not in POSITIONS:
                continue
            if not r.get("player_id"):
                continue
            out.append({"player_id": r["player_id"],
                        "name": r.get("player_display_name") or r.get("player_name"),
                        "position": r["position"],
                        "season": int(r["season"]),
                        "week": int(r["week"]),
                        "actual": actual_offense_points(r),
                        **{c: _f(r.get(c)) for c in CATEGORIES}})
    return out


class Running:
    """Per-player and per-position totals over everything seen so far.

    Updated only after a week is scored, which is what keeps the split honest:
    the object literally cannot contain a row from the week being predicted.
    """

    def __init__(self):
        self.player = defaultdict(lambda: {"games": 0, **{c: 0.0 for c in CATEGORIES}})
        self.position = defaultdict(lambda: {"games": 0, **{c: 0.0 for c in CATEGORIES}})

    def add(self, row: dict) -> None:
        for bucket in (self.player[row["player_id"]], self.position[row["position"]]):
            bucket["games"] += 1
            for c in CATEGORIES:
                bucket[c] += row[c]

    def decay(self, d: float) -> None:
        """Age everything by one week.

        Exponential recency, applied to the accumulated weighted sums rather
        than kept as a list of games: `games` stops being a count and becomes
        an effective sample size, which is exactly the denominator the EB
        formula already wants. d=1.0 is the flat model and is in the grid, so
        "no recency" has to win on TRAIN rather than being assumed away.

        It also handles the season boundary for free -- 2023 is simply 18-odd
        more weeks of decay back, which is the MARCEL-style cross-season
        downweighting dfs_component_eval.py tested for MLB, with no extra
        parameter to choose.
        """
        if d >= 1.0:
            return
        for bucket in list(self.player.values()) + list(self.position.values()):
            bucket["games"] *= d
            for c in CATEGORIES:
                bucket[c] *= d

    def prior(self, position: str, cat: str) -> float:
        p = self.position[position]
        return p[cat] / p["games"] if p["games"] else 0.0

    def means(self, row: dict, k: float) -> dict[str, float]:
        """Empirical-Bayes per-game means, shrunk toward the position rate.

        Same shape as MLB's EB K=60 on plate appearances (DFS_METHODOLOGY §18):
        a player with few games is mostly his position's average, a player with
        many is mostly himself, and K is where the crossover sits. K is in
        GAMES here because an NFL season is 17 of them -- the unit that makes
        `games + K` a meaningful denominator.
        """
        p = self.player[row["player_id"]]
        n = p["games"]
        out = {}
        for cat, market in CATEGORIES.items():
            prior = self.prior(row["position"], cat)
            # n + k == 0 is a real row, not an edge case to assert away: it is
            # every debutant under K=0 (a 2024 rookie in week 1 has no history
            # and no shrinkage to fall back on). The position rate is the only
            # honest answer there, and it is what K>0 converges to anyway.
            out[market] = prior if n + k == 0 else (p[cat] + k * prior) / (n + k)
        return out


def project_skill(row: dict, run: Running, k: float) -> float:
    means = run.means(row, k)
    return points_from_means(means, {}, NFL, row["position"])["proj"] or 0.0


def project_player_mean(row: dict, run: Running) -> float:
    """Baseline 1: the player's own per-game DK average so far, no shrinkage.

    The honest thing to beat. If a per-category model cannot beat 'what he has
    averaged', the categories are not earning their complexity.
    """
    p = run.player[row["player_id"]]
    if not p["games"]:
        return project_position_mean(row, run)
    return points_from_means(
        {m: p[c] / p["games"] for c, m in CATEGORIES.items()}, {}, NFL,
        row["position"])["proj"] or 0.0


def project_position_mean(row: dict, run: Running) -> float:
    """Baseline 0: the position's average. The floor any model must clear."""
    return points_from_means(
        {m: run.prior(row["position"], c) for c, m in CATEGORIES.items()}, {},
        NFL, row["position"])["proj"] or 0.0


def run_backtest(rows: list[dict], weeks: range, k: float,
                 d: float = 1.0) -> list[dict]:
    """Walk 2024 in order, predicting each week from strictly earlier data.

    The loop order IS the leak-free guarantee: predict week w, THEN add week w,
    then age everything. The Running object cannot contain the week being
    predicted because it has not been given it yet.
    """
    by_week = defaultdict(list)
    for r in rows:
        by_week[(r["season"], r["week"])].append(r)

    # TWO accumulators, and the second one is not redundant. The baselines
    # have to read UNDECAYED history, or "player mean" is computed from the
    # same recency-weighted state as the candidate and the two come out
    # byte-identical no matter what the decay is -- which is exactly what the
    # first version of this script did, hiding the decay's entire contribution
    # behind a baseline that had silently become the candidate.
    run, flat = Running(), Running()
    for w in sorted({wk for (s, wk) in by_week if s == 2023}):
        for r in by_week[(2023, w)]:
            run.add(r)
            flat.add(r)
        run.decay(d)

    graded = []
    for w in sorted({wk for (s, wk) in by_week if s == 2024}):
        week_rows = by_week[(2024, w)]
        if w in weeks:
            for r in week_rows:
                graded.append({
                    "position": r["position"], "name": r["name"], "week": w,
                    "actual": r["actual"],
                    "skill": project_skill(r, run, k),
                    "player_mean": project_player_mean(r, flat),
                    "position_mean": project_position_mean(r, flat),
                })
        for r in week_rows:
            run.add(r)
            flat.add(r)
        run.decay(d)
    return graded


def metrics(graded: list[dict], model: str) -> dict:
    a = [g["actual"] for g in graded]
    p = [g[model] for g in graded]
    n = len(a)
    if n < 3:
        return {"n": n, "corr": float("nan"), "mae": float("nan"), "rmse": float("nan")}
    try:
        corr = statistics.correlation(a, p)
    except statistics.StatisticsError:
        corr = float("nan")
    return {"n": n, "corr": corr,
            "mae": sum(abs(x - y) for x, y in zip(a, p)) / n,
            "rmse": math.sqrt(sum((x - y) ** 2 for x, y in zip(a, p)) / n)}


def _weeks(spec: str) -> range:
    lo, hi = spec.split("-")
    return range(int(lo), int(hi) + 1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--train-weeks", default="1-9")
    ap.add_argument("--test-weeks", default="10-18")
    ap.add_argument("--k-grid", default="0,1,2,3,4,6,8,12,16,24")
    ap.add_argument("--d-grid", default="1.0,0.98,0.95,0.92,0.90,0.85,0.80",
                    help="per-week recency decay. 1.0 (flat) is in the grid on "
                         "purpose: 'no recency' has to WIN on TRAIN, not be "
                         "assumed away.")
    args = ap.parse_args()

    train, test = _weeks(args.train_weeks), _weeks(args.test_weeks)
    rows = load_rows()
    n23 = sum(1 for r in rows if r["season"] == 2023)
    print(f"loaded {len(rows):,} regular-season player-weeks "
          f"({n23:,} from 2023, {len(rows) - n23:,} from 2024), "
          f"positions {'/'.join(POSITIONS)}")

    # --- choose K and the decay on TRAIN only -----------------------------
    print(f"\nTRAIN (2024 weeks {train.start}-{train.stop - 1}) -- choosing K and decay")
    print(f"  {'K':>5}  {'decay':>6}  {'corr':>7}  {'MAE':>7}  {'RMSE':>7}")
    ks = [float(x) for x in args.k_grid.split(",")]
    ds = [float(x) for x in args.d_grid.split(",")]
    scored = []
    for d in ds:
        for k in ks:
            m = metrics(run_backtest(rows, train, k, d), "skill")
            scored.append((k, d, m))
    # Selected on MAE, but corr is printed for every cell because the two can
    # disagree -- they did here, and a fit reported only through its winner
    # hides that. MLB's own rule (dfs_component_eval.py) is that a candidate
    # ships when corr improves and MAE does not get worse.
    for k, d, m in sorted(scored, key=lambda t: t[2]["mae"])[:12]:
        print(f"  {k:5.0f}  {d:6.2f}  {m['corr']:7.4f}  {m['mae']:7.3f}  {m['rmse']:7.3f}")
    best_k, best_d, best_m = min(scored, key=lambda t: t[2]["mae"])
    best_corr = max(scored, key=lambda t: t[2]["corr"])
    print(f"  -> lowest TRAIN MAE : K={best_k:.0f} decay={best_d:.2f} "
          f"(MAE {best_m['mae']:.3f}, corr {best_m['corr']:.4f})")
    print(f"  -> highest TRAIN corr: K={best_corr[0]:.0f} decay={best_corr[1]:.2f} "
          f"(MAE {best_corr[2]['mae']:.3f}, corr {best_corr[2]['corr']:.4f})")

    # --- report on TEST only ----------------------------------------------
    graded = run_backtest(rows, test, best_k, best_d)
    print(f"\nTEST (2024 weeks {test.start}-{test.stop - 1}) -- never seen by the fit")
    print(f"  {'model':>16}  {'n':>6}  {'corr':>7}  {'MAE':>7}  {'RMSE':>7}")
    for model, label in (("position_mean", "position mean"),
                         ("player_mean", "player mean"),
                         ("skill", f"skill K={best_k:.0f} d={best_d:.2f}")):
        m = metrics(graded, model)
        print(f"  {label:>16}  {m['n']:6,}  {m['corr']:7.4f}  "
              f"{m['mae']:7.3f}  {m['rmse']:7.3f}")

    print("\nBY POSITION (TEST)")
    print(f"  {'pos':>4}  {'n':>5}  {'corr skill':>11}  {'corr player':>12}  "
          f"{'MAE skill':>10}  {'MAE player':>11}")
    for pos in POSITIONS:
        sub = [g for g in graded if g["position"] == pos]
        s, pm = metrics(sub, "skill"), metrics(sub, "player_mean")
        print(f"  {pos:>4}  {s['n']:5,}  {s['corr']:11.4f}  {pm['corr']:12.4f}  "
              f"{s['mae']:10.3f}  {pm['mae']:11.3f}")

    print("\nREAD THIS BEFORE QUOTING ANY OF IT")
    print("  These measure projection accuracy given that the player PLAYED.")
    print("  They are not a lineup ROI, and they are not a props comparison --")
    print("  the historical props needed for that no longer exist. The props")
    print("  arm has to be rebuilt forward, from the dfs_nfl collector.")


if __name__ == "__main__":
    main()
