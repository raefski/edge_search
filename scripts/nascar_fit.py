#!/usr/bin/env python3
"""Fit every measured constant edge/nascar_sim.py ships, from NASCAR's own feeds.

    python3 scripts/nascar_fit.py                         # everything, 2022-2026
    python3 scripts/nascar_fit.py --seasons 2023 2024 2025 2026
    python3 scripts/nascar_fit.py --what finish dnf
    python3 scripts/nascar_fit.py --validate              # simulate held-out races

WHY NASCAR IS FITTED AND NOT PRICED
Every other DFS model in this repo reads a sportsbook. This one cannot: three
of the four things DraftKings scores -- place differential, laps led, fastest
laps -- have no market at any book. So the model is built from results, and
these are the pieces it needs.

  finish   E[finish] from starting position and prior form, PER TRACK TYPE.
           The per-track-type split is not tidiness. It is the finding:
           starting position correlates 0.458 with finish at a short track and
           0.076 at a superspeedway, and the regression R^2 is 0.287 against
           0.029. A superspeedway is very close to a lottery and every model
           constant has to say so.

  dnf      P(did not finish), by track type and starting bucket. The largest
           single source of variance in a NASCAR lineup, and asymmetric: a
           wreck on lap 10 from 5th on the grid costs the finishing points AND
           the whole place differential at once.

  laps     How concentrated laps led and fastest laps are. Both are FIXED
           POOLS -- one race has exactly `actual_laps` laps to lead and
           `green_laps` fastest laps to win -- which is why they are fitted as
           a concentration and allocated in the simulator rather than
           projected per driver independently. Six drivers cannot all dominate.

PROVENANCE IS THE SAME FOR ALL OF THEM: NASCAR's public timing feeds,
2022-2026 Cup, ~6,400 scored driver-races, joined to the loop-data feed for
fastest laps. Form is computed from the driver's PREVIOUS twelve races only,
never including the race being predicted.
"""
from __future__ import annotations

import argparse
import collections
import math
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np                        # noqa: E402

from edge import nascar as N              # noqa: E402

TRACK_TYPES = ("superspeedway", "intermediate", "short", "road")
#: Rolling window for a driver's form. Twelve is about a third of a season --
#: long enough that a single wreck does not dominate it, short enough to track
#: a team that has found something.
FORM_WINDOW = 12
MIN_FORM_RACES = 4
#: Starting buckets for the DNF table. Deciles of a ~37-car field.
START_BUCKETS = ((1, 10), (11, 20), (21, 30), (31, 99))


def load(seasons: list[int], series: int = N.NASCAR_CUP) -> list[dict]:
    rows: list[dict] = []
    for season in seasons:
        print(f"  loading {season}…", flush=True)
        rows.extend(N.season_rows(season, series))
    rows = [r for r in rows if r["scored"]]
    rows.sort(key=lambda r: (r["season"], r["race_date"] or "", r["race_id"]))
    return rows


def add_form(rows: list[dict]) -> list[dict]:
    """Attach PRIOR-ONLY rolling form to each row, then drop rows without it.

    Leak-free by construction: a row's form is computed from the driver's
    earlier races and the row is appended to his history only afterwards. The
    same discipline scripts/ncaaf_fit.py uses a leave-one-out mean for, and for
    the same reason -- here it is strictly one-directional because a race has a
    date and a model only ever runs forwards.
    """
    hist: dict = collections.defaultdict(list)
    for r in rows:
        prev = hist[r["driver_id"]][-FORM_WINDOW:]
        if len(prev) >= MIN_FORM_RACES:
            r["form_finish"] = statistics.fmean(x["finish"] for x in prev)
            r["form_led"] = statistics.fmean(
                x["laps_led"] / max(1, x["actual_laps"]) for x in prev)
            r["form_fast"] = statistics.fmean(
                x["fast_laps"] / max(1, x["green_laps"]) for x in prev)
            r["form_dnf"] = statistics.fmean(1.0 if x["dnf"] else 0.0 for x in prev)
        hist[r["driver_id"]].append(r)
    return [r for r in rows
            if r.get("form_finish") is not None and r["start"] > 0]


