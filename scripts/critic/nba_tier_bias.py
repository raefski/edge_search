#!/usr/bin/env python3
"""NBA DK-points bias by salary tier (NBA_MINUTES_CRITIQUE_LOG.md cycle 20).

scripts/pool_coverage.py (10/29) found $8.5k+ NBA players under-projected by 2.51
pts (n 52, 10 slates) and sub-$4k ones over-projected by 2.07. This is the held-out
harness for that question, in two parts:

  lab   leak-free player-games from scripts/nba_projection_backtest.py (the shipped
        minutes x pts/min x opponent model). No DK salaries exist for past seasons,
        so the TIER IS THE PROJECTION ITSELF (DK prices close to recent DK points:
        40+ projected ~ $8.5k+, under 15 ~ under $4k). Fit on 2024-25, held out
        2025-26; the DATE is the bootstrap unit.
  live  2026-27 slates: the last pre-lock row per player in data/dfs_proj_log_nba.csv
        (one row per draft group -- the projection is the same on every slate a
        game sits on, so a player-game counts once) against his box score, by real
        DK salary tier. Too few slates to fit on; it is the check that the lab's
        tiers describe the live board.

Metric: bias = mean(projection - actual) per tier (negative = under-projected),
MAE per tier and overall. Overall MAE is the guardrail.

    python3 scripts/critic/nba_tier_bias.py            # baseline tables
"""
from __future__ import annotations

import csv
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from edge import nba_minutes as NM, nba_project as NP   # noqa: E402
from edge.names import norm                              # noqa: E402

GT = ROOT / "data" / "nba_ground_truth"
PROJ_TIERS = ((40, 999, "40+"), (30, 40, "30-40"), (20, 30, "20-30"), (15, 20, "15-20"), (0, 15, "<15"))
SAL_TIERS = ((8500, 99999, "$8.5k+"), (6500, 8500, "$6.5-8.5k"), (5000, 6500, "$5-6.5k"),
             (4000, 5000, "$4-5k"), (0, 4000, "<$4k"))


def dk(r: dict) -> float:
    s = [r["PTS"], r["REB"], r["AST"], r["STL"], r["BLK"]]
    dd = sum(1 for x in s if x >= 10)
    return (r["PTS"] + 0.5 * r["FG3M"] + 1.25 * r["REB"] + 1.5 * r["AST"] + 2 * r["STL"]
            + 2 * r["BLK"] - 0.5 * r["TOV"] + (1.5 if dd >= 2 else 0) + (3 if dd >= 3 else 0))


def lab_rows(season: str) -> list[tuple]:
    """[(date, actual, proj, id, minutes, rate, opp factor, history)] per active player-game."""
    import nba_projection_backtest as BT
    out = []
    for t in BT.build(season):
        team = [{"id": r["id"], "position": r["position"], "inactive": r["inactive"],
                 "starter": r["starter"], "history": [(m, s) for m, s, _ in r["history"]]}
                for r in t["rows"]]
        mins = NM.project(team)
        of = NP.opponent_factor(t["opp_pm"], t["league_pm"])
        for r in t["rows"]:
            if r["inactive"] or len(r["history"]) < 5:
                continue
            m = mins[r["id"]]["minutes"]
            rt = NP.rate(r["history"], r["position"])
            out.append((t["date"], r["pts"], m * rt * of, r["id"], m, rt, of, r["history"], r["position"]))
    return out


def live_rows() -> list[tuple]:
    """[(date, actual, proj, salary)] -- last row per (gid, player), deduped per player-game."""
    box = {}
    for r in json.load(open(GT / "player_gamelog_2026-27.json")):
        box[(r["GAME_DATE"], norm(r["PLAYER_NAME"]))] = dk(r)
    last = {}
    for r in csv.DictReader(open(ROOT / "data" / "dfs_proj_log_nba.csv")):
        if r.get("source") not in ("live", "", None):
            continue
        last[(r["date"], r["gid"], norm(r["player"]))] = r          # file is append-only: last wins
    seen, out = set(), []
    for (d, gid, k), r in last.items():
        if (d, k) in seen or (d, k) not in box or not r["proj"]:
            continue
        if float(r.get("minutes") or 0) <= 0:
            continue
        seen.add((d, k))
        out.append((d, box[(d, k)], float(r["proj"]), float(r["salary"])))
    return out


def table(rows, key, tiers, preds=None) -> list[str]:
    """rows: (date, actual, proj, tier value). preds overrides proj."""
    p = np.array([r[2] for r in rows]) if preds is None else preds
    a = np.array([r[1] for r in rows])
    v = np.array([key(r) for r in rows])
    out = [f"| tier | n | dates | bias (proj-act) | MAE |", "|---|---|---|---|---|"]
    for lo, hi, name in tiers:
        m = (v >= lo) & (v < hi)
        if m.sum():
            out.append(f"| {name} | {m.sum():,} | {len({rows[i][0] for i in np.where(m)[0]})} | "
                       f"{(p[m] - a[m]).mean():+.2f} | {np.abs(p[m] - a[m]).mean():.3f} |")
    out.append(f"| all | {len(a):,} | {len({r[0] for r in rows})} | {(p - a).mean():+.2f} | {np.abs(p - a).mean():.4f} |")
    return out


def boot_mae(rows, p0, p1, n=1000, seed=0):
    by = defaultdict(list)
    for r, x0, x1 in zip(rows, p0, p1):
        by[r[0]].append(abs(x1 - r[1]) - abs(x0 - r[1]))
    dates, rng, s = list(by), random.Random(seed), []
    for _ in range(n):
        xs = [x for d in (rng.choice(dates) for _ in dates) for x in by[d]]
        s.append(sum(xs) / len(xs))
    s.sort()
    return s[int(.025 * n)], s[int(.975 * n)]


def main() -> int:
    for season in ("2024-25", "2025-26"):
        rows = lab_rows(season)
        print(f"\n## lab {season} (tier = projection)\n")
        print("\n".join(table(rows, lambda r: r[2], PROJ_TIERS)))
    rows = live_rows()
    print(f"\n## live 2026-27 (tier = DK salary), {len({r[0] for r in rows})} dates\n")
    print("\n".join(table(rows, lambda r: r[3], SAL_TIERS)))
    print("\n(same live rows by projection tier)\n")
    print("\n".join(table(rows, lambda r: r[2], PROJ_TIERS)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
