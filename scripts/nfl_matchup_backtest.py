#!/usr/bin/env python3
"""Travel, rest, scheme "buzz saws" and coordinators -- against the closing spread.

Adam's question, 2026-10-12: is there any correlation with travel, specific
head coaches, or defensive/offensive coordinators who are a buzz saw for
particular offenses and defenses?

Head coaches were already tested and are dead (PICKEM_MODEL.md 5b: every
coach-record rule flipped sign between eras -- the line prices reputation).
This file tests what was never tried. Every rule below was written down
BEFORE its numbers were looked at, and names the side it takes:

TRAVEL / REST (nflverse schedule: kickoff, home/away rest days, site)
  T1 west_early   Pacific team (incl. ARI) away in the Eastern zone at <= 1pm ET
                  -> take the HOME team (body clock says 10am)
  T2 west_night   Pacific team vs an Eastern/Central team at >= 7pm ET
                  -> take the PACIFIC team (the classic circadian finding)
  T3 two_zones    road team crossing >= 2 time zones -> take the HOME team
  T4 rest_edge    one team has >= 3 more days of rest -> take the RESTED team

SCHEME "BUZZ SAW" (data/pbp_team_game.csv, season-to-date, prior weeks only)
  S1 style        For each offense: (its pass rate - league pass rate) x (the
                  opposing defence's pass EPA allowed - its rush EPA allowed).
                  Positive = the offence leans into what this defence does
                  worst; negative = it runs into the buzz saw. Take the side
                  whose net style edge is larger (both teams >= 3 games).
  S1 strong       same, only the third of games with the largest |edge|

COORDINATORS (data/nfl_coordinators.csv: Wikipedia team-season infoboxes)
  C1 rematch      The buzz saw claim itself: the same defensive coordinator
                  meeting the same offensive coordinator again. If that
                  offence fell >= 7 points short of its implied team total
                  last time, fade it now; if it beat it by >= 7, back it.
  C2 persistence  Correlation between the offence's points-vs-implied in one
                  meeting of a DC/OC pair and the next.

Train 2010-2019, validate 2020-2022, fresh 2025 (pbp-based rules need
data/pbp_team_game.csv, which starts 2013; 2025 pbp from --pbp-2025).
2023-2024 are excluded: the pick'em project's holdout, already looked at.
A rule means something only if it beats 50% in all three periods.

    python3 scripts/nfl_matchup_backtest.py [--pbp-2025 path] [--coords path]
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.nfl_script_backtest import HOLDOUT, ats, load_games, rec  # noqa: E402

PERIODS = {"train 2010-19": range(2010, 2020), "validate 2020-22": range(2020, 2023),
           "fresh 2025": range(2025, 2026)}
PBP = ROOT / "data" / "pbp_team_game.csv"
COORDS = ROOT / "data" / "nfl_coordinators.csv"

#: Hours behind Eastern at the home stadium during the season. ARI keeps
#: Mountain Standard all year (= Pacific for most of the season).
ZONE = {**{t: 0 for t in ("ATL", "BAL", "BUF", "CAR", "CIN", "CLE", "DET", "IND", "JAX", "MIA",
                          "NE", "NYG", "NYJ", "PHI", "PIT", "TB", "WAS")},
        **{t: 1 for t in ("CHI", "DAL", "GB", "HOU", "KC", "MIN", "NO", "TEN")},
        "DEN": 2, "ARI": 3,
        **{t: 3 for t in ("SEA", "SF", "LA", "LAC", "LV", "OAK", "SD", "STL")}}
ZONE["STL"] = 1                     # the Rams were Central in St. Louis


def zone(team: str, season: int) -> int:
    if team == "LV" and season <= 2019:
        return 3                    # Oakland Raiders, nflverse may say LV or OAK
    if team == "LA" and season <= 2015:
        return 1                    # St. Louis
    return ZONE.get(team, 0)


def travel_votes(r: dict) -> dict[str, str]:
    v = {}
    if r.get("location") == "Neutral":
        return v
    s, h, a = r["season"], r["home_team"], r["away_team"]
    zh, za = zone(h, s), zone(a, s)
    t = r["gametime"] or "13:00"
    if za == 3 and zh == 0 and t <= "13:00":
        v["T1 west_early"] = "home"
    if t >= "19:00":
        if za == 3 and zh <= 1:
            v["T2 west_night"] = "away"
        elif zh == 3 and za <= 1:
            v["T2 west_night"] = "home"
    if abs(zh - za) >= 2:
        v["T3 two_zones"] = "home"
    try:
        rh, ra = int(r["home_rest"]), int(r["away_rest"])
        if abs(rh - ra) >= 3:
            v["T4 rest_edge"] = "home" if rh > ra else "away"
    except (TypeError, ValueError):
        pass
    return v


def load_pbp(extra: Path | None) -> list[dict]:
    rows = list(csv.DictReader(PBP.open()))
    if extra and extra.exists():
        rows += list(csv.DictReader(extra.open()))
    for r in rows:
        r["season"], r["week"] = int(r["season"]), int(r["week"])
        for k in ("off_pass_plays", "off_rush_plays"):
            r[k] = int(float(r[k] or 0))
        for k in ("off_pass_epa", "off_rush_epa"):
            r[k] = float(r[k] or 0)
    return rows


def style_edges(pbp: list[dict]) -> dict[tuple, float]:
    """{(season, week, team): that team's OFFENSIVE style edge vs its opponent
    this week}, from games strictly before this week."""
    by_sw = defaultdict(list)
    for r in pbp:
        by_sw[(r["season"], r["week"])].append(r)
    out = {}
    for season in sorted({r["season"] for r in pbp}):
        off = defaultdict(lambda: [0, 0])               # pass plays, rush plays
        dfn = defaultdict(lambda: [0.0, 0, 0.0, 0])     # pass epa, n, rush epa, n (allowed)
        games = defaultdict(int)
        for wk in sorted({w for (s, w) in by_sw if s == season}):
            rows = by_sw[(season, wk)]
            lg_p = sum(v[0] for v in off.values())
            lg_n = lg_p + sum(v[1] for v in off.values())
            lg_pr = lg_p / lg_n if lg_n else None
            for r in rows:
                t, o = r["team"], r["opp"]
                if lg_pr is None or games[t] < 3 or games[o] < 3:
                    continue
                pr = off[t][0] / max(1, off[t][0] + off[t][1])
                d = dfn[o]
                dpe = d[0] / max(1, d[1])
                dre = d[2] / max(1, d[3])
                out[(season, wk, t)] = (pr - lg_pr) * (dpe - dre)
            for r in rows:                              # then add this week
                t, o = r["team"], r["opp"]
                off[t][0] += r["off_pass_plays"]
                off[t][1] += r["off_rush_plays"]
                dfn[o][0] += r["off_pass_epa"]
                dfn[o][1] += r["off_pass_plays"]
                dfn[o][2] += r["off_rush_epa"]
                dfn[o][3] += r["off_rush_plays"]
                games[t] += 1
    return out


def implied(r: dict) -> tuple[float, float] | None:
    """(home, away) implied team totals from the closing total and spread."""
    try:
        tot, sp = float(r["total_line"]), r["spread"]
    except (TypeError, ValueError):
        return None
    return (tot + sp) / 2, (tot - sp) / 2


def coordinator_tests(games, coords_path: Path):
    """C1/C2: repeat meetings of the same DC against the same OC."""
    if not coords_path.exists():
        print("\n(no coordinator file -- C1/C2 skipped)")
        return
    alias = {"OAK": "LV", "SD": "LAC", "STL": "LA"}
    co = {(int(r["season"]), r["team"]): r for r in csv.DictReader(coords_path.open())}
    meetings = defaultdict(list)      # (dc, oc) -> [(game, offence side, resid)]
    for r in sorted(games, key=lambda r: (r["season"], r["week"])):
        imp = implied(r)
        if imp is None:
            continue
        for side, off_t, def_t, pts, exp in (
                ("home", r["home_team"], r["away_team"], int(r["home_score"]), imp[0]),
                ("away", r["away_team"], r["home_team"], int(r["away_score"]), imp[1])):
            o = co.get((r["season"], alias.get(off_t, off_t)))
            d = co.get((r["season"], alias.get(def_t, def_t)))
            if not o or not d or not o["oc"] or not d["dc"]:
                continue
            for oc in o["oc"].split("; "):
                meetings[(d["dc"].split("; ")[0], oc)].append((r, side, pts - exp))
    c1 = {p: [] for p in PERIODS}
    pairs = []
    for key, ms in meetings.items():
        for prev, cur in zip(ms, ms[1:]):
            pairs.append((prev[2], cur[2]))
            r, side, _ = cur
            period = next((p for p, yrs in PERIODS.items() if r["season"] in yrs), None)
            if period is None or abs(prev[2]) < 7:
                continue
            back = prev[2] > 0
            take = side if back else ("away" if side == "home" else "home")
            c1[period].append(ats(r, take))
    print("\nCOORDINATORS (same DC vs same OC, consecutive meetings)")
    print(f"  {len(meetings)} DC/OC pairs, {len(pairs)} repeat meetings")
    print(f"  {'C1 rematch':<14}" + "".join(f"{rec(c1[p]):>28}" for p in PERIODS))
    if len(pairs) > 2:
        xs, ys = zip(*pairs)
        mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
        cov = sum((x - mx) * (y - my) for x, y in pairs)
        sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
        sy = math.sqrt(sum((y - my) ** 2 for y in ys))
        rho = cov / (sx * sy)
        se = 1 / math.sqrt(len(pairs) - 3)
        print(f"  C2 persistence: corr(offence vs implied, meeting k -> k+1) = {rho:+.3f} "
              f"(95% CI {math.tanh(math.atanh(rho) - 1.96 * se):+.3f} to "
              f"{math.tanh(math.atanh(rho) + 1.96 * se):+.3f}, n={len(pairs)})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--pbp-2025", type=Path, help="2025 team-game pbp aggregate "
                    "(scripts/pickem_pbp_collect.collect_season(2025))")
    ap.add_argument("--coords", type=Path, default=COORDS)
    args = ap.parse_args()

    games = [g for g in load_games() if g["season"] not in HOLDOUT
             and any(g["season"] in yrs for yrs in PERIODS.values())]
    edges = style_edges(load_pbp(args.pbp_2025))
    rules = ["T1 west_early", "T2 west_night", "T3 two_zones", "T4 rest_edge",
             "S1 style", "S1 strong"]
    res = {k: {p: [] for p in PERIODS} for k in rules}
    nets = defaultdict(list)
    staged = []
    for r in games:
        period = next(p for p, yrs in PERIODS.items() if r["season"] in yrs)
        for k, side in travel_votes(r).items():
            res[k][period].append(ats(r, side))
        eh = edges.get((r["season"], r["week"], r["home_team"]))
        ea = edges.get((r["season"], r["week"], r["away_team"]))
        if eh is not None and ea is not None and eh != ea:
            net = eh - ea
            nets[r["season"]].append(abs(net))
            staged.append((r, period, net))
    cut = {s: sorted(v)[len(v) * 2 // 3] for s, v in nets.items()}
    for r, period, net in staged:
        side = "home" if net > 0 else "away"
        res["S1 style"][period].append(ats(r, side))
        if abs(net) >= cut[r["season"]]:
            res["S1 strong"][period].append(ats(r, side))

    print("=" * 98)
    print("TRAVEL, REST AND SCHEME MATCHUPS vs THE CLOSING SPREAD")
    print("=" * 98)
    print(f"{'rule':<14}" + "".join(f"{p:>28}" for p in PERIODS))
    for k in rules:
        print(f"{k:<14}" + "".join(f"{rec(res[k][p]):>28}" for p in PERIODS))
    coordinator_tests(games, args.coords)
    print("\nBar: beats 50% in all three periods. 2023-24 excluded (pick'em holdout).")


if __name__ == "__main__":
    main()
