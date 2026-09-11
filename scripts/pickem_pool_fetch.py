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
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.pickem_cbs import DEFAULT_SESSION_PATH, SessionExpired, fetch_pool_text  # noqa: E402
from edge.pickem_week import current_week  # noqa: E402
from scripts import pickem_pool_import as pool_import  # noqa: E402

#: Same file deploy/pickem-capture-failed@.service appends to, and the same
#: one pages/4_*_Pickem.py surfaces at the top of the page.
#:
#: WHY THIS SCRIPT HAS TO WRITE THERE TOO. It runs as a soft-failing
#: `ExecStartPre=-` so that a dead CBS session can never cancel the
#: market-half capture, which is the half that can never be retaken. But
#: `-` discards the exit status, so OnFailure= never fires and the journal
#: line is the only trace -- and nobody reads the journal. That is precisely
#: how the LD_LIBRARY_PATH breakage (see edge/pickem_cbs.py's
#: ensure_chromium_libs) ran undetected: the unit reported success every
#: time while the CBS half had never once run. A soft failure still has to
#: leave a mark somewhere a human actually looks.
FAILURES_LOG = ROOT / "data" / "pickem_capture_failures.log"


def record_failure(reason: str) -> None:
    """Append one line about a fetch that did not happen. Never raises.

    Best-effort by construction: this runs on the failure path, and a
    problem writing the log must not replace the original error with a
    confusing second one.
    """
    try:
        FAILURES_LOG.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        one_line = " ".join(str(reason).split())[:300]
        with FAILURES_LOG.open("a") as fh:
            fh.write(f"{stamp} | cbs-pool-fetch | {one_line}\n")
    except OSError:
        pass


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
        record_failure(f"session expired: {e}")
        sys.exit(1)
    except Exception as e:                                      # noqa: BLE001
        # Anything else -- a browser that cannot start, CBS changing its
        # markup, the network being down. Caught rather than allowed to
        # propagate ONLY so it gets recorded; the exit status is unchanged,
        # and the traceback still reaches the journal via the message.
        print(f"CBS FETCH FAILED: {type(e).__name__}: {e}")
        print(f"Nothing written -- {pool_import.OUT.name} is untouched.")
        record_failure(f"{type(e).__name__}: {e}")
        sys.exit(1)

    pool_import.run(text, week, no_enrich=args.no_enrich, write=args.write)


if __name__ == "__main__":
    main()
