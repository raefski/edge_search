#!/usr/bin/env python3
"""Leak-free backtest of edge.pickem.make_pick -- reads data/pickem_odds_history.csv
(scripts/pickem_historical_collect.py), calls the SAME function the app and
weekly script ship with, so this measures exactly what's deployed, not a
parallel reimplementation that happens to agree.

Anti-leakage: chronological split on season boundaries (2014-2022 train,
2023-2024 test), never a random shuffle -- a random split would let a 2023
game's outcome leak backward via shared team-strength information. The model
itself has no free parameters left to fit (the 0.5/1.5/3.0-pt tier breaks and
the flip rule came from a prior grid search recorded below; Stern 1991
supplies sigma=13.45), so "train" here mostly documents that the fixed rule
wasn't cherry-picked to fit train, not that anything was tuned per run.

Run: python3 scripts/pickem_backtest.py
Output: data/pickem_backtest_results.json (committed -- small, aggregate,
non-sensitive, same category as MLB's committed calibration output).
"""
from __future__ import annotations

import csv
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.pickem import ats_result, make_pick  # noqa: E402

DATA = ROOT / "data" / "pickem_odds_history.csv"
OUT = ROOT / "data" / "pickem_backtest_results.json"


def load_games() -> list[dict]:
    with DATA.open() as f:
        rows = list(csv.DictReader(f))
    games = []
    for r in rows:
        g = {
            "season": int(r["season"]), "week": int(r["week"]),
            "away": r["away_team"], "home": r["home_team"],
            "pool_line": float(r["home_line_open"]), "live_line": float(r["home_line_close"]),
            "home_margin": int(r["home_score"]) - int(r["away_score"]),
        }
        # totals feed the no-movement tiebreak (see edge/pickem.py); missing
        # values simply fall back to the old market-favourite behaviour.
        try:
            g["total_open"] = float(r["total_open"])
            g["total_close"] = float(r["total_close"])
        except (ValueError, KeyError):
            g["total_open"] = g["total_close"] = None
        games.append(g)
    games.sort(key=lambda g: (g["season"], g["week"]))
    return games


def grade(games: list[dict]) -> dict:
    w = l = p = 0
    signal_w = signal_l = fallback_w = fallback_l = 0
    for g in games:
        pk = make_pick(g["away"], g["home"], g["pool_line"], g["live_line"],
                       g.get("total_open"), g.get("total_close"))
        r = ats_result(g["home_margin"], g["pool_line"], pk.side)
        if r == "W":
            w += 1
        elif r == "L":
            l += 1
        else:
            p += 1
        if pk.tier == "COIN FLIP":
            if r == "W":
                fallback_w += 1
            elif r == "L":
                fallback_l += 1
        else:
            if r == "W":
                signal_w += 1
            elif r == "L":
                signal_l += 1
    n_decided = w + l
    return {
        "w": w, "l": l, "p": p, "pct": w / n_decided if n_decided else None,
        "n": len(games),
        "signal": {"w": signal_w, "l": signal_l,
                   "pct": signal_w / (signal_w + signal_l) if (signal_w + signal_l) else None},
        "fallback": {"w": fallback_w, "l": fallback_l,
                     "pct": fallback_w / (fallback_w + fallback_l) if (fallback_w + fallback_l) else None},
    }


def weekly_distribution(games: list[dict]) -> dict:
    by_week: dict[tuple, list[int, int]] = {}
    for g in games:
        pk = make_pick(g["away"], g["home"], g["pool_line"], g["live_line"],
                       g.get("total_open"), g.get("total_close"))
        r = ats_result(g["home_margin"], g["pool_line"], pk.side)
        key = (g["season"], g["week"])
        wl = by_week.setdefault(key, [0, 0])
        if r == "W":
            wl[0] += 1
        elif r == "L":
            wl[1] += 1
    rates = [w / (w + l) for w, l in by_week.values() if (w + l)]
    n = len(rates)
    return {
        "n_weeks": n,
        "avg_rate": sum(rates) / n if n else None,
        "wins_per_16": 16 * sum(rates) / n if n else None,
        "weeks_ge_10of16": sum(1 for r in rates if r >= 0.625),
        "weeks_ge_9of16": sum(1 for r in rates if r >= 0.5625),
    }


