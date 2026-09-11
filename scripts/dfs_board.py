#!/usr/bin/env python3
"""Projected DFS board for any sport, from free scraped props.

    python3 scripts/dfs_board.py --sport NFL
    python3 scripts/dfs_board.py --sport NFL --pos QB --top 20
    python3 scripts/dfs_board.py --sport NBA          # once NBA is verified

One script for every sport, because everything sport-specific lives in
edge/dfs_sport.py: the markets, the scoring, the sigmas, the imputation and
the roster. Adding NBA is filling in that table and checking it against a live
payload -- not writing this file again.

This prints a BOARD (salary, projection, value), not lineups. The MLB
optimiser (edge/dfs_opt.py) is built around batting-order stacking and does
not transfer to NFL as-is; see edge/dfs_sport.py on what is deliberately not
abstracted.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge import dfs, dfs_project, dfs_sport  # noqa: E402
from edge.odds.cli import add_source_args, client_from_args, describe  # noqa: E402

#: DK lobby code -> odds sport key
SPORT_KEYS = {"MLB": "baseball_mlb", "NFL": "americanfootball_nfl",
              "NBA": "basketball_nba"}


def collect_player_markets(client, sport_key: str, markets: list[str]) -> dict:
    """{book: {player: {market: {side: price, point: x}}}} for the slate.

    Kept per BOOK rather than merged into a consensus. Two books' prices for
    one prop are a genuine disagreement, and averaging them here would hide
    which book is the outlier -- the very thing that turned out to matter for
    Fanatics on pick'em. The caller picks a book, or compares them.
    """
    out: dict[str, dict] = {}
    for ev in client.get_events(sport_key):
        payload = client.get_event_odds(sport_key, ev["id"], markets, "us")
        for bk in payload.get("bookmakers", []):
            slot = out.setdefault(bk["key"], {})
            names = {o["description"] for m in bk.get("markets", [])
                     for o in m.get("outcomes", []) if o.get("description")}
            for name in names:
                pm = dfs.player_markets(bk, name)
                if pm:
                    slot.setdefault(name, {}).update(pm)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sport", default="NFL", choices=sorted(SPORT_KEYS))
    ap.add_argument("--book", default="draftkings",
                    help="which book's props to project from (default: %(default)s)")
    ap.add_argument("--draft-group", type=int, default=None)
    ap.add_argument("--pos", default=None, help="filter to one DK position")
    ap.add_argument("--top", type=int, default=40)
    ap.add_argument("--min-salary", type=int, default=0)
    add_source_args(ap)
    args = ap.parse_args()

    sport_key = SPORT_KEYS[args.sport]
    sport = dfs_sport.get(sport_key)
    print(f"{args.sport}: {sport.notes}\n" if sport.notes else "")

    client = client_from_args(args, sport_key, consumer="dfs")
    print(describe(client))

    if args.draft_group:
        gid, salaries = args.draft_group, dfs.fetch_draftables(args.draft_group)
    else:
        gid, salaries = dfs.pick_priced_group(args.sport)
    if gid is None:
        print(f"no PRICED {args.sport} draft group found. DraftKings lists a "
              f"slate's players before it prices them, so this is normal more "
              f"than a few days out.", file=sys.stderr)
        return 1
    priced = sum(1 for v in salaries.values() if v.get("salary"))
    print(f"draft group {gid}: {priced}/{len(salaries)} players priced")

    by_book = collect_player_markets(client, sport_key, sport.market_keys())
    print("prop coverage: " + ", ".join(
        f"{b}={len(p)}" for b, p in sorted(by_book.items())) or "none")
    props = by_book.get(args.book, {})
    if not props:
        print(f"no props from {args.book!r}; have {sorted(by_book)}", file=sys.stderr)
        return 1

    rows = []
    unmatched = 0
    for name, player_markets in props.items():
        info = salaries.get(dfs.norm(name))
        if not info or not info.get("salary"):
            unmatched += 1
            continue
        # position feeds the sport's imputation rule -- NFL's touchdown rate
        # per yard differs by position, and the slate is the only place the
        # position is known. See edge/dfs_sport.py::_nfl_impute.
        res = dfs_project.project(player_markets, sport,
                                  position=info.get("position"))
        if res["proj"] is None:
            continue
        salary = info["salary"]
        if salary < args.min_salary:
            continue
        rows.append({"name": info["name"], "pos": info.get("position") or "",
                     "team": info.get("team") or "", "salary": salary,
                     "proj": res["proj"], "value": 1000.0 * res["proj"] / salary,
                     "imputed": res["imputed"], "components": res["components"]})

    print(f"projected {len(rows)} players "
          f"({unmatched} priced by the book but not on the slate)\n")

    if args.pos:
        rows = [r for r in rows if args.pos.upper() in r["pos"].upper()]
    rows.sort(key=lambda r: -r["value"])

    print(f"{'player':<24}{'pos':<6}{'team':<5}{'salary':>7}{'proj':>7}{'val':>6}  components")
    for r in rows[: args.top]:
        comp = " ".join(f"{k}={v:g}" for k, v in r["components"].items())
        star = "*" if r["imputed"] else " "
        print(f"{r['name'][:23]:<24}{r['pos'][:5]:<6}{r['team']:<5}"
              f"{r['salary']:>7}{r['proj']:>7.1f}{r['value']:>6.2f}{star} {comp[:60]}")
    print("\n* = at least one scoring component was imputed, not priced by a book.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
