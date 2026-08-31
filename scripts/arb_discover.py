#!/usr/bin/env python3
"""Refresh the Fanatics (Oddschecker) league-id cache.

Adding a Fanatics league used to cost a DevTools capture per league, which is
why three had ids and twenty-odd did not. There is no listing endpoint -- see
edge/arb/oddschecker_discover.py for the routes that were tried -- but a miss
costs one 404, so the ids are enumerated instead.

    python3 scripts/arb_discover.py                  # sweep 1..30000, ~7 min
    python3 scripts/arb_discover.py --max-id 60000   # wider
    python3 scripts/arb_discover.py --show           # print the cache, no probing

This makes tens of thousands of requests to a third party. Run it weekly at
most: a fixed league's id does not move, and the file merges rather than
replaces, so ids stay known even when a sweep catches a league between rounds.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from edge.arb import catalog                              # noqa: E402
from edge.arb import oddschecker_discover as discover     # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--max-id", type=int, default=discover.DEFAULT_MAX_ID)
    ap.add_argument("--min-id", type=int, default=1)
    ap.add_argument("--workers", type=int, default=discover.DEFAULT_WORKERS)
    ap.add_argument("--path", default=str(ROOT / discover.CACHE_PATH))
    ap.add_argument("--show", action="store_true", help="print the cache and exit")
    args = ap.parse_args()

    if not args.show:
        def progress(done, total, found):
            print(f"  {done}/{total} probed, {found} leagues found",
                  file=sys.stderr, flush=True)

        rows = discover.sweep(range(args.min_id, args.max_id),
                              workers=args.workers, progress=progress)
        discover.save(rows, args.path)
        print(f"\nswept {args.max_id - args.min_id} ids, "
              f"{len(rows)} answered; cache now holds "
              f"{len(discover.load(args.path))}", file=sys.stderr)

    resolved = discover.resolve(path=args.path)
    print(f"\n{len(resolved)} catalogued leagues:")
    for row in resolved:
        print(f"  {row['event_id']:>7}  {row['sport_key']:<34} {row['name']}")

    # A league in the catalog with no id is Fanatics coverage that is missing
    # rather than absent -- worth saying out loud, since it is the difference
    # between two books and three.
    have = {r["sport_key"] for r in resolved}
    missing = [lg for lg in catalog.LEAGUES if lg.fx_paths and lg.key not in have]
    if missing:
        print(f"\n{len(missing)} catalogued league(s) with no Fanatics id "
              f"(two books, not three):")
        for lg in missing:
            print(f"  {lg.key:<34} expected under {lg.fx_paths[0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
