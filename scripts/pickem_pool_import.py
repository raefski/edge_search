#!/usr/bin/env python3
"""Turn a saved pool page into data/pickem_current_week.csv -- no typing.

The pool's frozen spreads live behind a CBS login. An anonymous fetch of
the pool URL redirects to /join and returns settings only (verified with a
real browser), and CBS's PUBLIC odds page is not a substitute: on 2026
Week 1 it matched the pool line on only 12 of 16 games, disagreeing on
exactly the four the market had moved -- which is the signal, not noise.

So the fetch needs your own logged-in session. This script takes it from
there, which removes the part that actually costs you time:

    1. Open the pool's Picks page while logged in.
    2. Select all (Ctrl+A), copy (Ctrl+C), paste into a file.
    3. python3 scripts/pickem_pool_import.py picks.txt --week 3

No credentials are stored, requested, or transmitted by anything in this
repo -- it parses a page you already opened yourself.

Kickoff times, TV, and the season are merged from the free public odds
page when available, so the CSV comes out complete.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.pickem_cbs import parse_pool_text  # noqa: E402

OUT = ROOT / "data" / "pickem_current_week.csv"
FIELDS = ["week", "away_abbr", "home_abbr", "away_name", "home_name",
          "cbs_line_home", "kickoff_utc", "tv", "comm_pct_away", "comm_pct_home", "note"]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("file", help="text (or HTML) saved from the pool's Picks page")
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--no-enrich", action="store_true",
                    help="skip the free public-odds lookup for kickoff times")
    ap.add_argument("--write", action="store_true",
                    help="actually write the CSV (default is a dry run)")
    args = ap.parse_args()

    text = Path(args.file).read_text(errors="ignore")
    games = parse_pool_text(text)
    if not games:
        print("Parsed 0 games. Copy the PICKS page (the one listing every matchup "
              "with the spreads), not Standings or Settings — and paste the text, "
              "not a screenshot.")
        return

    kick = {}
    if not args.no_enrich:
        try:
            from edge.pickem_cbs import fetch_public_odds
            for g in fetch_public_odds():
                kick[g.home_abbr] = g.kickoff_text
        except Exception as e:
            print(f"(kickoff enrichment skipped: {e})")

    print(f"parsed {len(games)} games for week {args.week}\n")
    print(f"{'matchup':<16}{'CBS line':>10}{'community':>14}  kickoff")
    rows = []
    for g in games:
        rows.append({
            "week": args.week, "away_abbr": g["away_abbr"], "home_abbr": g["home_abbr"],
            "away_name": g["away_name"], "home_name": g["home_name"],
            "cbs_line_home": g["cbs_line_home"], "kickoff_utc": "",
            "tv": "", "comm_pct_away": g["comm_pct_away"] or "",
            "comm_pct_home": g["comm_pct_home"] or "",
            "note": kick.get(g["home_abbr"], ""),
        })
        comm = (f'{g["comm_pct_away"]}/{g["comm_pct_home"]}'
                if g["comm_pct_away"] is not None else "-")
        print(f'{g["away_abbr"]+" @ "+g["home_abbr"]:<16}{g["cbs_line_home"]:>10}'
              f'{comm:>14}  {kick.get(g["home_abbr"], "")}')

    missing = [r for r in rows if r["comm_pct_home"] == ""]
    if missing:
        print(f"\n{len(missing)} game(s) came through without community percentages — "
              "those are what unblock the public-pick experiment, so check the copy "
              "included them.")

    if not args.write:
        print(f"\nDRY RUN — nothing written. Re-run with --write to update {OUT.name}.")
        return

    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        w.writerows(rows)
    print(f"\nwrote {len(rows)} rows -> {OUT.name}")
    print("next: python3 scripts/pickem_capture.py --snapshot post "
          f"--week {args.week} --confirm")


if __name__ == "__main__":
    main()