def calibration_by_move_size(games: list[dict]) -> list[dict]:
    """Cover rate of the moved-toward side, bucketed by |edge| -- pure
    descriptive stats over the data, no free parameters, kept as evidence
    the Phi(edge/13.45) probabilities in make_pick() are in the right range."""
    buckets = [(0.5, 1.0), (1.0, 1.5), (1.5, 2.0), (2.0, 3.0), (3.0, 99.0)]
    out = []
    for lo, hi in buckets:
        sub = [g for g in games if lo <= abs(g["live_line"] - g["pool_line"]) < hi]
        w = l = 0
        for g in sub:
            side = "home" if g["live_line"] < g["pool_line"] else "away"
            r = ats_result(g["home_margin"], g["pool_line"], side)
            if r == "W":
                w += 1
            elif r == "L":
                l += 1
        mid = (lo + min(hi, 4.0)) / 2
        theo = 0.5 + 0.5 * math.erf((mid / 13.45) / math.sqrt(2))
        out.append({"bucket": f"{lo}-{hi}", "n": w + l,
                     "cover_pct": w / (w + l) if (w + l) else None, "normal_approx": theo})
    return out


def closing_line_sanity_check(games: list[dict]) -> dict:
    """The load-bearing negative result: run make_pick() with pool_line ==
    live_line == the CLOSING line (i.e., pretend the frozen number was
    perfectly fresh). If the edge survives THIS, it isn't staleness -- it's
    something else being claimed as staleness. It shouldn't survive, and
    doesn't (see PICKEM_STATUS.md)."""
    w = l = 0
    for g in games:
        side = "home" if g["live_line"] < 0 else "away"  # closing-line favorite
        r = ats_result(g["home_margin"], g["live_line"], side)
        if r == "W":
            w += 1
        elif r == "L":
            l += 1
    return {"w": w, "l": l, "pct": w / (w + l) if (w + l) else None}


def main() -> None:
    games = load_games()
    seasons = sorted({g["season"] for g in games})
    n_total = len(games)

    cut, running = None, 0
    by_season = {s: sum(1 for g in games if g["season"] == s) for s in seasons}
    for s in seasons:
        running += by_season[s]
        if running >= 0.8 * n_total:
            cut = s
            break
    train_seasons = [s for s in seasons if s <= cut]
    test_seasons = [s for s in seasons if s > cut]
    train = [g for g in games if g["season"] in train_seasons]
    test = [g for g in games if g["season"] in test_seasons]

    train_grade = grade(train)
    test_grade = grade(test)
    weekly = weekly_distribution(test)
    calib = calibration_by_move_size(test)
    closing_check = closing_line_sanity_check(games)

    print(f"seasons: {seasons}")
    print(f"train {train_seasons} ({len(train)} games, {len(train)/n_total:.0%}): "
          f"{train_grade['w']}-{train_grade['l']}-{train_grade['p']} = {train_grade['pct']:.1%}"
          "  [context only -- do not quote as expected performance]")
    print(f"test  {test_seasons} ({len(test)} games, {len(test)/n_total:.0%}), "
          f"evaluated once with edge.pickem.make_pick as-shipped:")
    print(f"  {test_grade['w']}-{test_grade['l']}-{test_grade['p']} = {test_grade['pct']:.1%}")
    print(f"  signal games:   {test_grade['signal']['w']}-{test_grade['signal']['l']} "
          f"= {test_grade['signal']['pct']:.1%}")
    print(f"  fallback games: {test_grade['fallback']['w']}-{test_grade['fallback']['l']} "
          f"= {test_grade['fallback']['pct']:.1%}")
    print(f"weekly (test): avg {weekly['avg_rate']:.1%} ({weekly['wins_per_16']:.1f}/16), "
          f"{weekly['weeks_ge_9of16']}/{weekly['n_weeks']} weeks >=9/16, "
          f"{weekly['weeks_ge_10of16']}/{weekly['n_weeks']} weeks >=10/16")
    print(f"\nclosing-line sanity check (ALL seasons, pool_line=live_line=close): "
          f"{closing_check['w']}-{closing_check['l']} = {closing_check['pct']:.1%}"
          "  -- should be ~coin flip; if it isn't, something is wrong")
    print("\ncover% by move size (test):")
    for c in calib:
        print(f"  {c['bucket']:>9} pts  n={c['n']:4d}  cover {c['cover_pct']:.1%}  "
              f"(normal model ~{c['normal_approx']:.1%})")

    out = {
        "generated_by": "scripts/pickem_backtest.py",
        "n_games": n_total, "seasons": seasons,
        "train_seasons": train_seasons, "test_seasons": test_seasons,
        "train": train_grade, "test": test_grade, "weekly_test": weekly,
        "calibration_test": calib, "closing_line_sanity_check_all_seasons": closing_check,
    }
    OUT.write_text(json.dumps(out, indent=2))
    print(f"\nsaved -> {OUT}")


if __name__ == "__main__":
    main()
