#!/usr/bin/env python3
"""Validate DK NHL scoring and fit the simulator's constants on a full season.

    python3 scripts/nhl_fit.py --download         # cache the season's box scores
    python3 scripts/nhl_fit.py --scoring --gid 153977
    python3 scripts/nhl_fit.py --sim

--scoring recomputes every player's DraftKings points per game from the NHL's
own box scores and compares it with DraftKings' published FPPG on a live board
(draftStatAttributes id 341). Goalie values are tried in alternatives, so the
table in edge/nhl.py is chosen by the data rather than by an article. What the
box scores cannot see -- short-handed points (+2) and shootout goals (+1.5) --
is small and runs the recomputed number slightly LOW, never high.

--sim measures the constants edge/nhl_sim.py and edge/dfs_run_nhl.py use:
league goals per team-game, assists per goal, overtime and shootout shares,
empty-net goals, and the negative-binomial dispersion of shots and blocks.
"""
from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs, nhl  # noqa: E402
from edge.names import norm  # noqa: E402

SEASON, SEASON_ID = 2025, "20252026"


def games(season: int = SEASON) -> list[dict]:
    out = []
    for gid in nhl.season_game_ids(season):
        path = nhl.CACHE_DIR / "box" / f"{gid}.json"
        if path.exists():
            out.append(nhl.boxscore(gid))
    return out


def download(season: int = SEASON) -> None:
    ids = nhl.season_game_ids(season)
    for n, gid in enumerate(ids, 1):
        try:
            nhl.boxscore(gid)
        except Exception as exc:                            # noqa: BLE001
            print(f"  {gid}: {exc}")
        if n % 200 == 0:
            print(f"  {n}/{len(ids)}")
    nhl.season_skater_stats(SEASON_ID)


def _dk_board(gid: int) -> dict:
    return {norm(v["name"]): v for v in dfs.fetch_draftables(gid).values() if v.get("dk_fppg")}


def scoring(gid: int) -> None:
    gs = games()
    print(f"{len(gs)} games")
    names = {pid: r.get("skaterFullName") for pid, r in nhl.season_skater_stats(SEASON_ID).items()}
    board = _dk_board(gid)

    per = defaultdict(list)
    for g in gs:
        for s in g["skaters"]:
            per[s["id"]].append(float(nhl.skater_points(s["goals"], s["assists"], s["shots"],
                                                        s["blocks"])))
    rows = []
    for pid, pts in per.items():
        name = names.get(pid)
        dk = board.get(norm(name or ""))
        if dk and len(pts) >= 20:
            rows.append((statistics.fmean(pts), dk["dk_fppg"], name))
    errs = [m - d for m, d, _ in rows]
    print(f"\nSKATERS, {len(rows)} on the board with 20+ games: recomputed - DK FPPG "
          f"bias {statistics.fmean(errs):+.3f}, MAE {statistics.fmean(abs(e) for e in errs):.3f}, "
          f"within 0.25: {sum(abs(e) <= 0.25 for e in errs) / len(errs):.0%}")
    for m, d, n in sorted(rows, key=lambda r: -abs(r[0] - r[1]))[:6]:
        print(f"   {n:24} recomputed {m:5.2f}  DK {d:5.2f}")

    goalie_games = defaultdict(list)
    for g in gs:
        for side in ("away", "home"):
            team = g[side]
            opp_goals = g["home_goals"] if side == "away" else g["away_goals"]
            for gl in g["goalies"]:
                if gl["team"] != team or not gl.get("toi") or gl["toi"] in ("00:00",):
                    continue
                goalie_games[gl["id"]].append((gl, opp_goals, g["last_period"]))
    by_last = {}
    for v in board.values():
        if v.get("position") == "G":
            first, *rest = v["name"].split()
            by_last[(first[0].upper(), norm(" ".join(rest)))] = v
    variants = {"win6 sv.7 ga-3.5 so4 otl2": (6, .7, -3.5, 4, 2),
                "otl1": (6, .7, -3.5, 4, 1), "so2": (6, .7, -3.5, 2, 2),
                "sv.8 ga-3": (6, .8, -3, 4, 2), "win5": (5, .7, -3.5, 4, 2)}
    print("\nGOALIES (15+ appearances): mean |recomputed - DK FPPG| by scoring variant")
    for label, (w, sv, ga, so, otl) in variants.items():
        diffs = []
        for gid_, apps in goalie_games.items():
            if len(apps) < 15:
                continue
            gl0 = apps[0][0]
            ini, _, last = gl0["name"].partition(". ")
            dk = by_last.get((ini.upper(), norm(last)))
            if not dk:
                continue
            pts = []
            for gl, _, _ in apps:
                won = gl.get("decision") == "W"
                otloss = gl.get("decision") == "O"
                full = (gl.get("toi") or "0:0").split(":")[0].isdigit() and int(gl["toi"].split(":")[0]) >= 58
                shutout = won and gl["goals_against"] == 0 and full
                pts.append(sv * gl["saves"] + ga * gl["goals_against"] + w * won + otl * otloss
                           + so * shutout + 3 * (gl["saves"] >= 35))
            diffs.append(statistics.fmean(pts) - dk["dk_fppg"])
        if diffs:
            print(f"   {label:28} n={len(diffs):2}  bias {statistics.fmean(diffs):+.3f}  "
                  f"MAE {statistics.fmean(abs(d) for d in diffs):.3f}")


