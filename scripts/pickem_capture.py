#!/usr/bin/env python3
"""Record a timestamped line snapshot -- the weekly habit that unblocks 5f.

Two runs a week is the whole discipline:

    # Tuesday, right after CBS posts (the freeze). Do this FIRST -- the
    # market reading is only useful if it is contemporaneous with CBS's.
    python3 scripts/pickem_capture.py --snapshot post --week 3 --confirm

    # Before the first game of each day, once inactives are out.
    python3 scripts/pickem_capture.py --snapshot lock --week 3 --confirm

CBS's line and community percentages cannot be fetched -- they are behind a
login -- so they come from data/pickem_current_week.csv, which you fill in
from the pool screenshot. Everything else is pulled live.

DRY RUN BY DEFAULT, matching scripts/wnba_scout.py: without --confirm this
prints the credit estimate and writes nothing. A capture costs 2 credits
(spreads + totals, one region, one call for the whole slate).

Order matters on Tuesday: transcribe the screenshot into
data/pickem_current_week.csv, then run this within a few minutes, or the
"market at the moment CBS posted" reading is no longer that.
"""
from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.client import OddsAPIClient  # noqa: E402
from edge.pickem_live import MARKETS, REGIONS, fetch_week  # noqa: E402
from edge.pickem_log import LINE_LOG, Snapshot, append, utcnow  # noqa: E402

CURRENT_WEEK = ROOT / "data" / "pickem_current_week.csv"
CACHE_DIR = ROOT / "data" / "cache"
LEDGER = ROOT / "data" / "odds_api_credits.json"


def load_cbs(week: int) -> list[dict]:
    if not CURRENT_WEEK.exists():
        return []
    with CURRENT_WEEK.open() as f:
        return [r for r in csv.DictReader(f) if int(r["week"]) == week]


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--snapshot", required=True,
                    help="'post' (Tuesday freeze), 'lock' (pre-deadline), or a "
                         "free-form label for an extra mid-week reading")
    ap.add_argument("--week", type=int, required=True)
    ap.add_argument("--season", type=int, default=2026)
    ap.add_argument("--regions", default=REGIONS,
                    help="'us' (2 credits) or 'us,eu' to include Pinnacle (4 credits)")
    ap.add_argument("--confirm", action="store_true", help="actually spend credits and write")
    args = ap.parse_args()

    cost = len(MARKETS) * len(args.regions.split(","))
    cbs_rows = load_cbs(args.week)
    print(f"snapshot '{args.snapshot}' | season {args.season} week {args.week}")
    print(f"CBS rows from {CURRENT_WEEK.name}: {len(cbs_rows)}")
    print(f"estimated cost: {cost} credits ({len(MARKETS)} markets x "
          f"{len(args.regions.split(','))} region(s))")

    if not cbs_rows:
        print(f"\nNo week-{args.week} rows in {CURRENT_WEEK.name}. Transcribe the CBS "
              "screenshot there first -- a snapshot without CBS's number can't "
              "measure CBS bias, which is the point.")
        return

    if not args.confirm:
        print("\nDRY RUN -- nothing pulled, nothing written. Re-run with --confirm.")
        return

    client = OddsAPIClient(cache_dir=CACHE_DIR, ledger_path=LEDGER, dry_run=False)
    live = {g.home_abbr: g for g in fetch_week(client, regions=args.regions)}
    captured = utcnow()

    snaps, missing = [], []
    for r in cbs_rows:
        g = live.get(r["home_abbr"])
        if g is None:
            missing.append(f'{r["away_abbr"]}@{r["home_abbr"]}')
        snaps.append(Snapshot(
            season=args.season, week=args.week, snapshot=args.snapshot,
            captured_at=captured,
            away_team=r["away_abbr"], home_team=r["home_abbr"],
            kickoff_utc=(g.kickoff if g else r.get("kickoff_utc", "")),
            cbs_line_home=_f(r.get("cbs_line_home")),
            comm_pct_away=_f(r.get("comm_pct_away")),
            comm_pct_home=_f(r.get("comm_pct_home")),
            market_line_home=(g.live_line if g else None),
            market_line_mean=(g.live_line_mean if g else None),
            market_line_median=(g.live_line_median if g else None),
            market_total=(g.total if g else None),
            n_books=(g.n_books if g else 0),
            book_disagreement=(g.book_spread if g else None),
            book_lines=(g.book_lines if g else None),
        ))

    written = append(snaps)
    print(f"\nwrote {written} new rows -> {LINE_LOG.name}"
          f"{' (0 new: this snapshot was already recorded)' if not written else ''}")
    if missing:
        print(f"no live market found for: {', '.join(missing)} "
              "(logged with CBS data only)")

    print("\n(run `python3 scripts/pickem_blocked.py` to see how much closer this "
          "brings the blocked experiments)")

    with_both = [s for s in snaps if s.cbs_line_home is not None and s.market_line_home is not None]
    if with_both:
        biases = [s.cbs_line_home - s.market_line_home for s in with_both]
        print(f"\nCBS bias this snapshot (cbs - market), n={len(biases)}:")
        print(f"  mean {sum(biases)/len(biases):+.2f} pts | "
              f"min {min(biases):+.1f} | max {max(biases):+.1f}")
        if args.snapshot == "post":
            print("  ^ this is the number PICKEM_MODEL.md 5f has been waiting for.")


if __name__ == "__main__":
    main()
