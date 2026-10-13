#!/usr/bin/env python3
"""Check edge/nba_sim.py's PRIORS against real NBA box scores.

    python3 scripts/nba_fit.py                    # 2024-25, everything
    python3 scripts/nba_fit.py --season 2024-25 --what spread corr profiles

Reads data/nba_ground_truth/{player,team}_gamelog_<season>.json (gitignored;
`scripts/nba_ground_truth_collect.py` regenerates them for free from
stats.nba.com). Nothing here touches the network or ships a constant -- it
prints the measured value beside the shipped prior so a person decides.

WHAT IS MEASURED, AND AGAINST WHAT
  spread    each player-game's DK points against his LEAVE-ONE-OUT season mean
            (his other games -- the game being scored never predicts itself),
            binned by that mean: realised sd per bin, beside the simulator's sd
            for a player with the same mean in an average game.
  corr      DK-point residual correlation between teammates and between
            same-game opponents (residual = actual - leave-one-out mean), among
            players averaging 20+ DK points. The simulator's prior gives about
            +0.04 teammates / +0.05 opponents (tests/test_dfs_nba.py's board).
  team      team points: sd of total and margin around the two teams' LOO
            season means. NOT the closing-line residual nba_sim was set from
            (no lines here), so it runs WIDER than the prior by the market's
            skill -- read it as an upper bound.
  profiles  per primary position, the average stat line of 25-35 DK point
            player-games -- the shape dfs_run_nba.NBA_PROFILES splits FPPG by.
            Positions come from the props collection's player list if present,
            else the script says it cannot assign them.

Below ~5 slates nothing is a fit; a season of box scores is ~1,230 games, so
these are real samples, but they are not held out against the build -- they
check the simulator's SHAPE, not its projections.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import nba, nba_sim  # noqa: E402

GT = ROOT / "data" / "nba_ground_truth"


def load(season: str) -> tuple[list, list]:
    p, t = GT / f"player_gamelog_{season}.json", GT / f"team_gamelog_{season}.json"
    if not p.exists():
        sys.exit(f"{p} missing -- run scripts/nba_ground_truth_collect.py --season {season}")
    return json.loads(p.read_text()), (json.loads(t.read_text()) if t.exists() else [])


def loo_means(rows: list, key) -> list:
    """Leave-one-out mean of key(row) per player, aligned to rows (None if <10 other games)."""
    by = defaultdict(list)
    for i, r in enumerate(rows):
        by[r["PLAYER_ID"] if "PLAYER_ID" in r else r["PLAYER_NAME"]].append(i)
    out = [None] * len(rows)
    for idx in by.values():
        vals = [key(rows[i]) for i in idx]
        tot = sum(vals)
        for i, v in zip(idx, vals):
            if len(idx) > 10:
                out[i] = (tot - v) / (len(idx) - 1)
    return out


def sim_sd(mean_fp: float, n: int = 20000) -> float:
    m = nba_sim.NBA
    prof = {"pts": 13.5, "reb": 5.0, "ast": 3.2, "fg3": 1.4, "stl": 0.9, "blk": 0.6, "tov": 1.5}
    scale = mean_fp / nba_sim.fantasy_mean(prof)
    pool = [{"team": "A", "means": {k: v * scale for k, v in prof.items()}}]
    games = {"g": {"home": "A", "away": "B", "pts_home": m.team_points, "pts_away": m.team_points}}
    return float(nba_sim.simulate(pool, games, n_sims=n, seed=1)[:, 0].std())


def spread(rows):
    fp = [nba.actual_points(r) for r in rows]
    mu = loo_means(rows, nba.actual_points)
    print("\nSPREAD -- realised sd of DK points around the LOO season mean, vs nba_sim")
    print(f"  {'mean bin':>9} {'n':>6} {'real sd':>8} {'sim sd':>7}")
    for lo in range(10, 60, 5):
        res = [f - m for f, m in zip(fp, mu) if m is not None and lo <= m < lo + 5]
        if len(res) < 50:
            continue
        print(f"  {lo:>4}-{lo + 5:<4} {len(res):>6} {statistics.pstdev(res):>8.2f} "
              f"{sim_sd(lo + 2.5):>7.2f}")


def corr(rows):
    mu = loo_means(rows, nba.actual_points)
    by_game = defaultdict(list)
    for r, m in zip(rows, mu):
        if m is not None and m >= 20:
            by_game[r["GAME_ID"]].append((r["TEAM_ABBREVIATION"], nba.actual_points(r) - m))
    team, opp = [], []
    for players in by_game.values():
        for i in range(len(players)):
            for j in range(i + 1, len(players)):
                (ta, a), (tb, b) = players[i], players[j]
                (team if ta == tb else opp).append((a, b))
    print("\nCORRELATION -- DK-point residuals, players averaging 20+")
    for label, pairs in (("teammates", team), ("opponents", opp)):
        if len(pairs) > 100:
            x, y = np.array(pairs).T
            print(f"  {label:10} pairs {len(pairs):6}  r = {np.corrcoef(x, y)[0, 1]:+.3f}")


def team_spread(trows):
    if not trows:
        print("\nTEAM -- no team gamelog; skipped")
        return
    by_team = defaultdict(list)
    for r in trows:
        by_team[r["TEAM_ABBREVIATION"]].append(float(r["PTS"]))
    means = {t: statistics.fmean(v) for t, v in by_team.items()}
    by_game = defaultdict(dict)
    for r in trows:
        by_game[r["GAME_ID"]][r["TEAM_ABBREVIATION"]] = float(r["PTS"])
    tot, mar = [], []
    for g in by_game.values():
        if len(g) != 2:
            continue
        (a, pa), (b, pb) = g.items()
        tot.append((pa + pb) - (means[a] + means[b]))
        mar.append((pa - pb) - (means[a] - means[b]))
    m = nba_sim.NBA
    prior_tot = 2 * m.team_points * (m.pace_sd ** 2 + m.eff_sd ** 2 / 2) ** 0.5
    prior_mar = m.team_points * m.eff_sd * 2 ** 0.5
    print(f"\nTEAM -- {len(tot)} games, residual around season means (upper bound on the "
          "closing-line residual)")
    print(f"  total  sd {statistics.pstdev(tot):5.1f}   (prior {prior_tot:.1f})")
    print(f"  margin sd {statistics.pstdev(mar):5.1f}   (prior {prior_mar:.1f})")


def profiles(rows, season):
    pos_file = ROOT / "data" / f"nba_positions_{season}.json"
    if not pos_file.exists():
        print(f"\nPROFILES -- no {pos_file.name} ({{player: 'PG'}}); positions cannot be "
              "assigned from box scores alone. Skipped.")
        return
    pos = json.loads(pos_file.read_text())
    from edge.dfs_run_nba import NBA_PROFILES
    keys = {"pts": "PTS", "reb": "REB", "ast": "AST", "fg3": "FG3M", "stl": "STL",
            "blk": "BLK", "tov": "TOV"}
    print("\nPROFILES -- mean stat line of 25-35 DK point games, scaled to 30 DK points")
    for p in NBA_PROFILES:
        games = [r for r in rows if pos.get(r["PLAYER_NAME"]) == p
                 and 25 <= nba.actual_points(r) <= 35]
        if len(games) < 100:
            continue
        line = {k: statistics.fmean(float(r[v]) for r in games) for k, v in keys.items()}
        s = 30.0 / nba_sim.fantasy_mean(line)
        shipped = NBA_PROFILES[p]
        s2 = 30.0 / nba_sim.fantasy_mean(shipped)
        print(f"  {p:2} n={len(games):5}  " + "  ".join(
            f"{k} {line[k] * s:4.1f}/{shipped[k] * s2:4.1f}" for k in keys) + "   (real/shipped)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--season", default="2024-25")
    ap.add_argument("--what", nargs="*", default=["spread", "corr", "team", "profiles"])
    args = ap.parse_args()
    rows, trows = load(args.season)
    rows = [r for r in rows if float(r.get("MIN") or 0) > 0]
    print(f"{args.season}: {len(rows)} player-games with minutes, "
          f"{len({r['GAME_ID'] for r in rows})} games")
    if "spread" in args.what:
        spread(rows)
    if "corr" in args.what:
        corr(rows)
    if "team" in args.what:
        team_spread(trows)
    if "profiles" in args.what:
        profiles(rows, args.season)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
