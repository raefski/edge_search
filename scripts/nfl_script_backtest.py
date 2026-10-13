#!/usr/bin/env python3
"""If the NFL nudged results toward TV revenue, would it show up against the spread?

Adam's question, 2026-10-12: design the season a revenue-maximising league
would script (the "Vince McMahon" model) and backtest whether real results
lean that way BEYOND what the betting line already expects. If they do, it is
an edge; if not, the market already prices whatever the league wants.

THE SCRIPT -- what a league maximising TV, ad and sponsor money would want,
each translated into a side to take against the closing spread:

  H1 brand      The teams the league itself schedules in primetime most (its
                own revealed belief about who draws) win more. Defined from the
                PREVIOUS season's schedule, so nothing leaks. -> take the brand
                team when exactly one side is a brand team.
  H2 close      National-window games (Thu/Mon/Sun night, Thanksgiving,
                Saturday) stay close, because blowouts lose the audience the
                ads are sold against. -> take the underdog in national windows.
  H3 race       December stays alive: from week 12, a team still in the race
                (.400-.700) beats a team that is out of it (< .350). -> take
                the team in the race.
  H4 market     Big TV markets win. -> take the top-10-DMA side against a
                small-market side.
  SCRIPT        Every hypothesis that has an opinion on a game votes; take the
                majority side.

Graded against nflverse's closing spread (spread_line = expected HOME margin).
Train 2010-2019, validate 2020-2022, fresh check 2025. 2023-2024 are NOT
read: they are the pick'em project's spent-once holdout (PICKEM_MODEL.md s3).
A rule only means something if it beats 50% in all three periods.

    python3 scripts/nfl_script_backtest.py
"""
from __future__ import annotations

import csv
import io
import math
import sys
import urllib.request
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GAMES_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"
CACHE = ROOT / "data" / "cache" / "nflverse_games.csv"

PERIODS = {"train 2010-19": range(2010, 2020), "validate 2020-22": range(2020, 2023),
           "fresh 2025": range(2025, 2026)}
HOLDOUT = {2023, 2024}
#: Top-10 US TV markets (Nielsen DMA ranking), by franchise.
BIG_MARKET = {"NYG", "NYJ", "LA", "LAR", "LAC", "STL", "SD", "OAK", "CHI", "PHI", "DAL",
              "HOU", "ATL", "SF", "WAS", "NE", "TB"}
BRAND_TOP_N = 8


def load_games() -> list[dict]:
    if CACHE.exists():
        text = CACHE.read_text()
    else:
        text = urllib.request.urlopen(GAMES_URL, timeout=120).read().decode()
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(text)
    out = []
    for r in csv.DictReader(io.StringIO(text)):
        if r["game_type"] != "REG" or not r["home_score"] or not r["spread_line"]:
            continue
        r["season"], r["week"] = int(r["season"]), int(r["week"])
        r["margin"] = int(r["home_score"]) - int(r["away_score"])
        r["spread"] = float(r["spread_line"])            # expected HOME margin
        out.append(r)
    return out


def national(r: dict) -> bool:
    day, t = r["weekday"], r["gametime"] or "13:00"
    return day in ("Thursday", "Monday", "Saturday", "Wednesday", "Tuesday", "Friday") \
        or (day == "Sunday" and t >= "19:00")


def brand_teams(games: list[dict]) -> dict[int, set]:
    """{season: the BRAND_TOP_N teams with the most national-window games the
    season BEFORE} -- the league's own revealed belief about who draws."""
    count: dict[int, dict] = defaultdict(lambda: defaultdict(int))
    for r in games:
        if national(r):
            count[r["season"]][r["home_team"]] += 1
            count[r["season"]][r["away_team"]] += 1
    out = {}
    for s in {r["season"] for r in games}:
        prev = count.get(s - 1)
        if prev:
            out[s] = {t for t, _ in sorted(prev.items(), key=lambda kv: -kv[1])[:BRAND_TOP_N]}
    return out


