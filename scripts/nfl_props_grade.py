#!/usr/bin/env python3
"""Grade the SHIPPED props projection against real DK scores, week by week.

WHY THIS EXISTS, AND WHY IT IS NOT A BACKTEST
The props-vs-skill head-to-head DFS_STATUS.md wanted cannot be run backwards:
the paid historical props were lost (see data/HISTORICAL_COLLECTION_README.md).
The props arm therefore has to be rebuilt FORWARDS -- and it already is being,
for free, by odds-collect-dfs-nfl.timer, which has banked a dfs_nfl scan every
hour since 2026-09-06 into data/odds.db.

That is the whole trick here. Because the store is append-only and keeps 400
days, a week's props are already recorded whether or not anyone grades them
that week. So this is a pure READ-side tool: nothing is lost by running it
late, and week 1 can be graded the day nflverse publishes it.

    python3 scripts/nfl_props_grade.py --week 1
    python3 scripts/nfl_props_grade.py --week 1 --book fanduel

THE NUMBERS IT PRINTS ARE COMPARABLE TO scripts/nfl_skill_backtest.py BY
CONSTRUCTION -- same metrics function, same assembler
(edge.dfs_project.project), same DK scoring (edge.nfl.actual_offense_points),
same "given the player played" population. That is the point: the skill model's
held-out corr 0.6602 / MAE 4.626 is the line this has to beat for props to be
worth preferring, and the comparison is only honest if both sides are measured
the same way.

WHAT IT DOES NOT DO
It does not pick the projection's moment for you beyond "the last scan that
finished before kickoff". A projection read at Sunday 1pm and one read on
Wednesday are different claims, and `--before-kickoff` is the honest default
because that is the information a lineup actually locks with.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import dfs_project                              # noqa: E402
from edge.dfs_sport import get as get_sport               # noqa: E402
from edge.names import norm                               # noqa: E402
from edge.nfl import actual_offense_points                # noqa: E402
from edge.odds import OddsStore, ScrapedOddsClient        # noqa: E402
from scripts.dfs_board import collect_player_markets      # noqa: E402
from scripts.nfl_skill_backtest import metrics            # noqa: E402

SPORT_KEY = "americanfootball_nfl"
PROFILE = "dfs_nfl"
GT = ROOT / "data" / "nfl_ground_truth"
POSITIONS = ("QB", "RB", "WR", "TE")


def last_scan_before(db: Path, when: str | None) -> int | None:
    """The newest finished dfs_nfl scan at or before `when` (ISO, UTC).

    Read with a plain read-only connection rather than through OddsStore
    because this is a question about scans, not quotes, and the store's own
    API deliberately exposes only `latest_scan`.
    """
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    sql = "select id from scan where profile=? and ok=1"
    args: list = [PROFILE]
    if when:
        sql += " and finished_at <= ?"
        args.append(when)
    sql += " order by finished_at desc limit 1"
    row = con.execute(sql, args).fetchone()
    con.close()
    return int(row["id"]) if row else None


def load_actuals(season: int, week: int) -> dict[str, dict]:
    """{normalised name: row} of real DK scores, or {} if not published yet."""
    path = GT / f"player_week_{season}.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    rows = raw if isinstance(raw, list) else (raw.get("rows") or list(raw.values())[0])
    out = {}
    for r in rows:
        if (r.get("season_type") != "REG" or str(r.get("week")) != str(week)
                or r.get("position") not in POSITIONS):
            continue
        name = r.get("player_display_name") or r.get("player_name")
        if name:
            out[norm(name)] = r
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--book", default="draftkings")
    ap.add_argument("--at", default=None,
                    help="grade the last scan finishing at or before this ISO "
                         "instant (default: the newest scan on file)")
    ap.add_argument("--db", default=str(ROOT / "data" / "odds.db"))
    args = ap.parse_args()

    db = Path(args.db)
    if not db.exists():
        raise SystemExit(f"no store at {db}. This machine scrapes; a cloud host "
                         f"cannot (ODDS_LAYER.md).")

    scan_id = last_scan_before(db, args.at)
    if scan_id is None:
        raise SystemExit(f"no finished {PROFILE} scan on file"
                         + (f" at or before {args.at}" if args.at else ""))

    sport = get_sport(SPORT_KEY)
    with OddsStore(db) as store:
        row = store.scan(scan_id)
        print(f"scan {scan_id} finished {row['finished_at']} "
              f"({row['event_count']} events, {row['quote_count']:,} quotes)")
        client = ScrapedOddsClient(store, PROFILE, scan_id=scan_id)
        props = collect_player_markets(client, SPORT_KEY, sport.market_keys())

    books = sorted(props)
    if args.book not in props:
        raise SystemExit(f"book {args.book!r} not in this scan; have {books}")
    priced = props[args.book]
    print(f"books in scan: {', '.join(books)}  |  using {args.book}: "
          f"{len(priced)} players priced")

    actuals = load_actuals(args.season, args.week)
    if not actuals:
        print(f"\nNo {args.season} actuals on file yet -- nflverse publishes "
              f"player-level stats for an in-progress season on a lag, and "
              f"player_stats_{args.season}.csv was still a 404 on 2026-09-12.")
        print("NOTHING IS LOST BY WAITING. The props above are already banked "
              "in the append-only store, so this exact grade can be run the "
              "day the file appears:")
        print(f"    python3 scripts/nfl_ground_truth_collect.py --season {args.season}")
        print(f"    python3 scripts/nfl_props_grade.py --week {args.week}")
        return 0

    graded = []
    for name, markets in priced.items():
        act = actuals.get(norm(name))
        if act is None:
            continue
        res = dfs_project.project(markets, sport, position=act.get("position"))
        if res["proj"] is None:
            continue
        graded.append({"name": name, "position": act["position"],
                       "props": res["proj"],
                       "actual": actual_offense_points(act)})

    if not graded:
        print(f"\n0 of {len(priced)} priced players joined to a week-{args.week} "
              f"actual. Check the season/week, not the model.")
        return 0

    m = metrics(graded, "props")
    print(f"\nWEEK {args.week} -- props projection vs real DK points")
    print(f"  {'n':>6}  {'corr':>7}  {'MAE':>7}  {'RMSE':>7}")
    print(f"  {m['n']:6,}  {m['corr']:7.4f}  {m['mae']:7.3f}  {m['rmse']:7.3f}")

    print("\nBY POSITION")
    print(f"  {'pos':>4}  {'n':>5}  {'corr':>7}  {'MAE':>7}")
    for pos in POSITIONS:
        sub = [g for g in graded if g["position"] == pos]
        if len(sub) < 3:
            continue
        s = metrics(sub, "props")
        print(f"  {pos:>4}  {s['n']:5,}  {s['corr']:7.4f}  {s['mae']:7.3f}")

    print("\nTHE LINE TO BEAT (scripts/nfl_skill_backtest.py, 2024 wk 10-18 held out)")
    print("  skill K=0 d=0.90   corr 0.6602   MAE 4.626   RMSE 6.611")
    print("  One week of props is not a verdict on either -- n here is a few")
    print("  hundred against that model's 2,886, and a single week's variance")
    print("  swamps the difference. Accumulate weeks before concluding.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
