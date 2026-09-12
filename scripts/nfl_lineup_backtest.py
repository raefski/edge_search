#!/usr/bin/env python3
"""Do the two NFL game theories actually build the lineups they claim to?

THE QUESTION THIS ANSWERS, AND THE ONE IT DOES NOT
Answers: given a real DK board and a leak-free projection, does the CASH
construction produce a higher floor than the GPP construction, and does the GPP
construction produce a higher ceiling than the CASH one? That is a claim about
lineup construction and it is falsifiable on historical slates.

Does NOT answer: is this profitable. There is no real contest field here, no
rake, no payout curve, and 34 slates could not settle an ROI question if there
were. Do not quote anything below as an ROI. The field used for percentiles is
a SIMULATED one built from this repo's own unvalidated ownership prior, so a
percentile here is a construction-comparison device and not a prediction of
where a lineup would have finished on DraftKings.

WHY THESE SEASONS
2020 and 2021, because they are the intersection of the two things a lineup
backtest needs and this machine has: real DK salaries (RotoGuru, which stops
after 2021 -- scripts/nfl_dk_salary_collect.py) and nflverse player-weeks
(which start at 2020). The paid historical props that would have allowed the
LIVE projection to be replayed are gone (DFS_STATUS.md), so the projection here
is the skill model, not the props model. That is a real difference and it is
stated rather than hidden: what is being tested is the CONSTRUCTION, holding
the projection fixed, which is exactly the comparison the two modes are a
choice between.

WHAT THE POOL IS
Every DK-priced player that week, which is the full weekly slate rather than a
12-game Sunday main slate. That makes both constructions' jobs slightly easier
in the same way -- more players to choose from -- and does not favour either,
which is what matters for a head-to-head.

THE BENCHMARK THAT MATTERS IS `maxproj`
It is the behaviour cash mode had before edge/dfs_nfl_theory.py existed:
maximise the sum of projections, no variance term. If neither new mode beats
it at its own objective, the rewrite bought nothing and should be reverted.

    python3 scripts/nfl_lineup_backtest.py
    python3 scripts/nfl_lineup_backtest.py --seasons 2021 --iters 200
"""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import dfs_nfl_theory as theory                # noqa: E402
from edge import dfs_opt_nfl as opt                      # noqa: E402
from edge.dfs_project import points_from_means           # noqa: E402
from edge.dfs_roster import assign_slots                 # noqa: E402
from edge.dfs_sport import NFL                           # noqa: E402
from edge.names import norm                              # noqa: E402
from edge.nfl import actual_dst_points, actual_offense_points   # noqa: E402
from scripts.nfl_skill_backtest import CATEGORIES, Running, load_rows  # noqa: E402
from scripts.nfl_variance_fit import SKILL_DECAY, SKILL_K          # noqa: E402

GT = ROOT / "data" / "nfl_ground_truth"
SAL = ROOT / "data" / "nfl_dk_salaries"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------
def load_salaries(seasons) -> dict:
    """{(season, week): [salary rows]}, from scripts/nfl_dk_salary_collect.py."""
    out = defaultdict(list)
    for season in seasons:
        path = SAL / f"dk_salaries_{season}.json"
        if not path.exists():
            raise SystemExit(
                f"missing {path}. Run: python3 scripts/nfl_dk_salary_collect.py "
                f"--seasons {season}")
        for r in json.loads(path.read_text()):
            out[(r["season"], r["week"])].append(r)
    return out


def load_dst_actuals(seasons) -> dict:
    """{(season, week, team): DK points} for defences, from team box scores."""
    games = json.loads((GT / "games.json").read_text())
    games = games if isinstance(games, list) else games.get("rows", [])
    allowed = {}
    for g in games:
        try:
            s, w = int(g["season"]), int(g["week"])
            hs, as_ = int(g["home_score"]), int(g["away_score"])
        except (KeyError, TypeError, ValueError):
            continue
        allowed[(s, w, g["home_team"])] = as_
        allowed[(s, w, g["away_team"])] = hs

    out = {}
    for season in seasons:
        path = GT / f"team_week_{season}.json"
        if not path.exists():
            continue
        raw = json.loads(path.read_text())
        rows = raw if isinstance(raw, list) else (raw.get("rows") or list(raw.values())[0])
        for r in rows:
            if r.get("season_type") != "REG":
                continue
            team = r.get("team") or r.get("recent_team")
            key = (int(r["season"]), int(r["week"]), team)
            pa = allowed.get(key)
            if pa is not None:
                out[key] = actual_dst_points(r, pa)
    return out


