#!/usr/bin/env python3
"""Measure what an NFL DK projection's DOWNSIDE actually looks like.

WHY THIS EXISTS
edge/dfs_opt_nfl.py's cash mode maximised the sum of projections, which is not
a cash theory -- it is the GPP theory with the stack switched off. MLB has had
the distinction since the beginning: its cash optimiser maximises a
walk-rate-adjusted FLOOR, because a double-up pays the same for 1st and for
45th and the only thing that matters is clearing a line. NFL had no floor model
at all, so "cash" and "GPP" differed only in whether a stack was forced.

A floor needs a spread, and the spread was not known. This measures it.

WHAT IS MEASURED, AND WHY IT IS A QUANTILE RATHER THAN A SIGMA
The obvious move is to propagate edge/dfs_sport.py's per-stat sigmas into a
DK-point variance and call `proj - z*sd` the floor. That would be wrong twice
over. DK points are strongly right-skewed and zero-inflated -- a receiver's
modal bad game is 2-4 points, not a symmetric draw below his mean -- so a
normal's left tail is in the wrong place; and the per-stat sigmas describe
spread around a BOOK'S LINE, which is not the same quantity as spread around a
projection that came from somewhere else.

So the 25th percentile of the ACTUAL outcome is measured directly, against a
leak-free projection, and no distribution is assumed anywhere.

THE DISCIPLINE IS THE SAME AS EVERY OTHER FIT HERE
Projections are the shipped skill model (K=0, decay=0.90, the values
scripts/nfl_skill_backtest.py chose on its own train window) run strictly
forward: week w is predicted from weeks before w only. Seasons 2020 and 2021,
which are the seasons for which real DK salaries also survive -- see
scripts/nfl_dk_salary_collect.py for why that constraint exists.

    python3 scripts/nfl_variance_fit.py
    python3 scripts/nfl_variance_fit.py --seasons 2023,2024
"""
from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.dfs_project import points_from_means            # noqa: E402
from edge.dfs_sport import NFL                            # noqa: E402
from scripts.nfl_skill_backtest import (                  # noqa: E402
    CATEGORIES, POSITIONS, Running, load_rows)

#: The shipped skill hyperparameters, chosen on train in nfl_skill_backtest.
SKILL_K, SKILL_DECAY = 0.0, 0.90

#: Component labels (edge/dfs_sport.NFL Stat.name) that are touchdown points.
TD_LABELS = ("pass TD", "rush TD", "rec TD")


def walk(rows: list[dict], seasons: tuple[int, ...], min_week: int = 4) -> list[dict]:
    """Leak-free forward walk: every row projected from strictly earlier rows.

    `min_week` skips the opening weeks of the FIRST season only, where the
    accumulator holds almost nothing and a projection is really the position
    prior. Those rows are not representative of a live slate, where every
    player has a history behind him.
    """
    by_week = defaultdict(list)
    for r in rows:
        by_week[(r["season"], r["week"])].append(r)

    run = Running()
    out, first = [], min(seasons)
    for season, week in sorted(by_week):
        week_rows = by_week[(season, week)]
        if not (season == first and week < min_week):
            for r in week_rows:
                means = run.means(r, SKILL_K)
                res = points_from_means(means, {}, NFL, r["position"])
                proj = res["proj"] or 0.0
                if proj <= 0:
                    continue
                td = sum(v for k, v in (res.get("components") or {}).items()
                         if k in TD_LABELS)
                out.append({"position": r["position"], "proj": proj,
                            "actual": r["actual"], "td_share": td / proj,
                            "season": season, "week": week, "name": r["name"]})
        for r in week_rows:
            run.add(r)
        run.decay(SKILL_DECAY)
    return out


