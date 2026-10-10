#!/usr/bin/env python3
"""What would 150 lineups have done? A multi-entry backtest on real NFL contests.

    python3 scripts/multi_entry_backtest.py                 # every configured slate
    python3 scripts/multi_entry_backtest.py --n 150 --slate 2026-09-27

For each slate: rebuild the board exactly as the model saw it before lock (the
odds store's last scan before kickoff + DraftKings' salaries -- the method that
reproduced Adam's 2026-09-27 primetime lineups player for player), build N
different GPP lineups, score them with the real DK points from the contest
export, drop each into the REAL standings, and pay it from the contest's REAL
payout table (api.draftkings.com/contests/v1/contests/<id>).

HOW THE N LINEUPS ARE BUILT (the usual mass-entry recipe, not the app's)
  * each lineup's projections get independent noise (PROJ_NOISE), so the
    search lands on a different optimum each time;
  * quarterbacks are assigned in proportion to their best single-lineup GPP
    score among the top QB_POOL, so the portfolio is spread over several stacks;
  * no player in more than MAX_EXPOSURE of the lineups;
  * every lineup differs from every earlier one by at least MIN_UNIQUE players.

WHAT THIS CAN AND CANNOT SAY
It places 150 lineups in the field of the contest Adam actually played -- a
$1 SINGLE-entry GPP. A real 150-max contest has a sharper field (people running
the same tools) and a flatter-bottom, fatter-top payout. So this is an upper
bound on what the same lineups would do there, and three slates is nowhere near
enough to call a GPP edge: the answer is a range, not a verdict.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import subprocess
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs, dfs_nfl_theory as theory, dfs_opt_nfl, dfs_run_nfl as R  # noqa: E402
from edge.dfs_contest import parse_contest_file  # noqa: E402
from edge.names import norm as bare  # noqa: E402
from edge.odds.source import ScrapedOddsClient  # noqa: E402
from edge.odds.store import OddsStore  # noqa: E402

#: date, draft group, the last dfs_nfl scan before lock, the GPP export.
SLATES = {
    "2026-09-13": (151307, 1006, "193028246"),
    "2026-09-20": (153428, 2446, "195661382"),
    "2026-09-27": (153769, 3276, "195921991"),
}
PROJ_NOISE = 0.18
QB_POOL = 8
MAX_EXPOSURE = 0.40
MIN_UNIQUE = 2
PAYOUT_CACHE = ROOT / "data" / "cache" / "dk_payouts"


# --- inputs -----------------------------------------------------------------

def salaries_for(gid: int, date: str) -> dict:
    """DK salaries: the snapshot on disk, else its last copy in git, else
    rebuilt from the forward-test log (the 9/13 slate predates snapshots)."""
    snap = ROOT / "data" / "draftables_snapshot" / f"{gid}.json"
    if not snap.exists():
        rel = f"data/draftables_snapshot/{gid}.json"
        commits = subprocess.run(["git", "log", "--all", "--pretty=%h", "--", rel], cwd=ROOT,
                                 capture_output=True, text=True).stdout.split()
        for c in commits:
            blob = subprocess.run(["git", "show", f"{c}:{rel}"], cwd=ROOT,
                                  capture_output=True, text=True).stdout
            if blob.strip():
                snap.write_text(blob)
                break
    if snap.exists():
        try:
            return dfs.fetch_draftables(gid)
        finally:
            snap.unlink()          # the publisher prunes these; leave no trace
    out = {}
    with (ROOT / "data" / "dfs_proj_log_nfl.csv").open(newline="") as fh:
        for r in csv.DictReader(fh):
            if r["date"] != date or str(r["gid"]) != str(gid):
                continue
            a, b = sorted((r["team"], r["opp_team"]))
            out[dfs.norm(r["player"])] = {
                "name": r["player"], "salary": int(float(r["salary"])), "position": r["dk_pos"],
                "team": r["team"], "game": r["game"], "matchup": f"{a} @ {b}", "start": None,
                "dk_fppg": None, "player_id": None, "dk_status": None}
    return out


def payouts(contest_id: str) -> list[tuple[int, int, float]]:
    """[(min_rank, max_rank, dollars)] from DraftKings, cached."""
    path = PAYOUT_CACHE / f"{contest_id}.json"
    if path.exists():
        return [tuple(t) for t in json.loads(path.read_text())]
    d = dfs._get(f"https://api.draftkings.com/contests/v1/contests/{contest_id}?format=json")
    cd = d.get("contestDetail") or d
    tiers = [(t["minPosition"], t["maxPosition"],
              sum(p.get("value", 0) for p in t.get("payoutDescriptions", [])))
             for t in cd.get("payoutSummary", [])]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(tiers))
    return tiers


def leaderboard(path: str) -> list[float]:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        return sorted((float(r["Points"]) for r in csv.DictReader(fh) if r.get("EntryId")),
                      reverse=True)


def pay_for(score: float, field: list[float], tiers) -> tuple[int, float]:
    """Rank a new entry would take (ties share the better rank) and its payout."""
    lo, hi = 0, len(field)
    while lo < hi:
        mid = (lo + hi) // 2
        if field[mid] > score:
            lo = mid + 1
        else:
            hi = mid
    rank = lo + 1
    return rank, next((v for a, b, v in tiers if a <= rank <= b), 0.0)


# --- the portfolio ----------------------------------------------------------

def build_portfolio(pool: list[dict], n: int, seed: int = 0, iters: int = 300) -> list[list[dict]]:
    rng = random.Random(seed)
    qbs = [p for p in pool if "QB" in p["pos"]]
    best = []
    for q in sorted(qbs, key=lambda p: -p["proj"])[:QB_POOL * 2]:
        res = dfs_opt_nfl.optimize(pool, mode="gpp", stack_qb=q["name"], iters=iters, seed=seed)
        if res:
            best.append((res["ceil"], q["name"]))
    best = sorted(best, reverse=True)[:QB_POOL]
    floor_ceil = min(c for c, _ in best)
    weights = {name: (c - floor_ceil + 5.0) for c, name in best}
    total = sum(weights.values())
    plan = [name for name, w in weights.items() for _ in range(round(n * w / total))]
    while len(plan) < n:
        plan.append(best[0][1])
    rng.shuffle(plan)

    lineups, exposure = [], Counter()
    attempts = 0
    for qb in plan[:n]:
        for _try in range(8):
            attempts += 1
            capped = {k for k, c in exposure.items() if c >= MAX_EXPOSURE * n}
            noisy = []
            for p in pool:
                if p["name"] in capped and p["name"] != qb:
                    continue
                q = dict(p)
                q["proj"] = p["proj"] * max(0.3, rng.gauss(1.0, PROJ_NOISE))
                noisy.append(q)
            res = dfs_opt_nfl.optimize(noisy, mode="gpp", stack_qb=qb, iters=iters,
                                       seed=rng.randrange(10**6))
            if not res:
                continue
            names = {p["name"] for p, _ in res["lineup"]}
            if any(len(names & {p["name"] for p in lu}) > 9 - MIN_UNIQUE for lu in lineups):
                continue
            lineups.append([p for p, _ in res["lineup"]])
            exposure.update(names)
            break
    return lineups


def run_slate(date: str, n: int, iters: int) -> dict:
    gid, scan_id, contest = SLATES[date]
    client = ScrapedOddsClient(OddsStore(), "dfs_nfl", scan_id=scan_id)
    salaries = salaries_for(gid, date)
    pool, stats = R.build_pool(client, salaries)
    games = R.slate_games(salaries)
    theory.add_ownership(pool, cap=theory.slate_cap(len(games)))
    single = dfs_opt_nfl.optimize(pool, mode="gpp", iters=700, seed=0)
    lineups = build_portfolio(pool, n, iters=iters)

    board = parse_contest_file(ROOT / "data" / f"contest-standings-{contest}.csv")
    field = leaderboard(str(ROOT / "data" / f"contest-standings-{contest}.csv"))
    tiers = payouts(contest)

    def score(players):
        missing = [p["name"] for p in players if bare(p["name"]) not in board]
        return sum(board.get(bare(p["name"]), {}).get("fpts", 0.0) for p in players), missing

    results, missing_all = [], Counter()
    for lu in lineups:
        pts, missing = score(lu)
        missing_all.update(missing)
        rank, pay = pay_for(pts, field, tiers)
        results.append({"pts": pts, "rank": rank, "pay": pay})
    s_pts, _ = score([p for p, _ in single["lineup"]]) if single else (0.0, [])
    s_rank, s_pay = pay_for(s_pts, field, tiers)
    adam = next(((int(r["Rank"]), float(r["Points"])) for r in csv.DictReader(
        open(ROOT / "data" / f"contest-standings-{contest}.csv", encoding="utf-8-sig"))
        if r.get("EntryName", "").lower().startswith("gorillabiscuit")), None)
    return {"date": date, "contest": contest, "field": len(field), "scores": field,
            "pool": len(pool),
            "results": results, "single": (s_pts, s_rank, s_pay), "adam": adam,
            "missing": missing_all, "tiers": tiers,
            "expos": Counter(p["name"] for lu in lineups for p in lu)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--slate", default=None)
    ap.add_argument("--iters", type=int, default=300)
    args = ap.parse_args()
    grand_fee = grand_pay = 0.0
    for date in ([args.slate] if args.slate else sorted(SLATES)):
        r = run_slate(date, args.n, args.iters)
        res, field = r["results"], r["field"]
        fee = len(res) * 1.0
        pay = sum(x["pay"] for x in res)
        grand_fee += fee
        grand_pay += pay
        top = lambda f: sum(x["rank"] <= f * field for x in res)
        print(f"\n{date}  contest {r['contest']} ({field:,} entries, $1, pays "
              f"{r['tiers'][-1][1]:,} places)  pool {r['pool']}  lineups built {len(res)}")
        print(f"  {len(res)} lineups: paid ${pay:,.2f} on ${fee:,.0f}  ROI {pay / fee - 1:+.1%}  "
              f"cashed {sum(x['pay'] > 0 for x in res)}/{len(res)} "
              f"({sum(x['pay'] > 0 for x in res) / len(res):.0%}; field {r['tiers'][-1][1] / field:.0%})")
        print(f"  best rank {min(x['rank'] for x in res):,}  top 1%: {top(0.01)}  "
              f"top 0.1%: {top(0.001)}  median pts {sorted(x['pts'] for x in res)[len(res) // 2]:.1f}  "
              f"(field median {r['scores'][field // 2]:.1f}, winner {r['scores'][0]:.1f})")
        s_pts, s_rank, s_pay = r["single"]
        print(f"  single model GPP lineup: {s_pts:.1f} pts, rank {s_rank:,}, paid ${s_pay:.2f}"
              + (f"  | Adam's entry: rank {r['adam'][0]:,} ({r['adam'][1]:.1f})" if r["adam"] else ""))
        print("  top exposures: " + ", ".join(f"{k} {v / len(res):.0%}"
                                              for k, v in r["expos"].most_common(6)))
        if r["missing"]:
            print(f"  (players on nobody's lineup, scored 0: {dict(r['missing'].most_common(4))})")
    if grand_fee:
        print(f"\nALL SLATES: paid ${grand_pay:,.2f} on ${grand_fee:,.0f} -> ROI "
              f"{grand_pay / grand_fee - 1:+.1%}  (a random entry's expected ROI is the rake: "
              f"$15K prizes / 17,835 entries = -15.9%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
