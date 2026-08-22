#!/usr/bin/env python3
"""Simulate the model against 1,000 coin-flipping opponents on the holdout.

Not a validation exercise -- the model is unchanged and no decision here
feeds back into it. This is a DESCRIPTIVE comparison of the already-spent
2023-24 holdout against a pure-noise field, to answer a question the win
rate alone doesn't: how often does 55.9% actually WIN things?

Every simulated player flips a fair coin per game (home or away), graded
against the same frozen CBS-proxy line with the same ats_result() the real
backtest uses, so pushes and ties are handled identically for everyone.

Seeded, so re-running reproduces the chart exactly.
Run: python3 scripts/pickem_vs_random.py [n_players] [seed]
Output: data/pickem_vs_random.json
"""
from __future__ import annotations

import csv
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge.pickem import ats_result, make_pick  # noqa: E402

DATA = ROOT / "data" / "pickem_odds_history.csv"
OUT = ROOT / "data" / "pickem_vs_random.json"
HOLDOUT = {2023, 2024}


def load_holdout() -> list[dict]:
    games = []
    with DATA.open() as f:
        for r in csv.DictReader(f):
            s = int(r["season"])
            if s not in HOLDOUT:
                continue
            g = {
                "season": s, "week": int(r["week"]),
                "away": r["away_team"], "home": r["home_team"],
                "pool_line": float(r["home_line_open"]),
                "live_line": float(r["home_line_close"]),
                "margin": int(r["home_score"]) - int(r["away_score"]),
            }
            try:
                g["total_open"] = float(r["total_open"])
                g["total_close"] = float(r["total_close"])
            except ValueError:
                g["total_open"] = g["total_close"] = None
            games.append(g)
    games.sort(key=lambda g: (g["season"], g["week"]))
    return games


def simulate(games, n_players: int, seed: int) -> dict:
    rng = random.Random(seed)
    by_week: dict[tuple, list] = defaultdict(list)
    for g in games:
        by_week[(g["season"], g["week"])].append(g)

    seasons_out = {}
    for season in sorted(HOLDOUT):
        weeks = sorted(w for (s, w) in by_week if s == season)
        model_cum = 0
        decided_cum = 0
        field_cum = [0] * n_players
        rows = []

        for wk in weeks:
            slate = by_week[(season, wk)]

            model_w = model_dec = 0
            for g in slate:
                pk = make_pick(g["away"], g["home"], g["pool_line"], g["live_line"],
                               g.get("total_open"), g.get("total_close"))
                res = ats_result(g["margin"], g["pool_line"], pk.side)
                if res == "W":
                    model_w += 1
                if res != "P":
                    model_dec += 1

            week_scores = []
            for p in range(n_players):
                w = 0
                for g in slate:
                    side = "home" if rng.random() < 0.5 else "away"
                    if ats_result(g["margin"], g["pool_line"], side) == "W":
                        w += 1
                week_scores.append(w)
                field_cum[p] += w

            model_cum += model_w
            decided_cum += model_dec
            best = max(week_scores)
            n_beat_me = sum(1 for s in week_scores if s > model_w)
            n_tie_me = sum(1 for s in week_scores if s == model_w)

            rows.append({
                "week": wk, "n_games": len(slate),
                "model_wins": model_w, "model_cum": model_cum,
                "field_best": best,
                "field_mean": round(statistics.mean(week_scores), 2),
                "field_p90": sorted(week_scores)[int(0.90 * n_players)],
                "won_week_outright": n_beat_me == 0 and n_tie_me == 0,
                "won_week_shared": n_beat_me == 0 and n_tie_me > 0,
                "players_ahead_this_week": n_beat_me,
                "field_cum_best": max(field_cum),
                "field_cum_mean": round(statistics.mean(field_cum), 2),
                "rank_now": sum(1 for c in field_cum if c > model_cum) + 1,
            })

        total_games = sum(r["n_games"] for r in rows)
        seasons_out[str(season)] = {
            "weeks": rows,
            "n_games": total_games,
            "model_total": model_cum,
            # pushes excluded from the denominator, matching
            # scripts/pickem_backtest.py so the two numbers are comparable
            "model_pct": round(model_cum / decided_cum, 4) if decided_cum else None,
            "model_decided": decided_cum,
            "field_best_total": max(field_cum),
            "field_mean_total": round(statistics.mean(field_cum), 2),
            "final_rank": sum(1 for c in field_cum if c > model_cum) + 1,
            "beat_pct_of_field": round(
                100 * sum(1 for c in field_cum if c < model_cum) / n_players, 1),
            "weeks_won_outright": sum(1 for r in rows if r["won_week_outright"]),
            "weeks_won_shared": sum(1 for r in rows if r["won_week_shared"]),
        }

    return {"n_players": n_players, "seed": seed, "seasons": seasons_out}


def main() -> None:
    seed = int(sys.argv[1]) if len(sys.argv) > 1 else 20260822
    games = load_holdout()

    # 1,000 as asked, plus 18 -- the actual TOO-GOODE pool size, which is the
    # number that decides real money. Best-of-N luck scales hard with N, so
    # the two tell very different stories about weekly prizes.
    scenarios = {"n1000": simulate(games, 1000, seed),
                 "pool18": simulate(games, 18, seed)}
    OUT.write_text(json.dumps({"generated_by": "scripts/pickem_vs_random.py",
                               "scenarios": scenarios}, indent=2))

    for key, sc in scenarios.items():
        n = sc["n_players"]
        print(f"\n########## {n} coin-flip opponents (seed {sc['seed']}) ##########")
        for s, d in sc["seasons"].items():
            print(f"=== {s} ===")
            print(f"  model: {d['model_total']}/{d['model_decided']} decided = {d['model_pct']:.1%}")
            print(f"  best coin-flipper: {d['field_best_total']} | field mean: {d['field_mean_total']}")
            print(f"  FINAL RANK: {d['final_rank']} of {n+1} "
                  f"(beat {d['beat_pct_of_field']}% of the field)")
            print(f"  weekly prizes: {d['weeks_won_outright']} outright, "
                  f"{d['weeks_won_shared']} shared, of {len(d['weeks'])} weeks")
    print(f"\nsaved -> {OUT}")


if __name__ == "__main__":
    main()