def _bins(graded: list[dict], n: int = 8) -> list[list[dict]]:
    """Equal-count bins by projection, so every bin carries real weight."""
    s = sorted(graded, key=lambda g: g["proj"])
    size = max(1, len(s) // n)
    return [s[i:i + size] for i in range(0, len(s), size)][:n]


def _wls(points: list[tuple[float, float, int]]) -> tuple[float, float]:
    """Weighted least squares slope/intercept for (x, y, weight) triples."""
    sw = sum(w for _, _, w in points)
    if sw == 0:
        return 0.0, 0.0
    mx = sum(x * w for x, _, w in points) / sw
    my = sum(y * w for _, y, w in points) / sw
    num = sum(w * (x - mx) * (y - my) for x, y, w in points)
    den = sum(w * (x - mx) ** 2 for x, _, w in points)
    b = num / den if den else 0.0
    return my - b * mx, b


def fit_position(graded: list[dict], q: float) -> dict:
    """Fits for both `quantile(actual)` and `sd(actual)` against the projection.

    The SD fit is the one the optimiser actually consumes. A per-player
    quantile cannot be summed into a lineup floor -- the 25th percentile of a
    sum is not the sum of 25th percentiles, and treating it as one throws away
    the diversification that is the entire reason a cash lineup spreads across
    games. So the optimiser carries a mean and an SD per player and combines
    them through the measured correlation matrix (edge/dfs_nfl_theory.py). The
    quantile fit stays because it is the direct, assumption-free read on what
    a downside actually looks like, and it is what the SD path is checked
    against.
    """
    rows = []
    for b in _bins(graded):
        if len(b) < 25:
            continue
        proj = statistics.fmean(g["proj"] for g in b)
        acts = sorted(g["actual"] for g in b)
        rows.append({
            "n": len(b), "proj": proj,
            "mean": statistics.fmean(acts),
            "q": acts[max(0, min(len(acts) - 1, int(q * len(acts))))],
            "sd": statistics.pstdev(acts),
        })
    alpha, beta = _wls([(r["proj"], r["q"], r["n"]) for r in rows])
    sd_a, sd_b = _wls([(r["proj"], r["sd"], r["n"]) for r in rows])
    return {"alpha": alpha, "beta": beta, "sd_a": sd_a, "sd_b": sd_b, "bins": rows}


def dst_spread(seasons: tuple[int, ...]) -> dict:
    """Mean and SD of actual DST DK points, measured rather than assumed.

    A defence has no player-week row and no prop market, so it is outside the
    skill model entirely: edge/dfs_project.project_dst builds it from the
    opponent's implied team total. What that projection does NOT come with is a
    spread, and a cash lineup has to price one for the DST slot like any other.

    This is the UNCONDITIONAL spread, and that is close to the right quantity
    here for a reason worth stating: the projection's own range across a slate
    is only a few points wide (the points-allowed tiers are coarse), so almost
    all of a defence's variance is within-projection rather than across it.
    """
    import json

    from edge.nfl import actual_dst_points

    gt = ROOT / "data" / "nfl_ground_truth"
    games = json.loads((gt / "games.json").read_text())
    games = games if isinstance(games, list) else games.get("rows", [])
    scored: dict[tuple, int] = {}
    for g in games:
        if g.get("season_type", g.get("game_type")) not in ("REG", None):
            continue
        try:
            s, w = int(g["season"]), int(g["week"])
            hs, as_ = int(g["home_score"]), int(g["away_score"])
        except (KeyError, TypeError, ValueError):
            continue
        scored[(s, w, g["home_team"])] = as_      # points ALLOWED by home
        scored[(s, w, g["away_team"])] = hs

    pts = []
    for season in seasons:
        path = gt / f"team_week_{season}.json"
        if not path.exists():
            continue
        raw = json.loads(path.read_text())
        rows = raw if isinstance(raw, list) else (raw.get("rows") or list(raw.values())[0])
        for r in rows:
            if r.get("season_type") != "REG":
                continue
            team = r.get("team") or r.get("recent_team")
            allowed = scored.get((int(r["season"]), int(r["week"]), team))
            if allowed is None:
                continue
            pts.append(actual_dst_points(r, allowed))
    if len(pts) < 30:
        return {"n": len(pts)}
    pts.sort()
    return {"n": len(pts), "mean": statistics.fmean(pts),
            "sd": statistics.pstdev(pts),
            "p25": pts[int(0.25 * len(pts))]}


def td_effect(graded: list[dict], q: float) -> list[dict]:
    """Does TD-heavy projection have a LOWER floor at the same projection?

    The hypothesis worth testing, and the NFL analogue of MLB's walk-rate
    floor: six points for a touchdown is a near-binary event, so two players
    projected identically can have very different downsides if one of them
    gets there through receptions and yardage and the other through TD equity.
    If this shows nothing, the floor is a function of the projection alone and
    the cash optimiser should not pretend otherwise.
    """
    out = []
    for b in _bins(graded, 5):
        if len(b) < 60:
            continue
        s = sorted(b, key=lambda g: g["td_share"])
        half = len(s) // 2
        row = {"n": len(b), "proj": statistics.fmean(g["proj"] for g in b)}
        for tag, part in (("low", s[:half]), ("high", s[half:])):
            acts = sorted(g["actual"] for g in part)
            row[f"{tag}_td"] = statistics.fmean(g["td_share"] for g in part)
            row[f"{tag}_q"] = acts[max(0, min(len(acts) - 1, int(q * len(acts))))]
            row[f"{tag}_mean"] = statistics.fmean(acts)
        out.append(row)
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", default="2020,2021")
    ap.add_argument("--quantile", type=float, default=0.25,
                    help="which percentile of the outcome IS the floor "
                         "(default: %(default)s)")
    args = ap.parse_args()

    seasons = tuple(int(x) for x in args.seasons.split(","))
    rows = load_rows(seasons)
    graded = walk(rows, seasons)
    print(f"{len(graded):,} leak-free player-week projections, seasons "
          f"{'/'.join(map(str, seasons))}, floor = p{args.quantile * 100:.0f}\n")

    print("PER POSITION -- the outcome quantile against the projection")
    fits = {}
    for pos in POSITIONS:
        sub = [g for g in graded if g["position"] == pos]
        f = fit_position(sub, args.quantile)
        fits[pos] = f
        print(f"\n  {pos}  n={len(sub):,}   "
              f"floor ~ {f['alpha']:+.2f} + {f['beta']:.3f} * proj   "
              f"sd ~ {f['sd_a']:.2f} + {f['sd_b']:.3f} * proj")
        print(f"    {'n':>5} {'proj':>7} {'mean':>7} {'p25':>7} {'sd':>7} {'p25/proj':>9}")
        for b in f["bins"]:
            print(f"    {b['n']:5,} {b['proj']:7.2f} {b['mean']:7.2f} "
                  f"{b['q']:7.2f} {b['sd']:7.2f} {b['q'] / b['proj']:9.2f}")

    print("\n\nDOES TOUCHDOWN SHARE LOWER THE FLOOR AT THE SAME PROJECTION?")
    print(f"  {'n':>6} {'proj':>7} | {'tdshare':>8} {'p25':>7} {'mean':>7} "
          f"| {'tdshare':>8} {'p25':>7} {'mean':>7} | {'p25 gap':>8}")
    for r in td_effect(graded, args.quantile):
        print(f"  {r['n']:6,} {r['proj']:7.2f} | {r['low_td']:8.2f} "
              f"{r['low_q']:7.2f} {r['low_mean']:7.2f} | {r['high_td']:8.2f} "
              f"{r['high_q']:7.2f} {r['high_mean']:7.2f} | "
              f"{r['high_q'] - r['low_q']:+8.2f}")

    dst = dst_spread(seasons)
    print("\n\nDST -- unconditional, from team box scores + points allowed")
    if dst.get("n", 0) >= 30:
        print(f"  n={dst['n']:,}  mean={dst['mean']:.2f}  sd={dst['sd']:.2f}  "
              f"p25={dst['p25']:.2f}")
    else:
        print(f"  not enough rows (n={dst.get('n', 0)})")

    print("\nCONSTANTS for edge/dfs_nfl_theory.py:")
    print("SD_FIT = {")
    for pos, f in fits.items():
        print(f'    "{pos}": ({f["sd_a"]:.3f}, {f["sd_b"]:.4f}),')
    if dst.get("n", 0) >= 30:
        print(f'    "DST": ({dst["sd"]:.3f}, 0.0),')
    print("}")
    print("FLOOR_FIT = {")
    for pos, f in fits.items():
        print(f'    "{pos}": ({f["alpha"]:+.3f}, {f["beta"]:.4f}),')
    print("}")


if __name__ == "__main__":
    main()
