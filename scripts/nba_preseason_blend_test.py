#!/usr/bin/env python3
"""Opening-week role: last season's finale vs. this preseason (2026-10-29).

    python3 scripts/nba_preseason_blend_test.py

On 2026-10-21 (opening night) the board projected deep-bench players at 16-22 minutes
"Confirmed" (Reese 22.4, Slawson 19.8, Small 16.8; none played, 4 of our lineup slots).
Their role came from LAST season's final games -- Reese 24-39 minutes over his last 8,
the April shutdown stretch -- while this preseason he played 9 / 6 / 3. Preseason only
counts for a player with < 5 NBA games (edge/nba_minutes.project).
Test: while season_games < N, a player with >= 2 preseason games gets
    role = (1 - W) x role + W x mean(preseason minutes x pre_ratio)
W and N chosen on 2024-25 ONLY (slice: season games 0 and 1-4, plus all); 2025-26 and
2026-27 held out; both information states.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import nba_minutes_lab as L   # noqa: E402

P = {"W": 0.0, "N": 5}
orig = L.game_roles


def patched(D, g, R, prm, mode):
    team, base, srate = orig(D, g, R, prm, mode)
    if P["W"]:
        rows = D["rows"]
        for k, i in enumerate(g["idx"]):
            r = rows[i]
            pre = r["pre"]
            if r["season_games"] < P["N"] and len(pre) >= 2 and r["nba_games"] >= 5:
                pm = sum(pre) / len(pre) * prm["pre_ratio"]
                base[k] = (1 - P["W"]) * base[k] + P["W"] * pm
    return team, base, srate


L.game_roles = patched


def main() -> int:
    D = L.load(verbose=False)
    prm = L.final_params()
    seasons = ("2024-25", "2025-26", "2026-27")
    for mode in ("confirmed", "inactives"):
        for W, N in [(0.0, 5)] + [(w, n) for n in (1, 3, 5) for w in (0.25, 0.5, 0.75)]:
            P.update(W=W, N=N)
            r = L.evaluate(D, prm, mode, seasons=seasons)
            cells = " | ".join(
                f"{r[s]['all']['rmse']:.4f} / {r[s]['season games 0']['rmse']:.3f} n{r[s]['season games 0']['n']}"
                f" / {r[s]['season games 1-4']['rmse']:.3f}" for s in seasons)
            print(f"| {mode} | {W} | {N} | {cells} |", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