# ---------------------------------------------------------------------------
def fit_finish(rows: list[dict]) -> dict:
    """finish ~ a + b*start + c*form_finish + d*form_led + e*form_dnf, per track."""
    out = {}
    print("\nFINISH  E[finish] ~ start, prior form")
    print(f"{'track':<15}{'n':>6}{'const':>8}{'start':>8}{'formFin':>9}"
          f"{'formLed':>9}{'formDNF':>9}{'R2':>7}{'resid sd':>10}")
    for t in TRACK_TYPES:
        sel = [r for r in rows if r["track_type"] == t]
        if len(sel) < 200:
            print(f"{t:<15}{len(sel):>6}   too few")
            continue
        X = np.array([[1.0, r["start"], r["form_finish"], r["form_led"],
                       r["form_dnf"]] for r in sel])
        y = np.array([float(r["finish"]) for r in sel])
        beta, *_ = np.linalg.lstsq(X, y, rcond=None)
        resid = y - X @ beta
        out[t] = {"beta": [round(float(b), 4) for b in beta],
                  "resid_sd": round(float(resid.std()), 3),
                  "r2": round(float(1 - resid.var() / y.var()), 4), "n": len(sel)}
        print(f"{t:<15}{len(sel):>6}{beta[0]:>8.2f}{beta[1]:>8.3f}{beta[2]:>9.3f}"
              f"{beta[3]:>9.2f}{beta[4]:>9.2f}{out[t]['r2']:>7.3f}"
              f"{out[t]['resid_sd']:>10.2f}")
    print("\nR^2 of 0.029 at a superspeedway is the finding, not a failure: "
          "pack racing\nmakes the running order close to a lottery, so a "
          "cheap car starting 35th is a\nreal play there and nowhere else.")
    return out


def fit_dnf(rows: list[dict]) -> dict:
    """P(DNF) by track type and starting bucket."""
    out: dict = {}
    print("\nDNF  P(did not finish)")
    print(f"{'track':<15}" + "".join(f"{f'P{a}-{b}':>10}" for a, b in START_BUCKETS)
          + f"{'overall':>10}")
    for t in TRACK_TYPES:
        sel = [r for r in rows if r["track_type"] == t]
        if not sel:
            continue
        cells, table = [], {}
        for lo, hi in START_BUCKETS:
            grp = [r for r in sel if lo <= r["start"] <= hi]
            p = (sum(1 for r in grp if r["dnf"]) / len(grp)) if grp else None
            table[f"{lo}-{hi}"] = round(p, 4) if p is not None else None
            cells.append(f"{100 * p:>9.1f}%" if p is not None else f"{'-':>10}")
        overall = sum(1 for r in sel if r["dnf"]) / len(sel)
        out[t] = {"buckets": table, "overall": round(overall, 4)}
        print(f"{t:<15}" + "".join(cells) + f"{100 * overall:>9.1f}%")
    print("\nAt a superspeedway the FRONT of the grid is the most dangerous "
          "place to be --\nthe wreck happens in the pack and the pack is at "
          "the front. Nowhere else does\nthe table run that way.")
    return out


def fit_laps(rows: list[dict]) -> dict:
    """How concentrated laps led and fastest laps are, per track type.

    Reported as the quantities a simulator has to reproduce rather than as a
    per-driver projection: the top finisher's share, the fraction of the field
    that leads at all, and the Gini of the lap-led distribution. Laps led and
    fastest laps are FIXED POOLS and the allocation has to respect that.
    """
    out: dict = {}
    print("\nLAPS LED and FASTEST LAPS -- concentration, not per-driver means")
    print(f"{'track':<15}{'races':>7}{'led: top1':>11}{'P(led>0)':>10}{'gini':>7}"
          f"{'fast: top1':>12}{'P(fast>0)':>11}{'corr(led,fast)':>16}")
    for t in TRACK_TYPES:
        sel = [r for r in rows if r["track_type"] == t]
        if not sel:
            continue
        by_race: dict = collections.defaultdict(list)
        for r in sel:
            by_race[r["race_id"]].append(r)
        led_top = [max(x["laps_led"] for x in v) / max(1, v[0]["actual_laps"])
                   for v in by_race.values()]
        fast_top = [max(x["fast_laps"] for x in v) / max(1, v[0]["green_laps"])
                    for v in by_race.values()]
        shares = [r["laps_led"] / max(1, r["actual_laps"]) for r in sel]
        fshares = [r["fast_laps"] / max(1, r["green_laps"]) for r in sel]
        out[t] = {
            "led_top1": round(statistics.fmean(led_top), 4),
            "led_any": round(sum(1 for s in shares if s > 0) / len(shares), 4),
            "led_gini": round(_gini(shares), 4),
            "fast_top1": round(statistics.fmean(fast_top), 4),
            "fast_any": round(sum(1 for s in fshares if s > 0) / len(fshares), 4),
            "fast_gini": round(_gini(fshares), 4),
            "corr_led_fast": round(_corr(shares, fshares), 4),
            "races": len(by_race),
        }
        o = out[t]
        print(f"{t:<15}{o['races']:>7}{o['led_top1']:>10.1%}{o['led_any']:>10.3f}"
              f"{o['led_gini']:>7.3f}{o['fast_top1']:>11.1%}{o['fast_any']:>11.3f}"
              f"{o['corr_led_fast']:>16.3f}")
    print("\ncorr(led, fast) is 0.77 everywhere EXCEPT a superspeedway, where "
          "it is 0.01.\nAt Daytona the fastest lap is set by whoever happens "
          "to be in clean air on that\nlap, which has nothing to do with who "
          "is leading -- so a superspeedway\ndominator does not exist and a "
          "simulator that assumes one is wrong twice over.")
    return out


