#!/usr/bin/env python3
"""Stack-shape backtest on REAL 2025 team-games (free, statsapi boxscores).

Questions this answers with real data rather than DFS-community folklore:
  1. How correlated are teammates' DK points, by batting-order distance?
     (the mechanism stacking pays through -- §10 said the model has none)
  2. Which stack SHAPE has the best ceiling per roster slot: 4 consecutive,
     5 consecutive, top-of-order (1-4 / 1-5), or wraparound runs?
  3. Does a 5-stack beat a 4-stack + best other-team hitter (the actual
     construction tradeoff, holding roster slots at 5)?

Reads data/bt_boxscores/*.json (see scratchpad prefetch script; each file is
one final game with per-player {pa, pts, hr, bb} and the posted batting order).
Every number is computed from actual DK points of actual posted lineups.

Usage: python3 scripts/dfs_stack_shape_backtest.py [start] [end]
"""
import json
import statistics
import sys
from collections import defaultdict
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOX = ROOT / "data" / "bt_boxscores"


def load_team_games(start="2025-04-15", end="2025-07-31"):
    """-> list of {date, team_id, slot_pts: {slot: pts}, home: bool}"""
    out = []
    for f in sorted(BOX.glob("*.json")):
        g = json.loads(f.read_text())
        if not (start <= g["date"] <= end):
            continue
        for side in ("home", "away"):
            t = g[side]
            slot_pts = {}
            for pid, name, slot in t["lineup"]:
                st = t["stats"].get(str(pid))
                if st is not None and slot not in slot_pts:
                    slot_pts[slot] = st["pts"]
            if len(slot_pts) == 9:
                out.append({"date": g["date"], "team_id": t["team_id"],
                            "slot_pts": slot_pts, "home": side == "home"})
    return out


def pearson(xs, ys):
    n = len(xs)
    mx, my = statistics.mean(xs), statistics.mean(ys)
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
    sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
    return cov / (sx * sy) if sx and sy else float("nan")


def q(vals, p):
    s = sorted(vals)
    i = (len(s) - 1) * p
    lo, hi = int(i), min(int(i) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (i - lo)


def main():
    start = sys.argv[1] if len(sys.argv) > 2 else "2025-04-15"
    end = sys.argv[2] if len(sys.argv) > 2 else "2025-07-31"
    tg = load_team_games(start, end)
    print(f"{len(tg)} complete team-games {start}..{end}\n")

    # --- 1. teammate correlation by batting-order distance -------------------
    by_dist = defaultdict(lambda: ([], []))
    for g in tg:
        for a, b in combinations(range(1, 10), 2):
            d = min(abs(a - b), 9 - abs(a - b))  # cyclic distance
            by_dist[d][0].append(g["slot_pts"][a])
            by_dist[d][1].append(g["slot_pts"][b])
    print("teammate DK-pts correlation by cyclic batting-order distance:")
    for d in sorted(by_dist):
        xs, ys = by_dist[d]
        print(f"  distance {d}: corr {pearson(xs, ys):+.3f}   (n={len(xs)} pairs)")
    allx, ally = [], []
    for xs, ys in by_dist.values():
        allx += xs; ally += ys
    print(f"  ANY teammate pair: corr {pearson(allx, ally):+.3f}   (n={len(allx)})")

    # --- 2. stack shapes: actual total points of the stack, per team-game ----
    def run_total(g, start_slot, n):
        return sum(g["slot_pts"][((start_slot - 1 + i) % 9) + 1] for i in range(n))

    shapes = {
        "best 4 consecutive (hindsight)": lambda g: max(run_total(g, s, 4) for s in range(1, 10)),
        "slots 1-4": lambda g: run_total(g, 1, 4),
        "slots 2-5": lambda g: run_total(g, 2, 4),
        "slots 1-5 (5stk)": lambda g: run_total(g, 1, 5),
        "best 5 consecutive (hindsight)": lambda g: max(run_total(g, s, 5) for s in range(1, 10)),
        "random 4 (scattered)": None,  # computed analytically below via slot sampling
    }
    print("\nstack totals per team-game (actual DK pts). mean / P90 / P95 / per-player mean:")
    for label, fn in shapes.items():
        if fn is None:
            continue
        vals = [fn(g) for g in tg]
        n_players = 5 if "5" in label.split("(")[0] or "1-5" in label else 4
        print(f"  {label:32} mean {statistics.mean(vals):6.2f}  P90 {q(vals,0.90):6.1f}  "
              f"P95 {q(vals,0.95):6.1f}  perP {statistics.mean(vals)/n_players:5.2f}")

    # --- 3. the real construction tradeoff: 5th stack spot vs best 1-off -----
    # For each PAIR of team-games on the same date (stack team + donor team):
    # A = slots 1-4 stack + donor's best single hitter (independent)
    # B = slots 1-5 stack (5th correlated player)
    # Same 5 roster slots. Compare distribution tails.
    by_date = defaultdict(list)
    for g in tg:
        by_date[g["date"]].append(g)
    A, B = [], []
    import random
    rng = random.Random(7)
    for date, games in by_date.items():
        if len(games) < 2:
            continue
        for g in games:
            donor = rng.choice([x for x in games if x is not g])
            donor_best_slot_mean = max(donor["slot_pts"].values())  # hindsight best = upper bound for the 1-off
            donor_random = donor["slot_pts"][rng.randint(1, 4)]     # realistic: a top-order 1-off
            A.append(run_total(g, 1, 4) + donor_random)
            B.append(run_total(g, 1, 5))
    print("\n5th roster spot: 4-stack + top-order 1-off (A) vs 5-stack (B), same date:")
    for label, vals in (("A 4stk+1off", A), ("B 5stk", B)):
        print(f"  {label:14} mean {statistics.mean(vals):6.2f}  std {statistics.pstdev(vals):5.2f}  "
              f"P90 {q(vals,0.90):6.1f}  P95 {q(vals,0.95):6.1f}  P99 {q(vals,0.99):6.1f}")

    # --- 4. slot-PA table: empirical PA by batting slot, home vs away --------
    pa_by = defaultdict(list)
    for f in sorted(BOX.glob("*.json")):
        g = json.loads(f.read_text())
        if not (start <= g["date"] <= end):
            continue
        for side in ("home", "away"):
            t = g[side]
            for pid, name, slot in t["lineup"]:
                st = t["stats"].get(str(pid))
                if st:  # starters only; PA includes their whole slot's start
                    pa_by[(side, slot)].append(st["pa"])
    print("\nempirical starter PA by slot (note: starter-only, subs truncate):")
    print("  slot   home    away   (production SLOT_PA)")
    import sys as _s
    _s.path.insert(0, str(ROOT))
    from edge.dfs import SLOT_PA
    for slot in range(1, 10):
        h = statistics.mean(pa_by[("home", slot)])
        a = statistics.mean(pa_by[("away", slot)])
        print(f"  {slot:4} {h:6.2f}  {a:6.2f}   ({SLOT_PA[slot]})")


if __name__ == "__main__":
    main()
