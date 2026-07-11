#!/usr/bin/env python3
"""Does hitter projection quality (corr/MAE) or dispersion vary with slate
size (number of games that day)? Reuses the exact leak-free "+2025-to-date"
shape from dfs_hitter_backtest.py (2023-24 pooled skill baseline blended with
in-season-to-date game logs, no lookahead), applied to a stratified sample of
2025 dates chosen to cover the full range of real slate sizes (3-19 games),
oversampling rare small-slate days since they're a small fraction of the
season. Free (statsapi only), reuses scripts/dfs_hitter_backtest.py's cache.

Usage: python3 scripts/dfs_slate_size_backtest.py
"""
import sys
import json
import statistics
from pathlib import Path
from collections import defaultdict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from edge import dfs  # noqa: E402
from scripts.dfs_hitter_backtest import (  # noqa: E402
    load_cache, save_cache, game_data, gamelog_upto,
)

DATES_FILE = Path("/tmp/claude-1000/-home-asr-Downloads-edge-search/103e97b6-c937-4f4d-ae45-f3568fc0605c/scratchpad/slate_dates.json")


def games_for_date(date):
    sched = dfs._get(f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&startDate={date}&endDate={date}")
    out = []
    for d in sched.get("dates", []):
        for g in d.get("games", []):
            if g.get("status", {}).get("abstractGameState") == "Final" and g.get("gameType") == "R":
                out.append(g["gamePk"])
    return out


def main():
    dates_spec = json.loads(DATES_FILE.read_text())
    all_dates = sorted(set(dates_spec["small"] + dates_spec["medium"] + dates_spec["large"]))
    print(f"{len(all_dates)} dates selected across small/medium/large slate buckets", flush=True)

    cache = load_cache()
    old_skill, old_lg = dfs.pooled_skill_rates((2023, 2024), cache_path=str(ROOT / "data/bt_skill_2023_2024.json"))
    old_k9 = dfs.pitcher_k9(2024, cache_path=str(ROOT / "data/bt_k9_2024.json"))
    park = dfs.park_runs(2025)

    rows = []  # (date, n_games_that_day, proj, actual)
    for di, date in enumerate(all_dates):
        pks = games_for_date(date)
        n_games = len(pks)
        for pk in pks:
            try:
                sides, actuals = game_data(pk)
            except Exception:
                continue
            for side, other in (("home", "away"), ("away", "home")):
                team = sides[side]; opp = sides[other]
                opp_starter = opp["starter_id"]
                pk_val = park.get(str(team["team_id"]), 1.0) if side == "home" else park.get(str(opp["team_id"]), 1.0)
                for pid, name, slot in team["lineup"]:
                    actual = actuals.get(pid)
                    if actual is None:
                        continue
                    skill_old = old_skill.get(str(pid), old_lg)
                    k9_old = old_k9.get(str(opp_starter), dfs.LG_K9) if opp_starter else dfs.LG_K9
                    pts25, pa25 = gamelog_upto(pid, 2025, date, "hitting", cache, "hlog")
                    if pa25 >= 20:
                        w25 = pa25 / (pa25 + 300)
                        skill_new = (1 - w25) * skill_old + w25 * (pts25 / pa25)
                    else:
                        skill_new = skill_old
                    k25, outs25 = gamelog_upto(opp_starter, 2025, date, "pitching", cache, "plog") if opp_starter else (0, 0)
                    if outs25 >= 15:
                        k9_25 = 9 * k25 / outs25
                        w = outs25 / (outs25 + 180)
                        k9_new = (1 - w) * k9_old + w * k9_25
                    else:
                        k9_new = k9_old
                    proj_new = dfs.project_hitter_skill(skill_new, slot, pk_val, k9_new)
                    rows.append((date, n_games, proj_new, actual))
        if (di + 1) % 10 == 0:
            save_cache(cache)
            print(f"  {di+1}/{len(all_dates)} dates, {len(rows)} hitter rows so far", flush=True)

    save_cache(cache)
    Path(ROOT / "data/slate_size_backtest_rows.json").write_text(json.dumps(rows))
    print(f"\nTOTAL: {len(rows)} hitter-games -> data/slate_size_backtest_rows.json\n", flush=True)

    def corr(xs, ys):
        n = len(xs)
        if n < 3:
            return float("nan")
        mx, my = statistics.mean(xs), statistics.mean(ys)
        cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / n
        sx, sy = statistics.pstdev(xs), statistics.pstdev(ys)
        return cov / (sx * sy) if sx and sy else float("nan")

    buckets = [("small (<=8)", lambda n: n <= 8), ("medium (9-13)", lambda n: 9 <= n <= 13), ("large (14+)", lambda n: n >= 14)]
    print(f"{'bucket':16} {'n_games/day range':18} {'n_rows':8} {'corr':8} {'MAE':7} {'std(proj)':10} {'std(act)':9}")
    for label, pred in buckets:
        br = [r for r in rows if pred(r[1])]
        if not br:
            continue
        proj = [r[2] for r in br]; act = [r[3] for r in br]
        mae = statistics.mean(abs(p - a) for p, a in zip(proj, act))
        c = corr(proj, act)
        ns = sorted(set(r[1] for r in br))
        print(f"{label:16} {str(ns[0])+'-'+str(ns[-1]):18} {len(br):8} {c:8.3f} {mae:7.3f} {statistics.pstdev(proj):10.3f} {statistics.pstdev(act):9.3f}")


if __name__ == "__main__":
    main()
