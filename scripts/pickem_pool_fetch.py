#!/usr/bin/env python3
"""Automated replacement for the copy-paste half of the Tuesday step -- and,
wired into deploy/pickem-capture@.service, for every deadline's CBS reading,
not just Tuesday's.

    python3 scripts/pickem_pool_fetch.py --week auto --write

Fetches the pool's Picks page with a stored login session
(scripts/pickem_session_bootstrap.py creates one) and feeds the result
through the exact parser and merge logic scripts/pickem_pool_import.py uses
for a manual paste -- see that module's `run()` -- so "turn CBS text into
pickem_current_week.csv rows" has one implementation, not two that can
drift apart.

WHY EVERY DEADLINE, NOT JUST TUESDAY. CBS's line freezes at post and never
moves, but the community percentages do NOT -- measured 2026-09-10, 13 of 16
games moved between Tuesday's reading and Wednesday night's, one by 14
points. A percentage stamped once on Tuesday and reused by every later
snapshot label is a stale number wearing a fresh timestamp, the same shape
of bug HANDOFF.md already recorded once for captured_at. Fetching before
every label is what makes comm_pct_* actually contemporaneous with the
snapshot it's filed under.

DRY RUN BY DEFAULT, matching every other script here: without --write this
parses and prints but touches nothing.

WHEN THE SESSION HAS EXPIRED: this exits 1 and writes NOTHING, leaving
pickem_current_week.csv exactly as it was rather than guessing. Recreate the
session with scripts/pickem_session_bootstrap.py. deploy/pickem-capture@.service
runs this on a soft-failing ExecStartPre (leading `-`) for exactly this
reason: an expired session must never block that deadline's market-half
capture, which is the time-critical half and the one that can never be
retaken.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.pickem_cbs import DEFAULT_SESSION_PATH, SessionExpired, fetch_pool_text  # noqa: E402
from edge.pickem_week import current_week  # noqa: E402
from scripts import pickem_pool_import as pool_import  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", default="auto",
                    help="pool week, or 'auto' to derive it from today's ET date")
    ap.add_argument("--pool-url", default=None,
                    help="overrides CBS_POOL_URL from the environment/.env")
    ap.add_argument("--session-path", default=str(DEFAULT_SESSION_PATH))
    ap.add_argument("--no-enrich", action="store_true",
                    help="skip the board lookup for kickoff times -- see "
                         "pickem_pool_import.py's own flag of the same name")
    ap.add_argument("--write", action="store_true",
                    help="actually write the CSV (default is a dry run)")
    args = ap.parse_args()

    if args.week == "auto":
        week = current_week()
        if week is None:
            print("current_week() says we're outside the regular season -- "
                  "pass --week explicitly if that's wrong (see "
                  "edge/pickem_week.py's SEASON_START).")
            sys.exit(1)
    else:
        week = int(args.week)

    try:
        text = fetch_pool_text(args.pool_url, args.session_path)
    except SessionExpired as e:
        print(f"SESSION EXPIRED: {e}")
        print(f"Nothing written -- {pool_import.OUT.name} is untouched.")
        sys.exit(1)

    pool_import.run(text, week, no_enrich=args.no_enrich, write=args.write)


if __name__ == "__main__":
    main()