# ---------------------------------------------------------------------------
# One slate
# ---------------------------------------------------------------------------
def build_week_pool(sal_rows, run, dst_run, actuals, dst_actuals, season, week):
    """The DK board for one week: salary, leak-free projection, and the actual.

    `run` holds only weeks strictly before this one -- the caller guarantees it
    by adding the week AFTER scoring it, the same discipline
    scripts/nfl_skill_backtest.py's loop uses.
    """
    pool, missing = [], 0
    for r in sal_rows:
        pos, team = r["position"], r["team"]
        game = f"{season}-{week}-" + "-".join(sorted((team, r["opp"])))
        common = {"name": r["name"], "salary": r["salary"], "team": team,
                  "opp_team": r["opp"], "game": game, "dk_pos": pos,
                  "rotoguru_points": r.get("rotoguru_points")}

        if pos == "DST":
            actual = dst_actuals.get((season, week, team))
            if actual is None:
                missing += 1
                continue
            hist = dst_run.get(team)
            # A defence has no player row and, historically, no game line to
            # read an implied team total off -- the live path's project_dst
            # input simply does not exist for 2021. Its own decayed average is
            # the honest stand-in, and it is the SAME projection for both
            # modes, so it cannot tilt the head-to-head either way.
            proj = (hist["pts"] / hist["n"]) if hist and hist["n"] else 6.0
            pool.append({**common, "pos": {"DST"}, "proj": round(proj, 2),
                         "actual": actual})
            continue

        key = norm(r["name"])
        row = actuals.get((season, week, key))
        if row is None:
            missing += 1
            continue
        means = run.means({"player_id": row["player_id"], "position": pos},
                          SKILL_K)
        proj = points_from_means(means, {}, NFL, pos)["proj"] or 0.0
        if proj <= 0:
            continue
        pool.append({**common, "pos": opt.eligible_slots(pos),
                     "proj": round(proj, 2), "actual": row["actual"]})
    return pool, missing


def score(result) -> float | None:
    """What a lineup ACTUALLY scored. The only number that grades anything."""
    if not result:
        return None
    return round(sum(p["actual"] for p, _ in result["lineup"]), 2)


def max_proj_lineup(pool, iters, seed):
    """The pre-rewrite cash mode: maximise the sum of projections, full stop.

    Implemented by handing the optimizer a zero variance term rather than by
    keeping a second search, so the ONLY difference from cash mode is the
    objective -- same fill, same climb, same legality rules.
    """
    return opt.optimize(pool, mode="cash", iters=iters, seed=seed, z_cash=0.0)


def perfect_lineup(pool):
    """The best lineup in hindsight. A yardstick, not a target."""
    best = opt.optimize([{**p, "proj": p["actual"]} for p in pool],
                        mode="cash", iters=600, seed=0, z_cash=0.0)
    return None if best is None else round(
        sum(p["proj"] for p, _ in best["lineup"]), 2)


def field_scores(pool, n, rng) -> list[float]:
    """A simulated field of `n` lineups, sampled by projected ownership.

    A PROXY, and the weakest link in this script -- the ownership model behind
    it is an unvalidated prior (edge/dfs_nfl_theory.py). It is here because a
    raw DK total means nothing on its own: 140 points is a winning score on one
    slate and a losing one on another, and a percentile against a consistent
    field is what makes two weeks comparable. Sampling by ownership rather than
    uniformly matters: a uniform field is far weaker than a real one and would
    flatter everything measured against it.
    """
    by_slot = defaultdict(list)
    for p in pool:
        for slot in p["pos"]:
            by_slot[slot].append(p)
    out = []
    for _ in range(n):
        for _attempt in range(12):
            picked, used, spend = [], set(), 0
            ok = True
            for slot in opt.SLOTS:
                cands = [p for p in by_slot[slot] if p["name"] not in used
                         and spend + p["salary"] <= opt.CAP]
                if not cands:
                    ok = False
                    break
                weights = [max(0.01, p.get("own", 1.0)) for p in cands]
                pick = rng.choices(cands, weights=weights, k=1)[0]
                picked.append(pick)
                used.add(pick["name"])
                spend += pick["salary"]
            if ok and len(picked) == len(opt.SLOTS) \
                    and assign_slots(picked, opt.SLOTS) is not None:
                out.append(sum(p["actual"] for p in picked))
                break
    return sorted(out)


