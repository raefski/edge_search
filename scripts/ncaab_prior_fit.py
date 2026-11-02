#!/usr/bin/env python3
"""Check the college no-prop projection against the prop market, and refit its constants.

    python3 scripts/ncaab_prior_fit.py --fetch      # (re)write data/ncaab_torvik_<year>.json
    python3 scripts/ncaab_prior_fit.py --carryover  # refit CARRYOVER, hold out a season
    python3 scripts/ncaab_prior_fit.py <draft group>    # hide each game's props, predict them

The market check is a hold-out inside one night: every game with props has its props
removed, its players are projected exactly as a no-prop player is (edge/dfs_run_ncaab),
and the result is scored against the market's number for them. The market is not the
outcome and the priced players are the book's choice, so it says which rule agrees with
the books, not how good the rule is. Grade outcomes with scripts/nba_calibration.py
--sport ncaab once a contest export exists.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import numpy as np  # noqa: E402

from edge import dfs, dfs_slate, dfs_run_ncaab as R, ncaab_sim, ncaab_torvik as T  # noqa: E402
from edge.arb import http as requests  # noqa: E402
from edge.ncaab_names import Audit, Rosters  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/130.0 Safari/537.36"}


def season_rows(year: int) -> list[dict]:
    """Rows with Torvik's player id, which the stored file leaves out."""
    r = requests.get(T.URL.format(year=year), headers=UA, timeout=40)
    r.raise_for_status()
    raw = [x for x in csv.reader(io.StringIO(r.text)) if len(x) >= 65]
    ids = {(x[0], x[1]): x[32] for x in raw}
    rows = T.parse_csv(r.text)
    for x in rows:
        x["pid"] = ids.get((x["name"], x["team"]))
        x["fp"] = T.dk_points(x)
    return rows


def fetch(year: int) -> None:
    r = requests.get(T.URL.format(year=year), headers=UA, timeout=40)
    r.raise_for_status()
    rows = T.parse_csv(r.text)
    if len(rows) < 1000:
        raise SystemExit(f"Torvik {year}: only {len(rows)} rows, not writing")
    Path(str(T.PATH).format(year=year)).write_text(json.dumps(rows, separators=(",", ":")))
    print(f"wrote {len(rows)} rows for {year}")


def carryover() -> None:
    def pairs(a, b):
        was = {x["pid"]: x for x in season_rows(a) if x["pid"]}
        return [(was[x["pid"]], x) for x in season_rows(b)
                if x["pid"] in was and was[x["pid"]]["gp"] >= 8 and x["gp"] >= 8]

    def kind(o, x):
        if o["team"] == x["team"]:
            return "same"
        a, b = R.tier(o["conf"]), R.tier(x["conf"])
        return "up" if b > a else "down" if b < a else "side"

    train, test = pairs(2025, 2026), pairs(2024, 2025)
    for k in ("same", "up", "side", "down"):
        s = [p for p in train if kind(*p) == k]
        X = np.array([[1.0, o["fp"]] for o, _ in s])
        c = np.linalg.lstsq(X, np.array([x["fp"] for _, x in s]), rcond=None)[0]
        t = [p for p in test if kind(*p) == k]
        Xt = np.array([[1.0, o["fp"]] for o, _ in t])
        y = np.array([x["fp"] for _, x in t])
        print(f"{k:5} train n={len(s):5d}  {c[0]:6.2f} + {c[1]:.3f} FP  | held out n={len(t):5d}  MAE "
              f"{np.abs(Xt @ c - y).mean():.2f}  vs raw FPPG {np.abs(Xt[:, 1] - y).mean():.2f}  "
              f"bias {(Xt @ c - y).mean():+.2f}")


