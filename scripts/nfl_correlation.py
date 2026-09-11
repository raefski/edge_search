#!/usr/bin/env python3
"""Measure the NFL DFS correlation structure that edge/dfs_opt_nfl.py encodes.

    python3 scripts/nfl_ground_truth_collect.py --season 2023
    python3 scripts/nfl_ground_truth_collect.py --season 2024
    python3 scripts/nfl_correlation.py

WHY
An NFL optimizer is mostly a theory of what correlates with what. Stacking a
quarterback with his receivers, bringing one back from the other side, refusing
a defence that faces your own offence -- each of those is a claim about a
number, and this repo's rule is that a threshold is justified by a measurement
with a date on it rather than by folk wisdom. Two of the numbers below
contradict the folk wisdom, which is the argument for running it:

  * two pass-catchers on the SAME team are slightly NEGATIVELY correlated --
    they are sharing one football -- so a QB+3 stack is not three times a QB+1;
  * a defence against the opposing quarterback (-0.351) is a stronger
    relationship than the quarterback stack itself (+0.249), which is what
    makes it worth a hard constraint rather than a preference.

WHAT IT MEASURES
Pearson r between two players' DK Classic point totals in the SAME GAME, over
the pool a DFS player would actually consider -- 6+ games and a 5+ point
average, so the correlation is not dominated by pairs of replacement players
who both scored zero.
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.nfl import actual_dst_points, actual_offense_points  # noqa: E402

DATA = ROOT / "data" / "nfl_ground_truth"
OFFENSE = {"QB", "RB", "WR", "TE", "FB"}
CATCHERS = {"WR", "TE"}
BACKS = {"RB", "FB"}


def pearson(pairs, min_n=30):
    n = len(pairs)
    if n < min_n:
        return None, n
    mx = sum(a for a, _ in pairs) / n
    my = sum(b for _, b in pairs) / n
    sx = sum((a - mx) ** 2 for a, _ in pairs) ** 0.5
    sy = sum((b - my) ** 2 for _, b in pairs) ** 0.5
    if not sx or not sy:
        return None, n
    return sum((a - mx) * (b - my) for a, b in pairs) / (sx * sy), n


def load(seasons):
    rows = []
    for yr in seasons:
        path = DATA / f"player_week_{yr}.json"
        if not path.exists():
            raise SystemExit(f"{path} missing -- run scripts/nfl_ground_truth_collect.py")
        rows += [r for r in json.loads(path.read_text()) if r["season_type"] == "REG"]
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seasons", type=int, nargs="+", default=[2023, 2024])
    ap.add_argument("--min-games", type=int, default=6)
    ap.add_argument("--min-points", type=float, default=5.0)
    args = ap.parse_args()

    rows = load(args.seasons)
    recs = []
    for r in rows:
        if r["position"] not in OFFENSE:
            continue
        recs.append({"pos": r["position"], "pid": r["player_id"], "team": r["team"],
                     "opp": r["opponent_team"], "dk": actual_offense_points(r),
                     "key": (int(r["season"]), int(r["week"]), r["team"]),
                     "gid": (int(r["season"]), int(r["week"]),
                             *sorted((r["team"], r["opponent_team"])))})
    hist = collections.defaultdict(list)
    for x in recs:
        hist[x["pid"]].append(x["dk"])
    poolset = {p for p, v in hist.items()
               if len(v) >= args.min_games and statistics.mean(v) >= args.min_points}
    by_game = collections.defaultdict(list)
    for x in recs:
        if x["pid"] in poolset:
            by_game[x["gid"]].append(x)
    print(f"seasons {args.seasons}: {len(by_game)} games, {len(poolset)} players "
          f"({args.min_games}+ games, {args.min_points:g}+ DK pts)\n")

    def report(label, a_pos, b_pos, same_team):
        pairs = []
        for players in by_game.values():
            for a in players:
                if a["pos"] not in a_pos:
                    continue
                for b in players:
                    if b["pid"] == a["pid"] or b["pos"] not in b_pos:
                        continue
                    if (a["team"] == b["team"]) != same_team:
                        continue
                    pairs.append((a["dk"], b["dk"]))
        r, n = pearson(pairs)
        shown = f"{r:+.4f}" if r is not None else "n/a"
        print(f"  {label:36s} r={shown:>8s}  n={n}")

    print("SAME TEAM -- what a stack buys")
    report("QB <-> own WR/TE", {"QB"}, CATCHERS, True)
    report("QB <-> own WR", {"QB"}, {"WR"}, True)
    report("QB <-> own TE", {"QB"}, {"TE"}, True)
    report("QB <-> own RB", {"QB"}, BACKS, True)
    report("WR/TE <-> own WR/TE", CATCHERS, CATCHERS, True)
    report("RB <-> own WR/TE", BACKS, CATCHERS, True)
    print("\nOPPOSING TEAM -- what a bring-back buys")
    report("QB <-> OPPOSING QB", {"QB"}, {"QB"}, False)
    report("QB <-> OPPOSING WR/TE", {"QB"}, CATCHERS, False)
    report("WR/TE <-> OPPOSING WR/TE", CATCHERS, CATCHERS, False)
    report("RB <-> OPPOSING RB", BACKS, BACKS, False)
    report("RB <-> OPPOSING WR/TE", BACKS, CATCHERS, False)

    # --- DST, which needs the team-week and schedule files -------------------
    scores = {}
    gpath = DATA / "games.json"
    if gpath.exists():
        for g in json.loads(gpath.read_text()):
            if g.get("game_type") != "REG":
                continue
            try:
                hs, as_ = int(g["home_score"]), int(g["away_score"])
            except (TypeError, ValueError, KeyError):
                continue
            scores[(int(g["season"]), int(g["week"]), g["home_team"])] = (hs, as_)
            scores[(int(g["season"]), int(g["week"]), g["away_team"])] = (as_, hs)
    dst = {}
    for yr in args.seasons:
        tpath = DATA / f"team_week_{yr}.json"
        if not tpath.exists():
            continue
        for t in json.loads(tpath.read_text()):
            if t.get("season_type") != "REG":
                continue
            key = (int(t["season"]), int(t["week"]), t["team"])
            if key in scores:
                dst[key] = actual_dst_points(t, scores[key][1])
    if not dst:
        print("\n  (no team_week/games data -- skipping DST)")
        return 0

    by_team = collections.defaultdict(list)
    opp_of = {}
    for x in recs:
        if x["pid"] in poolset:
            by_team[x["key"]].append(x)
        opp_of[x["key"]] = x["opp"]
    print("\nDST -- why 'never face your own offence' is a HARD rule")
    for label, sel, opposing in (("DST <-> OPPOSING QB", {"QB"}, True),
                                 ("DST <-> OPPOSING RB", BACKS, True),
                                 ("DST <-> OPPOSING WR/TE", CATCHERS, True),
                                 ("DST <-> own QB", {"QB"}, False),
                                 ("DST <-> own WR/TE", CATCHERS, False)):
        pairs = []
        for key, d in dst.items():
            s, w, team = key
            target = (s, w, opp_of[key]) if opposing and key in opp_of else key
            for x in by_team.get(target, []):
                if x["pos"] in sel:
                    pairs.append((d, x["dk"]))
        r, n = pearson(pairs)
        shown = f"{r:+.4f}" if r is not None else "n/a"
        print(f"  {label:36s} r={shown:>8s}  n={n}")
    print("\n  NOTE the known undercount in edge/nfl.py::actual_dst_points: "
          "nflverse has no\n  blocked-kick-caused column, so DST scores here "
          "are a little low. It biases the\n  LEVEL, not the sign or the "
          "ordering, which is what these correlations use.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