def percentile_of(value: float, field: list[float]) -> float:
    if not field:
        return float("nan")
    below = sum(1 for f in field if f < value)
    return 100.0 * below / len(field)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def quantile(xs, q):
    if not xs:
        return float("nan")
    s = sorted(xs)
    return s[max(0, min(len(s) - 1, int(q * len(s))))]


def paired_t(a: list[float], b: list[float]) -> tuple[float, int]:
    """Paired t on the per-slate differences. Same slates, so pair them."""
    d = [x - y for x, y in zip(a, b)]
    n = len(d)
    if n < 3:
        return float("nan"), n
    m = statistics.fmean(d)
    sd = statistics.stdev(d)
    return (m / (sd / math.sqrt(n)) if sd else float("inf")), n


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", default="2020,2021")
    ap.add_argument("--min-week", type=int, default=4,
                    help="skip the opening weeks, where the projection is "
                         "mostly a position prior (default: %(default)s)")
    ap.add_argument("--iters", type=int, default=300)
    ap.add_argument("--field", type=int, default=400,
                    help="simulated field size per slate")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--z-grid", default=None,
                    help="comma-separated Z_CASH values to sweep instead of "
                         "running the full head-to-head. 0.0 IS the "
                         "pre-rewrite behaviour and must be in any grid, so "
                         "'no floor term' has to win rather than be assumed "
                         "away. Reported train (first season) / test (rest).")
    args = ap.parse_args()

    seasons = tuple(int(x) for x in args.seasons.split(","))
    rng = random.Random(args.seed)

    salaries = load_salaries(seasons)
    dst_actuals = load_dst_actuals(seasons)
    rows = load_rows(seasons)
    actuals = {(r["season"], r["week"], norm(r["name"])): r for r in rows}
    by_week = defaultdict(list)
    for r in rows:
        by_week[(r["season"], r["week"])].append(r)

    print(f"{len(rows):,} player-weeks, {sum(len(v) for v in salaries.values()):,} "
          f"DK-priced rows, seasons {'/'.join(map(str, seasons))}\n")

    run = Running()
    dst_run: dict = {}
    results = []
    rg_check: list[tuple[float, float]] = []

    for (season, week) in sorted(salaries):
        sal_rows = salaries[(season, week)]
        if (season, week) not in by_week:
            continue
        if season == min(seasons) and week < args.min_week:
            for r in by_week[(season, week)]:
                run.add(r)
            run.decay(SKILL_DECAY)
            for team, pts in _dst_week(dst_actuals, season, week):
                _dst_add(dst_run, team, pts)
            continue

        pool, missing = build_week_pool(sal_rows, run, dst_run, actuals,
                                        dst_actuals, season, week)
        if len(pool) >= 60 and args.z_grid:
            theory.add_ownership(pool)
            field = field_scores(pool, args.field, rng)
            row = {"season": season, "week": week}
            for z in (float(x) for x in args.z_grid.split(",")):
                r = opt.optimize(pool, mode="cash", iters=args.iters,
                                 seed=args.seed, z_cash=z)
                sc = score(r)
                row[f"z{z}"] = sc
                row[f"z{z}_pct"] = percentile_of(sc, field) if sc is not None else None
            if all(v is not None for v in row.values()):
                results.append(row)
                print(f"  {season} wk{week:>2}  " + "  ".join(
                    f"z={z}:{row[f'z{z}']:6.1f}(p{row[f'z{z}_pct']:4.1f})"
                    for z in (float(x) for x in args.z_grid.split(","))))
        elif len(pool) >= 60:
            theory.add_ownership(pool)
            for p in pool:
                if p.get("rotoguru_points") is not None:
                    rg_check.append((p["actual"], p["rotoguru_points"]))

            cash = opt.optimize(pool, mode="cash", iters=args.iters, seed=args.seed)
            gpp = opt.optimize(pool, mode="gpp", iters=args.iters, seed=args.seed)
            mx = max_proj_lineup(pool, args.iters, args.seed)
            field = field_scores(pool, args.field, rng)

            row = {"season": season, "week": week, "pool": len(pool),
                   "missing": missing, "perfect": perfect_lineup(pool),
                   "field_med": quantile(field, 0.50),
                   "field_p99": quantile(field, 0.99)}
            for tag, res in (("cash", cash), ("gpp", gpp), ("maxproj", mx)):
                s = score(res)
                row[tag] = s
                row[f"{tag}_pct"] = percentile_of(s, field) if s is not None else None
                row[f"{tag}_sd"] = res["sd"] if res else None
                row[f"{tag}_stack"] = bool(res and res.get("stack"))
            if all(row[t] is not None for t in ("cash", "gpp", "maxproj")):
                results.append(row)
                print(f"  {season} wk{week:>2}  pool {len(pool):>3}  "
                      f"cash {row['cash']:6.1f} (p{row['cash_pct']:4.1f})  "
                      f"gpp {row['gpp']:6.1f} (p{row['gpp_pct']:4.1f})  "
                      f"maxproj {row['maxproj']:6.1f} (p{row['maxproj_pct']:4.1f})  "
                      f"field med {row['field_med']:6.1f}")

        for r in by_week[(season, week)]:
            run.add(r)
        run.decay(SKILL_DECAY)
        for team, pts in _dst_week(dst_actuals, season, week):
            _dst_add(dst_run, team, pts)

    if len(results) < 5:
        print("\nnot enough graded slates to report", file=sys.stderr)
        return 1

    if args.z_grid:
        _report_sweep(results, [float(x) for x in args.z_grid.split(",")],
                      min(seasons))
    else:
        _report(results, rg_check)
    return 0