def market_players(gid: int) -> tuple:
    """(salaries, rosters, games, props, implied, starters, history, tiers)."""
    from edge.odds.cli import scraped_client
    client = scraped_client(R.SPORT, "dfs")
    sal = dfs.fetch_draftables(gid)
    rosters = Rosters(sal)
    games = dfs_slate.slate_games(sal)
    props, event_players, books = R.player_props(client, rosters, games, sal, Audit("props"))
    implied, _ = R.game_lines(client, games, event_players, sal)
    teams = {R.team(g[s]) for g in games.values() for s in ("home", "away")}
    starters, tags, *_ = R.lineup_news(sal, rosters, teams)
    rows = T.load()
    history, _ = T.join(sal, rows)
    return sal, games, props, implied, starters, tags, history, R.team_tiers(teams, rows), books


def market_check(gid: int) -> None:
    sal, games, props, implied, starters, tags, history, tiers, books = market_players(gid)
    print(f"{len(props)} players priced by {books}; {len({sal[k]['game'] for k in props})} games")

    def market(key):
        shape = R.shape_of(history.get(key), R.PROFILES[R.positions(sal[key]["position"])[0]])
        m, _ = R.means_from_props(props[key], shape)
        return ncaab_sim.fantasy_mean(m) if m else None

    truth = {k: market(k) for k in props}
    truth = {k: v for k, v in truth.items() if v is not None}
    y = np.array(list(truth.values()))
    x = np.array([sal[k]["salary"] / 1000.0 for k in truth])
    b, a = np.polyfit(x, y, 1)
    print(f"salary line on this board: {a:.2f} + {b:.2f} per $1K, r={np.corrcoef(x, y)[0, 1]:.2f}, "
          f"n={len(y)}  (shipped {R.SALARY_LINE[0]} + {R.SALARY_LINE[1]})")
    print(f"against the market: raw DK FPPG MAE "
          f"{np.mean([abs((sal[k].get('dk_fppg') or 0) - truth[k]) for k in truth if sal[k].get('dk_fppg')]):.2f}"
          f", 4.6 x salary {np.mean(np.abs(4.6 * x - y)):.2f}")
    variants = {"as shipped": {}, "w .3": {"weight": 0.3}, "w .7": {"weight": 0.7},
                "w 0 (salary)": {"weight": 0.0}, "w 1 (history)": {"weight": 1.0},
                "env 0": {"env_power": 0.0}, "env 1": {"env_power": 1.0}}
    listed = {R.team(sal[k]["team"]) for k in starters}
    for name, kw in variants.items():
        errs, rows = [], []
        for gm in sorted({sal[k]["game"] for k in truth}):
            board = []
            for key, s in sal.items():
                if not s.get("salary") or s.get("team") is None:
                    continue
                status = (s.get("dk_status") or "").upper()
                tag = tags.get(key)
                if status in R.OUT_STATUS or tag in R.ncaab_rotowire.OUT_TAGS:
                    continue
                t = R.team(s["team"])
                if s["game"] != gm and key in truth:
                    continue            # other games' props are not this hold-out's business
                if s["game"] != gm:
                    continue
                np_kw = {k: v for k, v in kw.items() if k in ("env_power", "weight")}
                fp, src = R.no_prop_points(s["salary"], history.get(key), s.get("dk_fppg"),
                                           key in starters, tiers.get(t), implied.get(t),
                                           team_listed=t in listed, **np_kw)
                board.append({"key": key, "team": t, "fp": fp, "source": src,
                              "known": history.get(key) is not None})
            R.apply_team_budget(board, implied)
            for r in board:
                if r["key"] in truth:
                    errs.append(r["fp"] - truth[r["key"]])
                    rows.append((sal[r["key"]]["name"], r["team"], sal[r["key"]]["salary"],
                                 round(truth[r["key"]], 1), round(r["fp"], 1), r["source"]))
        print(f"{name:14} n={len(errs)}  MAE {np.abs(errs).mean():.2f}  bias {np.mean(errs):+.2f}")
    for row in sorted(rows, key=lambda r: abs(r[3] - r[4]), reverse=True)[:12]:
        print("   ", row)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("draft_group", nargs="?", type=int)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--carryover", action="store_true")
    ap.add_argument("--year", type=int, default=T.PRIOR_YEAR)
    args = ap.parse_args()
    if args.fetch:
        fetch(args.year)
    if args.carryover:
        carryover()
    if args.draft_group:
        market_check(args.draft_group)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
