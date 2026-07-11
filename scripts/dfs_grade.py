#!/usr/bin/env python3
"""Calibrate DFS projections vs ACTUAL DK points for a past slate.

    python3 scripts/dfs_grade.py 2026-06-28

Reads data/dfs_proj_log.csv for the date, pulls final box scores (statsapi,
free), computes each player's real DK fantasy points, and reports projection
accuracy + how the logged cash/GPP lineups actually scored. Run after games end.
"""
import csv
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs  # noqa: E402


def actuals_for_date(date: str) -> dict:
    sched = dfs._get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}&hydrate=decisions")
    out, finals = {}, 0
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            if g.get("status", {}).get("abstractGameState") != "Final":
                continue
            finals += 1
            win = (g.get("decisions", {}).get("winner") or {}).get("id")
            try:
                box = dfs._get(f"https://statsapi.mlb.com/api/v1/game/{g['gamePk']}/boxscore")
            except Exception:
                continue
            for side in ("home", "away"):
                for pl in box["teams"][side]["players"].values():
                    st = pl.get("stats", {})
                    pts = 0.0
                    if st.get("batting", {}).get("plateAppearances"):
                        pts += dfs.actual_hitter_points(st["batting"])
                    pit = st.get("pitching", {})
                    if pit and pit.get("inningsPitched", "0.0") not in ("0.0", "-", None):
                        pts += dfs.actual_pitcher_points(pit, won=(pl["person"]["id"] == win))
                    if st.get("batting") or pit:
                        out[dfs.norm(pl["person"]["fullName"])] = round(pts, 1)
    return out, finals


def date_all_final(date: str) -> bool:
    """True only when every regular-season game that date is Final -- callers
    that persist actuals to disk must check this first. Found the hard way:
    2026-07-08's actuals cache was written mid-slate (1 final game, 25 players)
    and silently served that stub forever after, zeroing every lineup score
    computed from it."""
    sched = dfs._get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={date}")
    games = [g for d in sched.get("dates", []) for g in d.get("games", [])
             if g.get("gameType", "R") == "R"]
    return bool(games) and all(g.get("status", {}).get("abstractGameState") == "Final" for g in games)


def pearson(a, b):
    n = len(a)
    if n < 3:
        return float("nan")
    ma, mb = statistics.mean(a), statistics.mean(b)
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b)) / n
    sa, sb = statistics.pstdev(a), statistics.pstdev(b)
    return cov / (sa * sb) if sa and sb else float("nan")


def main():
    date = sys.argv[1] if len(sys.argv) > 1 else None
    log = [r for r in csv.DictReader(open(ROOT / "data/dfs_proj_log.csv"))] if (ROOT / "data/dfs_proj_log.csv").exists() else []
    rows = [r for r in log if not date or r["date"] == date]
    if not rows:
        sys.exit(f"no logged projections for {date}")
    date = date or rows[-1]["date"]
    act, finals = actuals_for_date(date)
    print(f"{date}: {finals} final games | {len(act)} players with actuals\n")

    m = [(float(r["proj"]), act[dfs.norm(r["player"])], r) for r in rows if dfs.norm(r["player"]) in act]
    if not m:
        print("no overlap yet (games not final?)"); return
    proj = [x[0] for x in m]; actual = [x[1] for x in m]
    err = [a - p for p, a in zip(proj, actual)]
    print(f"matched {len(m)} players")
    print(f"  corr(proj, actual) = {pearson(proj, actual):+.3f}")
    print(f"  MAE = {statistics.mean(abs(e) for e in err):.2f}  |  bias (actual-proj) = {statistics.mean(err):+.2f}")
    for label, sel in (("pitchers", lambda r: "P" in r["pos"]), ("hitters", lambda r: "P" not in r["pos"])):
        sub = [(p, a) for p, a, r in m if sel(r)]
        if len(sub) >= 3:
            print(f"  {label:8} n={len(sub):3} corr={pearson([s[0] for s in sub], [s[1] for s in sub]):+.3f} "
                  f"MAE={statistics.mean(abs(a-p) for p,a in sub):.2f}")

    lf = ROOT / f"data/dfs_lineups_{date}.csv"
    if lf.exists():
        print("\nactual lineup scores:")
        tot = {}
        for r in csv.DictReader(open(lf)):
            tot.setdefault(r["mode"], 0.0)
            tot[r["mode"]] += act.get(dfs.norm(r["player"]), 0.0)
        for mode, s in tot.items():
            print(f"  {mode.upper():4} actual {round(s,1)} DK pts")


if __name__ == "__main__":
    main()