def _report_sweep(results, zs, train_season):
    """Which floor weight, if any, earns its place -- split train/test.

    Choosing z on the same slates the result is quoted from would be the
    overfit this repo keeps warning about, so the grid is read on the first
    season and the winner is reported on the rest.
    """
    train = [r for r in results if r["season"] == train_season]
    test = [r for r in results if r["season"] != train_season]
    for label, rows in (("TRAIN " + str(train_season), train),
                        ("TEST (held out)", test)):
        if len(rows) < 5:
            continue
        print(f"\n{label} -- {len(rows)} slates")
        print(f"  {'z_cash':>7}{'mean':>8}{'sd':>7}{'p10 pct':>9}"
              f"{'mean pct':>10}{'>=50th':>9}{'>=90th':>9}")
        for z in zs:
            sc = [r[f"z{z}"] for r in rows]
            pc = [r[f"z{z}_pct"] for r in rows]
            print(f"  {z:7.2f}{statistics.fmean(sc):8.1f}"
                  f"{statistics.stdev(sc):7.1f}{quantile(pc, .10):9.1f}"
                  f"{statistics.fmean(pc):10.1f}"
                  f"{100 * sum(x >= 50 for x in pc) / len(pc):8.0f}%"
                  f"{100 * sum(x >= 90 for x in pc) / len(pc):8.0f}%")
    print("\n  >=50th is the CASH-relevant column: a double-up pays the same")
    print("  for 1st and for 45th, so clearing the line is the whole game.")


def _dst_week(dst_actuals, season, week):
    for (s, w, team), pts in dst_actuals.items():
        if s == season and w == week:
            yield team, pts


def _dst_add(dst_run, team, pts):
    b = dst_run.setdefault(team, {"n": 0.0, "pts": 0.0})
    b["n"] = b["n"] * SKILL_DECAY + 1
    b["pts"] = b["pts"] * SKILL_DECAY + pts


