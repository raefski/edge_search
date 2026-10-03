#!/usr/bin/env python3
"""Backtest edge/dfs_lineup_projection.py against the orders teams really posted.

    python3 scripts/dfs_lineup_projection_backtest.py --start 2026-09-15 --end 2026-10-02

For every team-game in the window, predict the nine from games STARTED BEFORE it
(and the opposing starter's real hand), then score against the real order:

  overlap   how many of the 9 projected were in the real nine (old method:
            the team's previous game's nine, what dfs.py shipped with)
  calibration  of players given p_start in a bucket, the share who started
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs  # noqa: E402
from edge import dfs_lineup_projection as lp  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--start", default="2026-09-15")
    ap.add_argument("--end", default="2026-10-02")
    ap.add_argument("--types", default="RFDLW", help="game types to score, e.g. FDLW for postseason only")
    args = ap.parse_args()

    end_next = (__import__("datetime").date.fromisoformat(args.end)
                + __import__("datetime").timedelta(days=1)).isoformat()
    hist = lp.fetch_history(end_next, days=(__import__("datetime").date.fromisoformat(args.end)
                                           - __import__("datetime").date.fromisoformat(args.start)).days + 50)
    sps = {str(sp) for g in hist for s in g["sides"].values() for sp in [s["opp_sp"]] if sp}
    hands = dfs.player_hands(sps, cache_path=str(ROOT / "data/dfs_hands.json"))
    print(f"{len(hist)} games of history, {len(sps)} starters' hands")

    new_ov, old_ov, base_ov = [], [], []
    by_hand = defaultdict(lambda: [[], []])
    cal = defaultdict(lambda: [0, 0])
    scored = 0
    for idx, g in enumerate(hist):
        d = g["date"][:10]
        if not (args.start <= d <= args.end) or g["type"] not in args.types:
            continue
        before = hist[:idx]
        for tid, side in g["sides"].items():
            games = lp.team_games(before, tid)
            if len(games) < 5:
                continue
            real = {pid for pid, _ in side["order"]}
            hand = (hands.get(str(side["opp_sp"])) or {}).get("throw")
            pred = lp.predict_lineup(games, hand, hands)
            if len(pred) < 9:
                continue
            ov = len({p["id"] for p in pred} & real)
            new_ov.append(ov)
            old = {pid for pid, _ in games[0]["order"]}
            old_ov.append(len(old & real))
            nohand = lp.predict_lineup(games, None, hands)
            base_ov.append(len({p["id"] for p in nohand} & real))
            by_hand[hand or "?"][0].append(ov)
            by_hand[hand or "?"][1].append(len(old & real))
            # calibration over a wider candidate list than the nine
            allp = lp.predict_lineup(games, hand, hands, n_games=20)
            scored += 1
    def avg(x): return sum(x) / len(x) if x else float("nan")
    print(f"\n{scored} team-games scored ({args.start}..{args.end}, types {args.types})")
    print(f"  mean starters right of 9:  new {avg(new_ov):.2f}   hand-blind {avg(base_ov):.2f}   "
          f"last-game (old) {avg(old_ov):.2f}")
    for h, (a, b) in sorted(by_hand.items()):
        print(f"    vs {h}-handed SP  n={len(a):3}  new {avg(a):.2f}  old {avg(b):.2f}")
    full = sum(1 for x in new_ov if x == 9) / len(new_ov)
    print(f"  all nine right: new {full:.0%}   old {sum(1 for x in old_ov if x == 9) / len(old_ov):.0%}")
    print(f"  8+ right: new {sum(1 for x in new_ov if x >= 8) / len(new_ov):.0%}   "
          f"old {sum(1 for x in old_ov if x >= 8) / len(old_ov):.0%}")

    # calibration of p_start across every player the model scored >= 0.2 in a top-12
    # (reuse the same loop, wider list)
    import statistics
    for idx, g in enumerate(hist):
        d = g["date"][:10]
        if not (args.start <= d <= args.end) or g["type"] not in args.types:
            continue
        before = hist[:idx]
        for tid, side in g["sides"].items():
            games = lp.team_games(before, tid)
            if len(games) < 5:
                continue
            real = {pid for pid, _ in side["order"]}
            hand = (hands.get(str(side["opp_sp"])) or {}).get("throw")
            for p in lp.predict_lineup(games, hand, hands, n_games=20):
                b = min(int(p["p_start"] * 10), 9)
                cal[b][0] += 1
                cal[b][1] += p["id"] in real
    print("\n  calibration of p_start (the projected nine only):")
    for b in sorted(cal):
        n, k = cal[b]
        print(f"    p {b / 10:.1f}-{(b + 1) / 10:.1f}  n={n:4}  actually started {k / n:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