def _gini(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    total = sum(s)
    if n == 0 or total <= 0:
        return 0.0
    cum = 0.0
    weighted = 0.0
    for i, v in enumerate(s, start=1):
        cum += v
        weighted += cum
    return (n + 1 - 2 * weighted / total) / n


def _corr(xs, ys) -> float:
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    num = sum((a - mx) * (b - my) for a, b in zip(xs, ys))
    den = math.sqrt(sum((a - mx) ** 2 for a in xs) * sum((b - my) ** 2 for b in ys))
    return num / den if den else float("nan")


def fit_salary(rows: list[dict]) -> None:
    """How much a driver's DK score varies, as a function of nothing at all.

    Reported so the optimiser's spread has a sanity check: the distribution of
    a NASCAR driver's DK points is very wide and very skewed, and a lineup of
    six of them is not a normal.
    """
    print("\nDK POINT DISTRIBUTION per driver-race")
    print(f"{'track':<15}{'n':>7}{'mean':>8}{'sd':>8}{'p10':>8}{'p50':>8}"
          f"{'p90':>8}{'p99':>8}{'min':>8}")
    for t in TRACK_TYPES:
        sel = sorted(r["dk"] for r in rows if r["track_type"] == t)
        if len(sel) < 50:
            continue
        def q(p):
            return sel[min(len(sel) - 1, int(p * len(sel)))]
        print(f"{t:<15}{len(sel):>7}{statistics.fmean(sel):>8.1f}"
              f"{statistics.pstdev(sel):>8.1f}{q(.10):>8.1f}{q(.50):>8.1f}"
              f"{q(.90):>8.1f}{q(.99):>8.1f}{min(sel):>8.1f}")
    print("\nThe minimum is strongly negative -- an expensive car that starts "
          "near the front\nand wrecks loses the whole place differential. That "
          "asymmetry is why cash and\nGPP diverge so hard in this sport.")


def validate(rows: list[dict], seasons_out: list[int]) -> None:
    """Simulate held-out races and compare the simulated field to the real one.

    The only check that tests the SIMULATOR rather than its inputs. Fitting on
    one set of seasons and simulating another is what stops a well-fitted set
    of marginals from hiding a joint distribution that is wrong.
    """
    from edge import nascar_sim

    held = [r for r in rows if r["season"] in seasons_out]
    by_race: dict = collections.defaultdict(list)
    for r in held:
        by_race[r["race_id"]].append(r)
    print(f"\nVALIDATE  {len(by_race)} held-out races from {seasons_out}")
    print(f"{'':<15}{'sim mean':>10}{'real mean':>11}{'sim sd':>9}{'real sd':>9}"
          f"{'sim p90':>9}{'real p90':>10}")

    sims, reals = [], []
    for race_rows in by_race.values():
        drivers = [{
            "driver_id": r["driver_id"], "name": r["driver"], "start": r["start"],
            "form_finish": r["form_finish"], "form_led": r["form_led"],
            "form_fast": r["form_fast"], "form_dnf": r["form_dnf"],
        } for r in race_rows]
        meta = {"track_type": race_rows[0]["track_type"],
                "actual_laps": race_rows[0]["actual_laps"],
                "green_laps": race_rows[0]["green_laps"]}
        scores = nascar_sim.simulate(drivers, meta, n_sims=300, seed=7)
        sims.append(scores)
        reals.extend(r["dk"] for r in race_rows)

    allsim = np.concatenate([s.ravel() for s in sims])
    reals = np.array(reals, dtype=float)
    print(f"{'DK points':<15}{allsim.mean():>10.2f}{reals.mean():>11.2f}"
          f"{allsim.std():>9.2f}{reals.std():>9.2f}"
          f"{np.percentile(allsim, 90):>9.2f}{np.percentile(reals, 90):>10.2f}")
    print("\nThe simulator is meant to reproduce the SHAPE of a driver-race, "
          "not to beat it.\nA mean within a point or two and an sd within "
          "10% is the bar; a simulated sd\nthat is too NARROW is the "
          "dangerous direction, because it makes every GPP\nlineup look safer "
          "than it is.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", type=int, nargs="+",
                    default=[2022, 2023, 2024, 2025, 2026])
    ap.add_argument("--what", nargs="+",
                    default=["finish", "dnf", "laps", "dist"],
                    choices=["finish", "dnf", "laps", "dist"])
    ap.add_argument("--validate", action="store_true",
                    help="also simulate the last season and compare")
    args = ap.parse_args()

    print(f"NASCAR Cup, seasons {args.seasons}")
    rows = add_form(load(args.seasons))
    print(f"  {len(rows)} scored driver-races with prior form")

    if "finish" in args.what:
        fit_finish(rows)
    if "dnf" in args.what:
        fit_dnf(rows)
    if "laps" in args.what:
        fit_laps(rows)
    if "dist" in args.what:
        fit_salary(rows)
    if args.validate:
        validate(rows, [max(args.seasons)])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