def _report(results: list[dict], rg_check):
    n = len(results)
    print(f"\n\n{'=' * 74}\n{n} GRADED SLATES\n{'=' * 74}")

    if rg_check:
        a = [x for x, _ in rg_check]
        b = [y for _, y in rg_check]
        try:
            r = statistics.correlation(a, b)
        except statistics.StatisticsError:
            r = float("nan")
        print(f"\nJOIN CHECK: our DK scoring vs RotoGuru's own, {len(rg_check):,} "
              f"player-weeks, corr {r:.4f}")
        print("  (RotoGuru's column is never the ground truth here -- this is "
              "only a check that the join and the scorer agree.)")

    print(f"\n{'':>9}{'mean':>8}{'sd':>7}{'p10':>8}{'p25':>8}{'p50':>8}"
          f"{'p75':>8}{'p90':>8}")
    for tag in ("cash", "gpp", "maxproj"):
        xs = [r[tag] for r in results]
        print(f"  {tag:>7}{statistics.fmean(xs):8.1f}{statistics.stdev(xs):7.1f}"
              f"{quantile(xs, .10):8.1f}{quantile(xs, .25):8.1f}"
              f"{quantile(xs, .50):8.1f}{quantile(xs, .75):8.1f}"
              f"{quantile(xs, .90):8.1f}")
    fm = [r["field_med"] for r in results]
    print(f"  {'field':>7}{statistics.fmean(fm):8.1f}{statistics.stdev(fm):7.1f}"
          f"{quantile(fm, .10):8.1f}{quantile(fm, .25):8.1f}"
          f"{quantile(fm, .50):8.1f}{quantile(fm, .75):8.1f}"
          f"{quantile(fm, .90):8.1f}")

    print("\nPERCENTILE WITHIN THE SIMULATED FIELD (a construction comparison, "
          "NOT a finish)")
    print(f"{'':>9}{'mean':>8}{'p10':>8}{'p25':>8}{'p50':>8}{'p90':>8}"
          f"{'>=50th':>9}{'>=90th':>9}{'>=99th':>9}")
    for tag in ("cash", "gpp", "maxproj"):
        xs = [r[f"{tag}_pct"] for r in results]
        print(f"  {tag:>7}{statistics.fmean(xs):8.1f}{quantile(xs, .10):8.1f}"
              f"{quantile(xs, .25):8.1f}{quantile(xs, .50):8.1f}"
              f"{quantile(xs, .90):8.1f}"
              f"{100 * sum(x >= 50 for x in xs) / len(xs):8.0f}%"
              f"{100 * sum(x >= 90 for x in xs) / len(xs):8.0f}%"
              f"{100 * sum(x >= 99 for x in xs) / len(xs):8.0f}%")

    print("\nTHE TWO CLAIMS, TESTED ON THE SAME SLATES (paired t)")
    cash_s = [r["cash"] for r in results]
    gpp_s = [r["gpp"] for r in results]
    mx_s = [r["maxproj"] for r in results]

    t, _ = paired_t([r["cash_pct"] for r in results],
                    [r["gpp_pct"] for r in results])
    print(f"  1. CASH has the better TYPICAL slate than GPP")
    print(f"     mean field-percentile  cash {statistics.fmean([r['cash_pct'] for r in results]):.1f}"
          f"  vs gpp {statistics.fmean([r['gpp_pct'] for r in results]):.1f}   t={t:+.2f}")
    print(f"     bad-slate floor (p10)  cash {quantile(cash_s, .10):.1f}"
          f"  vs gpp {quantile(gpp_s, .10):.1f}")

    print(f"  2. GPP reaches the TOP of the field more often than CASH")
    for thresh in (90, 95, 99):
        c = 100 * sum(r["cash_pct"] >= thresh for r in results) / n
        g = 100 * sum(r["gpp_pct"] >= thresh for r in results) / n
        print(f"     >= {thresh}th percentile:  cash {c:5.1f}%   gpp {g:5.1f}%")

    t_cash, _ = paired_t(cash_s, mx_s)
    t_gpp, _ = paired_t(gpp_s, mx_s)
    print(f"  3. Against the PRE-REWRITE behaviour (maxproj)")
    print(f"     cash - maxproj  mean {statistics.fmean(cash_s) - statistics.fmean(mx_s):+.2f} "
          f"DK pts   t={t_cash:+.2f}")
    print(f"     gpp  - maxproj  mean {statistics.fmean(gpp_s) - statistics.fmean(mx_s):+.2f} "
          f"DK pts   t={t_gpp:+.2f}")

    print("\nCONSTRUCTION PROPERTIES (what the objectives were supposed to do)")
    for tag in ("cash", "gpp", "maxproj"):
        sds = [r[f"{tag}_sd"] for r in results if r[f"{tag}_sd"]]
        stacked = 100 * sum(r[f"{tag}_stack"] for r in results) / n
        realised = statistics.stdev([r[tag] for r in results])
        print(f"  {tag:>7}  modelled lineup sd {statistics.fmean(sds):5.1f}   "
              f"realised week-to-week sd {realised:5.1f}   stacked {stacked:3.0f}% of slates")

    print(f"\n  perfect (hindsight) mean {statistics.fmean([r['perfect'] for r in results]):.1f}")
    print("\nREAD THIS BEFORE QUOTING ANY OF IT")
    print("  Construction comparison only. No rake, no real field, no payout")
    print("  curve, and the projection is the SKILL model -- the live app runs")
    print("  on props. These numbers say which construction does what, not")
    print("  whether either makes money.")


if __name__ == "__main__":
    raise SystemExit(main())