def sim_constants() -> None:
    gs = [g for g in games() if g.get("home_goals") is not None]
    n = len(gs)
    team_goals = [g["home_goals"] for g in gs] + [g["away_goals"] for g in gs]
    goals = sum(s["goals"] for g in gs for s in g["skaters"])
    assists = sum(s["assists"] for g in gs for s in g["skaters"])
    ot = [g["last_period"] for g in gs]
    stats = nhl.season_skater_stats(SEASON_ID)
    en = sum(r.get("emptyNetGoals") or 0 for r in stats.values())
    mean_tg = statistics.fmean(team_goals)
    print(f"{n} games")
    print(f"  goals per team-game          {mean_tg:.3f}   (variance {statistics.pvariance(team_goals):.3f}; "
          f"Poisson would be {mean_tg:.3f})")
    print(f"  assists per goal             {assists / goals:.3f}")
    print(f"  games to OT or SO            {sum(p in ('OT', 'SO') for p in ot) / n:.3f}   "
          f"of which decided in OT {sum(p == 'OT' for p in ot) / max(1, sum(p in ('OT', 'SO') for p in ot)):.3f}")
    print(f"  empty-net goals per game     {en / n:.3f}   share of all goals {en / sum(team_goals):.3f}")

    def nb_k(pairs):
        """Pooled method-of-moments NB shape from (player mean, game value) pairs."""
        num = den = 0.0
        for mu, xs in pairs:
            if len(xs) < 30 or mu <= 0.2:
                continue
            var = statistics.pvariance(xs)
            num += mu * mu
            den += max(var - mu, 1e-6)
        return num / den if den else float("inf")

    per_shots, per_blocks = defaultdict(list), defaultdict(list)
    team_shots = defaultdict(list)
    for g in gs:
        tot = defaultdict(int)
        for s in g["skaters"]:
            per_shots[s["id"]].append(s["shots"])
            per_blocks[s["id"]].append(s["blocks"])
            tot[s["team"]] += s["shots"]
        for t, v in tot.items():
            team_shots[t].append(v)
    print(f"  shots NB shape (k)           {nb_k((statistics.fmean(v), v) for v in per_shots.values()):.2f}")
    print(f"  blocks NB shape (k)          {nb_k((statistics.fmean(v), v) for v in per_blocks.values()):.2f}")
    ratios = [x / statistics.fmean(v) for v in team_shots.values() for x in v]
    cv2 = statistics.pvariance(ratios)
    print(f"  team shots per game          {statistics.fmean(x for v in team_shots.values() for x in v):.1f}"
          f"   gamma shape of the team factor ~ {1 / max(cv2 - 1 / 29.0, 1e-6):.1f} "
          f"(after removing Poisson noise at ~29 shots)")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--scoring", action="store_true")
    ap.add_argument("--sim", action="store_true")
    ap.add_argument("--gid", type=int, default=None, help="a live DK NHL draft group")
    args = ap.parse_args()
    if args.download:
        download()
    if args.scoring:
        if not args.gid:
            raise SystemExit("--scoring needs --gid (a live DK NHL draft group with FPPG)")
        scoring(args.gid)
    if args.sim:
        sim_constants()


if __name__ == "__main__":
    main()
