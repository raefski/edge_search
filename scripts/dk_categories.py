#!/usr/bin/env python3
"""Print DraftKings' real category ids for a league, and audit PROP_CATEGORIES.

    python3 scripts/dk_categories.py --sport americanfootball_nfl
    python3 scripts/dk_categories.py --audit          # every league we scrape

WHY THIS EXISTS
`PROP_CATEGORIES` is a hardcoded id map, and a wrong id filters to nothing --
which is indistinguishable from a league that posts no props. That is how the
NFL entries stayed wrong: 1343 and 1344 never existed, 1342 was Receiving
rather than Passing, and the scan quietly returned receiving markets only.
Found 2026-09-06, by which point it had been true for as long as anything had
looked. The league payload names every category for free in one call, so there
is no reason for the map to be a guess -- run this after any DraftKings layout
change, and before shipping a new sport.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.arb.draftkings_league import (  # noqa: E402
    LEAGUE_IDS, PROP_CATEGORIES, DraftKingsLeague)

#: Words that mark a category as one a player-projection model would want.
PROP_WORDS = ("prop", "points", "rebound", "assist", "passing", "rushing",
              "receiving", "batter", "pitcher", "scorer", "milestone")


def audit_sport(dk: DraftKingsLeague, sport: str, show_all: bool = False) -> int:
    try:
        payload = dk.fetch(sport)
    except Exception as exc:
        print(f"  !! {type(exc).__name__}: {exc}")
        return 1
    cats = {int(c["id"]): (c.get("name") or "").strip()
            for c in (payload.get("categories") or []) if c.get("id")}
    if not cats:
        print("  (no categories -- league out of season or not served)")
        return 1

    subs_by_cat: dict[int, int] = {}
    for sc in payload.get("subcategories") or []:
        cid = sc.get("categoryId")
        if cid is not None:
            subs_by_cat[int(cid)] = subs_by_cat.get(int(cid), 0) + 1

    print(f"  {len(cats)} categories, {sum(subs_by_cat.values())} subcategories")

    missing = [cid for cid in PROP_CATEGORIES if cid not in cats]
    mislabelled = [(cid, PROP_CATEGORIES[cid], cats[cid]) for cid in PROP_CATEGORIES
                   if cid in cats
                   and PROP_CATEGORIES[cid].split()[0].lower() not in cats[cid].lower()]
    present = [cid for cid in PROP_CATEGORIES if cid in cats]

    if present:
        print("  PROP_CATEGORIES present here:")
        for cid in sorted(present):
            print(f"    {cid:>6}  {cats[cid]:<24} ({subs_by_cat.get(cid, 0)} subs)"
                  f"   [map says: {PROP_CATEGORIES[cid]}]")
    if mislabelled:
        print("  !! MISLABELLED -- the map's name does not match the book's:")
        for cid, ours, theirs in mislabelled:
            print(f"    {cid:>6}  map={ours!r}  book={theirs!r}")

    # A category we ask for that this league does not serve is only a problem
    # if the league PLAINLY should have it; MLB does not serve Passing Props.
    likely = [cid for cid in cats
              if any(w in cats[cid].lower() for w in PROP_WORDS)
              and cid not in PROP_CATEGORIES and subs_by_cat.get(cid, 0) > 1]
    if likely:
        print("  ?? prop-looking categories NOT in PROP_CATEGORIES:")
        for cid in sorted(likely):
            print(f"    {cid:>6}  {cats[cid]:<24} ({subs_by_cat.get(cid, 0)} subs)")

    if show_all:
        print("  all categories:")
        for cid in sorted(cats):
            print(f"    {cid:>6}  {cats[cid]}")
    return 0 if present else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sport", help="one sport key (default: audit them all)")
    ap.add_argument("--audit", action="store_true", help="every league in LEAGUE_IDS")
    ap.add_argument("--all", action="store_true", help="print every category, not just props")
    args = ap.parse_args()

    dk = DraftKingsLeague(state="ct")
    sports = [args.sport] if args.sport else sorted(LEAGUE_IDS)
    bad = 0
    for sport in sports:
        print(f"\n=== {sport} (league {LEAGUE_IDS.get(sport)}) ===")
        bad += audit_sport(dk, sport, show_all=args.all)
    print(f"\n{len(sports) - bad}/{len(sports)} league(s) served at least one "
          f"mapped prop category.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