def records_before(games: list[dict]) -> dict[tuple, tuple]:
    """{(season, week, team): (wins, games)} entering that week -- leak-free."""
    by_season = defaultdict(list)
    for r in games:
        by_season[r["season"]].append(r)
    out = {}
    for s, rows in by_season.items():
        wins, played = defaultdict(float), defaultdict(int)
        for wk in sorted({r["week"] for r in rows}):
            this = [r for r in rows if r["week"] == wk]
            for r in this:
                for t in (r["home_team"], r["away_team"]):
                    out[(s, wk, t)] = (wins[t], played[t])
            for r in this:
                h, a = r["home_team"], r["away_team"]
                played[h] += 1
                played[a] += 1
                if r["margin"] > 0:
                    wins[h] += 1
                elif r["margin"] < 0:
                    wins[a] += 1
                else:
                    wins[h] += .5
                    wins[a] += .5
    return out


def ats(r: dict, side: str) -> str:
    cover = r["margin"] - r["spread"]
    if cover == 0:
        return "P"
    return "W" if (cover > 0) == (side == "home") else "L"


def votes(r, brands, recs) -> dict[str, str]:
    """{hypothesis: side} for every hypothesis with an opinion on this game."""
    v = {}
    h, a = r["home_team"], r["away_team"]
    b = brands.get(r["season"], set())
    if (h in b) != (a in b):
        v["H1 brand"] = "home" if h in b else "away"
    if national(r) and r["spread"] != 0:
        v["H2 close"] = "away" if r["spread"] > 0 else "home"     # the underdog
    if r["week"] >= 12:
        def pct(t):
            w, g = recs.get((r["season"], r["week"], t), (0, 0))
            return w / g if g else .5
        ph, pa = pct(h), pct(a)
        alive = lambda p: .4 <= p <= .7       # noqa: E731
        if alive(ph) and pa < .35:
            v["H3 race"] = "home"
        elif alive(pa) and ph < .35:
            v["H3 race"] = "away"
    if (h in BIG_MARKET) != (a in BIG_MARKET):
        v["H4 market"] = "home" if h in BIG_MARKET else "away"
    return v


def wilson(w, l, z=1.96):
    n = w + l
    if not n:
        return (0, 1)
    p = w / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - m) / d, (c + m) / d)


def rec(res: list[str]) -> str:
    w, l = res.count("W"), res.count("L")
    if not w + l:
        return "        --"
    lo, hi = wilson(w, l)
    return f"{w:>4}-{l:<4} {w / (w + l):5.1%} [{lo:.0%},{hi:.0%}]"


def main() -> None:
    every = load_games()
    # Brand teams come from the PREVIOUS season's schedule (who the league put
    # in primetime), not its results -- so 2024's schedule may define 2025's
    # brands without reading a single 2023-24 outcome.
    brands = brand_teams(every)
    games = [g for g in every if g["season"] not in HOLDOUT]
    recs = records_before(games)
    hyps = ["H1 brand", "H2 close", "H3 race", "H4 market", "SCRIPT"]
    res = {h: {p: [] for p in PERIODS} for h in hyps}
    close_abs = {p: {"national": [], "daytime": []} for p in PERIODS}
    for r in games:
        period = next((p for p, yrs in PERIODS.items() if r["season"] in yrs), None)
        if period is None:
            continue
        v = votes(r, brands, recs)
        for h, side in v.items():
            res[h][period].append(ats(r, side))
        if v:
            homes = sum(s == "home" for s in v.values())
            if homes * 2 != len(v):
                res["SCRIPT"][period].append(ats(r, "home" if homes * 2 > len(v) else "away"))
        close_abs[period]["national" if national(r) else "daytime"].append(
            abs(r["margin"] - r["spread"]))

    print("=" * 96)
    print("THE SCRIPTED-SEASON HYPOTHESES vs THE CLOSING SPREAD")
    print("=" * 96)
    print(f"{'side taken':<12}" + "".join(f"{p:>28}" for p in PERIODS))
    for h in hyps:
        print(f"{h:<12}" + "".join(f"{rec(res[h][p]):>28}" for p in PERIODS))
    print("\nH2's other prediction -- national games finish nearer the spread "
          "(mean |margin - spread|, points):")
    for p in PERIODS:
        n, d = close_abs[p]["national"], close_abs[p]["daytime"]
        print(f"  {p:<17} national {sum(n) / len(n):5.2f} (n={len(n)})   "
              f"daytime {sum(d) / len(d):5.2f} (n={len(d)})")
    print("\nBrand teams by season (top 8 national-window counts the season before):")
    for s in (2015, 2020, 2025):
        print(f"  {s}: {', '.join(sorted(brands.get(s, [])))}")
    print("\nBar: beats 50% in all three periods. 2023-24 untouched (pick'em holdout).")


if __name__ == "__main__":
    main()
